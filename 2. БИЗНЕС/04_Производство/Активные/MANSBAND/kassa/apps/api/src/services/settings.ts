import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { appSettings } from "../db/schema.js";
import {
  SARY_MIN_CHECK_DEFAULT,
  SARY_SUIT_GROUPS_DEFAULT,
  STOCK_AGE_RED_DAYS_DEFAULT,
  STOCK_AGE_YELLOW_DAYS_DEFAULT,
  SUIT_PART_KEYWORDS_DEFAULT,
  SUIT_SIZE_TOLERANCE_DEFAULT,
} from "@kassa/shared";
import type { AppSettings, SuitPart } from "@kassa/shared";
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
  const rawGroups = await readValue("sarySuitGroups");
  const groups = Array.isArray(rawGroups)
    ? rawGroups.filter((g): g is string => typeof g === "string" && g.trim().length > 0)
    : [];
  const value: AppSettings = {
    saryMinCheck,
    sarySuitGroups: groups.length > 0 ? groups : [...SARY_SUIT_GROUPS_DEFAULT],
    suitSizeTolerance: await readNumber("suitSizeTolerance", SUIT_SIZE_TOLERANCE_DEFAULT),
    suitPartKeywords: await readSuitPartKeywords(),
    stockAgeYellowDays: await readNumber("stockAgeYellowDays", STOCK_AGE_YELLOW_DAYS_DEFAULT),
    stockAgeRedDays: await readNumber("stockAgeRedDays", STOCK_AGE_RED_DAYS_DEFAULT),
    suitPriceOverrides: await readSuitPriceOverrides(),
  };
  cache = { value, at: Date.now() };
  return value;
}

async function readNumber(key: string, fallback: number): Promise<number> {
  const raw = await readValue(key);
  return typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? raw : fallback;
}

/**
 * Слова, по которым вид МойСклад относится к пиджаку, брюкам или жилету.
 * Новый вид («Слаксы») закрывается настройкой, без релиза.
 */
async function readSuitPartKeywords(): Promise<Record<SuitPart, string[]>> {
  const raw = await readValue("suitPartKeywords");
  const result = {
    jacket: [...SUIT_PART_KEYWORDS_DEFAULT.jacket],
    trousers: [...SUIT_PART_KEYWORDS_DEFAULT.trousers],
    vest: [...SUIT_PART_KEYWORDS_DEFAULT.vest],
  };
  if (raw && typeof raw === "object") {
    for (const part of ["jacket", "trousers", "vest"] as SuitPart[]) {
      const list = (raw as Record<string, unknown>)[part];
      if (!Array.isArray(list)) continue;
      const words = list.filter((w): w is string => typeof w === "string" && w.trim().length > 0);
      if (words.length > 0) result[part] = words;
    }
  }
  return result;
}

/**
 * Оверрайд цены/активности правил матрицы костюмов (созвон 09.09): владелец
 * правит цифры из «Настроек», логика match у правил остаётся в коде.
 */
async function readSuitPriceOverrides(): Promise<Record<string, { priceRub: number; active: boolean }>> {
  const raw = await readValue("suitPriceOverrides");
  const result: Record<string, { priceRub: number; active: boolean }> = {};
  if (!raw || typeof raw !== "object") return result;
  for (const [id, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    const price = (value as Record<string, unknown>).priceRub;
    const active = (value as Record<string, unknown>).active;
    if (typeof price === "number" && Number.isFinite(price) && price >= 0 && typeof active === "boolean") {
      result[id] = { priceRub: Math.round(price), active };
    }
  }
  return result;
}

export async function getSaryMinCheck(): Promise<number> {
  return (await getAppSettings()).saryMinCheck;
}

export async function getSarySuitGroups(): Promise<string[]> {
  return (await getAppSettings()).sarySuitGroups;
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
  if (patch.sarySuitGroups != null) {
    const groups = patch.sarySuitGroups.map((g) => g.trim()).filter(Boolean);
    await db
      .insert(appSettings)
      .values({ key: "sarySuitGroups", value: groups, updatedBy: actor.name })
      .onConflictDoUpdate({
        target: appSettings.key,
        set: { value: groups, updatedBy: actor.name, updatedAt: new Date() },
      });
  }
  for (const key of [
    "suitSizeTolerance",
    "stockAgeYellowDays",
    "stockAgeRedDays",
    "suitPartKeywords",
    "suitPriceOverrides",
  ] as const) {
    const next = patch[key];
    if (next == null) continue;
    await db
      .insert(appSettings)
      .values({ key, value: next, updatedBy: actor.name })
      .onConflictDoUpdate({
        target: appSettings.key,
        set: { value: next, updatedBy: actor.name, updatedAt: new Date() },
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
