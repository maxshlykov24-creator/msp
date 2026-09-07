import { sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { productEnters } from "../db/schema.js";
import * as ms from "../clients/ms.js";
import { extractIdFromHref } from "./bootstrap.js";
import { isHalfSetWarehouse, STORE_TO_WAREHOUSE } from "@kassa/shared";
import type { StockAgeBucket, StockStats } from "@kassa/shared";
import { getAppSettings } from "./settings.js";

/**
 * Статистика склада на тех данных, которые есть сегодня: возраст партии,
 * светофор по порогам и перекос размерных сеток.
 *
 * Оборачиваемость, излишки и неликвид считать пока не на чем: в МойСкладе
 * розничных продаж нет, история начнёт копиться с запуска кассы. Возраст партии
 * и перекос сеток — рабочий суррогат до этого момента.
 */

/** Даты оприходований из МойСклад в локальную таблицу. Зовётся синком. */
export async function syncEnterDates(): Promise<{ positions: number }> {
  const first = new Map<string, Date>();
  const last = new Map<string, Date>();
  for await (const batch of ms.iterateEnters()) {
    for (const enter of batch) {
      const moment = new Date(enter.moment.replace(" ", "T"));
      if (Number.isNaN(moment.getTime())) continue;
      for await (const positions of ms.iterateEnterPositions(enter.id)) {
        for (const position of positions) {
          const msId = extractIdFromHref(position.assortment.meta.href);
          if (!msId) continue;
          const known = first.get(msId);
          if (!known || moment < known) first.set(msId, moment);
          const latest = last.get(msId);
          if (!latest || moment > latest) last.set(msId, moment);
        }
      }
    }
  }

  for (const [msId, firstEnterAt] of first) {
    await db
      .insert(productEnters)
      .values({
        productMsId: msId,
        firstEnterAt,
        lastEnterAt: last.get(msId) ?? firstEnterAt,
        updatedAt: new Date(),
      })
      .onConflictDoUpdate({
        target: productEnters.productMsId,
        set: {
          // Первое оприходование не переписываем на более позднее: возраст
          // партии считается от самого раннего прихода.
          firstEnterAt: sql`least(${productEnters.firstEnterAt}, ${firstEnterAt.toISOString()}::timestamptz)`,
          lastEnterAt: sql`greatest(${productEnters.lastEnterAt}, ${(last.get(msId) ?? firstEnterAt).toISOString()}::timestamptz)`,
          updatedAt: new Date(),
        },
      });
  }
  return { positions: first.size };
}

export async function stockStats(query: {
  warehouse?: string;
  store?: string;
}): Promise<StockStats> {
  const settings = await getAppSettings();
  const warehouse =
    query.warehouse?.trim() ||
    (query.store ? STORE_TO_WAREHOUSE[query.store as keyof typeof STORE_TO_WAREHOUSE] : undefined) ||
    undefined;

  const rows = await db.execute(sql`
    SELECT
      s.warehouse_name AS warehouse,
      p.variation AS variation,
      p.suit_part AS part,
      p.size AS size,
      s.quantity::numeric AS qty,
      e.first_enter_at AS first_enter_at
    FROM products p
    JOIN stock s ON s.product_ms_id = p.ms_id
    LEFT JOIN product_enters e ON e.product_ms_id = p.ms_id
    WHERE s.quantity::numeric > 0
  `);
  const raw = (Array.isArray(rows) ? rows : ((rows as { rows?: unknown[] }).rows ?? [])) as Array<{
    warehouse: string;
    variation: string | null;
    part: string | null;
    size: string | null;
    qty: string | number;
    first_enter_at: string | null;
  }>;

  const scoped = raw.filter((r) =>
    warehouse ? r.warehouse === warehouse : !isHalfSetWarehouse(r.warehouse)
  );

  const buckets: Record<StockAgeBucket["bucket"], { items: number; models: Set<string> }> = {
    "0-3м": { items: 0, models: new Set() },
    "3-6м": { items: 0, models: new Set() },
    "6-12м": { items: 0, models: new Set() },
    "12м+": { items: 0, models: new Set() },
  };
  let oldest: Date | null = null;
  let redItems = 0;
  let yellowItems = 0;
  let datedItems = 0;

  for (const row of scoped) {
    const qty = Math.max(0, Math.floor(Number(row.qty) || 0));
    if (qty === 0) continue;
    if (!row.first_enter_at) continue; // без даты прихода возраст неизвестен
    const at = new Date(row.first_enter_at);
    if (Number.isNaN(at.getTime())) continue;
    if (!oldest || at < oldest) oldest = at;
    const days = Math.floor((Date.now() - at.getTime()) / 86_400_000);
    const bucket: StockAgeBucket["bucket"] =
      days < 90 ? "0-3м" : days < 180 ? "3-6м" : days < 365 ? "6-12м" : "12м+";
    buckets[bucket].items += qty;
    buckets[bucket].models.add(row.variation ?? "—");
    datedItems += qty;
    if (days >= settings.stockAgeRedDays) redItems += qty;
    else if (days >= settings.stockAgeYellowDays) yellowItems += qty;
  }

  // Светофор по доле старого остатка: треть в красной зоне — красный.
  const redShare = datedItems > 0 ? redItems / datedItems : 0;
  const yellowShare = datedItems > 0 ? yellowItems / datedItems : 0;
  const light: StockStats["light"] =
    redShare >= 0.33 ? "red" : yellowShare >= 0.33 ? "yellow" : "green";

  // Перекос сеток: части, у которых нет пары своего размера в той же вариации.
  const key = (variation: string | null, size: string | null, part: string | null) =>
    `${variation ?? ""}|${(size ?? "").trim()}|${part ?? ""}`;
  const qtyByKey = new Map<string, number>();
  for (const row of scoped) {
    const qty = Math.max(0, Math.floor(Number(row.qty) || 0));
    if (qty === 0 || !row.part) continue;
    const k = key(row.variation, row.size, row.part);
    qtyByKey.set(k, (qtyByKey.get(k) ?? 0) + qty);
  }
  let jacketsWithoutTrousers = 0;
  let trousersWithoutJackets = 0;
  for (const [k, qty] of qtyByKey) {
    const [variation, size, part] = k.split("|");
    if (part === "jacket") {
      const pair = qtyByKey.get(`${variation}|${size}|trousers`) ?? 0;
      if (pair < qty) jacketsWithoutTrousers += qty - pair;
    }
    if (part === "trousers") {
      const pair = qtyByKey.get(`${variation}|${size}|jacket`) ?? 0;
      if (pair < qty) trousersWithoutJackets += qty - pair;
    }
  }

  return {
    warehouse: warehouse ?? null,
    buckets: (Object.keys(buckets) as StockAgeBucket["bucket"][]).map((bucket) => ({
      bucket,
      items: buckets[bucket].items,
      models: buckets[bucket].models.size,
    })),
    light,
    oldestEnterDate: oldest ? oldest.toISOString() : null,
    gridSkew: { jacketsWithoutTrousers, trousersWithoutJackets },
  };
}
