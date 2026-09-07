import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock, suitBreaks } from "../db/schema.js";
import {
  countSuitsInLines,
  isHalfSetWarehouse,
  STORE_TO_WAREHOUSE,
  suitTitle,
} from "@kassa/shared";
import type { Deal, SuitBreak, SuitPart } from "@kassa/shared";

/**
 * Журнал разбитых костюмов (пункт владельца: ежедневная таблица новых
 * полупарков, чтобы видеть, кто их создаёт). Полупарк рождается в момент
 * продажи одной части костюма, и в кассе уже известно, кто продал, — поэтому
 * запись пишется прямо из чека, а не собирается сравнением остатков.
 *
 * Ночной снимок остаётся как сверка: он ловит разбиение, прошедшее не через
 * кассу (перемещение, инвентаризация, продажа в МойСкладе руками).
 */

interface SuitLineInfo {
  msId: string;
  variation: string;
  size: string | null;
  part: SuitPart;
  line: "smoking" | "regular";
  color: string | null;
  pattern: string | null;
  fit: string | null;
  height: string | null;
}

async function suitLinesByMsIds(msIds: string[]): Promise<Map<string, SuitLineInfo>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  if (unique.length === 0) return new Map();
  const rows = await db
    .select({
      msId: products.msId,
      variation: products.variation,
      size: products.size,
      part: products.suitPart,
      line: products.suitLine,
      color: products.color,
      pattern: products.pattern,
      fit: products.fit,
      height: products.height,
    })
    .from(products)
    .where(sql`${products.msId} = ANY(${unique})`);
  const map = new Map<string, SuitLineInfo>();
  for (const r of rows) {
    if (!r.part || !r.variation) continue;
    map.set(r.msId, {
      msId: r.msId,
      variation: r.variation,
      size: r.size,
      part: r.part as SuitPart,
      line: (r.line as "smoking" | "regular") ?? "regular",
      color: r.color,
      pattern: r.pattern,
      fit: r.fit,
      height: r.height,
    });
  }
  return map;
}

/** Остаток парных частей той же вариации и размера на складе магазина. */
async function pairStock(
  variation: string,
  size: string | null,
  warehouse: string | undefined
): Promise<Map<SuitPart, number>> {
  const rows = await db
    .select({
      part: products.suitPart,
      warehouse: stock.warehouseName,
      qty: stock.quantity,
      size: products.size,
    })
    .from(products)
    .innerJoin(stock, sql`${stock.productMsId} = ${products.msId}`)
    .where(and(eq(products.variation, variation), sql`${stock.quantity}::numeric > 0`));
  const result = new Map<SuitPart, number>();
  for (const r of rows) {
    if (!r.part) continue;
    if (isHalfSetWarehouse(r.warehouse)) continue;
    if (warehouse && r.warehouse !== warehouse) continue;
    if (size && (r.size ?? "").trim() !== size) continue;
    const part = r.part as SuitPart;
    result.set(part, (result.get(part) ?? 0) + (Number(r.qty) || 0));
  }
  return result;
}

/**
 * Запись в журнал по факту продажи. Пишем, только если после продажи парные
 * части остались на складе без пары: продали костюм целиком — полупарка нет.
 */
export async function noteSuitBreaks(deal: Deal): Promise<void> {
  const lines = deal.items.filter((item) => !item.isReturn && item.qty > 0 && item.productId);
  if (lines.length === 0) return;
  const info = await suitLinesByMsIds(lines.map((i) => i.productId));
  if (info.size === 0) return;

  const warehouse =
    STORE_TO_WAREHOUSE[deal.store as keyof typeof STORE_TO_WAREHOUSE] ?? undefined;
  // Сколько частей каждого вида продано в этом чеке по вариации и размеру:
  // костюм целиком в одном чеке полупарк не создаёт.
  const soldByModel = new Map<string, Map<SuitPart, number>>();
  for (const item of lines) {
    const suit = info.get(item.productId);
    if (!suit) continue;
    const key = `${suit.variation}|${(suit.size ?? "").trim()}`;
    const byPart = soldByModel.get(key) ?? new Map<SuitPart, number>();
    byPart.set(suit.part, (byPart.get(suit.part) ?? 0) + item.qty);
    soldByModel.set(key, byPart);
  }

  for (const item of lines) {
    const suit = info.get(item.productId);
    if (!suit) continue;
    const size = (suit.size ?? "").trim() || null;
    const sold = soldByModel.get(`${suit.variation}|${size ?? ""}`) ?? new Map<SuitPart, number>();
    const remaining = await pairStock(suit.variation, size, warehouse);
    // Части, которые остались на складе без пары к проданной.
    const leftParts = (["jacket", "trousers", "vest"] as SuitPart[]).filter((part) => {
      if (part === suit.part) return false;
      const stayed = (remaining.get(part) ?? 0) - (sold.get(part) ?? 0);
      return stayed > 0;
    });
    if (leftParts.length === 0) continue;

    await db
      .insert(suitBreaks)
      .values({
        store: deal.store,
        consultant: deal.consultant || "—",
        refDealNumber: String(deal.number),
        variation: suit.variation,
        title: suitTitle({
          hasVest: leftParts.includes("vest") || suit.part === "vest",
          line: suit.line,
          color: suit.color,
          pattern: suit.pattern,
          fit: suit.fit,
        }),
        soldPart: suit.part,
        size,
        leftParts,
        source: "sale",
        // Повторное проведение чека не должно удваивать запись.
        dedupKey: `sale:${deal.id}:${item.productId}`,
      })
      .onConflictDoNothing();
  }
}

export interface CartWarning {
  productId: string;
  variation: string;
  title: string;
  soldPart: SuitPart;
  size: string | null;
  /** Каких частей костюма нет в чеке. */
  missing: SuitPart[];
  /** Парная часть на складе магазина: её можно добить в этот же чек. */
  pairs: Array<{ part: SuitPart; qty: number }>;
}

/**
 * Предупреждение до продажи: пиджак уходит без брюк своего размера, а брюки
 * лежат на складе — костюм разбивается. Это то же «палить консультантов», но
 * до факта: полупарка ещё нет, костюм можно добить в чек.
 */
export async function cartWarnings(input: {
  store?: string;
  items: Array<{ productId: string; qty: number }>;
}): Promise<{ warnings: CartWarning[]; suits: number }> {
  const items = input.items.filter((i) => i.qty > 0 && i.productId);
  if (items.length === 0) return { warnings: [], suits: 0 };
  const info = await suitLinesByMsIds(items.map((i) => i.productId));
  if (info.size === 0) return { warnings: [], suits: 0 };

  // Костюмов в чеке — столько САР. Считаем тем же кодом, что и сервер продажи,
  // чтобы консультант видел в форме ровно то количество бонусов, которое
  // начислится.
  const { suits } = countSuitsInLines(
    items.map((item) => ({
      qty: item.qty,
      part: info.get(item.productId)?.part ?? null,
      variation: info.get(item.productId)?.variation ?? null,
    }))
  );

  const warehouse = input.store
    ? STORE_TO_WAREHOUSE[input.store as keyof typeof STORE_TO_WAREHOUSE] ?? undefined
    : undefined;

  const inCart = new Map<string, Map<SuitPart, number>>();
  for (const item of items) {
    const suit = info.get(item.productId);
    if (!suit) continue;
    const key = `${suit.variation}|${(suit.size ?? "").trim()}`;
    const byPart = inCart.get(key) ?? new Map<SuitPart, number>();
    byPart.set(suit.part, (byPart.get(suit.part) ?? 0) + item.qty);
    inCart.set(key, byPart);
  }

  const warnings: CartWarning[] = [];
  for (const item of items) {
    const suit = info.get(item.productId);
    if (!suit) continue;
    const size = (suit.size ?? "").trim() || null;
    const cart = inCart.get(`${suit.variation}|${size ?? ""}`) ?? new Map<SuitPart, number>();
    const stockNow = await pairStock(suit.variation, size, warehouse);
    // Жилет требуем только у моделей, где он вообще есть.
    const composition: SuitPart[] = stockNow.has("vest") || cart.has("vest")
      ? ["jacket", "trousers", "vest"]
      : ["jacket", "trousers"];
    const missing = composition.filter(
      (part) => part !== suit.part && (cart.get(part) ?? 0) < item.qty
    );
    if (missing.length === 0) continue;
    const pairs = missing
      .map((part) => ({ part, qty: (stockNow.get(part) ?? 0) - (cart.get(part) ?? 0) }))
      .filter((pair) => pair.qty > 0);
    if (pairs.length === 0) continue;
    warnings.push({
      productId: item.productId,
      variation: suit.variation,
      title: suitTitle({
        hasVest: composition.includes("vest"),
        line: suit.line,
        color: suit.color,
        pattern: suit.pattern,
        fit: suit.fit,
      }),
      soldPart: suit.part,
      size,
      missing,
      pairs,
    });
  }
  return { warnings, suits };
}

export interface BreaksQuery {
  from?: string;
  to?: string;
  consultant?: string;
}

export async function listSuitBreaks(query: BreaksQuery): Promise<SuitBreak[]> {
  const conditions = [];
  if (query.from) conditions.push(gte(suitBreaks.at, new Date(`${query.from}T00:00:00`)));
  if (query.to) conditions.push(lte(suitBreaks.at, new Date(`${query.to}T23:59:59`)));
  if (query.consultant) conditions.push(eq(suitBreaks.consultant, query.consultant));
  const rows = await db
    .select()
    .from(suitBreaks)
    .where(conditions.length > 0 ? and(...conditions) : undefined)
    .orderBy(desc(suitBreaks.at))
    .limit(500);
  return rows.map((r) => ({
    id: r.id,
    at: r.at.toISOString(),
    store: r.store,
    consultant: r.consultant,
    refDealNumber: r.refDealNumber,
    variation: r.variation,
    title: r.title,
    soldPart: r.soldPart as SuitPart,
    size: r.size,
    leftParts: (r.leftParts as SuitPart[]) ?? [],
    source: r.source === "snapshot" ? "snapshot" : "sale",
  }));
}

/**
 * Ночная сверка: изделия без пары, которых ещё нет в журнале за сегодня.
 * Консультант не назначается — разбиение прошло не через кассу.
 */
export async function snapshotSuitBreaks(): Promise<{ added: number }> {
  const { suitCompleteness } = await import("./suitSets.js");
  const { models } = await suitCompleteness({});
  const day = new Date().toISOString().slice(0, 10);
  let added = 0;
  for (const model of models) {
    for (const size of model.sizes) {
      for (const orphan of size.orphans) {
        const inserted = await db
          .insert(suitBreaks)
          .values({
            store: "—",
            consultant: "—",
            refDealNumber: null,
            variation: model.variation,
            title: model.title,
            soldPart: orphan.part,
            size: size.size,
            leftParts: orphan.missing,
            source: "snapshot",
            // Снимок за день по модели и размеру — один.
            dedupKey: `snapshot:${day}:${model.modelId}:${size.size}:${orphan.part}`,
          })
          .onConflictDoNothing()
          .returning({ id: suitBreaks.id });
        added += inserted.length;
      }
    }
  }
  return { added };
}
