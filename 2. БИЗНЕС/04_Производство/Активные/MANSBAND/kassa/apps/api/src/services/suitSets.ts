import { and, gt, isNotNull, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock } from "../db/schema.js";
import {
  computeCompleteness,
  STORE_TO_WAREHOUSE,
  suitPriceOf,
  effectiveSuitPriceRules,
  suitModelKey,
} from "@kassa/shared";
import type { SuitCompleteness, SuitLine, SuitModel, SuitPart, SuitStockLine } from "@kassa/shared";
import { getAppSettings } from "./settings.js";

/**
 * Комплектность костюмов: загрузка остатков костюмных изделий и расчёт.
 * Сам расчёт лежит в `@kassa/shared` (computeCompleteness) — он чистый, поэтому
 * проверяется на живой выгрузке МойСклада без поднятия базы.
 */

async function loadStockLines(): Promise<SuitStockLine[]> {
  const rows = await db
    .select({
      msId: products.msId,
      variation: products.variation,
      size: products.size,
      height: products.height,
      color: products.color,
      pattern: products.pattern,
      fit: products.fit,
      part: products.suitPart,
      line: products.suitLine,
      warehouse: stock.warehouseName,
      qty: stock.quantity,
    })
    .from(products)
    .innerJoin(stock, sql`${stock.productMsId} = ${products.msId}`)
    .where(
      and(isNotNull(products.suitPart), isNotNull(products.variation), gt(stock.quantity, "0"))
    );

  return rows
    .map((r) => ({
      msId: r.msId,
      variation: (r.variation ?? "").trim(),
      size: (r.size ?? "").trim(),
      height: (r.height ?? "").trim(),
      color: r.color,
      pattern: r.pattern,
      fit: (r.fit ?? "").trim(),
      part: r.part as SuitPart,
      line: (r.line as SuitLine) ?? "regular",
      warehouse: r.warehouse,
      qty: Math.max(0, Math.floor(Number(r.qty) || 0)),
    }))
    .filter((r) => r.variation && r.qty > 0);
}

/**
 * Все костюмные позиции каталога, включая те, у которых остатка нет ни на
 * одном складе (блок 3 плана «Костюм в кассе, цены прайса, поиск», созвон
 * 09.09): LEFT JOIN вместо inner — товар без строки в `stock` попадает в
 * список одной строкой с `qty: 0`, а не выпадает из модели совсем.
 */
async function loadCatalogSuitLines(): Promise<SuitStockLine[]> {
  const rows = await db
    .select({
      msId: products.msId,
      name: products.name,
      category: products.category,
      variation: products.variation,
      size: products.size,
      height: products.height,
      color: products.color,
      pattern: products.pattern,
      fit: products.fit,
      part: products.suitPart,
      line: products.suitLine,
      warehouse: stock.warehouseName,
      qty: stock.quantity,
    })
    .from(products)
    .leftJoin(stock, sql`${stock.productMsId} = ${products.msId}`)
    .where(and(isNotNull(products.suitPart), isNotNull(products.variation)));

  return rows
    .map((r) => ({
      msId: r.msId,
      name: r.name ?? "",
      category: r.category,
      variation: (r.variation ?? "").trim(),
      size: (r.size ?? "").trim(),
      height: (r.height ?? "").trim(),
      color: r.color,
      pattern: r.pattern,
      fit: (r.fit ?? "").trim(),
      part: r.part as SuitPart,
      line: (r.line as SuitLine) ?? "regular",
      warehouse: r.warehouse ?? "",
      qty: Math.max(0, Math.floor(Number(r.qty) || 0)),
    }))
    .filter((r) => r.variation);
}

export interface CatalogSuitModel extends SuitModel {
  /** Цена костюма из матрицы кассы; null — правило не нашлось (см. suitPricing.ts). */
  priceRub: number | null;
}

/**
 * Модели костюмов для поиска по каталогу: показывает модель, даже если
 * остатка нет ни на одном складе (в отличие от экрана «Костюмы», который
 * решает задачу комплектности реального остатка). Цена — из матрицы кассы.
 */
export async function catalogSuitModels(query: {
  q?: string;
  warehouse?: string;
  limit?: number;
}): Promise<{ models: CatalogSuitModel[]; total: number }> {
  const settings = await getAppSettings();
  const lines = await loadCatalogSuitLines();

  // Имя и группа пиджака по ключу модели — нужны только для сопоставления с
  // матрицей цен (suitPriceOf смотрит на реальное наименование МС, а не на
  // заголовок модели, который «Костюм тройка чёрный slim» и т.п.).
  const jacketByKey = new Map<string, { name: string; category: string | null }>();
  for (const line of lines) {
    if (line.part !== "jacket") continue;
    const key = suitModelKey(line);
    if (!jacketByKey.has(key)) {
      jacketByKey.set(key, { name: line.name ?? "", category: line.category ?? null });
    }
  }

  const { models } = computeCompleteness(lines, {
    tolerance: settings.suitSizeTolerance,
    warehouse: query.warehouse,
    q: query.q,
    keepZeroSizes: true,
  });

  const rules = effectiveSuitPriceRules(settings.suitPriceOverrides);
  const priced: CatalogSuitModel[] = models.map((m) => {
    const pieces = m.composition.includes("vest") ? 3 : 2;
    const jacket = jacketByKey.get(m.modelId);
    const matched = suitPriceOf(
      {
        jacketName: jacket?.name ?? m.title,
        category: jacket?.category ?? "",
        pieces,
        line: m.line,
        height: m.height ?? "",
      },
      rules
    );
    return { ...m, priceRub: matched ? matched.priceRub : null };
  });

  const limited = query.limit && query.limit > 0 ? priced.slice(0, query.limit) : priced;
  return { models: limited, total: priced.length };
}

export interface CompletenessQuery {
  /** Название склада МойСклад; для консультанта подставляется его магазин. */
  warehouse?: string;
  /** Магазин кассы — переводится в склад по STORE_TO_WAREHOUSE. */
  store?: string;
  /** Поиск по вариации, цвету, названию. */
  q?: string;
  limit?: number;
}

export async function suitCompleteness(query: CompletenessQuery): Promise<SuitCompleteness> {
  const settings = await getAppSettings();
  const warehouse =
    query.warehouse?.trim() ||
    (query.store ? STORE_TO_WAREHOUSE[query.store as keyof typeof STORE_TO_WAREHOUSE] : undefined) ||
    undefined;
  const lines = await loadStockLines();
  return computeCompleteness(lines, {
    tolerance: settings.suitSizeTolerance,
    warehouse,
    q: query.q,
    limit: query.limit,
  });
}

/**
 * Полупарки одной строкой на выгрузку колл-менеджеру: что лежит без пары, в
 * каком размере и чего к нему не хватает.
 */
export async function halfSetList(query: CompletenessQuery): Promise<
  Array<{
    variation: string;
    title: string;
    size: string;
    part: SuitPart;
    qty: number;
    missing: SuitPart[];
    nearestSizes: string[];
  }>
> {
  const { models } = await suitCompleteness({ ...query, limit: undefined });
  const rows: Array<{
    variation: string;
    title: string;
    size: string;
    part: SuitPart;
    qty: number;
    missing: SuitPart[];
    nearestSizes: string[];
  }> = [];
  for (const model of models) {
    for (const size of model.sizes) {
      for (const orphan of size.orphans) {
        rows.push({
          variation: model.variation,
          title: model.title,
          size: size.size,
          part: orphan.part,
          qty: orphan.qty,
          missing: orphan.missing,
          nearestSizes: orphan.nearestSizes,
        });
      }
    }
  }
  return rows;
}
