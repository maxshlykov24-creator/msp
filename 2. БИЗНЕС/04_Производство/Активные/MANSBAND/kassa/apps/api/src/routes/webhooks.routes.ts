import type { FastifyInstance } from "fastify";
import { getEnv } from "../env.js";
import { broadcast } from "../ws/hub.js";
import { syncCatalog } from "../services/bootstrap.js";
import { handleAmoWebhook } from "../services/amoWebhook.js";

// Входящие вебхуки из amoCRM и МойСклад. Проверка простого секрета в query (?secret=).

export default async function webhooksRoutes(app: FastifyInstance, dependencies: {
  handleAmoWebhook?: typeof handleAmoWebhook;
  syncCatalog?: typeof syncCatalog;
} = {}) {
  const handleAmo = dependencies.handleAmoWebhook ?? handleAmoWebhook;
  const synchronizeCatalog = dependencies.syncCatalog ?? syncCatalog;
  const env = getEnv();

  // amo шлёт application/x-www-form-urlencoded — без парсера body пустой.
  app.addContentTypeParser(
    "application/x-www-form-urlencoded",
    { parseAs: "string" },
    (_req, body, done) => {
      done(null, body);
    }
  );

  function checkSecret(req: { query: unknown }): boolean {
    const q = req.query as { secret?: string } | undefined;
    return q?.secret === env.WEBHOOK_SECRET;
  }

  app.post("/webhooks/amo", async (req, reply) => {
    if (!checkSecret(req)) return reply.code(403).send({ message: "forbidden" });
    try {
      const result = await handleAmo(req.body);
      for (const row of result.results) {
        if (!row.dealNumber || row.skipped === "stage_unchanged") continue;
        broadcast("deal.updated", { source: "amo", number: row.dealNumber });
      }
      // Очереди задач/финансов слушают те же обновления доски.
      if (result.results.some((r) => r.tasks)) {
        broadcast("queue.updated", { source: "amo" });
      }
      req.log.info({ result }, "amo webhook processed");
      return result;
    } catch (err) {
      req.log.error({ err }, "amo webhook failed");
      // Сбой нельзя подтверждать: отправитель должен повторить событие.
      broadcast("deal.updated", { source: "amo", error: true });
      return reply.code(503).send({
        ok: false,
        message: "Не удалось обработать событие amoCRM",
      });
    }
  });

  app.post("/webhooks/ms", async (req, reply) => {
    if (!checkSecret(req)) return reply.code(403).send({ message: "forbidden" });
    try {
      // Подтверждаем событие только после обновления каталога и остатков.
      await synchronizeCatalog();
      broadcast("catalog.updated", { source: "ms" });
      return { ok: true };
    } catch (err) {
      req.log.error({ err }, "ms webhook failed");
      return reply.code(503).send({ ok: false, message: "Не удалось обновить каталог МойСклад" });
    }
  });
}
