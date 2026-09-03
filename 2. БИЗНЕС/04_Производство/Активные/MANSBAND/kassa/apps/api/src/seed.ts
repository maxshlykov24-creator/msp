import { randomBytes } from "node:crypto";
import { eq } from "drizzle-orm";
import { db, sql } from "./db/index.js";
import { users } from "./db/schema.js";
import { getEnv } from "./env.js";
import { hashPassword } from "./services/auth.js";

// Сид двух пользователей: Максим и Миша — оба админы.
// Пароль: из env (SEED_*_PASSWORD) либо генерируется и печатается один раз.
// mustChangePassword = true → смена при первом входе.

function genPassword(): string {
  return randomBytes(9).toString("base64url"); // ~12 символов
}

async function seedUser(login: string, name: string, passwordEnv: string | undefined) {
  const existing = await db.select().from(users).where(eq(users.login, login)).limit(1);
  if (existing[0]) {
    // eslint-disable-next-line no-console
    console.log(`Пользователь "${login}" уже существует — пропускаю.`);
    return;
  }
  const password = passwordEnv && passwordEnv.length >= 8 ? passwordEnv : genPassword();
  const passwordHash = await hashPassword(password);
  await db.insert(users).values({
    login,
    name,
    passwordHash,
    role: "admin",
    mustChangePassword: true,
  });
  // eslint-disable-next-line no-console
  console.log(`Создан админ "${login}" (${name}). Пароль: ${password}`);
  if (!passwordEnv) {
    // eslint-disable-next-line no-console
    console.log(`  ↑ Пароль сгенерирован. Передай владельцу безопасным каналом и смени при первом входе.`);
  }
}

async function main() {
  const env = getEnv();
  await seedUser(env.SEED_ADMIN_LOGIN, env.SEED_ADMIN_NAME, env.SEED_ADMIN_PASSWORD);
  await seedUser(env.SEED_MISHA_LOGIN, env.SEED_MISHA_NAME, env.SEED_MISHA_PASSWORD);
  await sql.end();
  // eslint-disable-next-line no-console
  console.log("Сид завершён.");
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("Ошибка сида:", err);
  process.exit(1);
});
