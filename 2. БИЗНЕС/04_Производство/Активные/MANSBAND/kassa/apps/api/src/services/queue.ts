import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { and, desc, eq, inArray, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { accountBalances, expenses, queue, queuePayouts, sary, tasks } from "../db/schema.js";
import type { AccountBalance, Deal, Expense, ExpenseSplit, QueueItem, SaryPayout } from "@kassa/shared";
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
  const amount = Math.round(item.amount * 100);
  const existing = await db.select().from(queue).where(and(
    eq(queue.kind, item.kind),
    eq(queue.dealNumber, item.dealNumber),
    eq(queue.amount, amount),
    eq(queue.destination, item.destination),
    eq(queue.status, item.status ?? "pending")
  )).limit(1);
  if (existing[0]) return toQueueItem(existing[0]);
  const [row] = await db
    .insert(queue)
    .values({
      kind: item.kind,
      dealNumber: item.dealNumber,
      client: item.client,
      amount,
      destination: item.destination,
      status: item.status ?? "pending",
      metadata: item.metadata,
    })
    .returning();
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
    amountRub?: number;
    note?: string;
    metadata?: Record<string, unknown>;
  }
) {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1);
    if (!current) return null;

    const remaining = current.amount - current.issuedAmount;
    if (current.status === "issued" || remaining <= 0) {
      const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
      return { row: current, payouts: rows };
    }

    const requested = input.amountRub == null ? remaining : Math.round(input.amountRub * 100);
    const amount = Math.min(Math.max(1, requested), remaining);
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
        metadata: input.metadata ?? (current.metadata as Record<string, unknown> | null) ?? undefined,
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
  input: { by: string; methodId: string; amountRub?: number; note?: string }
): Promise<QueueItem | null> {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1);
    if (!current || current.kind !== "salary") return null;

    const remaining = current.amount - current.issuedAmount;
    if (current.status === "issued" || remaining <= 0) {
      const rows = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
      return { row: current, payouts: rows };
    }

    const requested = input.amountRub == null ? remaining : Math.round(input.amountRub * 100);
    const amount = Math.min(Math.max(1, requested), remaining);
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
    .where(eq(queue.id, id))
    .returning();
  if (!updated) return null;
  broadcast("queue.updated", {});
  return toQueueItem(updated);
}

/** Документы передал консультант из карточки — закрываем задачу в очереди Миши. */
export async function closeQueueByDeal(dealNumber: number, kind: string, by: string): Promise<void> {
  const updated = await db
    .update(queue)
    .set({ status: "issued", issuedAt: new Date(), issuedBy: by })
    .where(and(eq(queue.dealNumber, dealNumber), eq(queue.kind, kind), eq(queue.status, "pending")))
    .returning({ id: queue.id });
  if (updated.length > 0) broadcast("queue.updated", {});
}

/**
 * Вернуть закрытую позицию в «к выдаче»: откатывает частичные выплаты и балансы.
 * Статусы заявки компании (документы/счёт) откатывает вызывающий слой.
 */
export async function reopenQueue(id: string, by: string): Promise<QueueItem | null> {
  const result = await db.transaction(async (tx) => {
    const [current] = await tx.select().from(queue).where(eq(queue.id, id)).limit(1);
    if (!current) return null;
    if (current.status !== "issued" && current.issuedAmount <= 0) {
      return { row: current, payouts: [] as Array<typeof queuePayouts.$inferSelect> };
    }

    const payouts = await tx.select().from(queuePayouts).where(eq(queuePayouts.queueId, id));
    for (const p of payouts) {
      await tx
        .insert(accountBalances)
        .values({ account: p.methodId, balance: p.amount, updatedBy: by })
        .onConflictDoUpdate({
          target: accountBalances.account,
          set: {
            balance: sql`${accountBalances.balance} + ${p.amount}`,
            updatedAt: new Date(),
            updatedBy: by,
          },
        });
    }
    if (payouts.length > 0) {
      await tx.delete(queuePayouts).where(eq(queuePayouts.queueId, id));
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
}): Promise<SaryPayout> {
  const [row] = await db
    .insert(sary)
    .values({
      client: input.client,
      phone: input.phone,
      amount: Math.round(input.amount * 100),
      reason: input.reason,
      refDealNumber: input.refDealNumber ?? null,
      status: input.status ?? "pending",
      screenshotAttached: false,
      sentAt: input.status === "sent" ? new Date() : null,
    })
    .returning();
  if (!row) throw new Error("Не удалось создать выплату");
  broadcast("sary.updated", {});
  return toSary(row);
}

/** Есть ли уже сара по этой заявке — защита от повторного проведения. */
export async function saryExistsForDeal(dealNumber: number): Promise<boolean> {
  const rows = await db
    .select({ id: sary.id })
    .from(sary)
    .where(eq(sary.refDealNumber, dealNumber))
    .limit(1);
  return rows.length > 0;
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
  const rows = await db
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
    await addExpense({
      category: "Программа лояльности",
      account: opts.methodId,
      amount: row.amount / 100,
      description: `САР · ${row.client} ${row.phone}${row.refDealNumber ? ` · заявка #${row.refDealNumber}` : ""}`,
      createdBy: opts.by,
    }).catch((err) => {
      console.warn(`[sary] расход по САР ${row.id} не записан: ${(err as Error).message}`);
    });
  }
  // Пачку отправили из экрана сар — одноимённые задачи колл-менеджера закрываем,
  // иначе они висят в БД уже оплаченными (в общем списке их всё равно не показываем).
  const dealNumbers = rows
    .map((row) => row.refDealNumber)
    .filter((n): n is number => typeof n === "number");
  if (dealNumbers.length > 0) {
    await db
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
  broadcast("sary.updated", {});
  broadcast("queue.updated", { source: "sary" });
  return { updated: rows.length, screenshotFile };
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

export async function addExpense(input: {
  category: string;
  account: string;
  amount: number;
  splits?: ExpenseSplit[];
  description?: string;
  spentAt?: string;
  createdBy: string;
}): Promise<Expense> {
  return db.transaction(async (tx) => {
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
  });
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
