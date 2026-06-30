import { and, eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { amoMeta, msRefs, products, stock } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import * as ms from "../clients/ms.js";
import {
  AMO_WRITEBACK_FIELDS,
  MS_ORGANIZATION_NAME,
  MS_RETAIL_COUNTERPARTY_NAME,
  STORE_TO_WAREHOUSE,
} from "@kassa/shared";

// ── Синк справочников amoCRM (воронки/этапы/поля) ────────────────────

export async function syncAmoMeta(): Promise<void> {
  const pipelines = await amo.getPipelines();
  for (const p of pipelines) {
    await upsertAmoMeta("pipeline", p.id, null, p.name, p);
    for (const s of p._embedded?.statuses ?? []) {
      await upsertAmoMeta("status", s.id, p.id, s.name, s);
    }
  }

  const fields = await amo.getLeadCustomFields();
  for (const f of fields) {
    await upsertAmoMeta("field", f.id, null, f.name, f);
    for (const e of f.enums ?? []) {
      await upsertAmoMeta("enum", e.id, f.id, e.value, e);
    }
  }
}

async function upsertAmoMeta(
  kind: string,
  amoId: number,
  parentId: number | null,
  name: string,
  payload: unknown
) {
  await db
    .insert(amoMeta)
    .values({ kind, amoId, parentId, name, payload: payload as object, updatedAt: new Date() })
    .onConflictDoUpdate({
      target: [amoMeta.kind, amoMeta.amoId],
      set: { name, parentId, payload: payload as object, updatedAt: new Date() },
    });
}

// ── Резолверы amo по БД ─────────────────────────────────────────────

export async function getStatusId(pipelineId: number, stageName: string): Promise<number | null> {
  const rows = await db
    .select()
    .from(amoMeta)
    .where(and(eq(amoMeta.kind, "status"), eq(amoMeta.parentId, pipelineId)));
  const exact = rows.find((r) => r.name.toLowerCase() === stageName.toLowerCase());
  return exact?.amoId ?? rows.find((r) => r.name.toLowerCase().includes(stageName.toLowerCase()))?.amoId ?? null;
}

export async function getFieldIdByName(name: string): Promise<number | null> {
  const rows = await db
    .select()
    .from(amoMeta)
    .where(and(eq(amoMeta.kind, "field"), eq(amoMeta.name, name)));
  if (rows[0]) return rows[0].amoId;
  // мягкий поиск по вхождению
  const all = await db.select().from(amoMeta).where(eq(amoMeta.kind, "field"));
  return all.find((r) => r.name.toLowerCase().includes(name.toLowerCase()))?.amoId ?? null;
}

export async function getWritebackFieldIds(): Promise<{
  msOrder: number | null;
  msDemand: number | null;
  paymentStatus: number | null;
}> {
  return {
    msOrder: await getFieldIdByName(AMO_WRITEBACK_FIELDS.msOrder),
    msDemand: await getFieldIdByName(AMO_WRITEBACK_FIELDS.msDemand),
    paymentStatus: await getFieldIdByName(AMO_WRITEBACK_FIELDS.paymentStatus),
  };
}

// ── Синк meta МойСклад (организация/склады/контрагент/счёт) ──────────

export async function syncMsRefs(): Promise<void> {
  const orgs = await ms.getOrganizations();
  const org = orgs.find((o) => o.name === MS_ORGANIZATION_NAME) ?? orgs[0];
  if (org) {
    await upsertMsRef("organization", "default", org);
    // расчётный счёт организации (для безнала)
    const accounts = await ms.getOrganizationAccounts(org.id);
    const main = accounts.find((a) => (a as { isDefault?: boolean }).isDefault) ?? accounts[0];
    if (main) await upsertMsRef("account", "default", main);
  }

  const stores = await ms.getStores();
  for (const s of stores) {
    await upsertMsRef("store", s.name, s);
  }

  const retail = await ms.ensureRetailCounterparty(MS_RETAIL_COUNTERPARTY_NAME);
  await upsertMsRef("counterparty", "retail", retail);
}

async function upsertMsRef(kind: string, key: string, row: ms.MsRow) {
  const existing = await db
    .select()
    .from(msRefs)
    .where(and(eq(msRefs.kind, kind), eq(msRefs.key, key)))
    .limit(1);
  const values = {
    kind,
    key,
    msId: row.id,
    metaHref: row.meta.href,
    name: row.name,
    payload: row as object,
    updatedAt: new Date(),
  };
  if (existing[0]) {
    await db.update(msRefs).set(values).where(eq(msRefs.id, existing[0].id));
  } else {
    await db.insert(msRefs).values(values);
  }
}

export async function getMsRef(kind: string, key: string): Promise<ms.MsRow | null> {
  const rows = await db
    .select()
    .from(msRefs)
    .where(and(eq(msRefs.kind, kind), eq(msRefs.key, key)))
    .limit(1);
  return (rows[0]?.payload as ms.MsRow | undefined) ?? null;
}

// Склад по шоуруму: маппинг из shared → имя склада → meta из ms_refs.
// Если точное имя не найдено (например, Пятницкая = «На Новокузнецкой») — пробуем по подстроке.
export async function getWarehouseForStore(store: string): Promise<ms.MsRow | null> {
  const whName = STORE_TO_WAREHOUSE[store as keyof typeof STORE_TO_WAREHOUSE];
  if (!whName) return null;
  const exact = await getMsRef("store", whName);
  if (exact) return exact;
  const all = await db.select().from(msRefs).where(eq(msRefs.kind, "store"));
  const fuzzy = all.find((r) => r.name.toLowerCase().includes(whName.toLowerCase().replace("на ", "")));
  return (fuzzy?.payload as ms.MsRow | undefined) ?? null;
}

// ── Синк каталога + остатков ────────────────────────────────────────

export async function syncCatalog(): Promise<{ products: number; stock: number }> {
  let productCount = 0;
  for await (const batch of ms.iterateAssortment()) {
    for (const row of batch) {
      const firstBarcode = row.barcodes?.[0];
      const barcode = firstBarcode ? Object.values(firstBarcode)[0] ?? null : null;
      const price = row.salePrices?.[0]?.value ?? 0; // уже в копейках в МойСклад
      await db
        .insert(products)
        .values({
          msId: row.id,
          msMetaHref: row.meta.href,
          msType: row.meta.type,
          name: row.name,
          article: row.article ?? null,
          code: row.code ?? null,
          barcode: barcode ?? null,
          category: row.pathName ?? row.productFolder?.name ?? null,
          price,
          updatedAt: new Date(),
        })
        .onConflictDoUpdate({
          target: products.msId,
          set: {
            name: row.name,
            article: row.article ?? null,
            code: row.code ?? null,
            barcode: barcode ?? null,
            category: row.pathName ?? row.productFolder?.name ?? null,
            price,
            msMetaHref: row.meta.href,
            msType: row.meta.type,
            updatedAt: new Date(),
          },
        });
      productCount++;
    }
  }

  let stockCount = 0;
  const stockRows = await ms.getStockByStore();
  for (const sr of stockRows) {
    const assortmentId = extractIdFromHref(sr.meta?.href);
    if (!assortmentId) continue;
    for (const byStore of sr.stockByStore ?? []) {
      const whId = extractIdFromHref(byStore.meta.href);
      if (!whId) continue;
      await db
        .insert(stock)
        .values({
          productMsId: assortmentId,
          warehouseMsId: whId,
          warehouseName: byStore.name,
          quantity: String(byStore.stock),
          updatedAt: new Date(),
        })
        .onConflictDoUpdate({
          target: [stock.productMsId, stock.warehouseMsId],
          set: { quantity: String(byStore.stock), warehouseName: byStore.name, updatedAt: new Date() },
        });
      stockCount++;
    }
  }

  return { products: productCount, stock: stockCount };
}

export function extractIdFromHref(href: string | undefined): string | null {
  if (!href) return null;
  const m = href.match(/\/([0-9a-fA-F-]{36})(?:\/|$|\?)/);
  return m?.[1] ?? null;
}

// Полный bootstrap при старте: справочники amo + meta МС + каталог.
export async function runBootstrap(log: (msg: string) => void): Promise<void> {
  try {
    log("bootstrap: синк справочников amoCRM…");
    await syncAmoMeta();
    log("bootstrap: резолв meta МойСклад…");
    await syncMsRefs();
    log("bootstrap: синк каталога МойСклад…");
    const res = await syncCatalog();
    log(`bootstrap: каталог ${res.products} поз., остатки ${res.stock} строк.`);
  } catch (err) {
    log(`bootstrap: ошибка — ${(err as Error).message}`);
  }
}
