import { HttpClient, HttpError } from "../lib/http.js";
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
  serviceName: "МойСклад",
});

export function isMoySkladAuthError(error: unknown): boolean {
  return error instanceof HttpError && error.status === 401;
}

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
  /**
   * Характеристики модификации: Вариация, Рзамер (опечатка в МС), Ростовка,
   * Цвет, Узорность, Крой. Приходят прямо в assortment, отдельный запрос
   * к /entity/variant не нужен.
   */
  characteristics?: Array<{ name?: string; value?: string }>;
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

/**
 * Оприходования: по ним считается возраст партии на складе. Дата документа —
 * это дата, когда товар физически появился на складе.
 */
export interface MsEnterRow extends MsRow {
  moment: string;
}

export async function* iterateEnters(): AsyncGenerator<MsEnterRow[]> {
  const limit = 100;
  let offset = 0;
  while (true) {
    const res = await http.get<MsList<MsEnterRow>>(
      `/entity/enter?limit=${limit}&offset=${offset}&order=moment,asc`
    );
    if (res.rows.length === 0) break;
    yield res.rows;
    offset += res.rows.length;
    if (res.rows.length < limit) break;
  }
}

export interface MsEnterPosition {
  id: string;
  quantity: number;
  assortment: { meta: MsMeta };
}

export async function* iterateEnterPositions(enterId: string): AsyncGenerator<MsEnterPosition[]> {
  const limit = 1000;
  let offset = 0;
  while (true) {
    const res = await http.get<MsList<MsEnterPosition>>(
      `/entity/enter/${enterId}/positions?limit=${limit}&offset=${offset}`
    );
    if (res.rows.length === 0) break;
    yield res.rows;
    offset += res.rows.length;
    if (res.rows.length < limit) break;
  }
}

/** Группа товаров МойСклад. `pathName` — путь родителя, без собственного имени. */
export interface MsProductFolderRow extends MsRow {
  pathName?: string;
  productFolder?: { meta?: MsMeta; name?: string };
  archived?: boolean;
}

export async function* iterateProductFolders(): AsyncGenerator<MsProductFolderRow[]> {
  const limit = 1000;
  let offset = 0;
  while (true) {
    const res = await http.get<MsList<MsProductFolderRow>>(
      `/entity/productfolder?limit=${limit}&offset=${offset}`
    );
    if (res.rows.length === 0) break;
    yield res.rows;
    offset += res.rows.length;
    if (res.rows.length < limit) break;
  }
}

// Остатки по складам (отчёт). Возвращает массив по складам для каждого товара.
export interface MsStockByStore {
  name: string;
  stock: number; // остаток
  reserve?: number; // резерв
  inTransit?: number;
  meta: MsMeta;
}

export interface MsStockRow {
  meta?: MsMeta;
  stockByStore?: MsStockByStore[];
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

// Актуальный остаток конкретного товара (on-demand).
// filter-значение обязательно URL-encode — иначе часть клиентов/прокси ломает запрос.
export async function getStockForProduct(
  productMsId: string,
  assortmentType: "product" | "variant" | "bundle" | "service" = "product"
): Promise<MsStockRow[]> {
  const type = assortmentType === "variant" ? "variant" : "product";
  const href = `${env.MOYSKLAD_API_BASE}/entity/${type}/${productMsId}`;
  const res = await http.get<MsList<MsStockRow>>(
    `/report/stock/bystore?filter=${encodeURIComponent(`product=${href}`)}`
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
  orderMeta?: MsMeta;
  positions: SalePosition[];
  description?: string;
}): Promise<MsRow> {
  const body: Record<string, unknown> = {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    store: metaRef(input.store),
    description: input.description,
    positions: positionsBody(input.positions),
  };
  if (input.orderMeta) body.customerOrder = metaRef(input.orderMeta);
  return http.post<MsRow>("/entity/demand", body);
}

/** Возврат покупателя (salesreturn) — товар возвращается на склад. */
export async function createSalesReturn(input: {
  organization: MsMeta;
  agent: MsMeta;
  store: MsMeta;
  positions: SalePosition[];
  description?: string;
  demandMeta?: MsMeta;
}): Promise<MsRow> {
  const body: Record<string, unknown> = {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    store: metaRef(input.store),
    description: input.description,
    positions: positionsBody(input.positions),
  };
  if (input.demandMeta) body.demand = metaRef(input.demandMeta);
  return http.post<MsRow>("/entity/salesreturn", body);
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

// Наличный расходный ордер (сдача клиенту / чаевые «забрал наличкой»)
export async function createCashOut(input: {
  organization: MsMeta;
  agent: MsMeta;
  sum: number; // копейки
  description?: string;
}): Promise<MsRow> {
  return http.post<MsRow>("/entity/cashout", {
    organization: metaRef(input.organization),
    agent: metaRef(input.agent),
    sum: input.sum,
    description: input.description,
  });
}

/** Перемещение между складами МойСклад. Создаётся только явным действием пользователя. */
export async function createMove(input: {
  organization: MsMeta;
  sourceStore: MsMeta;
  targetStore: MsMeta;
  positions: SalePosition[];
  description?: string;
}): Promise<MsRow> {
  return http.post<MsRow>("/entity/move", {
    organization: metaRef(input.organization),
    sourceStore: metaRef(input.sourceStore),
    targetStore: metaRef(input.targetStore),
    positions: positionsBody(input.positions),
    description: input.description,
  });
}

// ── Печать этикеток (бирок) ─────────────────────────────────────────
// Шаблоны этикеток/ценников: metadata товара (embedded + custom).
// Экспорт: POST /entity/{product|variant}/{id}/export → 303 на PDF
// либо PDF в теле ответа. Обрабатываем оба варианта.

export interface MsTemplateRow extends MsRow {
  content?: string;
}

export async function listLabelTemplates(): Promise<Array<MsTemplateRow & { templateType: string }>> {
  const [embedded, custom] = await Promise.all([
    http
      .get<MsList<MsTemplateRow>>("/entity/assortment/metadata/embeddedtemplate")
      .catch(() => ({ rows: [] as MsTemplateRow[] })),
    http
      .get<MsList<MsTemplateRow>>("/entity/assortment/metadata/customtemplate")
      .catch(() => ({ rows: [] as MsTemplateRow[] })),
  ]);
  const rows = [
    ...embedded.rows.map((r) => ({ ...r, templateType: "embeddedtemplate" })),
    ...custom.rows.map((r) => ({ ...r, templateType: "customtemplate" })),
  ];
  if (rows.length > 0) return rows;
  // Запасной путь: у некоторых аккаунтов шаблоны видны в metadata товара.
  const [pe, pc] = await Promise.all([
    http
      .get<MsList<MsTemplateRow>>("/entity/product/metadata/embeddedtemplate")
      .catch(() => ({ rows: [] as MsTemplateRow[] })),
    http
      .get<MsList<MsTemplateRow>>("/entity/product/metadata/customtemplate")
      .catch(() => ({ rows: [] as MsTemplateRow[] })),
  ]);
  return [
    ...pe.rows.map((r) => ({ ...r, templateType: "embeddedtemplate" })),
    ...pc.rows.map((r) => ({ ...r, templateType: "customtemplate" })),
  ];
}

export async function getSalePriceTypeMeta(): Promise<MsMeta | null> {
  const res = await http
    .get<Array<{ meta: MsMeta; name: string }>>("/context/companysettings/pricetype")
    .catch(() => [] as Array<{ meta: MsMeta; name: string }>);
  if (!Array.isArray(res) || res.length === 0) return null;
  const sale = res.find((p) => /продаж/i.test(p.name)) ?? res[0];
  return sale?.meta ?? null;
}

/**
 * Печать этикеток для товара/вариации. Возвращает PDF.
 * Прямой fetch (не HttpClient): нужен redirect: manual, чтобы поймать
 * Location на print-prod.moysklad.ru и не разбирать бинарный ответ как текст.
 */
export async function printLabels(input: {
  entityType: "product" | "variant";
  id: string;
  template: MsMeta;
  organization: MsMeta;
  priceType: MsMeta | null;
  count: number;
}): Promise<Buffer> {
  const url = `${env.MOYSKLAD_API_BASE}/entity/${input.entityType}/${input.id}/export`;
  const body: Record<string, unknown> = {
    organization: metaRef(input.organization),
    count: Math.max(1, Math.min(input.count, 100)),
    template: metaRef(input.template),
  };
  if (input.priceType) body.salePrice = { priceType: metaRef(input.priceType) };

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.MOYSKLAD_API_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    redirect: "manual",
  });

  if (res.status === 303 || res.status === 302) {
    const location = res.headers.get("location");
    if (!location) throw new HttpError(res.status, "МойСклад: редирект печати без Location", null);
    const file = await fetch(location);
    if (!file.ok) throw new HttpError(file.status, "МойСклад: не удалось скачать файл этикетки", null);
    return Buffer.from(await file.arrayBuffer());
  }
  if (res.ok) {
    return Buffer.from(await res.arrayBuffer());
  }
  const text = await res.text().catch(() => "");
  throw new HttpError(res.status, `МойСклад: печать этикетки → ${res.status} ${text.slice(0, 300)}`, text);
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
