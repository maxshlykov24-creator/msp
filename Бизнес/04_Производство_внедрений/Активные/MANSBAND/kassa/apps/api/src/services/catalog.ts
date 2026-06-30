import { or, ilike, eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { products, stock } from "../db/schema.js";
import * as ms from "../clients/ms.js";
import { extractIdFromHref } from "./bootstrap.js";
import type { Product } from "@kassa/shared";

// Поиск по каталогу: имя / артикул / штрихкод. Остаток берём актуальный (on-demand)
// из МойСклад для найденных позиций, чтобы избежать «гонки остатков» при продаже.

export async function searchCatalog(q: string, limit: number, storeName?: string): Promise<Product[]> {
  const pattern = `%${q}%`;
  const rows = await db
    .select()
    .from(products)
    .where(or(ilike(products.name, pattern), ilike(products.article, pattern), eq(products.barcode, q)))
    .limit(limit);

  const result: Product[] = [];
  for (const r of rows) {
    let qty = 0;
    try {
      const live = await ms.getStockForProduct(r.msId);
      for (const sr of live) {
        for (const bs of sr.stockByStore ?? []) {
          if (!storeName || bs.name === storeName) qty += bs.stock;
        }
      }
    } catch {
      // фолбэк на кэш остатков
      const cached = await db.select().from(stock).where(eq(stock.productMsId, r.msId));
      qty = cached.reduce((acc, c) => acc + Number(c.quantity), 0);
    }
    result.push({
      id: r.msId,
      name: r.name,
      sku: r.article ?? r.code ?? "",
      category: r.category ?? "",
      price: r.price / 100,
      store: storeName ?? "",
      stock: qty,
    });
  }
  return result;
}

// Резолв href ассортимента по нашему productId (msId) для позиций документа.
export async function resolveAssortment(productMsId: string): Promise<{ href: string; type: string } | null> {
  const rows = await db.select().from(products).where(eq(products.msId, productMsId)).limit(1);
  const r = rows[0];
  if (!r) return null;
  return { href: r.msMetaHref, type: r.msType };
}

export { extractIdFromHref };
