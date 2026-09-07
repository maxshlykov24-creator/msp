import { AMO_COMPANY_FIELDS, AMO_LEAD_FIELDS, type AmoLeadFieldKey } from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import * as amo from "../clients/amo.js";
import { getCompanyFieldIdByName, getFieldIdByName, getLeadFieldMeta } from "./bootstrap.js";

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
  put("validUntil", deal.validUntil);
  put("invoiceNo", deal.invoiceNo);
  if (deal.items.length) put("orderContains", cartSummary(deal));
  put("msOrder", deal.msOrderId);
  put("msDemand", deal.msDemandId);
  put("paymentStatus", paymentStatusLabel(deal));
  put("meetingDate", deal.meetingDate);
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

  // Отложка (kind=deferred): срок + признак «платная/бесплатная» по наличию аванса.
  if (deal.kind === "deferred" && deal.reservedUntil) {
    put("reservedUntil", deal.reservedUntil);
    put("otlozhkaKind", deal.paid > 0 ? "Платная" : "Бесплатная");
  }
  // Резерв компании (kind=company).
  if (deal.kind === "company" && deal.deferredUntil) {
    put("deferredUntil", deal.deferredUntil);
  }

  const sara = saraNote(deal);
  if (sara) put("sara", sara);

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
