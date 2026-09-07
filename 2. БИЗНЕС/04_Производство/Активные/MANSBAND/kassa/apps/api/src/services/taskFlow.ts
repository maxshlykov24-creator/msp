import { and, eq, inArray, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { dealItemState, deals as dealsTable, tasks } from "../db/schema.js";
import type { Deal, Task, TaskAssigneeRole, TaskKind } from "@kassa/shared";
import {
  CDEK_LOCATION,
  CENTRAL_WAREHOUSE,
  STORE_TO_WAREHOUSE,
  flowsForStage,
  taskFlowOf,
  taskTitle,
} from "@kassa/shared";
import * as deals from "./deals.js";
import * as financeQueue from "./queue.js";
import { appendSystemAudit } from "./audit.js";

/**
 * Движок формулы задач (созвон 05.08.2026, п.7–9). Реестр правил лежит в
 * `@kassa/shared` (`taskFlow.ts`), здесь — только исполнение:
 *
 * - заявка встала на этап → появились задачи по правилам этапа;
 * - задачу закрыли → заявка автоматически перешла на следующий этап и, если
 *   нужно, родилась следующая задача цепочки;
 * - вышел срок отложки → задача колл-менеджеру, а после его звонка (если срок
 *   не продлили) — задача консультанту снять отложку.
 *
 * Смена этапа полностью автоматическая: консультант нажимает «Готово» и не
 * выбирает этап руками (решение владельца 06.08.2026).
 */

const SYSTEM_ACTOR = "Касса (авто)";

interface NewTask {
  kind: TaskKind;
  title: string;
  assigneeRole: TaskAssigneeRole;
  store?: string;
  dealNumber?: number;
  idempotencyKey: string;
  metadata?: Record<string, unknown>;
}

/** Вставка задачи без дублей: idempotencyKey уникален в БД. */
async function insertTask(input: NewTask, createdBy: string): Promise<void> {
  const metadata = input.metadata
    ? (JSON.parse(JSON.stringify(input.metadata)) as Record<string, unknown>)
    : null;
  const inserted = await db
    .insert(tasks)
    .values({
      kind: input.kind,
      title: input.title,
      assigneeRole: input.assigneeRole,
      store: input.store ?? null,
      dealNumber: input.dealNumber ?? null,
      idempotencyKey: input.idempotencyKey,
      metadata,
      createdBy,
    })
    .onConflictDoNothing()
    .returning({ id: tasks.id });
  // П9: автоматически рождённая задача — событие в истории изменений.
  if (inserted[0]) {
    await appendSystemAudit({
      action: `Создана задача «${input.title}»`,
      entityType: "task",
      entityId: inserted[0].id,
      metadata: { kind: input.kind, dealNumber: input.dealNumber, trigger: createdBy },
    });
  }
}

/** Есть ли незакрытая задача этих видов (cancelled не блокирует новую). */
async function hasTaskForDeal(dealNumber: number, kinds: TaskKind[]): Promise<boolean> {
  const rows = await db
    .select({ id: tasks.id })
    .from(tasks)
    .where(
      and(
        eq(tasks.dealNumber, dealNumber),
        inArray(tasks.kind, kinds),
        eq(tasks.status, "pending")
      )
    )
    .limit(1);
  return rows.length > 0;
}

/**
 * Заявка встала на этап — ставим задачи по правилам. Вызывается при создании
 * заявки и при любой смене этапа, включая автоматическую.
 */
export async function onDealStage(deal: Deal, who: string, depth = 0): Promise<void> {
  if (depth > 3) return; // защита от циклов в правилах
  const rules = flowsForStage(deal.stage, deal.kind);
  for (const rule of rules) {
    if (rule.kind === "movement") {
      // Перемещение уже оформляли по этой заявке — второй раз не напоминаем.
      if (await hasTaskForDeal(deal.number, ["movement", "movement_accept"])) continue;
      await insertTask(
        {
          kind: "movement",
          title: taskTitle("movement", deal.number),
          // Куда везти — известно (магазин заявки), откуда — выбирает консультант
          // в карточке. После выбора источника задача уходит логисту или
          // консультанту магазина-источника (см. tasks.createMovement).
          assigneeRole: "consultant",
          store: deal.store,
          dealNumber: deal.number,
          idempotencyKey: `movement-setup:${deal.id}`,
          // Клиент и телефон нужны и в перемещении: любая отложка привязана к
          // человеку, консультант должен видеть, кому везём (созвон 04.09).
          metadata: deferredTaskMeta(deal, deal.reservedUntil ?? deal.deferredUntil, {
            needsSetup: true,
            to: deal.store,
            source: who,
          }),
        },
        who
      );
      continue;
    }

    if (rule.kind === "reserve") {
      if (await hasTaskForDeal(deal.number, ["reserve"])) continue;
      await insertTask(
        {
          kind: "reserve",
          // Заголовок единый: «Сделать отложку (№N)» (созвон 04.09). Клиент и
          // позиции — в metadata, очередь показывает их второй строкой.
          title: taskTitle("reserve", deal.number),
          assigneeRole: "consultant",
          store: deal.store,
          dealNumber: deal.number,
          idempotencyKey: `reserve:${deal.id}`,
          // source: кто дал команду отложить — колл-менеджер из amo или касса.
          // Владелец отложки остаётся пустым до продажи (см. deals.convertDealKind).
          metadata: deferredTaskMeta(deal, deal.reservedUntil ?? deal.deferredUntil, {
            source: who,
          }),
        },
        who
      );
      continue;
    }

    if (
      rule.kind === "assemble_cdek" ||
      rule.kind === "call_courier" ||
      rule.kind === "take_to_cdek" ||
      rule.kind === "pickup_from_cdek"
    ) {
      if (await hasTaskForDeal(deal.number, [rule.kind])) continue;
      await insertTask(
        {
          kind: rule.kind,
          title: taskTitle(rule.kind, deal.number),
          assigneeRole:
            rule.assignee === "logist" ? "logist" : rule.assignee === "crm" ? "crm" : "consultant",
          store: deal.store,
          dealNumber: deal.number,
          idempotencyKey: `${rule.kind}:${deal.id}`,
          metadata: deliveryTaskMeta(deal),
        },
        who
      );
    }
  }
}

/**
 * Задачу закрыли: двигаем этап заявки и заводим следующую задачу цепочки.
 * Перемещение (movement → movement_accept) живёт в `services/tasks.ts`, потому
 * что там же создаётся документ в МойСклад.
 */
export async function onTaskCompleted(task: Task, who: string, depth = 0): Promise<void> {
  const rule = taskFlowOf(task.kind);
  if (!rule || !task.dealNumber) return;

  if (task.kind === "reserve_call") {
    await onReserveCallDone(task, who);
    return;
  }

  // Отложку разобрали: отметка в истории заявки и примечание в amoCRM —
  // колл-менеджер должен видеть это в карточке, не заходя в кассу.
  if (task.kind === "sary_send") {
    await financeQueue.markSarySentByDeal(task.dealNumber).catch(() => {});
    return;
  }

  if (task.kind === "unreserve") {
    await deals.addComment(String(task.dealNumber), "Отложка убрана", who).catch(() => {});
    // Товар вернулся на полку магазина заявки (П3: расположение на каждом переходе).
    await setDealItemsLocation(task.dealNumber, storeLocation(task.store), who, {
      state: "in_store",
    }).catch(() => {});
    return;
  }

  // Отложку сделали: позиции физически отложены в магазине заявки.
  if (task.kind === "reserve") {
    await setDealItemsLocation(task.dealNumber, storeLocation(task.store), who, {
      state: "reserved",
    }).catch(() => {});
  }

  // Расположение позиций по СДЭК: в кассе нет интеграции с СДЭК, поэтому
  // локацию двигают сами задачи (правки владельца 10.08.2026, п.10).
  // Единый статус «СДЭК» — созвон 20.08; у клиента посылка или вернулась к нам,
  // видно по этапу заявки (Отправлен / Не выкуплен).
  if (task.kind === "take_to_cdek" || task.kind === "pickup_from_cdek") {
    await setDealItemsLocation(task.dealNumber, CDEK_LOCATION, who, {
      state: task.kind === "take_to_cdek" ? "in_transit" : "in_store",
    }).catch(() => {});
  }

  if (rule.nextStage) {
    const deal = await deals.getByNumber(task.dealNumber);
    if (!deal) return;
    // Закрытую заявку автоматика не двигает — там решает РОП.
    if (deal.stage === "Успех" || deal.stage === "Провал") return;
    if (deal.stage !== rule.nextStage) {
      const updated = await deals.updateStage(String(task.dealNumber), rule.nextStage, who, {
        reason: `Задача «${rule.label}» выполнена`,
      });
      if (updated) {
        await appendSystemAudit({
          action: `Заявка #${task.dealNumber}: этап «${deal.stage}» → «${rule.nextStage}» (задача «${rule.label}» выполнена)`,
          entityType: "deal",
          entityId: String(task.dealNumber),
          before: { stage: deal.stage },
          after: { stage: rule.nextStage },
        });
        await onDealStage(updated, who, depth + 1);
      }
    }
  }
}

/** Расположение из справочника по магазину заявки («На Пятницкой» → «На Новокузнецкой»). */
function storeLocation(store: string | undefined | null): string {
  const wh = store ? STORE_TO_WAREHOUSE[store as keyof typeof STORE_TO_WAREHOUSE] : null;
  return wh ?? store ?? "";
}

/** Ставит всем позициям заявки одно расположение (например «СДЭК» или «В пути»). */
async function setDealItemsLocation(
  dealNumber: number,
  location: string,
  who: string,
  opts: { state?: string } = {}
): Promise<void> {
  if (!location) return;
  const deal = await deals.getByNumber(dealNumber);
  if (!deal) return;
  const state = opts.state ?? "in_transit";
  for (const item of deal.items) {
    if (!item.productId) continue;
    await db
      .insert(dealItemState)
      .values({
        dealNumber,
        itemId: item.productId,
        state,
        location,
        metadata: { by: who },
      })
      .onConflictDoUpdate({
        target: [dealItemState.dealNumber, dealItemState.itemId],
        set: { state, location, updatedAt: new Date() },
      });
  }
}

/**
 * Колл-менеджер отзвонился по просроченной отложке. Если срок продлили — цепочка
 * гаснет, товар остаётся у клиента. Если нет — консультанту прилетает задача
 * разобрать отложку. Этап заявки при этом не меняем (решение владельца).
 */
function dealPositionsMeta(deal: Deal) {
  return deal.items.map((item) => ({
    productId: item.productId,
    quantity: item.qty,
    name: item.name,
    barcode: item.barcode,
  }));
}

function deferredTaskMeta(deal: Deal, until: string | undefined, extra?: Record<string, unknown>) {
  return {
    reservedUntil: until,
    client: deal.clientName,
    phone: deal.clientPhone,
    consultant: deal.consultant,
    route: deal.store,
    dealKind: deal.kind,
    dealKindLabel: deal.kind === "promise" ? "Обещание" : "Отложка",
    positions: dealPositionsMeta(deal),
    ...extra,
  };
}

function deliveryTaskMeta(deal: Deal, extra?: Record<string, unknown>) {
  return {
    stage: deal.stage,
    client: deal.clientName,
    phone: deal.clientPhone,
    route: deal.store,
    dealKind: deal.kind,
    dealKindLabel: "Доставка",
    positions: dealPositionsMeta(deal),
    ...extra,
  };
}

async function onReserveCallDone(task: Task, who: string): Promise<void> {
  const deal = await deals.getByNumber(task.dealNumber!);
  if (!deal) return;
  const until = deal.reservedUntil ?? deal.deferredUntil;
  if (until && until >= todayYmd()) return; // срок продлили — снимать нечего

  await insertTask(
    {
      kind: "unreserve",
      title: taskTitle("unreserve", deal.number),
      assigneeRole: "consultant",
      store: deal.store,
      dealNumber: deal.number,
      idempotencyKey: `unreserve:${deal.id}`,
      metadata: deferredTaskMeta(deal, until, { afterCall: true }),
    },
    who
  );
}

/** Дата и час по Москве: сервер живёт в UTC, а регламент отложек — по МСК. */
function moscowNow(): { ymd: string; hour: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return { ymd: `${get("year")}-${get("month")}-${get("day")}`, hour: Number(get("hour")) };
}

function todayYmd(): string {
  return moscowNow().ymd;
}

/** Следующий календарный день после YYYY-MM-DD. */
function nextDay(ymd: string): string {
  const d = new Date(`${ymd}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

/**
 * Просроченные отложки. Колл-менеджеру задача «Связаться по отложке» ставится
 * сразу по просрочке, консультанту «Убрать отложку» — на следующий день после
 * крайней даты, не раньше 10:00 по Москве (правки владельца 10.08.2026, п.1.3).
 * Ключ идемпотентности задачи снятия — по сделке, чтобы за несколько дней
 * просрочки не появилось несколько одинаковых задач.
 */
export async function scanOverdueReserves(): Promise<number> {
  const { ymd: today, hour } = moscowNow();
  const rows = await db
    .select({ data: dealsTable.data })
    .from(dealsTable)
    .where(
      and(
        eq(dealsTable.stage, "Товар отложен"),
        sql`coalesce(${dealsTable.data}->>'reservedUntil', ${dealsTable.data}->>'deferredUntil') < ${today}`
      )
    )
    .limit(200);

  let created = 0;
  for (const row of rows) {
    const deal = row.data as Deal;
    const until = deal.reservedUntil ?? deal.deferredUntil;
    if (!until || until >= today) continue;

    await insertTask(
      {
        kind: "reserve_call",
        title: taskTitle("reserve_call", deal.number, `срок вышел ${until}`),
        assigneeRole: "crm",
        store: deal.store,
        dealNumber: deal.number,
        idempotencyKey: `reserve-call:${deal.id}:${until}`,
        metadata: deferredTaskMeta(deal, until),
      },
      SYSTEM_ACTOR
    );
    created += 1;

    // Консультанту магазина — на следующий день после крайней даты, с 10:00 МСК.
    const dueDay = nextDay(until);
    if (today < dueDay || (today === dueDay && hour < 10)) continue;
    await insertTask(
      {
        kind: "unreserve",
        title: taskTitle("unreserve", deal.number),
        assigneeRole: "consultant",
        store: deal.store,
        dealNumber: deal.number,
        idempotencyKey: `unreserve:${deal.id}`,
        metadata: deferredTaskMeta(deal, until),
      },
      SYSTEM_ACTOR
    );
    created += 1;
  }
  await cancelStaleDeferredTasks(today);
  return created;
}

/**
 * Отложку/обещание провели как продажу/компанию/аренду — снимаем задачи
 * «Сделать отложку» / просрочку резерва. Перемещения в пути не трогаем.
 */
export async function cancelOnKindConvert(dealNumber: number): Promise<void> {
  await db
    .update(tasks)
    .set({ status: "cancelled", completedBy: SYSTEM_ACTOR, completedAt: new Date() })
    .where(
      and(
        eq(tasks.dealNumber, dealNumber),
        eq(tasks.status, "pending"),
        inArray(tasks.kind, ["reserve", "reserve_call", "unreserve"])
      )
    );
}

/**
 * Продлили срок / ушли с этапа «Товар отложен» / закрыли заявку —
 * висящие reserve_call / unreserve снимаем.
 */
async function cancelStaleDeferredTasks(today: string): Promise<void> {
  const open = await db
    .select({ id: tasks.id, dealNumber: tasks.dealNumber, kind: tasks.kind })
    .from(tasks)
    .where(
      and(
        inArray(tasks.kind, ["unreserve", "reserve_call"]),
        eq(tasks.status, "pending")
      )
    )
    .limit(300);
  for (const task of open) {
    if (!task.dealNumber) continue;
    const deal = await deals.getByNumber(task.dealNumber);
    if (!deal) {
      await db.update(tasks).set({ status: "cancelled" }).where(eq(tasks.id, task.id));
      continue;
    }
    // Заявка уже не в отложке — физически разбирать не по этой задаче.
    if (deal.stage !== "Товар отложен") {
      await db.update(tasks).set({ status: "cancelled" }).where(eq(tasks.id, task.id));
      continue;
    }
    const until = deal.reservedUntil ?? deal.deferredUntil;
    if (until && until >= today) {
      await db.update(tasks).set({ status: "cancelled" }).where(eq(tasks.id, task.id));
    }
  }
}