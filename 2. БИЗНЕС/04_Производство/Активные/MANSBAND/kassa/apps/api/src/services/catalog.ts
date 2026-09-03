import { or, ilike, eq, inArray } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock } from "../db/schema.js";
import { STORE_TO_WAREHOUSE } from "@kassa/shared";
import { extractIdFromHref } from "./bootstrap.js";
import type { Product } from "@kassa/shared";

// Поиск по каталогу: имя / артикул / штрихкод. Остаток берём из локального кэша
// (таблица stock, обновляется фоново каждые ~10 мин) — чтобы поиск был мгновенным
// без сетевых запросов в МойСклад. Фактическое списание валидирует МойСклад при
// создании отгрузки, поэтому кэш допустим (устаревание до интервала синка остатков).

export async function searchCatalog(q: string, limit: number, storeName?: string): Promise<Product[]> {
  const pattern = `%${q}%`;
  const rows = await db
    .select()
    .from(products)
    .where(or(ilike(products.name, pattern), ilike(products.article, pattern), eq(products.barcode, q)))
    .limit(limit);

  if (rows.length === 0) return [];

  // Имя склада МойСклад для выбранного шоурума (для «На Пятницкой» это «На Новокузнецкой»).
  const warehouseName = storeName
    ? STORE_TO_WAREHOUSE[storeName as keyof typeof STORE_TO_WAREHOUSE] ?? undefined
    : undefined;

  // Остатки одним запросом по всем найденным позициям.
  const msIds = rows.map((r) => r.msId);
  const stockRows = await db.select().from(stock).where(inArray(stock.productMsId, msIds));
  const stockByProduct = new Map<string, number>();
  for (const s of stockRows) {
    if (warehouseName && s.warehouseName !== warehouseName) continue;
    stockByProduct.set(s.productMsId, (stockByProduct.get(s.productMsId) ?? 0) + Number(s.quantity));
  }

  return rows.map((r) => ({
    id: r.msId,
    name: r.name,
    sku: r.article ?? r.code ?? "",
    category: r.category ?? "",
    price: r.price / 100,
    store: storeName ?? "",
    stock: stockByProduct.get(r.msId) ?? 0,
  }));
}

// Резолв href ассортимента по нашему productId (msId) для позиций документа.
export async function resolveAssortment(productMsId: string): Promise<{ href: string; type: string } | null> {
  const rows = await db.select().from(products).where(eq(products.msId, productMsId)).limit(1);
  const r = rows[0];
  if (!r) return null;
  return { href: r.msMetaHref, type: r.msType };
}

export { extractIdFromHref };
