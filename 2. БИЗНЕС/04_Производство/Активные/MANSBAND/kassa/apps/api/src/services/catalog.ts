import { and, or, ilike, eq, inArray, asc, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock, msRefs, productFolders } from "../db/schema.js";
import {
  CDEK_LOCATION,
  CDEK_WAREHOUSE_ID,
  STORE_TO_WAREHOUSE,
  sortWarehousesByDisplayOrder,
} from "@kassa/shared";
import * as ms from "../clients/ms.js";
import { extractIdFromHref } from "./bootstrap.js";
import type { Product, SuitPart } from "@kassa/shared";

/** Группа для UI: pathName МС или префикс имени до « (» (у вариантов pathName часто пустой). */
function displayCategory(category: string | null | undefined, name: string): string {
  const fromMs = category?.trim();
  if (fromMs) return fromMs;
  const cut = name.indexOf(" (");
  const inferred = (cut > 0 ? name.slice(0, cut) : name).trim();
  return inferred || "Без категории";
}

type ProductRow = typeof products.$inferSelect;

/**
 * Позиции, лежащие в СДЭК. Склада «СДЭК» в МойСклад нет: это расположение
 * позиции в заявке кассы, поэтому количество собираем из `deal_item_state`
 * и количества позиции внутри `deals.data` (созвон 09.09 — видеть СДЭК строкой
 * в остатках товара).
 */
async function cdekQtyByProduct(productMsIds: string[]): Promise<Map<string, number>> {
  const result = new Map<string, number>();
  if (productMsIds.length === 0) return result;
  const rows = await db.execute(sql`
    select s.item_id as item_id,
           coalesce(sum(coalesce((i->>'qty')::numeric, 1)), 0) as qty
      from deal_item_state s
      join deals d on d.number = s.deal_number
      left join lateral jsonb_array_elements(d.data->'items') i
             on i->>'productId' = s.item_id
     where s.location ilike 'сдэк%'
       -- Закрытая заявка означает, что посылку уже забрали или вернули:
       -- в СДЭК такая позиция больше не лежит.
       and d.stage not in ('Успех', 'Провал')
       and s.item_id in (${sql.join(
         productMsIds.map((id) => sql`${id}`),
         sql`, `
       )})
     group by s.item_id
  `);
  const raw = (Array.isArray(rows) ? rows : ((rows as { rows?: unknown[] }).rows ?? [])) as Array<{
    item_id: string;
    qty: string | number;
  }>;
  for (const r of raw) {
    const qty = Number(r.qty) || 0;
    if (qty > 0) result.set(r.item_id, qty);
  }
  return result;
}

function cdekLine(qty: number): NonNullable<Product["warehouses"]>[number] {
  return {
    warehouseMsId: CDEK_WAREHOUSE_ID,
    name: CDEK_LOCATION,
    available: qty,
    reserve: 0,
    stock: qty,
  };
}

async function attachStock(rows: ProductRow[], storeName?: string): Promise<Product[]> {
  if (rows.length === 0) return [];
  const preferredWarehouse = storeName
    ? STORE_TO_WAREHOUSE[storeName as keyof typeof STORE_TO_WAREHOUSE] ?? undefined
    : undefined;

  const msIds = rows.map((r) => r.msId);
  const stockRows = await db.select().from(stock).where(inArray(stock.productMsId, msIds));
  const warehousesByProduct = new Map<string, Product["warehouses"]>();
  const stockByProduct = new Map<string, number>();
  for (const s of stockRows) {
    const qty = Number(s.quantity) || 0;
    const line = {
      warehouseMsId: s.warehouseMsId,
      name: s.warehouseName || s.warehouseMsId,
      available: qty,
      reserve: 0,
      stock: qty,
    };
    const list = warehousesByProduct.get(s.productMsId) ?? [];
    list.push(line);
    warehousesByProduct.set(s.productMsId, list);
    if (!preferredWarehouse || s.warehouseName === preferredWarehouse) {
      stockByProduct.set(s.productMsId, (stockByProduct.get(s.productMsId) ?? 0) + qty);
    }
  }

  const cdek = await cdekQtyByProduct(msIds);

  return rows.map((r) => {
    const warehouses = sortWarehousesByDisplayOrder(warehousesByProduct.get(r.msId) ?? []);
    const cdekQty = cdek.get(r.msId) ?? 0;
    if (cdekQty > 0) warehouses.push(cdekLine(cdekQty));
    return {
      id: r.msId,
      name: r.name,
      sku: r.article ?? r.code ?? "",
      category: displayCategory(r.category, r.name),
      price: r.price / 100,
      store: storeName ?? "",
      stock: stockByProduct.get(r.msId) ?? 0,
      barcode: r.barcode ?? undefined,
      warehouses,
    };
  });
}

// Поиск по каталогу: имя / артикул / код / штрихкод.
// Несколько слов через пробел — AND по токенам (пример: «GO109SL12 черный»).
// «Доступно» и разбивка по складам из локального кэша stock — без N запросов в МойСклад.
// Live-детализация одного товара — getProductStockByWarehouses.

function tokenMatchesField(token: string) {
  const safe = token.replace(/[%_\\]/g, "");
  if (!safe) return undefined;
  const pattern = `%${safe}%`;
  return or(
    ilike(products.name, pattern),
    ilike(products.article, pattern),
    ilike(products.code, pattern),
    eq(products.barcode, safe),
    ilike(products.barcode, pattern)
  );
}

export async function searchCatalog(
  q: string,
  limit: number,
  storeName?: string,
  category?: string,
  browse?: boolean
): Promise<Product[]> {
  if (browse && !q.trim() && !category?.trim()) {
    // Обзор по разделам: больше позиций, чтобы UI собрал модели без модификаций.
    return browseCatalogByGroups(storeName, Math.min(Math.max(limit, 80), 120));
  }

  const tokens = q
    .trim()
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean);
  const tokenConditions = tokens
    .map((token) => tokenMatchesField(token))
    .filter((c): c is NonNullable<typeof c> => c != null);
  const textCondition = tokenConditions.length > 0 ? and(...tokenConditions) : undefined;
  const cat = category?.trim();
  // Фильтр группы: либо pathName МС, либо префикс имени (для вариантов без категории).
  const categoryCondition = cat
    ? or(
        ilike(products.category, `${cat}%`),
        and(
          sql`coalesce(trim(${products.category}), '') = ''`,
          or(eq(sql`split_part(${products.name}, ' (', 1)`, cat), ilike(products.name, `${cat} (%`))
        )
      )
    : undefined;
  const rows = await db
    .select()
    .from(products)
    .where(and(
      inArray(products.msType, ["product", "variant"]),
      textCondition,
      categoryCondition
    ))
    .orderBy(asc(products.name))
    .limit(limit);

  return attachStock(rows, storeName);
}

/**
 * Обзор без поиска: только 5 разделов зала (1–5).
 * Берём до perModel вариаций на модель, чтобы список не залипал на одном «Брюки».
 */
export async function browseCatalogByGroups(storeName?: string, perGroup = 80): Promise<Product[]> {
  const perModel = 4;
  const result = await db.execute(sql`
    WITH hall AS (
      SELECT
        p.*,
        CASE
          WHEN p.category LIKE '1. Костюмы%' THEN '1. Костюмы'
          WHEN p.category LIKE '3. Верхняя одежда%' THEN '3. Верхняя одежда'
          WHEN p.category LIKE '2. Одежда%' THEN '2. Одежда'
          WHEN p.category LIKE '4. Обувь%' THEN '4. Обувь'
          WHEN p.category LIKE '5. Аксессуары%' THEN '5. Аксессуары'
          ELSE NULL
        END AS hall_grp,
        nullif(trim(split_part(p.name, ' (', 1)), '') AS model
      FROM products p
      WHERE p.ms_type IN ('product', 'variant')
        AND coalesce(trim(p.category), '') <> ''
    ),
    numbered AS (
      SELECT
        hall.*,
        row_number() OVER (
          PARTITION BY hall_grp, model
          ORDER BY CASE WHEN ms_type = 'product' THEN 0 ELSE 1 END, name
        ) AS rn_model,
        row_number() OVER (
          PARTITION BY hall_grp
          ORDER BY CASE WHEN ms_type = 'product' THEN 0 ELSE 1 END, model, name
        ) AS rn_hall
      FROM hall
      WHERE hall_grp IS NOT NULL AND model IS NOT NULL
    )
    SELECT
      id, ms_id, name, article, code, barcode, category, price,
      ms_meta_href, ms_type, updated_at, hall_grp AS grp
    FROM numbered
    WHERE rn_model <= ${perModel} AND rn_hall <= ${Math.max(perGroup, 120)}
    ORDER BY hall_grp, model, name
    LIMIT ${Math.max(perGroup, 120) * 5}
  `);

  const rawRows = (Array.isArray(result)
    ? result
    : ((result as { rows?: unknown[] }).rows ?? [])) as Array<Record<string, unknown>>;

  const rows = rawRows.map((row) => ({
    id: String(row.id),
    msId: String(row.ms_id),
    name: String(row.name),
    article: (row.article as string | null) ?? null,
    code: (row.code as string | null) ?? null,
    barcode: (row.barcode as string | null) ?? null,
    // Полный path МС сохраняем — верхний раздел для UI режется на клиенте.
    category: (row.category as string | null) ?? null,
    price: Number(row.price) || 0,
    msMetaHref: String(row.ms_meta_href),
    msType: String(row.ms_type),
    updatedAt: row.updated_at ? new Date(String(row.updated_at)) : new Date(),
  })) as ProductRow[];

  return attachStock(rows, storeName);
}

export type WarehouseStockLine = NonNullable<Product["warehouses"]>[number];

function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(fallback), ms);
    p.then((v) => {
      clearTimeout(t);
      resolve(v);
    }).catch(() => {
      clearTimeout(t);
      resolve(fallback);
    });
  });
}

/**
 * Остатки по складам для позиции.
 * Сначала локальный кэш (быстро, для мобилки), затем живой отчёт МойСклад (до 8с).
 * `cacheOnly` — только кэш, для списков без N live-запросов в МС.
 */
export async function getProductStockByWarehouses(
  productMsId: string,
  opts?: { cacheOnly?: boolean }
): Promise<{
  productId: string;
  name: string;
  warehouses: WarehouseStockLine[];
  source: "cache" | "live" | "mixed";
}> {
  const productRows = await db.select().from(products).where(eq(products.msId, productMsId)).limit(1);
  const product = productRows[0];
  const productName = product?.name ?? productMsId;
  const assortmentType = (product?.msType as "product" | "variant" | undefined) ?? "product";

  const knownStores = await db.select().from(msRefs).where(eq(msRefs.kind, "store"));
  const byId = new Map<string, WarehouseStockLine>();
  for (const s of knownStores) {
    byId.set(s.msId, {
      warehouseMsId: s.msId,
      name: s.name,
      available: 0,
      reserve: 0,
      stock: 0,
    });
  }

  // Локальный кэш (доступно ≈ quantity после синка)
  const localRows = await db.select().from(stock).where(eq(stock.productMsId, productMsId));
  let fromCache = false;
  for (const s of localRows) {
    fromCache = true;
    const qty = Number(s.quantity) || 0;
    byId.set(s.warehouseMsId, {
      warehouseMsId: s.warehouseMsId,
      name: s.warehouseName || byId.get(s.warehouseMsId)?.name || s.warehouseMsId,
      available: qty,
      reserve: byId.get(s.warehouseMsId)?.reserve ?? 0,
      stock: qty,
    });
  }

  let fromLive = false;
  if (!opts?.cacheOnly) {
    const report = await withTimeout(
      ms.getStockForProduct(productMsId, assortmentType === "variant" ? "variant" : "product").catch(
        (error) => {
          if (ms.isMoySkladAuthError(error)) throw error;
          return [] as ms.MsStockRow[];
        }
      ),
      8_000,
      [] as ms.MsStockRow[]
    );

    for (const row of report) {
      for (const byStore of row.stockByStore ?? []) {
        const whId = extractIdFromHref(byStore.meta.href);
        if (!whId) continue;
        fromLive = true;
        const stockQty = byStore.stock ?? 0;
        const reserve = byStore.reserve ?? 0;
        byId.set(whId, {
          warehouseMsId: whId,
          name: byStore.name || byId.get(whId)?.name || whId,
          stock: stockQty,
          reserve,
          available: stockQty - reserve,
        });
      }
    }
  }

  const warehouses = sortWarehousesByDisplayOrder([...byId.values()]);
  // СДЭК идёт последней строкой: это не склад, а положение позиции в заявке.
  const cdekQty = (await cdekQtyByProduct([productMsId])).get(productMsId) ?? 0;
  if (cdekQty > 0) warehouses.push(cdekLine(cdekQty));
  const source: "cache" | "live" | "mixed" =
    fromLive && fromCache ? "mixed" : fromLive ? "live" : "cache";

  return { productId: productMsId, name: productName, warehouses, source };
}

export async function resolveAssortment(productMsId: string): Promise<{ href: string; type: string } | null> {
  const rows = await db.select().from(products).where(eq(products.msId, productMsId)).limit(1);
  const r = rows[0];
  if (!r) return null;
  return { href: r.msMetaHref, type: r.msType };
}

/** Имена товаров по msId — для карточек задач (позиции перемещения). */
export async function productNamesByMsIds(msIds: string[]): Promise<Map<string, string>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  if (unique.length === 0) return new Map();
  const rows = await db
    .select({ msId: products.msId, name: products.name })
    .from(products)
    .where(inArray(products.msId, unique));
  return new Map(rows.map((r) => [r.msId, r.name]));
}

/** Группа МойСклад по msId — по ней считаются костюмы в чеке для САР. */
export async function productCategoriesByMsIds(msIds: string[]): Promise<Map<string, string>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  if (unique.length === 0) return new Map();
  const rows = await db
    .select({ msId: products.msId, category: products.category })
    .from(products)
    .where(inArray(products.msId, unique));
  return new Map(rows.map((r) => [r.msId, r.category ?? ""]));
}

/**
 * Часть костюма и вариация по msId — по ним костюм в чеке считается как костюм,
 * а не как три позиции. Позиции без части костюма в карту не попадают.
 */
export async function suitPartsByMsIds(
  msIds: string[]
): Promise<Map<string, { part: SuitPart; variation: string }>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  if (unique.length === 0) return new Map();
  const rows = await db
    .select({ msId: products.msId, part: products.suitPart, variation: products.variation })
    .from(products)
    .where(inArray(products.msId, unique));
  const map = new Map<string, { part: SuitPart; variation: string }>();
  for (const r of rows) {
    if (!r.part || !r.variation) continue;
    map.set(r.msId, { part: r.part as SuitPart, variation: r.variation });
  }
  return map;
}

/** Данные части костюма по msId — вход для подбора цены костюма (suitPricing.ts). */
export interface SuitPricingInfo {
  msId: string;
  name: string;
  category: string | null;
  variation: string;
  part: SuitPart;
  size: string;
  height: string;
  line: "smoking" | "regular";
  priceRub: number;
}

export async function suitPricingInfoByMsIds(msIds: string[]): Promise<Map<string, SuitPricingInfo>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  const map = new Map<string, SuitPricingInfo>();
  if (unique.length === 0) return map;
  const rows = await db
    .select({
      msId: products.msId,
      name: products.name,
      category: products.category,
      variation: products.variation,
      part: products.suitPart,
      size: products.size,
      height: products.height,
      line: products.suitLine,
      price: products.price,
    })
    .from(products)
    .where(inArray(products.msId, unique));
  for (const r of rows) {
    if (!r.part || !r.variation) continue;
    map.set(r.msId, {
      msId: r.msId,
      name: r.name,
      category: r.category,
      variation: r.variation,
      part: r.part as SuitPart,
      size: (r.size ?? "").trim(),
      height: (r.height ?? "").trim(),
      line: (r.line as "smoking" | "regular") ?? "regular",
      priceRub: r.price / 100,
    });
  }
  return map;
}

/** Штрихкод / код / артикул по msId — для скана при отправке и приёмке. */
export async function productCodesByMsIds(
  msIds: string[]
): Promise<Map<string, { barcode?: string; code?: string; article?: string }>> {
  const unique = [...new Set(msIds.map((id) => id.trim()).filter(Boolean))];
  if (unique.length === 0) return new Map();
  const rows = await db
    .select({
      msId: products.msId,
      barcode: products.barcode,
      code: products.code,
      article: products.article,
    })
    .from(products)
    .where(inArray(products.msId, unique));
  return new Map(
    rows.map((r) => [
      r.msId,
      {
        barcode: r.barcode ?? undefined,
        code: r.code ?? undefined,
        article: r.article ?? undefined,
      },
    ])
  );
}

export async function listCatalogReferences(): Promise<{
  categories: string[];
  groups: Array<{ name: string; path: string }>;
  /** Дерево групп из МойСклад: эталон иерархии, двухуровневый выбор в поиске. */
  folders: Array<{ id: string; name: string; path: string; parentPath: string | null; level: number }>;
  warehouses: Array<{ id: string; name: string }>;
}> {
  const rows = await db
    .selectDistinct({ category: products.category })
    .from(products)
    .where(inArray(products.msType, ["product", "variant"]))
    .orderBy(asc(products.category));
  const fromMs = rows
    .map((r) => r.category?.trim())
    .filter((v): v is string => Boolean(v));
  // Префиксы имён для вариантов без pathName — чтобы фильтр «группы» работал в зале.
  const inferred = await db.execute(sql`
    SELECT DISTINCT nullif(trim(split_part(name, ' (', 1)), '') AS grp
    FROM products
    WHERE ms_type IN ('product', 'variant')
      AND coalesce(trim(category), '') = ''
      AND nullif(trim(split_part(name, ' (', 1)), '') IS NOT NULL
    ORDER BY 1
    LIMIT 80
  `);
  const inferredRows = (Array.isArray(inferred)
    ? inferred
    : ((inferred as { rows?: unknown[] }).rows ?? [])) as Array<{ grp: string | null }>;
  const fromNames = inferredRows
    .map((r) => r.grp?.trim())
    .filter((v): v is string => Boolean(v));
  const categories = [...new Set([...fromMs, ...fromNames])].sort((a, b) => a.localeCompare(b, "ru"));
  const groups = new Map<string, { name: string; path: string }>();
  for (const path of categories) {
    const parts = path.split(/[\\/]/).map((v) => v.trim()).filter(Boolean);
    parts.forEach((name, i) => {
      const groupPath = parts.slice(0, i + 1).join("/");
      groups.set(groupPath, { name, path: groupPath });
    });
  }
  const folderRows = await db
    .select()
    .from(productFolders)
    .where(eq(productFolders.archived, false))
    .orderBy(asc(productFolders.path));
  const folders = folderRows.map((f) => {
    const cut = f.path.lastIndexOf("/");
    return {
      id: f.msId,
      name: f.name,
      path: f.path,
      parentPath: cut > 0 ? f.path.slice(0, cut) : null,
      level: f.level,
    };
  });
  const warehouseRows = await db
    .select({ id: msRefs.msId, name: msRefs.name })
    .from(msRefs)
    .where(eq(msRefs.kind, "store"))
    .orderBy(asc(msRefs.name));
  return { categories, groups: [...groups.values()], folders, warehouses: warehouseRows };
}

/**
 * Сверка прайса после синка из МойСклад (созвон 04.09, п.7): сколько позиций
 * приехало, у скольких нет цены и когда последнее обновление. Редактирования цен
 * в кассе нет — это только контроль, что новый прайс доехал.
 */
export async function priceAudit(): Promise<{
  total: number;
  withoutPrice: number;
  updatedLast24h: number;
  lastUpdatedAt: string | null;
  samples: Array<{ name: string; article: string | null; category: string | null }>;
}> {
  const [totals] = await db
    .select({
      total: sql<number>`count(*)::int`,
      withoutPrice: sql<number>`count(*) FILTER (WHERE coalesce(price, 0) = 0)::int`,
      updatedLast24h: sql<number>`count(*) FILTER (WHERE updated_at > now() - interval '24 hours')::int`,
      lastUpdatedAt: sql<string | null>`max(updated_at)`,
    })
    .from(products);
  const samples = await db
    .select({ name: products.name, article: products.article, category: products.category })
    .from(products)
    .where(sql`coalesce(${products.price}, 0) = 0`)
    .orderBy(asc(products.name))
    .limit(20);
  return {
    total: totals?.total ?? 0,
    withoutPrice: totals?.withoutPrice ?? 0,
    updatedLast24h: totals?.updatedLast24h ?? 0,
    lastUpdatedAt: totals?.lastUpdatedAt ? new Date(totals.lastUpdatedAt).toISOString() : null,
    samples,
  };
}

export { extractIdFromHref };
