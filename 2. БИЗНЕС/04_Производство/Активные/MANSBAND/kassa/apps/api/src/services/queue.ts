import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { and, desc, eq, inArray, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { accountBalances, expenses, queue, queuePayouts, sary, tasks, deals as dealsTable, certificates, ledger, auditLog, dealItemState } from "../db/schema.js";
import type { AccountBalance, Deal, Expense, ExpenseSplit, QueueItem, SaryPayout } from "@kassa/shared";
import { paymentKindOf, findPaymentMethod } from "@kassa/shared";
import { withOperationLock } from "../lib/idempotency.js";
import type { AuditActor } from "./audit.js";
import { broadcast } from "../ws/hub.js";

// ── Очередь Эдвина (сдача / возврат «не выдано») ────────────────────

function toQueueItem(
  r: typeof queue.$inferSelect,
  payouts: Array<typeof queuePayouts.$inferSelect> = []
): QueueItem {
  return {
    id: r.id,
    kind: r.kind as QueueItem["kind"],
    dealNumber: r.dealNumber,
    client: r.client,
    amount: r.amount / 100,
    destination: r.destination,
    status: r.status as QueueItem["status"],
    issuedAmount: r.issuedAmount / 100,
    payouts: payouts.map((p) => ({
      id: p.id,
      amount: p.amount / 100,
      methodId: p.methodId,
      issuedBy: p.issuedBy,
      issuedAt: p.issuedAt.toISOString(),
      note: p.note ?? undefined,
    })),
    createdAt: r.createdAt.toISOString(),
    issuedAt: r.issuedAt?.toISOString(),
    issuedBy: r.issuedBy ?? undefined,
    issueMethod: r.issueMethod ?? undefined,
    issueAccount: r.issueAccount ?? undefined,
    metadata: (r.metadata as Record<string, unknown> | null) ?? undefined,
  };
}

export async function listQueue(): Promise<QueueItem[]> {
  const rows = await db.select().from(queue).orderBy(desc(queue.createdAt));
  const payouts = await db.select().from(queuePayouts).orderBy(queuePayouts.issuedAt);
  const byQueue = new Map<string, Array<typeof queuePayouts.$inferSelect>>();
  for (const p of payouts) {
    byQueue.set(p.queueId, [...(byQueue.get(p.queueId) ?? []), p]);
  }
  return rows.map((r) => toQueueItem(r, byQueue.get(r.id) ?? []));
}

export async function addQueue(
  item: Omit<QueueItem, "id" | "createdAt" | "status" | "issuedAmount" | "payouts"> & {
    status?: string;
  }
) {
  if (item.metadata?.source === "money_cancellation") throw Object.assign(new Error("Отмену подтверждает руководитель в карточке заявки"), {statusCode:403});
  const amount = Math.round(item.amount * 100);
  if (!Number.isSafeInteger(amount) || amount < 0) throw new Error("Некорректная сумма задачи");
  const row = await db.transaction(async tx => {
    const [sourceRow]=await tx.select().from(dealsTable).where(eq(dealsTable.number,item.dealNumber)).for("update");
    const source=sourceRow?.data as Deal | undefined;
    if(source?.moneyCancellation || (source && item.kind==="refund" && ["rental","deferred","cert_plastic","cert_digital"].includes(source.kind)))throw Object.assign(new Error("Денежную отмену подтверждает руководитель в карточке заявки"),{statusCode:409});
    const key = JSON.stringify([item.kind, item.dealNumber, item.dealNumber > 0 ? "" : item.destination]);
    await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${key}, 713))`);
    // Выполненная обязанность тоже является дублем. Повтор события не создаёт выплату заново.
    const [existing] = await tx.select().from(queue).where(and(
      eq(queue.kind, item.kind), eq(queue.dealNumber, item.dealNumber),
      item.dealNumber > 0 ? undefined : eq(queue.destination, item.destination)
    )).limit(1);
    if (existing) {
      if ((existing.metadata as Record<string,unknown>)?.source === "money_cancellation") throw Object.assign(new Error("Подтверждённую сумму отмены нельзя изменить через очередь"), {statusCode:409});
      if (existing.amount === amount && existing.destination === item.destination) return existing;
      if (existing.issuedAmount > 0 || existing.status === "issued") throw Object.assign(new Error("Обязательство уже оплачено. Нельзя создавать повторную выплату при изменении суммы или реквизитов."), {statusCode:409});
      const [updated] = await tx.update(queue).set({amount,destination:item.destination,metadata:item.metadata,client:item.client}).where(eq(queue.id,existing.id)).returning();
      return updated;
    }
    const [created] = await tx.insert(queue).values({
      kind: item.kind, dealNumber: item.dealNumber, client: item.client,
      amount, destination: item.destination, status: item.status ?? "pending", metadata: item.metadata,
    }).returning();
    return created;
  });
  broadcast("queue.updated", {});
  return row ? toQueueItem(row) : null;
}

/**
 * Выдача из очереди. Без `amountRub` закрывает остаток целиком, с суммой —
 * пишет частичную выдачу и оставляет позицию в очереди до полного погашения.
 */
export async function issueQueue(
  id: string,
  input: {
    by: string;
    methodId: string;
    expectedIssuedAmount?: number;
    amountRub?: number;
    note?: string;
    metadata?: Record<string, unknown>;
  }
) {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1).for("update");
    if (!current) return null;

    if (["invoice", "invoice_check", "documents", "documents_hand"].includes(current.kind)) {
      throw new Error("Документ не является денежной выплатой");
    }
    const remaining = current.amount - current.issuedAmount;
    if (current.status === "issued" || remaining <= 0) {
      const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
      return { row: current, payouts: rows };
    }

    if (input.expectedIssuedAmount != null && Math.round(input.expectedIssuedAmount * 100) !== current.issuedAmount) {
      throw Object.assign(new Error("Остаток выплаты изменился. Обновите очередь перед новой выплатой."), { statusCode: 409 });
    }
    const [source]=await tx.select().from(dealsTable).where(eq(dealsTable.number,current.dealNumber));
    if((source?.data as Deal|undefined)?.isTest)throw Object.assign(new Error("Тестовая выплата исключена из финансового проведения"),{statusCode:409});
    const requested = input.amountRub == null ? remaining : Math.round(input.amountRub * 100);
    if (!Number.isSafeInteger(requested) || requested <= 0 || requested > remaining) {
      throw new Error("Сумма выплаты должна быть положительной и не превышать остаток");
    }
    const amount = requested;
    const issuedTotal = current.issuedAmount + amount;
    const closed = issuedTotal >= current.amount;

    const [payout] = await tx.insert(queuePayouts).values({
      queueId: id,
      amount,
      methodId: input.methodId,
      issuedBy: input.by,
      note: input.note ?? null,
    }).returning();
    if ((current.metadata as Record<string,unknown>)?.source === "money_cancellation") {
      if (!findPaymentMethod(input.methodId) || paymentKindOf(input.methodId) === "certificate") throw Object.assign(new Error("Выберите денежный способ возврата"), {statusCode:400});
      await tx.insert(ledger).values({dealNumber:current.dealNumber,methodId:input.methodId,
        kind:paymentKindOf(input.methodId)==="cash" && input.methodId!=="cash_edwin" ? "cash_out" : "refund_out",amount:-amount,
        idempotencyKey:`cancellation:${payout!.id}`,createdBy:input.by});
    }

    const [updated] = await tx
      .update(queue)
      .set({
        issuedAmount: issuedTotal,
        status: closed ? "issued" : "pending",
        issuedAt: closed ? new Date() : null,
        issuedBy: input.by,
        issueMethod: input.methodId,
        issueAccount: input.methodId,
        metadata: (current.metadata as Record<string, unknown>)?.source === "money_cancellation" ? {...(current.metadata as Record<string,unknown>),financialSyncError:"Документ МойСклад ещё не подтверждён"} : input.metadata ?? current.metadata ?? undefined,
      })
      .where(eq(queue.id, id))
      .returning();

    await tx
      .insert(accountBalances)
      .values({ account: input.methodId, balance: -amount, updatedBy: input.by })
      .onConflictDoUpdate({
        target: accountBalances.account,
        set: {
          balance: sql`${accountBalances.balance} - ${amount}`,
          updatedAt: new Date(),
          updatedBy: input.by,
        },
      });

    const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
    return { row: updated ?? current, payouts: rows };
  });
  if (!result) return null;
  broadcast("queue.updated", {});
  return toQueueItem(result.row, result.payouts);
}

/**
 * Выдача зарплаты (созвон 20.08, П5): каждая часть (нал / перевод) пишется
 * расходом категории «Зарплата» — баланс счёта списывает сам расход, поэтому
 * прямое списание из issueQueue здесь не используется (иначе двойной минус).
 */
export async function issueSalary(
  id: string,
  input: { by: string; methodId: string; amountRub?: number; expectedIssuedAmount?: number; note?: string }
): Promise<QueueItem | null> {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1).for("update");
    if (!current || current.kind !== "salary") return null;

    if (["invoice", "invoice_check", "documents", "documents_hand"].includes(current.kind)) {
      throw new Error("Документ не является денежной выплатой");
    }
    const remaining = current.amount - current.issuedAmount;
    if (current.status === "issued" || remaining <= 0) {
      const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
      return { row: current, payouts: rows };
    }

    if (input.expectedIssuedAmount != null && Math.round(input.expectedIssuedAmount * 100) !== current.issuedAmount) {
      throw Object.assign(new Error("Остаток выплаты изменился. Обновите очередь перед новой выплатой."), { statusCode: 409 });
    }
    const requested = input.amountRub == null ? remaining : Math.round(input.amountRub * 100);
    if (!Number.isSafeInteger(requested) || requested <= 0 || requested > remaining) {
      throw new Error("Сумма выплаты должна быть положительной и не превышать остаток");
    }
    const amount = requested;
    const issuedTotal = current.issuedAmount + amount;
    const closed = issuedTotal >= current.amount;

    await tx.insert(queuePayouts).values({
      queueId: id,
      amount,
      methodId: input.methodId,
      issuedBy: input.by,
      note: input.note ?? null,
    });

    const [updated] = await tx
      .update(queue)
      .set({
        issuedAmount: issuedTotal,
        status: closed ? "issued" : "pending",
        issuedAt: closed ? new Date() : null,
        issuedBy: input.by,
        issueMethod: input.methodId,
        issueAccount: input.methodId,
      })
      .where(eq(queue.id, id))
      .returning();

    // Расход «Зарплата» на выданную часть; баланс счёта уменьшает он.
    const [expenseRow] = await tx
      .insert(expenses)
      .values({
        category: "Зарплата",
        account: input.methodId,
        amount,
        splits: null,
        description: input.note ?? current.destination ?? "Зарплата",
        spentAt: new Date(),
        createdBy: input.by,
      })
      .returning();
    if (!expenseRow) throw new Error("Не удалось записать расход по зарплате");
    await tx
      .insert(accountBalances)
      .values({ account: input.methodId, balance: -amount, updatedBy: input.by })
      .onConflictDoUpdate({
        target: accountBalances.account,
        set: {
          balance: sql`${accountBalances.balance} - ${amount}`,
          updatedAt: new Date(),
          updatedBy: input.by,
        },
      });

    const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
    return { row: updated ?? current, payouts: rows };
  });
  if (!result) return null;
  broadcast("queue.updated", {});
  return toQueueItem(result.row, result.payouts);
}

/**
 * Закрытие неденежной позиции очереди (документы по продаже компании):
 * статус меняется, балансы счетов не трогаем.
 */
export async function closeQueue(id: string, by: string): Promise<QueueItem | null> {
  const [updated] = await db
    .update(queue)
    .set({ status: "issued", issuedAt: new Date(), issuedBy: by })
    .where(and(eq(queue.id, id), inArray(queue.kind, ["invoice", "invoice_check", "documents", "documents_hand"])))
    .returning();
  if (!updated) return null;
  broadcast("queue.updated", {});
  return toQueueItem(updated);
}

/** Документы передал консультант из карточки — закрываем задачу в очереди Миши. */
export async function closeQueueByDeal(dealNumber: number, kind: string, by: string): Promise<void> {
  if (!["invoice", "invoice_check", "documents", "documents_hand"].includes(kind)) throw new Error("Денежная задача закрывается только выплатой");
  const updated = await db
    .update(queue)
    .set({ status: "issued", issuedAt: new Date(), issuedBy: by })
    .where(and(eq(queue.dealNumber, dealNumber), eq(queue.kind, kind), eq(queue.status, "pending")))
    .returning({ id: queue.id });
  if (updated.length > 0) broadcast("queue.updated", {});
}

/**
 * Вернуть неденежную закрытую позицию в работу. Выплаты и балансы не откатываются.
 * Статусы заявки компании (документы/счёт) откатывает вызывающий слой.
 */
export async function reopenQueue(id: string, by: string): Promise<QueueItem | null> {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1).for("update");
    if (!current) return null;
    if (current.status !== "issued" && current.issuedAmount <= 0) {
      return { row: current, payouts: [] as Array<typeof queuePayouts.$inferSelect> };
    }

    const payouts = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
    if (payouts.length || current.issuedAmount > 0) {
      throw Object.assign(new Error("Выплата уже записана. Нужна отдельная корректировка, история выплат сохраняется."), { statusCode: 409 });
    }

    const [updated] = await tx
      .update(queue)
      .set({
        status: "pending",
        issuedAmount: 0,
        issuedAt: null,
        issuedBy: null,
        issueMethod: null,
        issueAccount: null,
      })
      .where(eq(queue.id, id))
      .returning();

    return { row: updated ?? current, payouts: [] as Array<typeof queuePayouts.$inferSelect> };
  });
  if (!result) return null;
  broadcast("queue.updated", {});
  return toQueueItem(result.row, result.payouts);
}

/**
 * После «Счёт оплачен» — сразу три задачи Миши (или две без ателье).
 * В UI доступны по порядку: документы → передать → ателье.
 */
export async function enqueueMishaChain(deal: Deal, _who: string): Promise<void> {
  const company = deal.companyName?.trim() || deal.clientName;
  const meta = {
    source: "deal",
    invoiceNo: deal.invoiceNo,
    invoiceDate: deal.invoiceDate,
    consultant: deal.consultant,
    companyName: deal.companyName,
    buyerName: deal.clientName,
    dealTotal: deal.total,
    dealPaid: deal.paid,
    atelierAmount: deal.atelierAmount ?? 0,
  };
  const common = {
    dealNumber: deal.number,
    client: company,
    status: "pending" as const,
    destination: company,
    metadata: meta,
  };

  await addQueue({ ...common, kind: "documents", amount: 0, metadata: { ...meta, chainStep: 1 } });
  await addQueue({
    ...common,
    kind: "documents_hand",
    amount: 0,
    metadata: { ...meta, chainStep: 2 },
  });
  const atelier = deal.atelierAmount ?? 0;
  if (atelier > 0 && deal.atelierStatus !== "paid") {
    await addQueue({
      ...common,
      kind: "atelier",
      amount: atelier,
      metadata: { ...meta, chainStep: 3 },
    });
  }
}

// ── Сары (реферальные выплаты) ──────────────────────────────────────

/** Сохраняем реальный скрин пачки на диск — без файла отметить «отправлено» нельзя. */
async function saveSaryScreenshot(photo: {
  filename: string;
  contentBase64: string;
}): Promise<string> {
  const buf = Buffer.from(photo.contentBase64.replace(/\s/g, ""), "base64");
  if (buf.length < 800) {
    throw new Error("Скриншот слишком маленький — приложите реальный скрин перевода");
  }
  const isJpeg = buf[0] === 0xff && buf[1] === 0xd8;
  const isPng = buf[0] === 0x89 && buf[1] === 0x50;
  const isWebp = buf.length > 12 && buf.toString("ascii", 0, 4) === "RIFF";
  if (!isJpeg && !isPng && !isWebp) {
    throw new Error("Нужен файл изображения (JPG/PNG/WebP)");
  }
  const dir = join(process.cwd(), "data", "sary-screenshots");
  await mkdir(dir, { recursive: true });
  const hash = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  const safe = (photo.filename || "sary.jpg").replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80);
  const name = `${new Date().toISOString().replace(/[:.]/g, "-")}_${hash}_${safe}`;
  await writeFile(join(dir, name), buf);
  return name;
}

function toSary(r: typeof sary.$inferSelect): SaryPayout {
  return {
    id: r.id,
    client: r.client,
    phone: r.phone,
    amount: r.amount / 100,
    reason: r.reason,
    refDealNumber: r.refDealNumber ?? undefined,
    status: r.status as SaryPayout["status"],
    screenshotAttached: r.screenshotAttached,
    createdAt: r.createdAt.toISOString(),
    sentAt: r.sentAt?.toISOString(),
    seq: r.seq,
    phoneFound: r.phoneFound,
  };
}

export async function listSary(): Promise<SaryPayout[]> {
  const rows = await db.select().from(sary).orderBy(desc(sary.createdAt));
  return rows.map(toSary);
}

/** Заводит выплату «сара» в очередь кол-менеджера (тому, кто направил клиента). */
export async function createSary(input: {
  client: string;
  phone: string;
  amount: number;
  reason: string;
  refDealNumber?: number;
  /** «in_check» — деньги уже учтены бонусом в чеке, переводить нечего. */
  status?: SaryPayout["status"];
  /** Номер САР внутри заявки (костюмов несколько — САР столько же). */
  seq?: number;
  /** false — телефон друга не найден в базе, колл-менеджер проверяет вручную. */
  phoneFound?: boolean;
}): Promise<SaryPayout> {
  // Ручная выплата по заявке, где САР уже начислялась: занимаем следующий номер,
  // иначе упрёмся в уникальность (ref_deal_number, seq).
  const seq =
    input.seq ??
    (input.refDealNumber != null ? (await countSaryForDeal(input.refDealNumber)) + 1 : 1);
  const [row] = await db
    .insert(sary)
    .values({
      client: input.client,
      phone: input.phone,
      amount: Math.round(input.amount * 100),
      reason: input.reason,
      refDealNumber: input.refDealNumber ?? null,
      seq,
      phoneFound: input.phoneFound ?? true,
      status: input.status ?? "pending",
      screenshotAttached: false,
      sentAt: input.status === "sent" ? new Date() : null,
    })
    .returning();
  if (!row) throw new Error("Не удалось создать выплату");
  broadcast("sary.updated", {});
  return toSary(row);
}

/**
 * Сколько САР уже начислено по заявке. Костюмов в чеке может быть несколько,
 * поэтому проверяем не факт наличия, а количество: добираем недостающие.
 */
export async function countSaryForDeal(dealNumber: number): Promise<number> {
  const rows = await db
    .select({ id: sary.id })
    .from(sary)
    .where(eq(sary.refDealNumber, dealNumber));
  return rows.length;
}

/**
 * Гасим выплату по заявке только если уже есть подтверждённый скрин
 * (пачка из раздела «Сары»). Без скрина — не отмечаем отправленным.
 */
export async function markSarySentByDeal(dealNumber: number): Promise<void> {
  const rows = await db
    .update(sary)
    .set({ status: "sent", sentAt: new Date() })
    .where(
      and(
        eq(sary.refDealNumber, dealNumber),
        eq(sary.status, "pending"),
        eq(sary.screenshotAttached, true)
      )
    )
    .returning({ id: sary.id });
  if (rows.length > 0) broadcast("sary.updated", {});
}

/** Одиночная отметка без скрина запрещена — только пачка с файлом. */
export async function markSarySent(_id: string): Promise<never> {
  throw new Error("Отметить сару отправленной можно только пачкой со скрином перевода");
}

/**
 * Пачка: один реальный скриншот подтверждения на группу выплат.
 * По каждой отправленной САР пишется расход «Программа лояльности» со счёта,
 * с которого колл-менеджер перевёл пачку (созвон 20.08).
 */
export async function markSaryBatchSent(
  ids: string[],
  screenshot: { filename: string; contentBase64: string },
  opts: { methodId: string; by: string }
): Promise<{ updated: number; screenshotFile: string }> {
  if (ids.length === 0) return { updated: 0, screenshotFile: "" };
  const screenshotFile = await saveSaryScreenshot(screenshot);
  const sentAt = new Date();
  const updatedCount = await db.transaction(async tx => {
  const rows = await tx
    .update(sary)
    .set({ status: "sent", screenshotAttached: true, sentAt })
    .where(and(inArray(sary.id, ids), eq(sary.status, "pending")))
    .returning({
      id: sary.id,
      refDealNumber: sary.refDealNumber,
      amount: sary.amount,
      client: sary.client,
      phone: sary.phone,
    });
  // Расход по каждой строке: САР со статусом in_check расходом не является
  // (скидка в чеке), сюда попадают только реально отправленные переводы.
  for (const row of rows) {
    await insertExpense(tx, {
      category: "Программа лояльности",
      account: opts.methodId,
      amount: row.amount / 100,
      description: `САР · ${row.client} ${row.phone}${row.refDealNumber ? ` · заявка #${row.refDealNumber}` : ""}`,
      createdBy: opts.by,
    });
  }
  // Пачку отправили из экрана сар — одноимённые задачи колл-менеджера закрываем,
  // иначе они висят в БД уже оплаченными (в общем списке их всё равно не показываем).
  const dealNumbers = rows
    .map((row) => row.refDealNumber)
    .filter((n): n is number => typeof n === "number");
  if (dealNumbers.length > 0) {
    await tx
      .update(tasks)
      .set({ status: "done", completedAt: new Date() })
      .where(
        and(
          eq(tasks.kind, "sary_send"),
          eq(tasks.status, "pending"),
          inArray(tasks.dealNumber, dealNumbers)
        )
      );
  }
    return rows.length;
  });
  broadcast("sary.updated", {});
  broadcast("queue.updated", { source: "sary" });
  return { updated: updatedCount, screenshotFile };
}

function toExpense(r: typeof expenses.$inferSelect): Expense {
  const splits = (r.splits as ExpenseSplit[] | null) ?? undefined;
  return {
    id: r.id,
    category: r.category,
    account: r.account,
    amount: r.amount / 100,
    splits: splits?.map((s) => ({ methodId: s.methodId, amount: s.amount / 100 })),
    description: r.description ?? undefined,
    spentAt: r.spentAt.toISOString(),
    createdBy: r.createdBy,
  };
}

export async function listExpenses(): Promise<Expense[]> {
  const rows = await db.select().from(expenses).orderBy(desc(expenses.spentAt));
  return rows.map(toExpense);
}

type ExpenseInput = {
  category: string;
  account: string;
  amount: number;
  splits?: ExpenseSplit[];
  description?: string;
  spentAt?: string;
  createdBy: string;
};
export async function addExpense(input: ExpenseInput): Promise<Expense> {
  return db.transaction(tx => insertExpense(tx, input));
}
async function insertExpense(tx: Parameters<Parameters<typeof db.transaction>[0]>[0], input: ExpenseInput): Promise<Expense> {
    const amount = Math.round(input.amount * 100);
    // Без разбивки расход целиком списывается со счёта из поля «откуда оплатил».
    const parts: ExpenseSplit[] = input.splits?.length
      ? input.splits.map((s) => ({ methodId: s.methodId, amount: Math.round(s.amount * 100) }))
      : [{ methodId: input.account, amount }];
    const [row] = await tx
      .insert(expenses)
      .values({
        category: input.category,
        account: input.account,
        amount,
        splits: input.splits?.length ? parts : null,
        description: input.description,
        spentAt: input.spentAt ? new Date(input.spentAt) : new Date(),
        createdBy: input.createdBy,
      })
      .returning();
    for (const part of parts) {
      await tx
        .insert(accountBalances)
        .values({ account: part.methodId, balance: -part.amount, updatedBy: input.createdBy })
        .onConflictDoUpdate({
          target: accountBalances.account,
          set: {
            balance: sql`${accountBalances.balance} - ${part.amount}`,
            updatedAt: new Date(),
            updatedBy: input.createdBy,
          },
        });
    }
    if (!row) throw new Error("Не удалось записать расход");
    return toExpense(row);
}

export async function listBalances(): Promise<AccountBalance[]> {
  const rows = await db.select().from(accountBalances).orderBy(accountBalances.account);
  return rows.map((r) => ({
    account: r.account,
    balance: r.balance / 100,
    updatedAt: r.updatedAt.toISOString(),
  }));
}

export async function setBalance(account: string, balanceRub: number, by: string): Promise<AccountBalance> {
  const balance = Math.round(balanceRub * 100);
  const [row] = await db
    .insert(accountBalances)
    .values({ account, balance, updatedBy: by })
    .onConflictDoUpdate({
      target: accountBalances.account,
      set: { balance, updatedAt: new Date(), updatedBy: by },
    })
    .returning();
  if (!row) throw new Error("Не удалось обновить баланс");
  return { account: row.account, balance: row.balance / 100, updatedAt: row.updatedAt.toISOString() };
}


/** Подтверждение руководителя и резерв денег выполняются атомарно.
 * Сертификат блокируется в той же транзакции, что создаёт обязательство возврата.
 * Денег здесь ещё не выдаём; факт выдачи хранит финансовая очередь.
 */
export async function approveMoneyCancellation(number: number, input: {amount:number; reason:string; destination:string}, actor:AuditActor): Promise<Deal> {
  if (!["admin","rop"].includes(actor.role)) throw Object.assign(new Error("Отмену подтверждает руководитель"),{statusCode:403});
  const {getByNumber}=await import("./deals.js");
  const initial=await getByNumber(number);
  if(!initial)throw Object.assign(new Error("Заявка не найдена"),{statusCode:404});
  return withOperationLock(`sale:${initial.id}`,async()=>{
    const result=await db.transaction(async tx=>{
      const [row]=await tx.select().from(dealsTable).where(eq(dealsTable.number,number)).for("update");
      const deal=row?.data as Deal;
      if(!deal || !["rental","deferred","cert_plastic","cert_digital"].includes(deal.kind))throw Object.assign(new Error("Денежная отмена доступна для аренды, отложки и сертификата"),{statusCode:400});
      if(deal.isTest)throw Object.assign(new Error("По тестовой заявке нельзя возвращать реальные деньги"),{statusCode:409});
      const amount=Math.round(input.amount*100);
      if(!Number.isSafeInteger(amount)||amount<=0||Math.abs(input.amount*100-amount)>0.00001||input.reason.trim().length<3||!input.destination.trim())throw Object.assign(new Error("Укажите положительную сумму до копеек, причину и получателя"),{statusCode:400});
      if(deal.moneyCancellation){
        const old=deal.moneyCancellation;
        if(old.amount===input.amount&&old.reason===input.reason.trim()&&old.destination===input.destination.trim())return deal;
        throw Object.assign(new Error("Отмена уже подтверждена. Повторную выплату создавать нельзя"),{statusCode:409});
      }
      if(deal.financialSyncRequired || deal.syncStatus === "failed")throw Object.assign(new Error("Сначала завершите сверку и синхронизацию оплаты"),{statusCode:409});
      const obligations=await tx.select().from(queue).where(eq(queue.dealNumber,number));
      if(obligations.some(q=>q.kind==="refund"))throw Object.assign(new Error("По заявке уже есть возврат. Нужна сверка"),{statusCode:409});
      const outgoing=await tx.select().from(ledger).where(and(eq(ledger.dealNumber,number),sql`${ledger.amount}<0`));
      const cashPaid=deal.payments.filter(p=>paymentKindOf(p.methodId)!=="certificate").reduce((s,p)=>s+Math.round(p.amount*100),0);
      const incoming=await tx.select().from(ledger).where(and(eq(ledger.dealNumber,number),sql`${ledger.amount}>0 and ${ledger.kind} in ('cash','card','account')`));
      if(incoming.reduce((s,p)=>s+p.amount,0)!==cashPaid || incoming.some(p=>!p.msPaymentId))throw Object.assign(new Error("Поступления не полностью сверены с МойСклад. Сначала нужна сверка оплаты"),{statusCode:409});
      const paid=Math.min(Math.round(deal.paid*100),cashPaid);
      // Сдача/чаевые остаются отдельными обязательствами, их нельзя выдать дважды.
      const reservedChange=Math.max(0,Math.round((deal.paid-Math.max(0,deal.total))*100));
      const outgoingTotal=outgoing.reduce((s,p)=>s-p.amount,0);
      const queueTotal=obligations.filter(q=>["change","tips"].includes(q.kind)).reduce((s,q)=>s+q.amount,0);
      let limit=paid-Math.max(reservedChange,outgoingTotal,queueTotal);
      if(![paid,reservedChange,outgoingTotal,queueTotal,limit].every(Number.isSafeInteger))throw Object.assign(new Error("Некорректные суммы исходной оплаты. Нужна сверка"),{statusCode:409});
      if(deal.kind.startsWith("cert_")){
        const [cert]=await tx.select().from(certificates).where(eq(certificates.number,deal.certificateNumber??"")).for("update");
        if(!cert||cert.buyerDealNumber!==number||!["active","expired"].includes(cert.status))throw Object.assign(new Error("Не найден доступный сертификат этой заявки. Нужна сверка"),{statusCode:409});
        limit=Math.min(limit,cert.balance);
        if(amount>limit)throw Object.assign(new Error(`Доступно к возврату не больше ${Math.max(0,limit)/100} ₽`),{statusCode:409});
        await tx.update(certificates).set({balance:cert.balance-amount,status:"cancelled"}).where(eq(certificates.number,cert.number));
      }
      if(amount>limit)throw Object.assign(new Error(`Доступно к возврату не больше ${Math.max(0,limit)/100} ₽`),{statusCode:409});
      const approvedAt=new Date().toISOString();
      const [obligation]=await tx.insert(queue).values({kind:"refund",dealNumber:number,client:deal.clientName,amount,destination:input.destination.trim(),metadata:{source:"money_cancellation",reason:input.reason.trim(),approvedBy:actor.name,approvedById:actor.id,approvedAt}}).returning();
      const updated:Deal={...deal,moneyCancellation:{queueId:obligation!.id,amount:amount/100,reason:input.reason.trim(),approvedBy:actor.name,approvedAt,destination:input.destination.trim()},history:[...(deal.history??[]),{at:approvedAt,who:actor.name,action:`Подтверждён возврат денег при отмене: ${amount/100} ₽. ${input.reason.trim()}`} ]};
      await tx.update(dealsTable).set({data:updated,updatedAt:new Date()}).where(eq(dealsTable.number,number));
      await tx.update(tasks).set({status:"cancelled",completedBy:actor.name,completedAt:new Date()}).where(and(eq(tasks.dealNumber,number),eq(tasks.status,"pending"),sql`${tasks.kind} not in ('movement','movement_accept','unreserve')`));
      await tx.insert(auditLog).values({actorId:actor.id,actorName:actor.name,actorRole:actor.role,action:"deal.money_cancellation_approved",entityType:"deal",entityId:String(number),after:updated.moneyCancellation});
      return updated;
    });
    broadcast("deal.updated",{number});broadcast("queue.updated",{});broadcast("certificate.updated",{});return result;
  });
}

/** Возврат арендованных вещей не выдаёт денег и не меняет сумму отмены. */
export async function receiveCancelledRental(number:number,by:string):Promise<Deal>{
  const result=await db.transaction(async tx=>{
    const [row]=await tx.select().from(dealsTable).where(eq(dealsTable.number,number)).for("update");const deal=row?.data as Deal;
    if(!deal?.moneyCancellation||deal.kind!=="rental")throw Object.assign(new Error("Нет отменённой аренды"),{statusCode:409});
    if(deal.rentalReturnedAt)return deal;
    if(!deal.rentalIssuedAt&&deal.rentalStatus!=="issued")throw Object.assign(new Error("Выдача вещей не подтверждена"),{statusCode:409});
    const at=new Date().toISOString();const updated:Deal={...deal,rentalReturnedAt:at,rentalStatus:"returned",history:[...(deal.history??[]),{at,who:by,action:"Вещи после отмены аренды приняты. Денежная выплата отдельно в очереди."}]};
    for(const item of deal.items.filter(i=>i.productId!=="rent"))await tx.insert(dealItemState).values({dealNumber:number,itemId:item.productId,state:"in_store",location:deal.store,metadata:{by}}).onConflictDoUpdate({target:[dealItemState.dealNumber,dealItemState.itemId],set:{state:"in_store",location:deal.store,updatedAt:new Date()}});
    await tx.update(dealsTable).set({data:updated,updatedAt:new Date()}).where(eq(dealsTable.number,number));
    await tx.insert(auditLog).values({actorName:by,actorRole:"operator",action:"rental.received_after_cancellation",entityType:"deal",entityId:String(number),metadata:{at}});return updated;
  });broadcast("deal.updated",{number});return result;
}


export async function syncMoneyCancellationQueue(id:string):Promise<QueueItem>{
  const item=(await listQueue()).find(q=>q.id===id);
  if(!item)throw Object.assign(new Error("Выплата не найдена"),{statusCode:404});
  if(item.metadata?.source!=="money_cancellation")return item;
  const {synchronizeCancellationPayouts}=await import("./sale.js");
  try{
    await synchronizeCancellationPayouts(item);
    item.metadata={...item.metadata,financialSyncError:null};
    await db.update(queue).set({metadata:item.metadata}).where(eq(queue.id,id));
    return item;
  }catch(e){
    const message=e instanceof Error?e.message:"Ошибка МойСклад";
    await db.update(queue).set({metadata:{...item.metadata,financialSyncError:message}}).where(eq(queue.id,id));
    throw Object.assign(new Error("Выдача уже записана в кассе; повторно деньги не выдавайте. Документ МойСклад требует сверки: "+message),{statusCode:409});
  }finally{broadcast("queue.updated",{});}
}
