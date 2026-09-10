import { and, desc, eq, inArray, ne, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { dealItemState, deals as dealsTable, msRefs, tasks } from "../db/schema.js";
import type { Task } from "@kassa/shared";
import {
  findWarehouseId,
  isCentralWarehouse,
  itemLocationFromMsName,
  storeForAcceptQueue,
  taskTitle,
} from "@kassa/shared";
import * as ms from "../clients/ms.js";
import { getMsRef } from "./bootstrap.js";
import { productCodesByMsIds, productNamesByMsIds, resolveAssortment } from "./catalog.js";
import * as deals from "./deals.js";
import * as taskFlow from "./taskFlow.js";

/** Перемещение вручную — только из отложки / обещания. */
const MOVEMENT_DEAL_KINDS = new Set(["deferred", "promise"]);
const MOVEMENT_DEAL_KIND_LABEL: Record<string, string> = {
  deferred: "Отложка",
  promise: "Обещание",
};

function toTask(row: typeof tasks.$inferSelect): Task {
  return {
    id: row.id,
    kind: row.kind as Task["kind"],
    dealNumber: row.dealNumber ?? undefined,
    dealItemId: row.dealItemId ?? undefined,
    store: row.store ?? undefined,
    assigneeRole: row.assigneeRole as Task["assigneeRole"],
    status: row.status as Task["status"],
    title: row.title,
    idempotencyKey: row.idempotencyKey ?? undefined,
    metadata: (row.metadata as Record<string, unknown> | null) ?? undefined,
    createdBy: row.createdBy,
    completedBy: row.completedBy ?? undefined,
    createdAt: row.createdAt.toISOString(),
    completedAt: row.completedAt?.toISOString(),
  };
}

function isReadableProductName(name: string | undefined | null): boolean {
  const s = (name ?? "").trim();
  if (!s) return false;
  // msId / uuid не показываем как название.
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return false;
  return true;
}

/** Подставляет полные названия позиций из каталога, если в metadata только productId. */
async function enrichTaskPositionNames(list: Task[]): Promise<Task[]> {
  const ids: string[] = [];
  for (const task of list) {
    const raw = task.metadata?.positions;
    if (!Array.isArray(raw)) continue;
    for (const row of raw) {
      if (!row || typeof row !== "object") continue;
      const p = row as { productId?: string; name?: string };
      if (p.productId && !isReadableProductName(p.name)) ids.push(p.productId);
    }
  }
  if (ids.length === 0) return list;
  const names = await productNamesByMsIds(ids);
  if (names.size === 0) return list;
  return list.map((task) => {
    const raw = task.metadata?.positions;
    if (!Array.isArray(raw)) return task;
    let changed = false;
    const positions = raw.map((row) => {
      if (!row || typeof row !== "object") return row;
      const p = row as { productId?: string; name?: string; quantity?: number };
      if (!p.productId || isReadableProductName(p.name)) return row;
      const name = names.get(p.productId);
      if (!name) return row;
      changed = true;
      return { ...p, name };
    });
    if (!changed) return task;
    return { ...task, metadata: { ...task.metadata, positions } };
  });
}

export async function listTasks(filters: {
  role?: string;
  status?: string;
  store?: string;
}): Promise<Task[]> {
  const clauses = [];
  if (filters.role) clauses.push(eq(tasks.assigneeRole, filters.role));
  if (filters.status) clauses.push(eq(tasks.status, filters.status));
  if (filters.store) clauses.push(eq(tasks.store, filters.store));
  // Сары ведутся только в разделе «Сары», не в общем списке задач.
  clauses.push(ne(tasks.kind, "sary_send"));
  const rows = await db
    .select()
    .from(tasks)
    .where(clauses.length ? and(...clauses) : undefined)
    .orderBy(desc(tasks.createdAt));
  const withNames = await enrichTaskPositionNames(rows.map(toTask));
  return enrichDealInfoOnTasks(withNames);
}

/** Вид и этап заявки — чтобы в очереди был цветной статус, как на доске. */
async function enrichDealInfoOnTasks(list: Task[]): Promise<Task[]> {
  const numbers = [...new Set(list.map((t) => t.dealNumber).filter((n): n is number => n != null))];
  if (numbers.length === 0) return list;
  const rows = await db
    .select({
      number: dealsTable.number,
      stage: dealsTable.stage,
      data: dealsTable.data,
    })
    .from(dealsTable)
    .where(inArray(dealsTable.number, numbers));
  const info = new Map<number, { kind: string; label: string; stage: string }>();
  for (const row of rows) {
    const kind =
      row.data && typeof row.data === "object" && "kind" in row.data
        ? String((row.data as { kind?: string }).kind ?? "")
        : "";
    const label = MOVEMENT_DEAL_KIND_LABEL[kind] || kind;
    info.set(row.number, { kind, label, stage: row.stage });
  }
  if (info.size === 0) return list;
  return list.map((task) => {
    if (!task.dealNumber) return task;
    const found = info.get(task.dealNumber);
    if (!found) return task;
    const meta = task.metadata ?? {};
    return {
      ...task,
      metadata: {
        ...meta,
        dealKind: typeof meta.dealKind === "string" && meta.dealKind ? meta.dealKind : found.kind,
        dealKindLabel:
          typeof meta.dealKindLabel === "string" && meta.dealKindLabel
            ? meta.dealKindLabel
            : found.label,
        dealStage: found.stage,
      },
    };
  });
}

export async function createTask(
  input: Omit<Task, "id" | "status" | "createdAt" | "createdBy">,
  createdBy: string
): Promise<Task> {
  if (input.idempotencyKey) {
    const [existing] = await db
      .select()
      .from(tasks)
      .where(eq(tasks.idempotencyKey, input.idempotencyKey))
      .limit(1);
    if (existing) return toTask(existing);
  }
  const [row] = await db.insert(tasks).values({
    kind: input.kind,
    dealNumber: input.dealNumber,
    dealItemId: input.dealItemId,
    store: input.store,
    assigneeRole: input.assigneeRole,
    title: input.title,
    idempotencyKey: input.idempotencyKey,
    metadata: input.metadata,
    createdBy,
  }).onConflictDoNothing().returning();
  if (!row && input.idempotencyKey) {
    const [existing] = await db
      .select()
      .from(tasks)
      .where(eq(tasks.idempotencyKey, input.idempotencyKey))
      .limit(1);
    if (existing) return toTask(existing);
  }
  if (!row) throw new Error("Не удалось создать задачу");
  return toTask(row);
}

/**
 * Завершение задачи. Перемещение идёт в два шага (решение Миши 31.07.2026):
 * отправитель отмечает «Отправлено» → товар «В пути» и появляется задача приёмки
 * у принимающего магазина; приёмка создаёт документ перемещения в МойСклад.
 *
 * После закрытия задачи движок формулы (`services/taskFlow.ts`) сам двигает этап
 * заявки и заводит следующую задачу цепочки.
 */
export async function completeTask(id: string, by: string): Promise<Task | null> {
  const [current] = await db.select().from(tasks).where(eq(tasks.id, id)).limit(1);
  if (!current) return null;
  if (current.status === "done") return toTask(current);

  if (current.kind === "sary_send") {
    throw new Error("Сары закрываются в разделе «Сары» — с реальным скрином перевода");
  }

  if (current.kind === "movement_accept") {
    const accepted = await acceptMovement(current, by);
    await taskFlow.onTaskCompleted(accepted, by).catch(() => {});
    return accepted;
  }

  // Перемещение без выбранных позиций ещё не оформлено — закрывать нечего.
  const meta = (current.metadata as MovementMeta | null) ?? {};
  if (current.kind === "movement" && meta.needsSetup) {
    throw new Error("Сначала оформите перемещение в карточке заявки: откуда, куда и позиции");
  }

  // Сначала задача приёмки и «в пути» — иначе при ошибке отправка уже done, а принять некому.
  if (current.kind === "movement") {
    await openAcceptance(current, by);
  }

  const [row] = await db
    .update(tasks)
    .set({ status: "done", completedBy: by, completedAt: new Date() })
    .where(eq(tasks.id, id))
    .returning();
  if (!row) return null;

  const task = toTask(row);
  await taskFlow.onTaskCompleted(task, by).catch(() => {});
  return task;
}

type TaskRow = typeof tasks.$inferSelect;

interface MovementMeta {
  positions?: Array<{ productId: string; quantity: number; name?: string } | string>;
  fromStoreMsId?: string;
  toStoreMsId?: string;
  from?: string;
  to?: string;
  productName?: string;
  sourceTaskId?: string;
  /** Задача-напоминание из движка: перемещение ещё не оформлено (нет позиций и складов). */
  needsSetup?: boolean;
  [key: string]: unknown;
}

type MovementPosition = {
  productId: string;
  quantity: number;
  name?: string;
  barcode?: string;
  code?: string;
  article?: string;
};

/**
 * Заголовок задачи перемещения. По заявке — единый канон «Сделать перемещение
 * (№N)» (созвон 04.09): позиции и клиент видны второй строкой карточки.
 * Свободное перемещение без заявки называем по товарам — номера у него нет.
 */
function movementTitle(
  kind: "movement" | "movement_accept",
  dealNumber: number | null | undefined,
  positions: Array<{ name?: string }>
): string {
  if (dealNumber) return taskTitle(kind, dealNumber);
  const names = positions
    .map((p) => (typeof p.name === "string" ? p.name.trim() : ""))
    .filter(Boolean);
  return names.length > 0 ? names.join(" · ") : taskTitle(kind);
}

/** Нормализуем позиции перемещения (отсекаем битый metadata со строками-именами). */
function movementPositions(raw: MovementMeta["positions"]): MovementPosition[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const out: MovementPosition[] = [];
  for (const row of raw) {
    if (typeof row === "string") {
      const name = row.trim();
      if (name) out.push({ productId: `name:${name}`, quantity: 1, name });
      continue;
    }
    if (!row || typeof row !== "object") continue;
    const productId = "productId" in row ? String(row.productId ?? "") : "";
    const quantity = "quantity" in row ? Number(row.quantity) : 0;
    const name =
      "name" in row && row.name != null && String(row.name).trim()
        ? String(row.name).trim()
        : undefined;
    if (!productId || !(quantity > 0)) continue;
    out.push(name ? { productId, quantity, name } : { productId, quantity });
  }
  return out;
}

/** Товар уехал: ставим позицию «в пути» и заводим задачу приёмки принимающей стороне. */
async function openAcceptance(row: TaskRow, by: string): Promise<void> {
  const meta = (row.metadata as MovementMeta | null) ?? {};
  const refs = await db.select().from(msRefs).where(eq(msRefs.kind, "store"));
  const refList = refs.map((r) => ({ id: r.msId, name: r.name }));

  const toStoreMsId =
    meta.toStoreMsId || (meta.to ? findWarehouseId(refList, String(meta.to)) : undefined);
  const fromStoreMsId =
    meta.fromStoreMsId || (meta.from ? findWarehouseId(refList, String(meta.from)) : undefined);
  const positions = movementPositions(meta.positions);

  if (!toStoreMsId || positions.length === 0) {
    throw new Error(
      "Нельзя отметить «Отправлено»: в задаче нет склада назначения или позиций. Откройте заявку и оформите перемещение заново."
    );
  }

  const targetName = refs.find((r) => r.msId === toStoreMsId)?.name ?? String(meta.to ?? "");
  const sourceName =
    (fromStoreMsId ? refs.find((r) => r.msId === fromStoreMsId)?.name : undefined) ??
    String(meta.from ?? "");
  const acceptRole: Task["assigneeRole"] = isCentralWarehouse(targetName)
    ? "logist"
    : "consultant";
  // Очередь принимающего отдела (Новокузнецкая → Пятницкая и т.п.).
  const acceptStore = storeForAcceptQueue(targetName) || targetName || row.store || "";
  const acceptMeta: MovementMeta = {
    ...meta,
    positions,
    fromStoreMsId: fromStoreMsId ?? meta.fromStoreMsId,
    toStoreMsId,
    from: sourceName || meta.from,
    to: targetName || meta.to,
    sourceTaskId: row.id,
    sentBy: by,
    sentByRole: row.assigneeRole,
    acceptStore,
  };

  const productTitle = movementTitle("movement_accept", row.dealNumber, positions);

  const idempotencyKey = `${row.id}:accept`;
  await db
    .insert(tasks)
    .values({
      kind: "movement_accept",
      dealNumber: row.dealNumber,
      dealItemId: row.dealItemId,
      store: acceptStore,
      assigneeRole: acceptRole,
      title: productTitle,
      idempotencyKey,
      metadata: acceptMeta,
      createdBy: by,
    })
    .onConflictDoNothing();

  const [acceptTask] = await db
    .select()
    .from(tasks)
    .where(eq(tasks.idempotencyKey, idempotencyKey))
    .limit(1);
  if (!acceptTask) {
    throw new Error("Не удалось создать задачу приёмки для принимающего отдела");
  }

  // Позиции заявки → «В пути» (по всем productId из задачи, не только dealItemId).
  if (row.dealNumber) {
    const itemIds = new Set<string>();
    if (row.dealItemId) itemIds.add(row.dealItemId);
    for (const p of positions) {
      if (p.productId && !p.productId.startsWith("name:")) itemIds.add(p.productId);
    }
    for (const itemId of itemIds) {
      await setItemState(row.dealNumber, itemId, "in_transit", "В пути", acceptMeta);
    }
  }
}

/** Приёмка: документ перемещения в МойСклад создаётся здесь, а не при постановке задачи. */
async function acceptMovement(row: TaskRow, by: string): Promise<Task> {
  const meta = (row.metadata as MovementMeta | null) ?? {};
  const movePositions = movementPositions(meta.positions).filter(
    (p) => p.productId && !p.productId.startsWith("name:")
  );
  if (!meta.fromStoreMsId || !meta.toStoreMsId || movePositions.length === 0) {
    throw new Error("В задаче приёмки нет данных о перемещении");
  }

  const refs = await db.select().from(msRefs).where(eq(msRefs.kind, "store"));
  const source = refs.find((r) => r.msId === meta.fromStoreMsId)?.payload as ms.MsRow | undefined;
  const target = refs.find((r) => r.msId === meta.toStoreMsId)?.payload as ms.MsRow | undefined;
  const org = await getMsRef("organization", "default");
  if (!source || !target || !org) throw new Error("Не найдены организация или склады МойСклад");

  const positions: ms.SalePosition[] = [];
  for (const item of movePositions) {
    const assortment = await resolveAssortment(item.productId);
    if (!assortment) throw new Error(`Товар ${item.productId} не найден в каталоге`);
    positions.push({
      assortmentHref: assortment.href,
      assortmentType: assortment.type,
      quantity: item.quantity,
      price: 0,
    });
  }
  const movement = await ms.createMove({
    organization: org.meta,
    sourceStore: source.meta,
    targetStore: target.meta,
    positions,
    description: `Касса MANSBAND · принял ${by}${row.dealNumber ? ` · заявка #${row.dealNumber}` : ""}`,
  });

  const [updated] = await db
    .update(tasks)
    .set({
      status: "done",
      completedBy: by,
      completedAt: new Date(),
      metadata: { ...meta, msMovementId: movement.id },
    })
    .where(eq(tasks.id, row.id))
    .returning();

  if (row.dealNumber) {
    const location = itemLocationFromMsName(target.name);
    const itemMeta = { ...meta, msMovementId: movement.id };
    const itemIds = new Set<string>();
    if (row.dealItemId) itemIds.add(row.dealItemId);
    for (const p of movePositions) {
      if (p.productId) itemIds.add(p.productId);
    }
    for (const itemId of itemIds) {
      // Приёмка сразу = отложка: этап сделки «Товар отложен».
      await setItemState(row.dealNumber, itemId, "reserved", location, itemMeta);
    }
  }
  return toTask(updated ?? row);
}

async function setItemState(
  dealNumber: number,
  itemId: string,
  state: string,
  location: string,
  metadata: Record<string, unknown>
): Promise<void> {
  await db
    .insert(dealItemState)
    .values({ dealNumber, itemId, state, location, metadata })
    .onConflictDoUpdate({
      target: [dealItemState.dealNumber, dealItemState.itemId],
      set: { state, location, metadata, updatedAt: new Date() },
    });
}

export async function createMovement(input: {
  idempotencyKey: string;
  fromStoreMsId: string;
  toStoreMsId: string;
  positions: Array<{ productId: string; quantity: number; name?: string }>;
  dealNumber?: number;
  dealItemId?: string;
  store?: string;
  assigneeRole: "consultant" | "logist" | "crm";
  title: string;
  metadata?: Record<string, unknown>;
}, createdBy: string): Promise<Task> {
  // С заявкой — только отложка / обещание. Без заявки — свободное перемещение из очереди задач.
  let dealKind: string | undefined;
  let dealKindLabel: string | undefined;
  if (input.dealNumber) {
    const deal = await deals.getByNumber(input.dealNumber);
    if (!deal) throw new Error(`Заявка #${input.dealNumber} не найдена`);
    if (!MOVEMENT_DEAL_KINDS.has(deal.kind)) {
      throw new Error("Перемещение по заявке можно создать только из отложки или обещания");
    }
    dealKind = deal.kind;
    dealKindLabel = MOVEMENT_DEAL_KIND_LABEL[deal.kind] ?? deal.kind;
  }

  const task = await db.transaction(async (tx) => {
    await tx.execute(sql`select pg_advisory_xact_lock(hashtext(${`movement:${input.idempotencyKey}`}))`);
    const [existing] = await tx
      .select()
      .from(tasks)
      .where(eq(tasks.idempotencyKey, input.idempotencyKey))
      .limit(1);
    if (existing) return toTask(existing);

    const refs = await tx.select().from(msRefs).where(eq(msRefs.kind, "store"));
    const source = refs.find((r) => r.msId === input.fromStoreMsId);
    const target = refs.find((r) => r.msId === input.toStoreMsId);
    if (!source || !target) throw new Error("Не найдены склады МойСклад");

    // Перемещение с центрального склада ведёт логист, из магазина — консультант
    // магазина-источника (п.15). Ответственность всегда на источнике.
    const sourceName = source.name;
    const assigneeRole: Task["assigneeRole"] = isCentralWarehouse(sourceName)
      ? "logist"
      : input.assigneeRole;
    // Позиции запоминаем в задаче: документ перемещения в МойСклад создаётся
    // только при приёмке, чтобы остаток не «прыгал» на склад-получатель заранее.
    for (const item of input.positions) {
      const assortment = await resolveAssortment(item.productId);
      if (!assortment) throw new Error(`Товар ${item.productId} не найден в каталоге`);
    }
    // Имена: из запроса → legacy metadata → каталог МойСклад.
    const legacyNames = Array.isArray(input.metadata?.positions)
      ? (input.metadata!.positions as unknown[]).filter((p): p is string => typeof p === "string")
      : [];
    const catalogNames = await productNamesByMsIds(input.positions.map((p) => p.productId));
    const catalogCodes = await productCodesByMsIds(input.positions.map((p) => p.productId));
    const positionsWithNames: MovementPosition[] = input.positions.map((p, i) => {
      const named = p as MovementPosition;
      const name =
        (isReadableProductName(named.name) ? named.name!.trim() : undefined) ||
        (legacyNames[i] && isReadableProductName(legacyNames[i]) ? legacyNames[i] : undefined) ||
        catalogNames.get(p.productId) ||
        undefined;
      const codes = catalogCodes.get(p.productId);
      const row: MovementPosition = {
        productId: p.productId,
        quantity: p.quantity,
      };
      if (name) row.name = name;
      if (codes?.barcode) row.barcode = codes.barcode;
      if (codes?.code) row.code = codes.code;
      if (codes?.article) row.article = codes.article;
      return row;
    });
    const title = movementTitle("movement", input.dealNumber, positionsWithNames);
    const authorRoleFromMeta =
      typeof input.metadata?.authorRole === "string" ? input.metadata.authorRole : undefined;
    const movementMeta = {
      ...input.metadata,
      positions: positionsWithNames,
      fromStoreMsId: input.fromStoreMsId,
      toStoreMsId: input.toStoreMsId,
      // UI-имена из формы приоритетнее (понятнее консультанту), иначе — как в МС.
      from: (typeof input.metadata?.from === "string" && input.metadata.from) || sourceName,
      to: (typeof input.metadata?.to === "string" && input.metadata.to) || target.name,
      needsSetup: false,
      authorRole: authorRoleFromMeta || input.assigneeRole,
      ...(dealKind ? { dealKind, dealKindLabel } : {}),
    };

    // Движок мог заранее поставить задачу-напоминание «Оформить перемещение».
    // Дозаполняем её, а не плодим вторую задачу по той же заявке.
    if (input.dealNumber) {
      const [pending] = await tx
        .select()
        .from(tasks)
        .where(
          and(
            eq(tasks.dealNumber, input.dealNumber),
            eq(tasks.kind, "movement"),
            eq(tasks.status, "pending")
          )
        )
        .limit(1);
      if (pending && (pending.metadata as MovementMeta | null)?.needsSetup) {
        const [filled] = await tx
          .update(tasks)
          .set({
            assigneeRole,
            title,
            store: input.store ?? pending.store,
            dealItemId: input.dealItemId ?? pending.dealItemId,
            idempotencyKey: input.idempotencyKey,
            metadata: movementMeta,
          })
          .where(eq(tasks.id, pending.id))
          .returning();
        if (filled) return toTask(filled);
      }
    }

    const [row] = await tx.insert(tasks).values({
      kind: "movement",
      dealNumber: input.dealNumber,
      dealItemId: input.dealItemId,
      store: input.store,
      assigneeRole,
      title,
      idempotencyKey: input.idempotencyKey,
      metadata: movementMeta,
      createdBy,
    }).returning();
    if (!row) throw new Error("Не удалось создать задачу перемещения");

    if (input.dealNumber && input.dealItemId) {
      // В location всегда имя из справочника расположений, а не msId склада:
      // это же значение видно консультанту в поиске товара и в карточке заявки.
      const location = itemLocationFromMsName(sourceName);
      await tx.insert(dealItemState).values({
        dealNumber: input.dealNumber,
        itemId: input.dealItemId,
        state: "needs_move",
        location,
        metadata: movementMeta,
      }).onConflictDoUpdate({
        target: [dealItemState.dealNumber, dealItemState.itemId],
        set: {
          state: "needs_move",
          location,
          metadata: movementMeta,
          updatedAt: new Date(),
        },
      });
    }
    return toTask(row);
  });

  // По заявке — всегда уводим на «Ждет товар» (отложка / обещание).
  if (input.dealNumber && dealKind) {
    const deal = await deals.getByNumber(input.dealNumber).catch(() => null);
    if (deal && deal.stage !== "Ждет товар") {
      await deals.updateStage(String(input.dealNumber), "Ждет товар", createdBy).catch(() => {});
    }
    // Товар ещё едет — откладывать рано; снимаем висящую «Сделать отложку».
    await db
      .update(tasks)
      .set({
        status: "cancelled",
        completedBy: createdBy,
        completedAt: new Date(),
      })
      .where(
        and(
          eq(tasks.dealNumber, input.dealNumber),
          eq(tasks.kind, "reserve"),
          eq(tasks.status, "pending")
        )
      )
      .catch(() => {});
  }

  return task;
}
