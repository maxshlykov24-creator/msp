import type { FastifyInstance } from "fastify";
import { getEnv } from "../env.js";
import { broadcast } from "../ws/hub.js";
import { syncCatalog } from "../services/bootstrap.js";
import { handleAmoWebhook } from "../services/amoWebhook.js";

// Входящие вебхуки из amoCRM и МойСклад. Проверка простого секрета в query (?secret=).

export default async function webhooksRoutes(app: FastifyInstance) {
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
      const result = await handleAmoWebhook(req.body);
      broadcast("deal.updated", { source: "amo", processed: result.processed });
      // Очереди задач/финансов слушают те же обновления доски.
      if (result.results.some((r) => r.tasks)) {
        broadcast("queue.updated", { source: "amo" });
      }
      req.log.info({ result }, "amo webhook processed");
      return result;
    } catch (err) {
      req.log.error({ err }, "amo webhook failed");
      // amo ретраит 5xx — отвечаем 200 после логирования, чтобы не зациклить,
      // но только если секрет верный (уже проверили).
      broadcast("deal.updated", { source: "amo", error: true });
      return reply.code(200).send({
        ok: false,
        message: err instanceof Error ? err.message : "webhook error",
      });
    }
  });

  app.post("/webhooks/ms", async (req, reply) => {
    if (!checkSecret(req)) return reply.code(403).send({ message: "forbidden" });
    // Изменение товара/остатков в МойСклад → обновляем кэш каталога в фоне.
    syncCatalog()
      .then(() => broadcast("catalog.updated", { source: "ms" }))
      .catch(() => {});
    return { ok: true };
  });
}
