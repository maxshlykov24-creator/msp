import { buildServer } from "./server.js";
import { getEnv } from "./env.js";
import { runBootstrap, syncProducts, syncStock } from "./services/bootstrap.js";

// Мс до ближайшего наступления hour:00 по локальному времени контейнера (TZ).
function msUntilNextHour(hour: number): number {
  const now = new Date();
  const next = new Date(now);
  next.setHours(hour, 0, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}

async function main() {
  const env = getEnv();
  const app = await buildServer();

  await app.listen({ port: env.PORT, host: env.HOST });
  app.log.info(`Касса API слушает ${env.HOST}:${env.PORT}`);

  // Bootstrap-синк в фоне (не блокирует старт; ошибки не валят сервер).
  runBootstrap((m) => app.log.info(m));

  // Остатки — часто (по умолчанию каждые 10 мин): лёгкий отчёт по двум складам.
  const stockIntervalMs = env.STOCK_SYNC_INTERVAL_MIN * 60_000;
  setInterval(() => {
    syncStock()
      .then((r) => app.log.info(`Синк остатков: ${r.stock} строк.`))
      .catch((e) => app.log.warn(`Синк остатков не удался: ${(e as Error).message}`));
  }, stockIntervalMs);

  // Номенклатура — раз в сутки в CATALOG_SYNC_HOUR:00: тяжёлый перебор всего каталога.
  const scheduleCatalogSync = () => {
    const delay = msUntilNextHour(env.CATALOG_SYNC_HOUR);
    app.log.info(`Синк номенклатуры запланирован через ${Math.round(delay / 60_000)} мин.`);
    setTimeout(() => {
      syncProducts()
        .then((r) => app.log.info(`Суточный синк номенклатуры: ${r.products} поз.`))
        .catch((e) => app.log.warn(`Синк номенклатуры не удался: ${(e as Error).message}`))
        .finally(scheduleCatalogSync);
    }, delay);
  };
  scheduleCatalogSync();

  const shutdown = async () => {
    app.log.info("Остановка…");
    await app.close();
    process.exit(0);
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("Фатальная ошибка старта:", err);
  process.exit(1);
});
