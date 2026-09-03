import type { FastifyInstance } from "fastify";
import {
  addCommentSchema,
  companyHandoverSchema,
  companyStatusSchema,
  convertKindSchema,
  createDealSchema,
  dealsSearchSchema,
  updateDealSchema,
  updateStageSchema,
} from "@kassa/shared";
import { taskTitle } from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import * as deals from "../services/deals.js";
import * as sale from "../services/sale.js";
import type { PhotoInput } from "../services/sale.js";
import { notifyDeal } from "../services/notify.js";
import { appendAudit } from "../services/audit.js";
import * as taskService from "../services/tasks.js";
import * as taskFlow from "../services/taskFlow.js";
import * as queueService from "../services/queue.js";
import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { dealItemState } from "../db/schema.js";

// Виды заявок, по которым касса проводит складскую отгрузку/возврат при Успехе.
const FULFILLABLE = new Set(["sale", "company", "deferred", "delivery", "exchange", "refund"]);
const COMPLETED_STAGES = new Set(["Успех", "Провал"]);

async function validateReturnItems(deal: Deal): Promise<string | null> {
  if (deal.kind !== "refund" && deal.kind !== "exchange") return null;
  if (!deal.linkedDealNumber) return "Нужна исходная сделка";
  const source = await deals.getByNumber(deal.linkedDealNumber);
  if (!source) return `Исходная сделка #${deal.linkedDealNumber} не найдена`;
  const available = new Map<string, number>();
  for (const item of source.items) available.set(item.productId, (available.get(item.productId) ?? 0) + item.qty);
  const priorReturns = (await deals.listLocal()).filter(
    (row) =>
      row.linkedDealNumber === deal.linkedDealNumber &&
      (row.kind === "refund" || row.kind === "exchange") &&
      row.stage === "Успех"
  );
  for (const prior of priorReturns) {
    const returned = prior.kind === "refund" ? prior.items : prior.items.filter((item) => item.isReturn);
    for (const item of returned) {
      available.set(item.productId, (available.get(item.productId) ?? 0) - item.qty);
    }
  }
  const selected = deal.kind === "refund" ? deal.items : deal.items.filter((i) => i.isReturn);
  for (const item of selected) {
    const left = available.get(item.productId) ?? 0;
    if (item.qty > left) return `Позиция «${item.name}» отсутствует в исходной сделке или превышает проданное количество`;
    available.set(item.productId, left - item.qty);
  }
  return selected.length ? null : "Не выбраны позиции возврата";
}

function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(fallback), ms);
    p.then((v) => {
      clearTimeout(t);
      resolve(v);
    }).catch(() => {
      clearTimeout(t);
      resolve(fallback);
    });
  });
}

export default async function dealsRoutes(app: FastifyInstance) {
  // Список заявок: локальное зеркало + amo (короткий потолок — иначе мобильный логин/первый экран стопорятся).
  app.get("/deals", { preHandler: [app.authenticate] }, async () => {
    const local = await deals.listLocal();
    // Холодный listAmoOpen на ~1.8k+ сделок занимает ~25–30с. Таймаут — с запасом
    // сверху; listAmoOpen сам отдаёт устаревший кэш почти мгновенно, если он есть,
    // так что 40с реально ждём только на самом первом запросе после старта.
    const amoDeals = await withTimeout(deals.listAmoOpen().catch(() => [] as Deal[]), 40_000, []);
    const localAmoIds = new Set(local.map((d) => d.amoLeadId).filter(Boolean));
    const merged = [...local, ...amoDeals.filter((d) => !localAmoIds.has(d.amoLeadId))];
    merged.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
    return merged;
  });

  // Реальные открытые заявки из amoCRM (экран «Заявки»).
  app.get("/deals/amo", { preHandler: [app.authenticate] }, async () => {
    const list = await deals.listAmoOpen();
    list.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
    return list;
  });

  app.get("/deals/search", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = dealsSearchSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return deals.searchAmo(parsed.data);
  });

  app.get("/deals/:ref", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const deal = await deals.resolve(ref);
    if (!deal) return reply.code(404).send({ message: "Заявка не найдена" });
    return deal;
  });

  // Расположение позиций заявки (П3, созвон 20.08): состояние deal_item_state
  // для карточки — где физически лежит каждая позиция.
  app.get("/deals/:ref/item-state", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const deal = await deals.resolve(ref);
    if (!deal) return reply.code(404).send({ message: "Заявка не найдена" });
    const rows = await db
      .select()
      .from(dealItemState)
      .where(eq(dealItemState.dealNumber, deal.number));
    return rows.map((r) => ({
      itemId: r.itemId,
      state: r.state,
      location: r.location ?? null,
      updatedAt: r.updatedAt.toISOString(),
    }));
  });

  // Создание/проведение заявки (фронт отправляет полный Deal + опционально photos).
  app.post("/deals", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = createDealSchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({
        message: parsed.error.issues[0]?.message ?? "Некорректная заявка",
        issues: parsed.error.issues,
      });
    }
    const { photos, number: _ignoredNumber, createdAt, ...raw } = parsed.data;
    const idempotencyKey = raw.idempotencyKey ?? raw.id!;
    const requested = raw as unknown as Omit<Deal, "number" | "createdAt">;
    requested.id = raw.id ?? idempotencyKey;
    requested.subtotal = requested.items.reduce((sum, i) => sum + i.price * i.qty, 0);
    requested.discountTotal = Math.max(0, requested.subtotal - requested.total);
    const returnError = await validateReturnItems(requested as Deal);
    if (returnError) return reply.code(400).send({ message: returnError });

    const reserved = await deals.reserveServerDeal({ ...requested, createdAt }, idempotencyKey);
    const who = req.user.name;
    // Повтор того же idempotencyKey: заявку не дублируем, но задачи по этапу
    // и очередь Эдвина досоздаём (если первый прогон упал до enqueue).
    if (reserved.duplicate) {
      await taskFlow.onDealStage(reserved.deal, who).catch(() => {});
      await sale.enqueueEdwin(reserved.deal).catch(() => {});
      return reserved.deal;
    }
    const deal = reserved.deal;
    // Возврат: total ≤ 0; обмен: доплата (total>0) или возврат клиенту (total≤0).
    const paidOk = deal.total <= 0 || deal.paid >= deal.total;
    const fulfillmentStage =
      deal.stage === "Успех" ||
      (deal.kind === "company" && deal.stage === "Товар выдан" && deal.issued === true);
    const fulfill =
      fulfillmentStage &&
      paidOk &&
      FULFILLABLE.has(deal.kind) &&
      (deal.kind !== "company" || deal.issued === true);

    try {
      const saved = await sale.processSale(deal, { fulfill, photos: photos as PhotoInput[] | undefined, who });
      // Заявка без amoLeadId в amo не уехала — не помечаем такую synced, иначе отказ
      // синка виден только в логах и заявка навсегда остаётся только в кассе.
      const synced = saved.amoLeadId
        ? await deals.markSync(saved, "synced")
        : await deals.markSync(saved, "failed", "amoCRM: сделка не создана — см. логи api");
      await appendAudit({
        actor: { id: req.user.sub, name: who, role: req.user.role },
        action: "deal.created",
        entityType: "deal",
        entityId: String(synced.number),
        after: synced,
      });
      // Проброс в Telegram магазина: создание и «Успех» (созвон 20.08, п.8).
      await notifyDeal(synced, synced.stage === "Успех" ? "success" : "created").catch(() => {});
      return synced;
    } catch (error) {
      const message = (error as Error).message;
      await deals.markSync(deal, "failed", message).catch(() => {});
      return reply.code(502).send({ message, number: deal.number, syncStatus: "failed" });
    }
  });

  function canEditClosed(user: { role: string; name?: string; login?: string }): boolean {
    if (["rop", "admin"].includes(user.role)) return true;
    const who = `${user.login ?? ""} ${user.name ?? ""}`.toLowerCase();
    return who.includes("max") || who.includes("максим");
  }

  app.patch("/deals/:ref", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = updateDealSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message ?? "Некорректные данные" });
    const existing = await deals.resolve(ref);
    if (!existing) return reply.code(404).send({ message: "Заявка не найдена" });
    if (COMPLETED_STAGES.has(existing.stage) && !canEditClosed(req.user)) {
      return reply.code(403).send({ message: "Завершённые сделки может менять только РОП или Максим" });
    }
    if (COMPLETED_STAGES.has(existing.stage) && parsed.data.stage && parsed.data.stage !== existing.stage && !parsed.data.reason?.trim()) {
      return reply.code(400).send({ message: "Укажите причину изменения завершённой сделки" });
    }
    const updated = await deals.updateDealFields(ref, parsed.data, req.user.name);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    if (updated.stage !== existing.stage) {
      await taskFlow.onDealStage(updated, req.user.name).catch(() => {});
      if (updated.stage === "Успех") {
        await sale.enqueueSary(updated, req.user.name).catch(() => {});
        await sale.noteSaryInAmo(updated, req.user.name).catch(() => {});
        await notifyDeal(updated, "success").catch(() => {});
      }
    }
    // Сменили раскладку возврата → досоздаём задачу Эдвину «Сделать возврат».
    if (
      parsed.data.returnPayouts !== undefined ||
      parsed.data.returnStatus !== undefined ||
      parsed.data.returnDestination !== undefined
    ) {
      await sale.enqueueEdwin(updated).catch(() => {});
    }
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "deal.updated",
      entityType: "deal",
      entityId: String(updated.number),
      before: { stage: existing.stage },
      after: { stage: updated.stage },
      metadata: parsed.data.reason ? { reason: parsed.data.reason } : undefined,
    });
    return updated;
  });

  // Отложку/обещание проводят как продажу, продажу компании или аренду —
  // тот же номер заявки + обязательные поля целевого типа (п.1.1 правок 10.08).
  app.patch("/deals/:ref/kind", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = convertKindSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const existing = await deals.resolve(ref);
    if (!existing) return reply.code(404).send({ message: "Заявка не найдена" });
    if (existing.kind !== "deferred" && existing.kind !== "promise") {
      return reply.code(400).send({ message: "Менять тип можно только у отложки и обещания" });
    }
    if (COMPLETED_STAGES.has(existing.stage) && !canEditClosed(req.user)) {
      return reply.code(403).send({ message: "Завершённые сделки может менять только РОП или Максим" });
    }
    try {
      const { photos, ...kindPatch } = parsed.data;
      const updated = await deals.convertDealKind(ref, kindPatch, req.user.name);
      if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
      await sale.afterKindConverted(updated, req.user.name, photos as PhotoInput[] | undefined);
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "deal.kind_converted",
        entityType: "deal",
        entityId: String(updated.number),
        before: { kind: existing.kind, stage: existing.stage },
        after: {
          kind: updated.kind,
          stage: updated.stage,
          invoiceStatus: updated.invoiceStatus,
          companyName: updated.companyName,
        },
      });
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось сменить тип заявки";
      return reply.code(400).send({ message });
    }
  });

  app.patch("/deals/:ref/stage", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = updateStageSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message ?? "Некорректный этап" });
    const existing = await deals.resolve(ref);
    if (!existing) return reply.code(404).send({ message: "Заявка не найдена" });
    if (COMPLETED_STAGES.has(existing.stage) && !canEditClosed(req.user)) {
      return reply.code(403).send({ message: "Завершённые сделки может менять только РОП или Максим" });
    }
    if (COMPLETED_STAGES.has(existing.stage) && !parsed.data.reason?.trim()) {
      return reply.code(400).send({ message: "Укажите причину изменения завершённой сделки" });
    }
    if (existing.kind === "company" && parsed.data.stage === "Успех" && parsed.data.issued !== true && !existing.issued) {
      return reply.code(400).send({ message: "Сначала отметьте фактическую выдачу товара" });
    }
    const updated = await deals.updateStage(ref, parsed.data.stage, req.user.name, parsed.data);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    if (updated.kind === "deferred" && parsed.data.stage === "Провал") {
      await taskService.createTask({
        kind: "unreserve",
        dealNumber: updated.number,
        store: updated.store,
        assigneeRole: "consultant",
        title: taskTitle("unreserve", updated.number),
        idempotencyKey: `unreserve:${updated.id}`,
        metadata: { confirmedByRole: req.user.role, route: updated.store },
      }, req.user.name);
    }
    // Новый этап может сам рождать задачи (перемещение, отложка, СДЭК).
    await taskFlow.onDealStage(updated, req.user.name).catch(() => {});
    // Сарафан: задача колл-менеджеру при первом заходе в «Успех» (не только при создании).
    if (updated.stage === "Успех" && existing.stage !== "Успех") {
      await sale.enqueueSary(updated, req.user.name).catch(() => {});
      await sale.noteSaryInAmo(updated, req.user.name).catch(() => {});
      await notifyDeal(updated, "success").catch(() => {});
    }
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "deal.stage_changed",
      entityType: "deal",
      entityId: String(updated.number),
      before: { stage: existing.stage },
      after: { stage: updated.stage, issued: updated.issued },
      metadata: parsed.data.reason ? { reason: parsed.data.reason } : undefined,
    });
    return updated;
  });

  // Счёт и оплата — Эдвин (и Максим), документы и ателье — Миша; консультанту только чтение.
  app.patch(
    "/deals/:ref/company",
    { preHandler: [app.requireFinanceQueues] },
    async (req, reply) => {
      const { ref } = req.params as { ref: string };
      const parsed = companyStatusSchema.safeParse(req.body);
      if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
      const existing = await deals.resolve(ref);
      if (!existing) return reply.code(404).send({ message: "Заявка не найдена" });
      if (existing.kind !== "company") {
        return reply.code(400).send({ message: "Конвейер доступен только для продажи компании" });
      }
      const updated = await deals.updateCompanyStatus(ref, parsed.data, req.user.name);
      if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "deal.company_updated",
        entityType: "deal",
        entityId: String(updated.number),
        before: {
          stage: existing.stage,
          invoiceStatus: existing.invoiceStatus,
          documentsStatus: existing.documentsStatus,
          atelierStatus: existing.atelierStatus,
        },
        after: {
          stage: updated.stage,
          invoiceStatus: updated.invoiceStatus,
          documentsStatus: updated.documentsStatus,
          atelierStatus: updated.atelierStatus,
        },
      });
      return updated;
    }
  );

  // Выдача клиенту по продаже компании — действие консультанта в зале.
  app.patch("/deals/:ref/handover", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = companyHandoverSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const existing = await deals.resolve(ref);
    if (!existing) return reply.code(404).send({ message: "Заявка не найдена" });
    if (existing.kind !== "company") {
      return reply.code(400).send({ message: "Выдача с документами есть только у продажи компании" });
    }
    if (parsed.data.documentsHanded && existing.documentsStatus !== "ready" && existing.documentsStatus !== "handed") {
      return reply.code(400).send({ message: "Документы ещё не готовы — их готовит Миша" });
    }
    const updated = await deals.handoverCompany(ref, parsed.data, req.user.name);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    if (parsed.data.documentsHanded) {
      await queueService.closeQueueByDeal(updated.number, "documents", req.user.name);
      await queueService.closeQueueByDeal(updated.number, "documents_hand", req.user.name);
    }
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "deal.handover",
      entityType: "deal",
      entityId: String(updated.number),
      before: { issued: existing.issued, documentsStatus: existing.documentsStatus },
      after: { issued: updated.issued, documentsStatus: updated.documentsStatus },
    });
    return updated;
  });

  app.post("/deals/:ref/comments", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = addCommentSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Пустой комментарий" });
    const updated = await deals.addComment(ref, parsed.data.text, req.user.name);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "deal.comment_added",
      entityType: "deal",
      entityId: String(updated.number),
      metadata: { text: parsed.data.text },
    });
    return updated;
  });

  app.post(
    "/deals/:ref/sync",
    { preHandler: [app.requireRoles(["rop", "admin"])] },
    async (req, reply) => {
      const { ref } = req.params as { ref: string };
      const deal = await deals.resolve(ref);
      if (!deal) return reply.code(404).send({ message: "Заявка не найдена" });
      const paidOk = deal.total <= 0 || deal.paid >= deal.total;
      const fulfillmentStage =
        deal.stage === "Успех" ||
        (deal.kind === "company" && deal.stage === "Товар выдан" && deal.issued === true);
      const fulfill =
        fulfillmentStage &&
        paidOk &&
        FULFILLABLE.has(deal.kind) &&
        (deal.kind !== "company" || deal.issued === true);
      try {
        const saved = await sale.processSale(deal, { fulfill, who: req.user.name });
        const synced = await deals.markSync(saved, "synced");
        await appendAudit({
          actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
          action: "deal.resynced",
          entityType: "deal",
          entityId: String(deal.number),
          after: { syncStatus: "synced" },
        });
        return synced;
      } catch (error) {
        const message = (error as Error).message;
        await deals.markSync(deal, "failed", message).catch(() => {});
        return reply.code(502).send({ message, syncStatus: "failed" });
      }
    }
  );
}
