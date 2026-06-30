import { buildServer } from "./server.js";
import { getEnv } from "./env.js";
import { runBootstrap, syncCatalog } from "./services/bootstrap.js";

async function main() {
  const env = getEnv();
  const app = await buildServer();

  await app.listen({ port: env.PORT, host: env.HOST });
  app.log.info(`Касса API слушает ${env.HOST}:${env.PORT}`);

  // Bootstrap-синк в фоне (не блокирует старт; ошибки не валят сервер).
  runBootstrap((m) => app.log.info(m));

  // Периодический рефреш каталога/остатков (фолбэк к вебхукам).
  const intervalMs = env.SYNC_INTERVAL_MIN * 60_000;
  setInterval(() => {
    syncCatalog()
      .then((r) => app.log.info(`Периодический синк каталога: ${r.products} поз.`))
      .catch((e) => app.log.warn(`Синк каталога не удался: ${(e as Error).message}`));
  }, intervalMs);

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
