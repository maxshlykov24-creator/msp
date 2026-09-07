import { and, eq, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { amoMeta, msRefs, productFolders, products, stock } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import * as ms from "../clients/ms.js";
import {
  AD_SOURCES,
  AMO_LEAD_FIELDS,
  AMO_NEW_LEAD_FIELDS,
  AMO_COMPANY_FIELDS,
  AMO_NEW_COMPANY_FIELDS,
  AMO_PIPELINE_SALES,
  AMO_SYSTEM_STATUS_IDS,
  AMO_WRITEBACK_FIELDS,
  MS_ORGANIZATION_NAME,
  MS_RETAIL_COUNTERPARTY_NAME,
  STAGE_ALIASES,
  STOCK_WAREHOUSES,
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

  await ensureChannelEnums(fields);

  const companyFields = await amo.getCompanyCustomFields();
  for (const f of companyFields) {
    await upsertAmoMeta("company_field", f.id, null, f.name, f);
  }
}

// «Канал продаж» — select: значения из AD_SOURCES (в т.ч. промокоды) должны
// существовать в amoCRM, иначе writeback заявки отвалится по неизвестному enum.
async function ensureChannelEnums(fields: amo.AmoField[]): Promise<void> {
  const channel = fields.find((f) => f.name.toLowerCase().trim() === AMO_LEAD_FIELDS.channel.toLowerCase());
  if (!channel || !channel.type.includes("select")) return;
  try {
    const updated = await amo.addLeadFieldEnums(channel, AD_SOURCES);
    if (!updated) return;
    await upsertAmoMeta("field", updated.id, null, updated.name, updated);
    for (const e of updated.enums ?? []) {
      await upsertAmoMeta("enum", e.id, updated.id, e.value, e);
    }
  } catch {
    // не критично — новые значения канала просто не запишутся до ручной правки
  }
}

// Создаёт в amoCRM недостающие кастом-поля лида/компании (идемпотентно —
// пропускает поля, найденные по имени). Нужно, чтобы деплой не требовал
// ручных действий в интерфейсе amoCRM.
export async function ensureCustomFieldsExist(): Promise<void> {
  for (const { key, type, isApiOnly } of AMO_NEW_LEAD_FIELDS) {
    const name = AMO_LEAD_FIELDS[key];
    const existing = await getFieldIdByName(name);
    if (existing) {
      // Дожимаем «только API» для флажков возврата/обмена, если поле уже было.
      if (isApiOnly) {
        try {
          const updated = await amo.updateLeadCustomField(existing, { is_api_only: true });
          await upsertAmoMeta("field", updated.id, null, updated.name, updated);
        } catch {
          // не критично
        }
      }
      continue;
    }
    try {
      const created = await amo.createLeadCustomField(name, type, { isApiOnly });
      await upsertAmoMeta("field", created.id, null, created.name, created);
    } catch {
      // не критично — writeback пропустит поле, пока оно не создано
    }
  }

  for (const { key, type } of AMO_NEW_COMPANY_FIELDS) {
    const name = AMO_COMPANY_FIELDS[key];
    const existing = await getCompanyFieldIdByName(name);
    if (existing) continue;
    try {
      const created = await amo.createCompanyCustomField(name, type);
      await upsertAmoMeta("company_field", created.id, null, created.name, created);
    } catch {
      // аналогично
    }
  }
}

/**
 * Этап «Счёт запрошен» перед «Счет выставлен» (продажа компании).
 * Если в воронке Продажи ещё нет — создаём сразу перед «Счет выставлен».
 */
export async function ensureCompanyPreInvoiceStage(): Promise<void> {
  const already = await getStatusId(AMO_PIPELINE_SALES, "Счёт запрошен");
  if (already) return;

  const pipelines = await amo.getPipelines();
  const sales = pipelines.find((p) => p.id === AMO_PIPELINE_SALES);
  const statuses = sales?._embedded?.statuses ?? [];
  const invoice = statuses.find((s) => {
    const n = s.name.toLowerCase();
    return n === "счет выставлен" || n === "счёт выставлен";
  });
  const sort = invoice ? Math.max(1, (invoice.sort ?? 20) - 1) : 15;
  try {
    const created = await amo.createPipelineStatus(AMO_PIPELINE_SALES, "Счёт запрошен", {
      sort,
      // Палитра amoCRM узкая — #99ccff на create отклоняет, рабочие: #98cbff / #c1c1c1.
      color: "#98cbff",
    });
    await upsertAmoMeta("status", created.id, AMO_PIPELINE_SALES, created.name, created);
  } catch (err) {
    // не критично — заявки останутся в кассе; writeback этапа в amo пропустит
    console.warn(`ensureCompanyPreInvoiceStage: ${(err as Error).message}`);
  }
}

/**
 * Этап «Товар в пути» между «Ждет товар» и «Товар в магазине»
 * (после кнопки «Отправлено» на перемещении).
 */
export async function ensureInTransitStage(): Promise<void> {
  const already = await getStatusId(AMO_PIPELINE_SALES, "Товар в пути");
  if (already) return;

  const pipelines = await amo.getPipelines();
  const sales = pipelines.find((p) => p.id === AMO_PIPELINE_SALES);
  const statuses = sales?._embedded?.statuses ?? [];
  const waiting = statuses.find((s) => {
    const n = s.name.toLowerCase().replace(/ё/g, "е");
    return n === "ждет товар";
  });
  const inStore = statuses.find((s) => s.name.toLowerCase() === "товар в магазине");
  const sort =
    waiting && inStore
      ? Math.round(((waiting.sort ?? 20) + (inStore.sort ?? 30)) / 2)
      : waiting
        ? (waiting.sort ?? 20) + 1
        : 25;
  try {
    const created = await amo.createPipelineStatus(AMO_PIPELINE_SALES, "Товар в пути", {
      sort,
      color: "#ffce5a",
    });
    await upsertAmoMeta("status", created.id, AMO_PIPELINE_SALES, created.name, created);
  } catch (err) {
    console.warn(`ensureInTransitStage: ${(err as Error).message}`);
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

function normalizeStageName(name: string): string {
  return name.toLowerCase().trim().replace(/ё/g, "е").replace(/\s+/g, " ");
}

/**
 * Резолв этапа только по точному имени (+ алиасы STAGE_ALIASES).
 * Раньше был includes()-фолбэк — он мог сматчить не тот этап при похожих названиях.
 */
export async function getStatusId(pipelineId: number, stageName: string): Promise<number | null> {
  const rows = await db
    .select()
    .from(amoMeta)
    .where(and(eq(amoMeta.kind, "status"), eq(amoMeta.parentId, pipelineId)));
  const candidates = [stageName, ...(STAGE_ALIASES[stageName] ?? [])];
  for (const candidate of candidates) {
    const needle = normalizeStageName(candidate);
    const exact = rows.find((r) => normalizeStageName(r.name) === needle);
    if (exact?.amoId) return exact.amoId;
  }
  return AMO_SYSTEM_STATUS_IDS[normalizeStageName(stageName)] ?? null;
}

/**
 * Тип и справочник значений кастом-полей лида из кэша (для select-полей amoCRM
 * значение вне справочника отбивает весь запрос с NotSupportedChoice).
 */
export async function getLeadFieldMeta(): Promise<Map<number, { type: string; enums: string[] }>> {
  const rows = await db.select().from(amoMeta).where(eq(amoMeta.kind, "field"));
  const out = new Map<number, { type: string; enums: string[] }>();
  for (const row of rows) {
    const payload = row.payload as { type?: string; enums?: Array<{ value?: string }> } | null;
    out.set(row.amoId, {
      type: payload?.type ?? "text",
      enums: (payload?.enums ?? []).map((e) => String(e.value ?? "")).filter(Boolean),
    });
  }
  return out;
}

// Нечёткое совпадение — только для мелких отличий в написании (регистр/пробелы/
// дефис и т.п.), НЕ для коротких имён-подстрок вроде «Консультант» внутри
// «Отв-ный консультант» — иначе разные поля схлопнутся в одно и данные перепишут
// друг друга. Порог длины отсекает такие ложные совпадения.
function fuzzyFieldMatch<T extends { name: string }>(rows: T[], name: string): T | undefined {
  const target = name.toLowerCase().trim();
  return rows.find((r) => {
    const candidate = r.name.toLowerCase().trim();
    if (Math.abs(candidate.length - target.length) > 3) return false;
    return candidate.includes(target) || target.includes(candidate);
  });
}

export async function getFieldIdByName(name: string): Promise<number | null> {
  const rows = await db
    .select()
    .from(amoMeta)
    .where(and(eq(amoMeta.kind, "field"), eq(amoMeta.name, name)));
  if (rows[0]) return rows[0].amoId;
  const all = await db.select().from(amoMeta).where(eq(amoMeta.kind, "field"));
  return fuzzyFieldMatch(all, name)?.amoId ?? null;
}

export async function getCompanyFieldIdByName(name: string): Promise<number | null> {
  const rows = await db
    .select()
    .from(amoMeta)
    .where(and(eq(amoMeta.kind, "company_field"), eq(amoMeta.name, name)));
  if (rows[0]) return rows[0].amoId;
  const all = await db.select().from(amoMeta).where(eq(amoMeta.kind, "company_field"));
  return fuzzyFieldMatch(all, name)?.amoId ?? null;
}

/** @deprecated используйте buildLeadCustomFields из services/amoMapping.ts. */
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

// Склад по шоуруму: маппинг из shared (STORE_TO_WAREHOUSE) → имя склада → meta из ms_refs.
// Если по какой-то причине точное имя не найдено — пробуем по подстроке (защита от
// переименования склада в МойСклад без обновления константы).
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

// Синк номенклатуры (без остатков). Тяжёлый (десятки тысяч позиций) — запускается
// раз в день по расписанию, т.к. состав каталога меняется редко.
/**
 * Дерево групп товаров из МойСклад — эталон иерархии категорий (подтверждено 31.07.2026).
 * Переименовали группу в МС — касса подхватит на следующем синке.
 */
export async function syncProductFolders(): Promise<{ folders: number }> {
  let count = 0;
  for await (const batch of ms.iterateProductFolders()) {
    for (const row of batch) {
      const parentPath = row.pathName?.trim() ?? "";
      const path = parentPath ? `${parentPath}/${row.name}` : row.name;
      const parentMsId = row.productFolder?.meta?.href
        ? extractIdFromHref(row.productFolder.meta.href)
        : null;
      const values = {
        name: row.name,
        path,
        parentMsId,
        level: path.split("/").length,
        archived: Boolean(row.archived),
        updatedAt: new Date(),
      };
      await db
        .insert(productFolders)
        .values({ msId: row.id, ...values })
        .onConflictDoUpdate({ target: productFolders.msId, set: values });
      count++;
    }
  }
  return { folders: count };
}

/**
 * У модификаций в МС часто пустой pathName. Проставляем category от карточки
 * товара с тем же именем до « (» — иначе поиск по разделам зала пустой.
 */
export async function backfillVariantCategories(): Promise<{ updated: number }> {
  const result = await db.execute(sql`
    UPDATE products AS v
    SET category = p.category
    FROM products AS p
    WHERE v.ms_type = 'variant'
      AND p.ms_type = 'product'
      AND coalesce(trim(v.category), '') = ''
      AND coalesce(trim(p.category), '') <> ''
      AND v.name LIKE p.name || ' (%'
  `);
  const meta = result as unknown as { rowCount?: number };
  return { updated: meta.rowCount ?? 0 };
}

export async function syncProducts(): Promise<{ products: number }> {
  await syncProductFolders().catch(() => ({ folders: 0 }));
  let productCount = 0;
  for await (const batch of ms.iterateAssortment()) {
    for (const row of batch) {
      if (row.meta.type !== "product" && row.meta.type !== "variant") continue;
      const firstBarcode = row.barcodes?.[0];
      const barcode = firstBarcode ? Object.values(firstBarcode)[0] ?? null : null;
      const price = row.salePrices?.[0]?.value ?? 0; // уже в копейках в МойСклад
      // pathName — полный путь папок; для variant часто пуст (дозаполним backfill).
      const category = row.pathName?.trim() || row.productFolder?.name || null;
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
          category,
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
            // Не затираем уже проставленный backfill пустым pathName у variant.
            ...(category ? { category } : {}),
            price,
            msMetaHref: row.meta.href,
            msType: row.meta.type,
            updatedAt: new Date(),
          },
        });
      productCount++;
    }
  }
  await backfillVariantCategories().catch(() => ({ updated: 0 }));
  return { products: productCount };
}

// Синк остатков (без номенклатуры). Лёгкий (только два физических склада шоурумов) —
// запускается часто (каждые ~10 мин), чтобы остатки в поиске были свежими.
export async function syncStock(): Promise<{ stock: number }> {
  let stockCount = 0;
  // Считаем по всем складам из списка расположений (п.13): шоурумы, центральный,
  // полупарки, ателье, в пути. Выгрузка постраничная — иначе report/stock/bystore
  // отваливается по таймауту на ~19к модификаций.
  const warehouseHrefs: string[] = [];
  const knownStores = await db.select().from(msRefs).where(eq(msRefs.kind, "store"));
  for (const wh of new Set(STOCK_WAREHOUSES)) {
    const exact = await getMsRef("store", wh);
    const needle = wh.toLowerCase().replace("на ", "");
    const fuzzy = knownStores.find((row) => row.name.toLowerCase().includes(needle))?.payload as
      | ms.MsRow
      | undefined;
    const href = exact?.meta.href ?? fuzzy?.meta.href;
    if (href && !warehouseHrefs.includes(href)) warehouseHrefs.push(href);
  }
  const stockRows = warehouseHrefs.length ? await ms.getStockByStore(warehouseHrefs) : [];
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
          quantity: String((byStore.stock ?? 0) - (byStore.reserve ?? 0)),
          updatedAt: new Date(),
        })
        .onConflictDoUpdate({
          target: [stock.productMsId, stock.warehouseMsId],
          set: {
            quantity: String((byStore.stock ?? 0) - (byStore.reserve ?? 0)),
            warehouseName: byStore.name,
            updatedAt: new Date(),
          },
        });
      stockCount++;
    }
  }
  return { stock: stockCount };
}

// Обёртка: номенклатура + остатки. Для первичного bootstrap и ручного /admin/sync.
export async function syncCatalog(): Promise<{ products: number; stock: number }> {
  const { products: productCount } = await syncProducts();
  const { stock: stockCount } = await syncStock();
  return { products: productCount, stock: stockCount };
}

export function extractIdFromHref(href: string | undefined): string | null {
  if (!href) return null;
  const m = href.match(/\/([0-9a-fA-F-]{36})(?:\/|$|\?)/);
  return m?.[1] ?? null;
}

// Полный bootstrap при старте: справочники amo + meta МС + каталог.
export async function runBootstrap(
  log: (msg: string) => void,
  options: { throwOnError?: boolean } = {}
): Promise<void> {
  try {
    log("bootstrap: синк справочников amoCRM…");
    await syncAmoMeta();
    log("bootstrap: проверка кастом-полей amoCRM (лид/компания)…");
    await ensureCustomFieldsExist();
    log("bootstrap: этап «Счёт запрошен» в воронке Продажи…");
    await ensureCompanyPreInvoiceStage();
    log("bootstrap: этап «Товар в пути» в воронке Продажи…");
    await ensureInTransitStage();
    await syncAmoMeta(); // повторный синк, чтобы подтянуть id только что созданных полей/этапов
    log("bootstrap: резолв meta МойСклад…");
    await syncMsRefs();
    log("bootstrap: синк каталога МойСклад…");
    const res = await syncCatalog();
    log(`bootstrap: каталог ${res.products} поз., остатки ${res.stock} строк.`);
  } catch (err) {
    log(`bootstrap: ошибка — ${(err as Error).message}`);
    if (options.throwOnError) throw err;
  }
}
