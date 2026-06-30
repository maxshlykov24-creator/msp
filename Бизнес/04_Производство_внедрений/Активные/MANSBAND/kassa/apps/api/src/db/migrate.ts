import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import postgres from "postgres";
import { getEnv } from "../env.js";

// Применение миграций Drizzle. Запускается как отдельный шаг при деплое.
async function main() {
  const env = getEnv();
  const migrationClient = postgres(env.DATABASE_URL, { max: 1 });
  const dbm = drizzle(migrationClient);
  await migrate(dbm, { migrationsFolder: "./drizzle" });
  await migrationClient.end();
  // eslint-disable-next-line no-console
  console.log("Миграции применены.");
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("Ошибка миграции:", err);
  process.exit(1);
});
