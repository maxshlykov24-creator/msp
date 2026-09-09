import type { FastifyInstance } from "fastify";
import { catalogBrowseSchema, catalogSearchSchema } from "@kassa/shared";
import {
  browseCatalogPaged,
  getProductStockByWarehouses,
  listCatalogReferences,
  resolveAssortment,
  searchCatalog,
} from "../services/catalog.js";
import * as ms from "../clients/ms.js";
import { getMsRef } from "../services/bootstrap.js";
import { appendSystemAudit } from "../services/audit.js";

export default async function catalogRoutes(app: FastifyInstance) {
  app.get("/catalog/references", { preHandler: [app.authenticate] }, async () => {
    return listCatalogReferences();
  });

  app.get("/catalog/search", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = catalogSearchSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return searchCatalog(
      parsed.data.q,
      parsed.data.limit,
      parsed.data.store,
      parsed.data.category,
      parsed.data.browse
    );
  });

  // Полный каталог с серверной пагинацией (блок 3, созвон 09.09): один SQL с
  // LEFT JOIN stock, фильтры по остатку и характеристикам, костюмы моделями.
  app.get("/catalog/browse", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = catalogBrowseSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return browseCatalogPaged(parsed.data);
  });

  // Остатки по складам: доступно / резерв / остаток.
  // ?cache=1 — только локальный кэш (для списков); иначе кэш + live МС.
  app.get("/catalog/:productId/stock", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { productId } = req.params as { productId: string };
    if (!productId?.trim()) return reply.code(400).send({ message: "Нет productId" });
    const q = req.query as { cache?: string };
    const cacheOnly = q.cache === "1" || q.cache === "true";
    return getProductStockByWarehouses(productId.trim(), { cacheOnly });
  });

  // ── Печать бирки (П6, созвон 20.08) ──────────────────────────────
  // Список шаблонов этикеток/ценников из МойСклад (кэш на процесс).
  let templatesCache: { at: number; rows: Awaited<ReturnType<typeof ms.listLabelTemplates>> } | null = null;
  app.get("/catalog/label-templates", { preHandler: [app.authenticate] }, async () => {
    if (!templatesCache || Date.now() - templatesCache.at > 10 * 60_000) {
      templatesCache = { at: Date.now(), rows: await ms.listLabelTemplates() };
    }
    return templatesCache.rows.map((t) => ({ id: t.id, name: t.name, templateType: t.templateType }));
  });

  // Печать бирки: PDF из печатных форм МойСклад. Консультант выбирает позицию
  // (поиск по производителю/артикулу/размеру уже есть в /catalog/search) и печатает.
  app.post("/catalog/:productId/label", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { productId } = req.params as { productId: string };
    if (!productId?.trim()) return reply.code(400).send({ message: "Нет productId" });
    const body = (req.body ?? {}) as { templateId?: string; count?: number };

    const assortment = await resolveAssortment(productId.trim());
    if (!assortment) return reply.code(404).send({ message: "Позиция не найдена в каталоге" });
    const entityType = assortment.type === "variant" ? "variant" : "product";

    const org = await getMsRef("organization", "default");
    if (!org) return reply.code(502).send({ message: "МойСклад: организация не синхронизирована (bootstrap)" });

    if (!templatesCache || Date.now() - templatesCache.at > 10 * 60_000) {
      templatesCache = { at: Date.now(), rows: await ms.listLabelTemplates() };
    }
    const templates = templatesCache.rows;
    if (templates.length === 0) {
      return reply.code(502).send({ message: "МойСклад: шаблоны этикеток недоступны через API" });
    }
    const template = body.templateId
      ? templates.find((t) => t.id === body.templateId)
      : templates.find((t) => /этикет|бирк|ценник/i.test(t.name)) ?? templates[0];
    if (!template) return reply.code(400).send({ message: "Шаблон этикетки не найден" });

    const priceType = await ms.getSalePriceTypeMeta();
    try {
      const pdf = await ms.printLabels({
        entityType,
        id: productId.trim(),
        template: template.meta,
        organization: org.meta,
        priceType,
        count: body.count && body.count > 0 ? body.count : 1,
      });
      await appendSystemAudit({
        action: "label_printed",
        entityType: "product",
        entityId: productId.trim(),
        metadata: { template: template.name, count: body.count ?? 1, by: req.user.name },
      });
      reply.header("Content-Type", "application/pdf");
      reply.header("Content-Disposition", `inline; filename="label-${productId.trim()}.pdf"`);
      return reply.send(pdf);
    } catch (error) {
      return reply.code(502).send({ message: (error as Error).message });
    }
  });
}
