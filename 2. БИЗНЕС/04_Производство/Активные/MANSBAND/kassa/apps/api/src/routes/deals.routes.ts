import type { FastifyInstance } from "fastify";
import { dealsQuerySchema, dealsSearchSchema, updateStageSchema, addCommentSchema } from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import * as deals from "../services/deals.js";
import * as sale from "../services/sale.js";
import type { PhotoInput } from "../services/sale.js";
import { notifySale } from "../services/notify.js";

// Виды заявок, по которым касса проводит складскую отгрузку при Успехе.
const FULFILLABLE = new Set(["sale", "company", "deferred", "delivery", "exchange"]);

export default async function dealsRoutes(app: FastifyInstance) {
  // Список заявок: локальное зеркало кассы + реальные сделки amoCRM (дедуп по amoLeadId).
  app.get("/deals", { preHandler: [app.authenticate] }, async () => {
    const local = await deals.listLocal();
    let amoDeals: Awaited<ReturnType<typeof deals.listAmo>> = [];
    try {
      amoDeals = await deals.listAmo({ pipelineId: undefined, limit: 100, page: 1 });
    } catch {
      amoDeals = []; // amo недоступен — отдаём хотя бы локальные
    }
    const localAmoIds = new Set(local.map((d) => d.amoLeadId).filter(Boolean));
    const merged = [...local, ...amoDeals.filter((d) => !localAmoIds.has(d.amoLeadId))];
    return merged;
  });

  // Реальные заявки из amoCRM (экран «Заявки»).
  app.get("/deals/amo", { preHandler: [app.authenticate] }, async (req) => {
    const q = dealsQuerySchema.parse(req.query);
    return deals.listAmo({
      pipelineId: undefined,
      limit: q.limit,
      page: q.page,
    });
  });

  app.get("/deals/search", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = dealsSearchSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return deals.searchAmo(parsed.data);
  });

  app.get("/deals/:ref", { preHandler: [app.authenticate] }, async (req) => {
    const { ref } = req.params as { ref: string };
    return deals.resolve(ref);
  });

  // Создание/проведение заявки (фронт отправляет полный Deal + опционально photos).
  app.post("/deals", { preHandler: [app.authenticate] }, async (req) => {
    const { photos, ...deal } = req.body as Deal & { photos?: PhotoInput[] };
    const who = req.user.name;
    const fullyPaid = deal.total > 0 && deal.paid >= deal.total;
    const fulfill = deal.stage === "Успех" && fullyPaid && FULFILLABLE.has(deal.kind);

    const saved = await sale.processSale(deal, { fulfill, photos, who });
    if (fulfill) await notifySale(saved);
    return saved;
  });

  app.patch("/deals/:ref/stage", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = updateStageSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный этап" });
    const updated = await deals.updateStage(ref, parsed.data.stage, req.user.name);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    return updated;
  });

  app.post("/deals/:ref/comments", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { ref } = req.params as { ref: string };
    const parsed = addCommentSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Пустой комментарий" });
    const updated = await deals.addComment(ref, parsed.data.text, req.user.name);
    if (!updated) return reply.code(404).send({ message: "Заявка не найдена" });
    return updated;
  });
}
