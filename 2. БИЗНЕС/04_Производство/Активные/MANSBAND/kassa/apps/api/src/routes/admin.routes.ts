import type { FastifyInstance } from "fastify";
import {
  runBootstrap,
  syncCatalog,
  syncProducts,
  syncStock,
  syncAmoMeta,
  syncMsRefs,
} from "../services/bootstrap.js";
import * as sale from "../services/sale.js";

// Ручной запуск синхронизаций (только админ).
export default async function adminRoutes(app: FastifyInstance) {
  app.post("/admin/sync", { preHandler: [app.requireAdmin] }, async () => {
    await runBootstrap((m) => app.log.info(m), { throwOnError: true });
    return { ok: true };
  });

  app.post("/admin/sync/catalog", { preHandler: [app.requireAdmin] }, async () => {
    const res = await syncCatalog();
    return { ok: true, ...res };
  });

  app.post("/admin/sync/products", { preHandler: [app.requireAdmin] }, async () => {
    const res = await syncProducts();
    return { ok: true, ...res };
  });

  app.post("/admin/sync/stock", { preHandler: [app.requireAdmin] }, async () => {
    const res = await syncStock();
    return { ok: true, ...res };
  });

  app.post("/admin/sync/amo", { preHandler: [app.requireAdmin] }, async () => {
    await syncAmoMeta();
    return { ok: true };
  });

  app.post("/admin/sync/ms-refs", { preHandler: [app.requireAdmin] }, async () => {
    await syncMsRefs();
    return { ok: true };
  });

  // Перезалив конкретной заявки в amoCRM (заявки, застрявшие локально).
  app.post("/admin/resync/deal/:number", { preHandler: [app.requireAdmin] }, async (req, reply) => {
    const number = Number((req.params as { number: string }).number);
    if (!Number.isInteger(number)) return reply.code(400).send({ message: "Некорректный номер заявки" });
    const deal = await sale.resyncToAmo(number, req.user.name);
    if (!deal) return reply.code(404).send({ message: "Заявка не найдена" });
    return { ok: deal.syncStatus === "synced", amoLeadId: deal.amoLeadId, syncStatus: deal.syncStatus, syncError: deal.syncError };
  });
}
