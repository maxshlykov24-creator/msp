import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { appSettings } from "../db/schema.js";
import { SARY_MIN_CHECK_DEFAULT } from "@kassa/shared";
import type { AppSettings } from "@kassa/shared";
import { appendAudit, type AuditActor } from "./audit.js";

/**
 * Настройки кассы (созвон 20.08): параметры, которые владелец меняет без релиза.
 * Хранятся в app_settings по ключам; кэш в памяти с коротким TTL, чтобы
 * enqueueSary не ходил в БД на каждую продажу.
 */

const CACHE_TTL_MS = 30_000;
let cache: { value: AppSettings; at: number } | null = null;

async function readValue(key: string): Promise<unknown> {
  const rows = await db.select().from(appSettings).where(eq(appSettings.key, key)).limit(1);
  return rows[0]?.value;
}

export async function getAppSettings(): Promise<AppSettings> {
  if (cache && Date.now() - cache.at < CACHE_TTL_MS) return cache.value;
  const raw = await readValue("saryMinCheck");
  const saryMinCheck =
    typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? raw : SARY_MIN_CHECK_DEFAULT;
  const value: AppSettings = { saryMinCheck };
  cache = { value, at: Date.now() };
  return value;
}

export async function getSaryMinCheck(): Promise<number> {
  return (await getAppSettings()).saryMinCheck;
}

export async function updateAppSettings(
  patch: Partial<AppSettings>,
  actor: AuditActor
): Promise<AppSettings> {
  const before = await getAppSettings();
  if (patch.saryMinCheck != null) {
    await db
      .insert(appSettings)
      .values({ key: "saryMinCheck", value: patch.saryMinCheck, updatedBy: actor.name })
      .onConflictDoUpdate({
        target: appSettings.key,
        set: { value: patch.saryMinCheck, updatedBy: actor.name, updatedAt: new Date() },
      });
  }
  cache = null;
  const after = await getAppSettings();
  await appendAudit({
    actor,
    action: "settings.updated",
    entityType: "settings",
    entityId: "app",
    before,
    after,
  });
  return after;
}
