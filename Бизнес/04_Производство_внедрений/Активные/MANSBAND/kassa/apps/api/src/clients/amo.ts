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
  if (input.contactId) body._embedded = { contacts: [{ id: input.contactId }] };

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

export async function addLeadNote(leadId: number, text: string): Promise<void> {
  await http.post(`/leads/${leadId}/notes`, [
    { note_type: "common", params: { text } },
  ]);
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
