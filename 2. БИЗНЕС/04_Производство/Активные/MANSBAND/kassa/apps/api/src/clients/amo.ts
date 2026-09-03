import { HttpClient } from "../lib/http.js";
import { getEnv } from "../env.js";
import { formatPhoneE164, phoneTail } from "../lib/phone.js";

// Клиент amoCRM API v4. Долгосрочный токен (Bearer). rps ~5, ретраи на 429/5xx.

const env = getEnv();

const http = new HttpClient({
  baseUrl: env.AMOCRM_API_BASE,
  headers: {
    Authorization: `Bearer ${env.AMOCRM_LONG_LIVED_TOKEN}`,
    "Content-Type": "application/json",
  },
  rps: 5,
});

export interface AmoPipeline {
  id: number;
  name: string;
  _embedded?: { statuses?: Array<{ id: number; name: string; pipeline_id: number; sort: number }> };
}

export interface AmoField {
  id: number;
  name: string;
  type: string;
  enums?: Array<{ id: number; value: string }> | null;
}

export interface AmoLead {
  id: number;
  name: string;
  price: number;
  status_id: number;
  pipeline_id: number;
  custom_fields_values?: Array<{ field_id: number; values: Array<{ value: unknown }> }> | null;
  _embedded?: {
    contacts?: Array<{ id: number }>;
    tags?: Array<{ id: number; name: string }>;
  };
}

interface AmoListResponse<T> {
  _page?: number;
  _links?: { next?: { href: string } };
  _embedded?: Record<string, T[]>;
}

// ── Справочники ─────────────────────────────────────────────────────

export async function getPipelines(): Promise<AmoPipeline[]> {
  const res = await http.get<AmoListResponse<AmoPipeline>>("/leads/pipelines");
  return res._embedded?.pipelines ?? [];
}

export async function getLeadCustomFields(): Promise<AmoField[]> {
  return paginateEmbedded<AmoField>("/leads/custom_fields", "custom_fields");
}

export async function getContactCustomFields(): Promise<AmoField[]> {
  return paginateEmbedded<AmoField>("/contacts/custom_fields", "custom_fields");
}

export async function getCompanyCustomFields(): Promise<AmoField[]> {
  return paginateEmbedded<AmoField>("/companies/custom_fields", "custom_fields");
}

// Создание кастом-поля (идемпотентно вызывающей стороной — сначала искать по имени в синке).
export async function createLeadCustomField(name: string, type: "date" | "text"): Promise<AmoField> {
  const res = await http.post<AmoListResponse<AmoField>>("/leads/custom_fields", [{ name, type }]);
  const created = res._embedded?.custom_fields?.[0];
  if (!created) throw new Error(`amoCRM: не удалось создать поле лида «${name}»`);
  return created;
}

export async function createCompanyCustomField(name: string, type: "date" | "text"): Promise<AmoField> {
  const res = await http.post<AmoListResponse<AmoField>>("/companies/custom_fields", [{ name, type }]);
  const created = res._embedded?.custom_fields?.[0];
  if (!created) throw new Error(`amoCRM: не удалось создать поле компании «${name}»`);
  return created;
}

// ── Сделки (чтение) ─────────────────────────────────────────────────

export interface ListLeadsParams {
  pipelineId?: number;
  statusId?: number;
  query?: string;
  page?: number;
  limit?: number;
}

export async function listLeads(params: ListLeadsParams): Promise<AmoLead[]> {
  const sp = new URLSearchParams();
  sp.set("with", "contacts");
  sp.set("limit", String(params.limit ?? 50));
  sp.set("page", String(params.page ?? 1));
  if (params.pipelineId) sp.set("filter[pipeline_id]", String(params.pipelineId));
  if (params.statusId) sp.set("filter[statuses][0][status_id]", String(params.statusId));
  if (params.query) sp.set("query", params.query);
  const res = await http.get<AmoListResponse<AmoLead>>(`/leads?${sp.toString()}`);
  return res._embedded?.leads ?? [];
}

export async function getLead(id: number): Promise<AmoLead | null> {
  try {
    return await http.get<AmoLead>(`/leads/${id}?with=contacts`);
  } catch {
    return null;
  }
}

// ── Контакты ────────────────────────────────────────────────────────

export interface AmoContact {
  id: number;
  name: string;
  custom_fields_values?: Array<{ field_id?: number; field_code?: string; values: Array<{ value: unknown }> }> | null;
}

export async function findContactByPhone(phone: string): Promise<AmoContact | null> {
  const tail = phoneTail(phone);
  if (!tail) return null;
  const res = await http.get<AmoListResponse<AmoContact>>(
    `/contacts?query=${encodeURIComponent(tail)}&limit=10`
  );
  const contacts = res._embedded?.contacts ?? [];
  // Доп. фильтр по совпадению последних 10 цифр в любом телефонном поле.
  for (const c of contacts) {
    const phones = (c.custom_fields_values ?? [])
      .filter((f) => f.field_code === "PHONE")
      .flatMap((f) => f.values.map((v) => String(v.value)));
    if (phones.some((p) => phoneTail(p) === tail)) return c;
  }
  return contacts[0] ?? null;
}

// ── Компании (юрлица) ──────────────────────────────────────────────

export interface AmoCompany {
  id: number;
  name: string;
  custom_fields_values?: Array<{ field_id?: number; field_code?: string; values: Array<{ value: unknown }> }> | null;
}

export async function findCompanyByName(name: string): Promise<AmoCompany | null> {
  const res = await http.get<AmoListResponse<AmoCompany>>(
    `/companies?query=${encodeURIComponent(name)}&limit=10`
  );
  const companies = res._embedded?.companies ?? [];
  return companies.find((c) => c.name.toLowerCase() === name.toLowerCase()) ?? companies[0] ?? null;
}

export async function createCompany(
  name: string,
  phone?: string,
  managerFieldId?: number | null,
  managerName?: string
): Promise<AmoCompany> {
  const customFields: Array<Record<string, unknown>> = [];
  if (phone) {
    customFields.push({ field_code: "PHONE", values: [{ value: formatPhoneE164(phone), enum_code: "WORK" }] });
  }
  if (managerFieldId && managerName) {
    customFields.push({ field_id: managerFieldId, values: [{ value: managerName }] });
  }
  const res = await http.post<AmoListResponse<AmoCompany>>("/companies", [
    { name, custom_fields_values: customFields.length ? customFields : undefined },
  ]);
  const created = res._embedded?.companies?.[0];
  if (!created) throw new Error("amoCRM: не удалось создать компанию");
  return created;
}

export async function updateCompany(
  id: number,
  patch: { customFields?: Array<{ field_id: number; values: Array<{ value: unknown }> }> }
): Promise<void> {
  const body: Record<string, unknown> = {};
  if (patch.customFields?.length) body.custom_fields_values = patch.customFields;
  await http.patch(`/companies/${id}`, body);
}

// Находит компанию по имени или создаёт новую с телефоном/руководителем, идемпотентно.
export async function ensureCompany(
  name: string,
  phone?: string,
  managerFieldId?: number | null,
  managerName?: string
): Promise<AmoCompany> {
  const existing = await findCompanyByName(name).catch(() => null);
  if (existing) {
    const customFields: Array<{ field_id: number; values: Array<{ value: unknown }> }> = [];
    if (managerFieldId && managerName) customFields.push({ field_id: managerFieldId, values: [{ value: managerName }] });
    if (customFields.length) await updateCompany(existing.id, { customFields }).catch(() => {});
    return existing;
  }
  return createCompany(name, phone, managerFieldId, managerName);
}

export async function createContact(name: string, phone?: string, email?: string): Promise<AmoContact> {
  const customFields: Array<Record<string, unknown>> = [];
  if (phone) {
    customFields.push({
      field_code: "PHONE",
      values: [{ value: formatPhoneE164(phone), enum_code: "WORK" }],
    });
  }
  if (email) {
    customFields.push({ field_code: "EMAIL", values: [{ value: email, enum_code: "WORK" }] });
  }
  const res = await http.post<AmoListResponse<AmoContact>>("/contacts", [
    { name, custom_fields_values: customFields.length ? customFields : undefined },
  ]);
  const created = res._embedded?.contacts?.[0];
  if (!created) throw new Error("amoCRM: не удалось создать контакт");
  return created;
}

// ── Сделки (запись) ─────────────────────────────────────────────────

export interface CreateLeadInput {
  name: string;
  price: number;
  pipelineId: number;
  statusId?: number;
  contactId?: number;
  companyId?: number;
  customFields?: Array<{ field_id: number; values: Array<{ value: unknown }> }>;
}

export async function createLead(input: CreateLeadInput): Promise<AmoLead> {
  const body: Record<string, unknown> = {
    name: input.name,
    price: input.price,
    pipeline_id: input.pipelineId,
  };
  if (input.statusId) body.status_id = input.statusId;
  if (input.customFields?.length) body.custom_fields_values = input.customFields;
  if (input.contactId || input.companyId) {
    const embedded: Record<string, Array<{ id: number }>> = {};
    if (input.contactId) embedded.contacts = [{ id: input.contactId }];
    if (input.companyId) embedded.companies = [{ id: input.companyId }];
    body._embedded = embedded;
  }

  const res = await http.post<AmoListResponse<AmoLead>>("/leads", [body]);
  const created = res._embedded?.leads?.[0];
  if (!created) throw new Error("amoCRM: не удалось создать сделку");
  return created;
}

export async function updateLead(
  id: number,
  patch: {
    statusId?: number;
    pipelineId?: number;
    price?: number;
    customFields?: Array<{ field_id: number; values: Array<{ value: unknown }> }>;
  }
): Promise<void> {
  const body: Record<string, unknown> = {};
  if (patch.statusId) body.status_id = patch.statusId;
  if (patch.pipelineId) body.pipeline_id = patch.pipelineId;
  if (typeof patch.price === "number") body.price = patch.price;
  if (patch.customFields?.length) body.custom_fields_values = patch.customFields;
  await http.patch(`/leads/${id}`, body);
}

// Привязка компании к уже существующей сделке (для случаев, когда компания
// определяется/создаётся после первого сохранения лида).
export async function linkCompanyToLead(leadId: number, companyId: number): Promise<void> {
  await http.post(`/leads/${leadId}/link`, [
    { to_entity_id: companyId, to_entity_type: "companies" },
  ]);
}

export async function addLeadNote(leadId: number, text: string): Promise<void> {
  await http.post(`/leads/${leadId}/notes`, [
    { note_type: "common", params: { text } },
  ]);
}

// ── Файлы (фотофиксации к сделке — аренда/брак без документа МойСклад) ──
// amoCRM требует загрузку файла на отдельный хост «сервиса файлов» (drive) через
// сессии загрузки частями, затем привязку по uuid к сделке. Подробности:
// https://www.amocrm.ru/developers/content/files/files-api

interface DriveSession {
  session_id: number;
  upload_url: string;
  max_part_size: number;
}
interface DriveUploadResult {
  next_url?: string;
  uuid?: string;
}

let driveUrlCache: string | null = null;

async function getDriveUrl(): Promise<string> {
  if (driveUrlCache) return driveUrlCache;
  const res = await http.get<{ drive_url?: string }>("/account?with=drive_url");
  driveUrlCache = res.drive_url ?? "https://drive-b.amocrm.ru";
  return driveUrlCache;
}

async function uploadPart(url: string, chunk: Buffer): Promise<DriveUploadResult> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.AMOCRM_LONG_LIVED_TOKEN}`,
      "Content-Type": "application/octet-stream",
    },
    body: chunk,
  });
  const text = await res.text();
  const data = text ? (JSON.parse(text) as DriveUploadResult) : {};
  if (!res.ok) throw new Error(`amoCRM drive upload → ${res.status}: ${text}`);
  return data;
}

/** Загружает файл в файловый сервис amoCRM и возвращает его uuid. */
export async function uploadFileToDrive(
  buffer: Buffer,
  filename: string,
  contentType: string
): Promise<string> {
  const driveUrl = await getDriveUrl();
  const session = await http.post<DriveSession>(`${driveUrl}/v1.0/sessions`, {
    file_name: filename,
    file_size: buffer.length,
    content_type: contentType,
  });
  const maxPart = session.max_part_size > 0 ? session.max_part_size : 524288;
  let url = session.upload_url;
  let offset = 0;
  while (offset < buffer.length) {
    const chunk = buffer.subarray(offset, Math.min(offset + maxPart, buffer.length));
    const result = await uploadPart(url, chunk);
    offset += chunk.length;
    if (result.uuid) return result.uuid;
    if (!result.next_url) break;
    url = result.next_url;
  }
  throw new Error(`amoCRM: не удалось загрузить файл «${filename}» — сервис не вернул uuid`);
}

/** Привязывает уже загруженные файлы (uuid) к сделке. */
export async function attachFilesToLead(leadId: number, fileUuids: string[]): Promise<void> {
  if (!fileUuids.length) return;
  await http.put(`/leads/${leadId}/files`, fileUuids.map((file_uuid) => ({ file_uuid })));
}

/** Полный цикл: base64 → drive uuid → привязка к сделке. */
export async function attachPhotoToLead(
  leadId: number,
  filename: string,
  contentBase64: string
): Promise<void> {
  const buffer = Buffer.from(contentBase64, "base64");
  const ext = filename.split(".").pop()?.toLowerCase();
  const contentType = ext === "png" ? "image/png" : ext === "webp" ? "image/webp" : "image/jpeg";
  const uuid = await uploadFileToDrive(buffer, filename, contentType);
  await attachFilesToLead(leadId, [uuid]);
}

// ── Утилиты ─────────────────────────────────────────────────────────

async function paginateEmbedded<T>(path: string, key: string): Promise<T[]> {
  const out: T[] = [];
  let page = 1;
  while (true) {
    const sep = path.includes("?") ? "&" : "?";
    const res = await http.get<AmoListResponse<T>>(`${path}${sep}limit=250&page=${page}`);
    const items = res._embedded?.[key] ?? [];
    out.push(...items);
    if (!res._links?.next || items.length === 0) break;
    page++;
    if (page > 50) break; // защита от бесконечного цикла
  }
  return out;
}
