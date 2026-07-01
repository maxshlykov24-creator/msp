import { HttpClient } from "../lib/http.js";
import { getEnv } from "../env.js";

// Клиент МойСклад JSON API 1.2 (Bearer). Касса — единственный, кто пишет в МойСклад:
// заказ покупателя → отгрузка → входящий платёж, плюс фотофиксации (файлы к документу).

const env = getEnv();

const http = new HttpClient({
  baseUrl: env.MOYSKLAD_API_BASE,
  headers: {
    Authorization: `Bearer ${env.MOYSKLAD_API_TOKEN}`,
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip",
  },
  rps: 4,
});

export interface MsMeta {
  href: string;
  type: string;
  mediaType: string;
}
export interface MsRow {
  meta: MsMeta;
  id: string;
  name: string;
  [k: string]: unknown;
}
interface MsList<T> {
  meta: { size: number };
  rows: T[];
}

const metaRef = (m: MsMeta) => ({ meta: { href: m.href, type: m.type, mediaType: m.mediaType } });

// ── Справочники / резолв meta ───────────────────────────────────────

export async function getOrganizations(): Promise<MsRow[]> {
  const res = await http.get<MsList<MsRow>>("/entity/organization");
  return res.rows;
}

export async function getStores(): Promise<MsRow[]> {
  const res = await http.get<MsList<MsRow>>("/entity/store");
  return res.rows;
}

export async function getOrganizationAccounts(orgId: string): Promise<MsRow[]> {
  const res = await http.get<MsList<MsRow>>(`/entity/organization/${orgId}/accounts`);
  return res.rows;
}

export async function findCounterpartyByName(name: string): Promise<MsRow | null> {
  const res = await http.get<MsList<MsRow>>(
    `/entity/counterparty?filter=name=${encodeURIComponent(name)}`
  );
  return res.rows[0] ?? null;
}

export async function createCounterparty(name: string, phone?: string): Promise<MsRow> {
  return http.post<MsRow>("/entity/counterparty", { name, phone });
}

export async function ensureRetailCounterparty(name: string): Promise<MsRow> {
  const existing = await findCounterpartyByName(name);
  if (existing) return existing;
  return createCounterparty(name);
}

// ── Каталог + остатки ───────────────────────────────────────────────

export interface MsAssortmentRow extends MsRow {
  article?: string;
  code?: string;
  barcodes?: Array<Record<string, string>>;
  salePrices?: Array<{ value: number }>;
  productFolder?: { name?: string };
  pathName?: string;
}

export async function* iterateAssortment(): AsyncGenerator<MsAssortmentRow[]> {
  const limit = 1000;
  let offset = 0;
  while (true) {
    const res = await http.get<MsList<MsAssortmentRow>>(
      `/entity/assortment?limit=${limit}&offset=${offset}`
    );
    if (res.rows.length === 0) break;
    yield res.rows;
    offset += res.rows.length;
    if (res.rows.length < limit) break;
  }
}

// Остатки по складам (отчёт). Возвращает массив по складам для каждого товара.
export interface MsStockRow {
  meta?: MsMeta;
  stockByStore?: Array<{ name: string; stock: number; meta: MsMeta }>;
  assortmentId?: string;
}

// ВАЖНО: без фильтра по складу отчёт агрегирует остатки по ВСЕМ складам сразу —
// для каталога в десятки тысяч позиций МойСклад не успевает посчитать это за
// время таймаута шлюза (проверено на проде 2026-07-01: ~70с и обрыв соединения
// независимо от limit/offset). Фильтр по конкретному складу сокращает расчёт
// до секунд, поэтому запрашиваем остатки склад-за-складом.
export async function getStockByStore(storeHrefs: string[]): Promise<MsStockRow[]> {
  const limit = 1000;
  const out: MsStockRow[] = [];
  for (const storeHref of storeHrefs) {
    let offset = 0;
    while (true) {
      const res = await http.get<MsList<MsStockRow>>(
        `/report/stock/bystore?limit=${limit}&offset=${offset}&filter=${encodeURIComponent(`store=${storeHref}`)}`
      );
      out.push(...res.rows);
      if (res.rows.length < limit) break;
      offset += res.rows.length;
    }
  }
  return out;
}

// Актуальный остаток конкретного товара (on-demand, анти-гонка).
export async function getStockForProduct(productMsId: string): Promise<MsStockRow[]> {
  const res = await http.get<MsList<MsStockRow>>(
    `/report/stock/bystore?filter=product=${env.MOYSKLAD_API_BASE}/entity/product/${productMsId}`
  );
  return res.rows;
}

// ── Документы продажи ───────────────────────────────────────────────

export interface SalePosition {
  assortmentHref: string;
  assortmentType: string;
  quantity: number;
  price: number; // копейки
}

export interface CreateOrderInput {
  organization: MsMeta;
  agent: MsMeta;
  store: MsMeta;
  positions: SalePosition[];
  name?: string;
  description?: string;
}

function positionsBody(positions: SalePosition[]) {
  return positions.map((p) => ({
    quantity: p.quantity,
    price: p.price,
    assortment: { meta: { href: p.assortmentHref, type: p.assortmentType, mediaType: "application/json" } },
  }));
}

export async function createCustomerOrder(input: CreateOrderInput): Promise<MsRow> {
  return http.post<MsRow>("/entity/customerorder", {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    store: metaRef(input.store),
    name: input.name,
    description: input.description,
    positions: positionsBody(input.positions),
  });
}

export async function createDemand(input: {
  organization: MsMeta;
  agent: MsMeta;
  store: MsMeta;
  orderMeta: MsMeta;
  positions: SalePosition[];
  description?: string;
}): Promise<MsRow> {
  return http.post<MsRow>("/entity/demand", {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    store: metaRef(input.store),
    customerOrder: metaRef(input.orderMeta),
    description: input.description,
    positions: positionsBody(input.positions),
  });
}

// Безналичный входящий платёж
export async function createPaymentIn(input: {
  organization: MsMeta;
  agent: MsMeta;
  sum: number; // копейки
  organizationAccount?: MsMeta;
  orderMeta?: MsMeta;
  description?: string;
}): Promise<MsRow> {
  const body: Record<string, unknown> = {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    sum: input.sum,
    description: input.description,
  };
  if (input.organizationAccount) body.organizationAccount = metaRef(input.organizationAccount);
  if (input.orderMeta) body.operations = [metaRef(input.orderMeta)];
  return http.post<MsRow>("/entity/paymentin", body);
}

// Наличный приходный ордер
export async function createCashIn(input: {
  organization: MsMeta;
  agent: MsMeta;
  sum: number; // копейки
  orderMeta?: MsMeta;
  description?: string;
}): Promise<MsRow> {
  const body: Record<string, unknown> = {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    sum: input.sum,
    description: input.description,
  };
  if (input.orderMeta) body.operations = [metaRef(input.orderMeta)];
  return http.post<MsRow>("/entity/cashin", body);
}

// ── Файлы (фотофиксации) ────────────────────────────────────────────
// POST /entity/{type}/{id}/files — массив { filename, content(base64) }.

export async function uploadFile(
  entityType: string,
  entityId: string,
  filename: string,
  contentBase64: string
): Promise<unknown> {
  return http.post(`/entity/${entityType}/${entityId}/files`, [
    { filename, content: contentBase64 },
  ]);
}

export async function listFiles(entityType: string, entityId: string): Promise<MsRow[]> {
  const res = await http.get<MsList<MsRow>>(`/entity/${entityType}/${entityId}/files`);
  return res.rows;
}
