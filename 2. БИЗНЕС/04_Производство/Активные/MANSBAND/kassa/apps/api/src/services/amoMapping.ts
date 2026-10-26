import {
  AMO_COMPANY_FIELDS,
  AMO_LEAD_FIELDS,
  amoOtlozhka,
  dealDeepLink,
  type AmoLeadFieldKey,
} from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import { eq } from "drizzle-orm";
import * as amo from "../clients/amo.js";
import { db } from "../db/index.js";
import { deals as dealsTable } from "../db/schema.js";
import { getEnv } from "../env.js";
import { getCompanyFieldIdByName, getFieldIdByName, getFieldIdsByName, getLeadFieldMeta, syncAmoMeta } from "./bootstrap.js";

// Сведение полей форм кассы → кастом-поля сделки amoCRM (аудит зафиксирован
// в плане 2026-07-01). Здесь только чтение резолва id по имени — сами поля
// создаются/синкаются в bootstrap.ts (ensureCustomFieldsExist/syncAmoMeta).

export type LeadCustomFieldValue = { field_id: number; values: Array<{ value: unknown }> };

let cachedIds: Record<AmoLeadFieldKey, number | null> | null = null;
let cachedAt = 0;
const CACHE_TTL_MS = 5 * 60_000;

async function getLeadFieldIdMap(): Promise<Record<AmoLeadFieldKey, number | null>> {
  if (cachedIds && Date.now() - cachedAt < CACHE_TTL_MS) return cachedIds;
  const out = {} as Record<AmoLeadFieldKey, number | null>;
  for (const key of Object.keys(AMO_LEAD_FIELDS) as AmoLeadFieldKey[]) {
    out[key] = await getFieldIdByName(AMO_LEAD_FIELDS[key]);
  }
  cachedIds = out;
  cachedAt = Date.now();
  return out;
}

/** Сбросить кэш id полей (например, сразу после bootstrap создал новые поля). */
export function invalidateLeadFieldCache(): void {
  cachedIds = null;
}

/**
 * Проставить в сделке amoCRM ссылку на заявку в кассе (созвон 04.09). Нужно для
 * сделок, которые завёл колл-менеджер: касса в них ничего не пишет, а перейти
 * в кассу и поставить задачу колл-менеджеру надо в один клик.
 */
async function kassaLinkFieldIds(): Promise<number[]> {
  let ids = await getFieldIdsByName(AMO_LEAD_FIELDS.kassaLink);
  // Поле могли создать в amo после последнего синка справочника.
  if (ids.length === 0) {
    await syncAmoMeta();
    invalidateLeadFieldCache();
    ids = await getFieldIdsByName(AMO_LEAD_FIELDS.kassaLink);
  }
  return ids;
}

export async function writeKassaLink(leadId: number, dealNumber: number): Promise<void> {
  const fieldIds = await kassaLinkFieldIds();
  if (fieldIds.length === 0) {
    throw new Error("В amoCRM нет поля «Ссылка на кассу»");
  }
  const link = dealDeepLink(getEnv().PUBLIC_BASE_URL, dealNumber);
  // Повторный вебхук update после нашей же записи не должен писать снова:
  // иначе amo шлёт leads[update] и цикл не кончается.
  // Пишем только в живые поля с этим именем. Текстовый дубль удалён.
  const current = await amo.getLead(leadId);
  const missing = fieldIds.filter((fieldId) => {
    const existing = current?.custom_fields_values
      ?.find((f) => f.field_id === fieldId)
      ?.values?.[0]?.value;
    return !(typeof existing === "string" && existing.trim() === link);
  });
  if (missing.length === 0) return;
  await amo.updateLead(leadId, {
    customFields: missing.map((fieldId) => ({ field_id: fieldId, values: [{ value: link }] })),
  });
}

/** Номер в ссылке: локальная заявка, если она уже есть, иначе id сделки amo. */
export async function ensureKassaLink(amoLeadId: number): Promise<void> {
  if (!Number.isInteger(amoLeadId) || amoLeadId <= 0) return;
  const [local] = await db
    .select({ number: dealsTable.number })
    .from(dealsTable)
    .where(eq(dealsTable.amoLeadId, amoLeadId))
    .limit(1);
  await writeKassaLink(amoLeadId, local?.number ?? amoLeadId);
}

const LINK_BACKFILL_BATCH = 40;
let linkBackfillRunning = false;

function linkFieldText(lead: amo.AmoLead, fieldId: number): string {
  const value = lead.custom_fields_values?.find((field) => field.field_id === fieldId)?.values?.[0]?.value;
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Дописать «Ссылку на кассу» открытым сделкам, у которых поле пустое.
 * За один проход — пачка, остаток подхватит следующий снимок открытых.
 * Уже верную ссылку не переписываем.
 */
export function scheduleKassaLinkBackfill(leads: amo.AmoLead[]): void {
  if (linkBackfillRunning || leads.length === 0) return;
  linkBackfillRunning = true;
  void fillMissingKassaLinks(leads)
    .catch((err: Error) => console.warn(`[amo] ссылки на кассу: ${err.message}`))
    .finally(() => {
      linkBackfillRunning = false;
    });
}

async function fillMissingKassaLinks(leads: amo.AmoLead[]): Promise<void> {
  const fieldIds = await kassaLinkFieldIds();
  if (fieldIds.length === 0) return;
  const rows = await db
    .select({ number: dealsTable.number, amoLeadId: dealsTable.amoLeadId })
    .from(dealsTable);
  const localNumber = new Map<number, number>();
  for (const row of rows) {
    if (row.amoLeadId) localNumber.set(row.amoLeadId, row.number);
  }
  const base = getEnv().PUBLIC_BASE_URL;
  const missing = leads.filter((lead) => {
    const number = localNumber.get(lead.id) ?? lead.id;
    const link = dealDeepLink(base, number);
    return fieldIds.some((fieldId) => linkFieldText(lead, fieldId) !== link);
  });
  const batch = missing.slice(0, LINK_BACKFILL_BATCH);
  let written = 0;
  for (const lead of batch) {
    try {
      await writeKassaLink(lead.id, localNumber.get(lead.id) ?? lead.id);
      written += 1;
    } catch (err) {
      console.warn(`[amo] ссылка на кассу, сделка #${lead.id}: ${(err as Error).message}`);
    }
  }
  if (written > 0) {
    const left = missing.length - batch.length;
    console.log(
      `[amo] ссылка на кассу: дописано ${written}${left > 0 ? `, ещё ${left} в следующем проходе` : ""}`
    );
  }
}

function cartSummary(deal: Deal): string {
  return deal.items.map((i) => `${i.name} × ${i.qty}`).join(", ");
}

export function paymentStatusLabel(deal: Deal): string {
  if (deal.total <= 0) return "—";
  if (deal.paid >= deal.total) return "Оплачено";
  return deal.paid > 0 ? "Частично" : "Не оплачено";
}

// «Сара» — унаследованное текстовое поле очереди сдачи/возврата/чаевых (аналог
// нынешнего «Эдвина»), используем его же, новых полей под это не создаём.
function saraNote(deal: Deal): string | null {
  const parts: string[] = [];
  if (deal.changeStatus === "pending" && deal.changeDestination) {
    const amount = Math.max(0, deal.paid - deal.total);
    parts.push(`Сдача ${amount}₽ → ${deal.changeDestination}`);
  }
  if (deal.returnStatus === "pending" && deal.returnDestination) {
    parts.push(`Возврат ${Math.abs(deal.total)}₽ → ${deal.returnDestination}`);
  }
  if (deal.tips && deal.tipsDestination) {
    parts.push(`Чаевые ${deal.tips}₽ → ${deal.tipsDestination}`);
  }
  if (deal.saryPhone) {
    const who = deal.saryClient ? `${deal.saryClient} ${deal.saryPhone}` : deal.saryPhone;
    parts.push(
      deal.saryBonus
        ? `Сарафан ${who} · бонус ${deal.saryBonus}₽ учтён в продаже`
        : `Сарафан ${who} · выплата ожидает`
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

/** Строит массив custom_fields_values сделки amoCRM из данных заявки кассы. */
export async function buildLeadCustomFields(deal: Deal): Promise<LeadCustomFieldValue[]> {
  const ids = await getLeadFieldIdMap();
  const out: LeadCustomFieldValue[] = [];
  const put = (key: AmoLeadFieldKey, value: unknown) => {
    const id = ids[key];
    if (!id || value === undefined || value === null || value === "") return;
    out.push({ field_id: id, values: [{ value }] });
  };

  put("consultant", deal.consultant);
  put("purpose", deal.purpose);
  put("channel", deal.channel);
  put("comment", deal.comment);
  put("storeAddress", deal.storeAddress);
  put("guestName", deal.guestName);
  put("certificateNumber", deal.certificateNumber);
  if (deal.kind === "cert_digital" || deal.kind === "cert_plastic") {
    // Почтовый сервис читает поля сделки, а не email контакта.
    // Пустые значения не стирают данные, заполненные менеджером в amoCRM.
    if (deal.kind === "cert_digital") put("certificateEmail", deal.email?.trim());
    if (deal.receivedAt && /^\d{4}-\d{2}-\d{2}$/.test(deal.receivedAt)) {
      const timestamp = Date.parse(`${deal.receivedAt}T00:00:00+03:00`);
      if (Number.isFinite(timestamp)) put("certificateFrom", Math.floor(timestamp / 1000));
    }
  }
  put("validUntil", deal.validUntil);
  put("invoiceNo", deal.invoiceNo);
  if (deal.items.length) put("orderContains", cartSummary(deal));
  put("msOrder", deal.msOrderId);
  put("msDemand", deal.msDemandId);
  put("paymentStatus", paymentStatusLabel(deal));
  put("meetingDate", deal.meetingDate);
  put("approxMeetingDate", deal.approxMeetingDate);
  put("actualUntil", deal.actualUntil);
  put("rentalFrom", deal.rentalFrom);
  put("rentalTo", deal.rentalTo);
  put("referredBy", deal.referredBy);
  put("callManager", deal.callManager);
  if (deal.deliveryCity) put("deliveryAddress", deal.deliveryCity);
  put("atelierAmount", deal.atelierAmount);
  put("deliveryAmount", deal.deliveryAmount);
  put("closingDocumentsRequired", deal.closingDocumentsRequired);
  put("invoiceDate", deal.invoiceDate);
  put("invoiceStatus", deal.invoiceStatus);
  put("rentalStatus", deal.rentalStatus);
  put("rentalIssuedAt", deal.rentalIssuedAt);
  put("rentalReturnedAt", deal.rentalReturnedAt);
  put("rentalDeposit", deal.rentalDeposit);

  // Частичная оплата (от 3 000 ₽ и любая другая, если заказ ею не закрыт)
  // ставит платную отложку, даже когда этап ещё не «Товар отложен».
  // «Отложка до» без этого этапа пишется, только если дата уже есть в заявке.
  const hold = amoOtlozhka(deal);
  if (hold.until) put("reservedUntil", hold.until);
  if (hold.kind) put("otlozhkaKind", hold.kind);
  // Резерв компании (kind=company).
  if (deal.kind === "company" && deal.deferredUntil) {
    put("deferredUntil", deal.deferredUntil);
  }

  const sara = saraNote(deal);
  if (sara) put("sara", sara);

  // Ссылка на заявку в кассе: из сделки колл-менеджер попадает сразу в карточку
  // и ставит задачу на перемещение или отложку (созвон 04.09).
  put("kassaLink", dealDeepLink(getEnv().PUBLIC_BASE_URL, deal.number));

  return sanitizeSelectValues(out);
}

// Написание значений в справочниках amoCRM своё («на Бауманской», «Оплачен»),
// поэтому подгоняем значение под справочник, а неизвестное — выбрасываем:
// одно чужое значение select-поля раньше отбивало создание сделки целиком.
const ENUM_SYNONYMS: Record<string, string[]> = {
  Оплачено: ["Оплачен", "Оплачено полностью"],
  Частично: ["Частично оплачен", "Частичная оплата"],
  "Не оплачено": ["Не оплачен"],
};

/** amoCRM ждёт дату числом unix. Полдень по Москве не съезжает на соседние сутки. */
function amoDateEntry(value: unknown): { value: unknown } {
  if (typeof value === "number") return { value };
  const match = String(value ?? "").match(/^(\d{4}-\d{2}-\d{2})/);
  if (!match) return { value };
  const timestamp = Date.parse(`${match[1]}T12:00:00+03:00`);
  return { value: Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : value };
}

function normalizeEnum(value: string): string {
  return value.toLowerCase().trim().replace(/ё/g, "е").replace(/\s+/g, " ");
}

function resolveEnumValue(enums: string[], value: string): string | null {
  const candidates = [value, ...(ENUM_SYNONYMS[value] ?? [])].map(normalizeEnum);
  for (const candidate of candidates) {
    const hit = enums.find((e) => normalizeEnum(e) === candidate);
    if (hit) return hit;
  }
  return null;
}

async function sanitizeSelectValues(values: LeadCustomFieldValue[]): Promise<LeadCustomFieldValue[]> {
  const meta = await getLeadFieldMeta().catch(() => new Map());
  const out: LeadCustomFieldValue[] = [];
  for (const item of values) {
    const field = meta.get(item.field_id);
    if (field && field.type.includes("date") && !field.type.includes("select")) {
      out.push({
        field_id: item.field_id,
        values: item.values.map((entry) => amoDateEntry(entry.value)),
      });
      continue;
    }
    if (!field || !field.type.includes("select") || field.enums.length === 0) {
      out.push(item);
      continue;
    }
    const resolved = item.values
      .map((v) => resolveEnumValue(field.enums, String(v.value)))
      .filter((v): v is string => v !== null)
      .map((value) => ({ value }));
    if (resolved.length) {
      out.push({ field_id: item.field_id, values: resolved });
    } else {
      console.warn(
        `[amoMapping] поле #${item.field_id}: «${String(item.values[0]?.value)}» нет в справочнике amoCRM — пропущено`
      );
    }
  }
  return out;
}

/**
 * Для kind=company: находит/создаёт сущность «Компания» в amoCRM и пишет
 * телефон + руководителя в её карточку. Возвращает id компании для привязки
 * к лиду (или null, если данных компании нет/amo недоступен).
 */
export async function ensureLeadCompany(deal: Deal): Promise<number | null> {
  if (deal.kind !== "company" || !deal.companyName) return null;
  try {
    const managerFieldId = await getCompanyFieldIdByName(AMO_COMPANY_FIELDS.manager);
    const company = await amo.ensureCompany(deal.companyName, deal.managerPhone, managerFieldId, deal.managerName);
    return company.id;
  } catch {
    return null;
  }
}
