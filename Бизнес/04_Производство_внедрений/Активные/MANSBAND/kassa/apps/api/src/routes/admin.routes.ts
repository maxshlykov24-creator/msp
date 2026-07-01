import type { FastifyInstance } from "fastify";
import {
  runBootstrap,
  syncCatalog,
  syncProducts,
  syncStock,
  syncAmoMeta,
  syncMsRefs,
} from "../services/bootstrap.js";

// Ручной запуск синхронизаций (только админ).
export default async function adminRoutes(app: FastifyInstance) {
  app.post("/admin/sync", { preHandler: [app.requireAdmin] }, async () => {
    await runBootstrap((m) => app.log.info(m));
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
}
