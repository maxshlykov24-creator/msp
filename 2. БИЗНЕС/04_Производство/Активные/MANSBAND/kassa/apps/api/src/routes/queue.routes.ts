import type { FastifyInstance } from "fastify";
import {
  balanceSchema,
  createSarySchema,
  expenseSchema,
  EXPENSE_CATEGORIES,
  issueQueueSchema,
  queueItemSchema,
  sarySentBatchSchema,
} from "@kassa/shared";
import * as q from "../services/queue.js";
import * as deals from "../services/deals.js";
import { appendAudit } from "../services/audit.js";

export default async function queueRoutes(app: FastifyInstance) {
  // Очередь Эдвина
  app.get("/queue", { preHandler: [app.authenticate] }, async () => q.listQueue());

  app.post("/queue", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = queueItemSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return q.addQueue(parsed.data);
  });

  // Сдачу и чаевые может закрыть и консультант (созвон 29.07, п.7);
  // возвраты и счета компаний остаются за финансами.
  const CONSULTANT_ISSUABLE = new Set(["change", "tips"]);

  app.patch("/queue/:id/issue", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { id } = req.params as { id: string };
    const parsed = issueQueueSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const who = `${req.user.role} ${req.user.name}`.toLowerCase();
    const isFinance =
      ["finance", "rop", "admin"].includes(req.user.role) ||
      who.includes("max") ||
      who.includes("максим");
    const allItems = await q.listQueue();
    const item = allItems.find((row) => row.id === id);
    if (!item) return reply.code(404).send({ message: "Запись очереди не найдена" });
    if (!isFinance && !CONSULTANT_ISSUABLE.has(item.kind)) {
      return reply.code(403).send({ message: "Эту выплату закрывает Эдвин" });
    }
    // Зарплата (П5): каждая выданная часть — расход категории «Зарплата».
    const updated =
      item.kind === "salary"
        ? await q.issueSalary(id, {
            by: req.user.name,
            methodId: parsed.data.methodId,
            amountRub: parsed.data.amount,
            note: parsed.data.note ?? item.destination,
          })
        : await q.issueQueue(id, {
            by: req.user.name,
            methodId: parsed.data.methodId,
            amountRub: parsed.data.amount,
            note: parsed.data.note,
            metadata: parsed.data.metadata,
          });
    if (!updated) return reply.code(404).send({ message: "Запись очереди не найдена" });
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: updated.status === "issued" ? "queue.issued" : "queue.partially_issued",
      entityType: "queue",
      entityId: id,
      after: updated,
      metadata: { methodId: parsed.data.methodId, amount: parsed.data.amount },
    });
    return updated;
  });

  // Документы по продаже компании денег не двигают — закрываем без выплаты.
  app.patch(
    "/queue/:id/close",
    { preHandler: [app.requireFinanceQueues] },
    async (req, reply) => {
      const { id } = req.params as { id: string };
      const updated = await q.closeQueue(id, req.user.name);
      if (!updated) return reply.code(404).send({ message: "Запись очереди не найдена" });
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "queue.closed",
        entityType: "queue",
        entityId: id,
        after: updated,
      });
      return updated;
    }
  );

  // Вернуть закрытую задачу в работу (ошибка закрытия у Эдвина/Миши).
  app.patch(
    "/queue/:id/reopen",
    { preHandler: [app.requireFinanceQueues] },
    async (req, reply) => {
      const { id } = req.params as { id: string };
      const updated = await q.reopenQueue(id, req.user.name);
      if (!updated) return reply.code(404).send({ message: "Запись очереди не найдена" });
      const companyKinds = new Set([
        "invoice",
        "invoice_check",
        "documents",
        "documents_hand",
        "atelier",
      ]);
      if (companyKinds.has(updated.kind)) {
        await deals.revertCompanyStep(updated.dealNumber, updated.kind, req.user.name);
      }
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "queue.reopened",
        entityType: "queue",
        entityId: id,
        after: updated,
      });
      return updated;
    }
  );

  app.get("/expenses/categories", { preHandler: [app.authenticate] }, async () => EXPENSE_CATEGORIES);
  app.get("/expenses", { preHandler: [app.requireFinanceQueues] }, async () => q.listExpenses());
  app.post("/expenses", { preHandler: [app.requireFinanceQueues] }, async (req, reply) => {
    const parsed = expenseSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    if (!EXPENSE_CATEGORIES.includes(parsed.data.category as (typeof EXPENSE_CATEGORIES)[number])) {
      return reply.code(400).send({ message: "Неизвестная статья расхода" });
    }
    const expense = await q.addExpense({ ...parsed.data, createdBy: req.user.name });
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "expense.created",
      entityType: "expense",
      entityId: expense.id,
      after: expense,
    });
    return expense;
  });

  app.get("/balances", { preHandler: [app.requireFinanceQueues] }, async () => q.listBalances());
  app.put("/balances/:account", { preHandler: [app.requireFinanceQueues] }, async (req, reply) => {
    const { account } = req.params as { account: string };
    const parsed = balanceSchema.safeParse({ ...(req.body as object), account });
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const balance = await q.setBalance(parsed.data.account, parsed.data.balance, req.user.name);
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "balance.set",
      entityType: "balance",
      entityId: parsed.data.account,
      after: balance,
    });
    return balance;
  });

  // Сары
  app.get("/sary", { preHandler: [app.authenticate] }, async () => q.listSary());

  app.post("/sary", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = createSarySchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const created = await q.createSary(parsed.data);
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "sary.created",
      entityType: "sary",
      entityId: created.id,
      after: created,
    });
    return created;
  });

  app.patch("/sary/:id/sent", { preHandler: [app.authenticate] }, async (_req, reply) => {
    return reply.code(400).send({
      message: "Отметить сару отправленной можно только пачкой со скрином перевода",
    });
  });

  // Пачка: обязателен реальный скриншот перевода на группу выплат.
  app.patch("/sary/sent", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = sarySentBatchSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    try {
      const result = await q.markSaryBatchSent(parsed.data.ids, parsed.data.screenshot, {
        methodId: parsed.data.methodId,
        by: req.user.name,
      });
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "sary.batch_sent",
        entityType: "sary",
        entityId: parsed.data.ids.join(","),
        after: {
          updated: result.updated,
          methodId: parsed.data.methodId,
          screenshotAttached: true,
          screenshotFile: result.screenshotFile,
        },
      });
      return { ok: true, updated: result.updated };
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось отметить сары";
      return reply.code(400).send({ message });
    }
  });
}
