import { buildServer } from "./server.js";
import { getEnv } from "./env.js";
import { runBootstrap, syncProducts, syncStock } from "./services/bootstrap.js";
import { scanOverdueReserves } from "./services/taskFlow.js";
import { enqueueWeeklySalary } from "./services/payroll.js";
import { prewarmAmoOpen } from "./services/deals.js";

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

  // Прогрев доски заявок (amoCRM) сразу после старта и раз в 35с дальше —
  // чтобы пользователи не попадали в холодный фетч на ~25–30с после деплоя/рестарта.
  prewarmAmoOpen();
  setInterval(prewarmAmoOpen, 35_000);

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

  // Просроченные отложки: при старте и раз в 15 мин — колл-менеджеру «Связаться»,
  // консультанту магазина «Убрать отложку» (кнопка «Отложка убрана»).
  const OVERDUE_SCAN_MS = 15 * 60_000;
  const runOverdueScan = () => {
    scanOverdueReserves()
      .then((n) => {
        if (n > 0) app.log.info(`Просроченные отложки: поставлено задач — ${n}.`);
      })
      .catch((e) => app.log.warn(`Скан просрочек не удался: ${(e as Error).message}`));
  };
  runOverdueScan();
  setInterval(runOverdueScan, OVERDUE_SCAN_MS);

  // ЗП консультантам: по вторникам Эдвину падает задача «Выдать зарплату»
  // за прошлую неделю (созвон 20.08). Проверка раз в час, addQueue дедуплицирует.
  const runSalaryCheck = () => {
    enqueueWeeklySalary()
      .then((created) => {
        if (created) app.log.info("Поставлена недельная задача «Выдать зарплату».");
      })
      .catch((e) => app.log.warn(`Задача ЗП не поставлена: ${(e as Error).message}`));
  };
  runSalaryCheck();
  setInterval(runSalaryCheck, 60 * 60_000);

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
