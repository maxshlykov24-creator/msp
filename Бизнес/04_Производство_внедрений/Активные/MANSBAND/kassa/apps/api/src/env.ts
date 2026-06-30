import { z } from "zod";

// Все секреты и настройки — только из окружения (.env на сервере, _private локально).
// Падаем на старте, если не хватает критичного.

const schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("production"),
  PORT: z.coerce.number().default(3001),
  HOST: z.string().default("0.0.0.0"),

  DATABASE_URL: z.string().min(1, "DATABASE_URL обязателен"),

  JWT_SECRET: z.string().min(16, "JWT_SECRET слишком короткий"),
  JWT_EXPIRES_IN: z.string().default("12h"),

  // amoCRM
  AMOCRM_API_BASE: z.string().url(),
  AMOCRM_LONG_LIVED_TOKEN: z.string().min(20),
  AMOCRM_PIPELINE_SALES: z.coerce.number(),
  AMOCRM_PIPELINE_COMPLAINTS: z.coerce.number(),

  // МойСклад
  MOYSKLAD_API_BASE: z.string().url(),
  MOYSKLAD_API_TOKEN: z.string().min(20),

  // Сидируемые пользователи (логин:пароль), пароли при первом входе меняются
  SEED_ADMIN_LOGIN: z.string().default("max"),
  SEED_ADMIN_PASSWORD: z.string().optional(),
  SEED_ADMIN_NAME: z.string().default("Максим"),
  SEED_MISHA_LOGIN: z.string().default("misha"),
  SEED_MISHA_PASSWORD: z.string().optional(),
  SEED_MISHA_NAME: z.string().default("Миша"),

  // Webhooks (секрет для проверки входящих)
  WEBHOOK_SECRET: z.string().default("change-me"),

  // Telegram (Фаза 4) — опционально
  TELEGRAM_BOT_TOKEN: z.string().optional(),
  TELEGRAM_GROUP_BAUMANSKAYA: z.string().optional(),
  TELEGRAM_GROUP_PYATNITSKAYA: z.string().optional(),

  // Публичный адрес (для регистрации вебхуков)
  PUBLIC_BASE_URL: z.string().url().default("https://mansband-kassa.ru"),

  // Период фонового рефреша каталога/справочников, мин
  SYNC_INTERVAL_MIN: z.coerce.number().default(30),
});

export type Env = z.infer<typeof schema>;

let cached: Env | null = null;

export function getEnv(): Env {
  if (cached) return cached;
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`).join("\n");
    throw new Error(`Некорректное окружение:\n${issues}`);
  }
  cached = parsed.data;
  return cached;
}
