import type { FastifyInstance } from "fastify";
import { getEnv } from "../env.js";
import { broadcast } from "../ws/hub.js";
import { syncCatalog } from "../services/bootstrap.js";

// Входящие вебхуки из amoCRM и МойСклад. Проверка простого секрета в query (?secret=).
// На событие: инвалидация кэша + WebSocket-push на открытые кассы.

export default async function webhooksRoutes(app: FastifyInstance) {
  const env = getEnv();

  function checkSecret(req: { query: unknown }): boolean {
    const q = req.query as { secret?: string } | undefined;
    return q?.secret === env.WEBHOOK_SECRET;
  }

  app.post("/webhooks/amo", async (req, reply) => {
    if (!checkSecret(req)) return reply.code(403).send({ message: "forbidden" });
    // amoCRM шлёт form-encoded с изменениями сделок/контактов.
    broadcast("deal.updated", { source: "amo" });
    return { ok: true };
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
