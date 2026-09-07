import { desc, eq, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { deals as dealsTable, amoMeta } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import {
  AMO_LEAD_FIELDS,
  AMO_PIPELINE_COMPLAINTS,
  AMO_PIPELINE_SALES,
  AMO_SYSTEM_STATUS_IDS,
  RENTAL_SERVICE_PRICE,
  STAGE_ALIASES,
} from "@kassa/shared";
import type { Deal, DealKind, FunnelType, Store } from "@kassa/shared";
import { getFieldIdByName } from "./bootstrap.js";
import { broadcast } from "../ws/hub.js";
import * as financeQueue from "./queue.js";

// Системные закрытые статусы amo (общие id для воронок).
const CLOSED_OR_JUNK_STATUS_IDS = new Set([
  142, // Успех
  143, // Провал
]);

// В кассе не тянем/не показываем эти этапы продаж (как считает мама: 107 без них).
const HIDDEN_SALES_STAGE_NAMES = new Set([
  "новая заявка",
  "взято в работу",
  "взята в работу",
  "неразобранное",
]);

/** Успех/Провал в списке кассы — с 1 июня 2026 (МСК). */
const CLOSED_SINCE_UNIX = Math.floor(new Date("2026-06-01T00:00:00+03:00").getTime() / 1000);

// ── Локальное зеркало заявок (то, что знает касса) ──────────────────

export async function listLocal(): Promise<Deal[]> {
  const rows = await db.select().from(dealsTable).orderBy(desc(dealsTable.number)).limit(500);
  return rows.map((r) => r.data as Deal);
}

export async function getByNumber(n: number): Promise<Deal | null> {
  const rows = await db.select().from(dealsTable).where(eq(dealsTable.number, n)).limit(1);
  return (rows[0]?.data as Deal | undefined) ?? null;
}

export async function getByAmoLeadId(amoLeadId: number): Promise<Deal | null> {
  const rows = await db
    .select()
    .from(dealsTable)
    .where(eq(dealsTable.amoLeadId, amoLeadId))
    .limit(1);
  return (rows[0]?.data as Deal | undefined) ?? null;
}

/** Имя этапа по id статуса amo (для вебхука). */
export async function stageNameByStatusId(
  statusId: number,
  pipelineId?: number
): Promise<string> {
  const statuses = await statusRows();
  if (pipelineId != null) return stageNameFor(statusId, pipelineId, statuses);
  return statuses.find((s) => s.amoId === statusId)?.name ?? "—";
}

/** Сбросить кэш списка amo — после входящего этапа данные на доске устарели. */
export function invalidateAmoOpenCache(): void {
  amoOpenCache = null;
}

/**
 * Зеркало сделки из amo, если в кассе ещё нет записи по amoLeadId
 * (доставку завели в CRM, а не в кассе). number = id сделки amo.
 */
export async function ensureLocalFromAmoLead(lead: amo.AmoLead, stage: string): Promise<Deal> {
  if (!lead?.id) throw new Error("amo lead без id");
  const existing = await getByAmoLeadId(lead.id);
  if (existing) return existing;

  const byNumber = await getByNumber(lead.id);
  if (byNumber) {
    const patched: Deal = {
      ...byNumber,
      amoLeadId: lead.id,
      stage,
      kind:
        ["Передан на сборку", "Собран", "Вызван курьер", "Отправлен", "Доставлен", "Не выкуплен"].includes(
          stage
        )
          ? "delivery"
          : byNumber.kind,
    };
    await persist(patched);
    return patched;
  }

  const statuses = await statusRows();
  const storeFieldId = await getFieldIdByName(AMO_LEAD_FIELDS.storeAddress).catch(() => null);
  const contacts = await contactsForLeads([lead]);
  const mapped = mapLeadToDeal(lead, statuses, storeFieldId, contacts);
  const deliveryStages = new Set([
    "Передан на сборку",
    "Собран",
    "Вызван курьер",
    "Отправлен",
    "Доставлен",
    "Не выкуплен",
  ]);
  const deal: Deal = {
    ...mapped,
    stage,
    kind: deliveryStages.has(stage) ? "delivery" : mapped.kind,
    history: [
      {
        at: new Date().toISOString(),
        who: "amoCRM",
        action: `Зеркало из amoCRM · этап «${stage}»`,
      },
    ],
  };
  await persist(deal);
  return deal;
}

// Резолв по ссылке с фронта: это может быть number (строкой) или Deal.id (строка).
export async function resolve(ref: string): Promise<Deal | null> {
  const asNum = Number(ref);
  if (Number.isInteger(asNum) && String(asNum) === ref) {
    const byNum = await getByNumber(asNum);
    if (byNum) return byNum;
  }
  const all = await db.select().from(dealsTable).limit(1000);
  return (all.find((r) => (r.data as Deal).id === ref)?.data as Deal | undefined) ?? null;
}

/** postgres.js не принимает undefined в jsonb — вычищаем перед записью. */
function dealForDb(deal: Deal): Deal {
  return JSON.parse(JSON.stringify(deal)) as Deal;
}

export async function persist(deal: Deal): Promise<Deal> {
  const data = dealForDb(deal);
  await db
    .insert(dealsTable)
    .values({
      number: data.number,
      amoLeadId: data.amoLeadId ?? null,
      msOrderId: data.msOrderId ?? null,
      msDemandId: data.msDemandId ?? null,
      data: data as object,
      stage: data.stage,
      paymentStatus: data.paymentStatus ?? null,
      idempotencyKey: data.idempotencyKey ?? data.id,
      syncStatus: data.syncStatus ?? "pending",
      syncError: data.syncError ?? null,
      updatedAt: new Date(),
    })
    .onConflictDoUpdate({
      target: dealsTable.number,
      set: {
        amoLeadId: data.amoLeadId ?? null,
        msOrderId: data.msOrderId ?? null,
        msDemandId: data.msDemandId ?? null,
        data: data as object,
        stage: data.stage,
        paymentStatus: data.paymentStatus ?? null,
        syncStatus: data.syncStatus ?? "pending",
        syncError: data.syncError ?? null,
        updatedAt: new Date(),
      },
    });
  return data;
}

/** Резервирует серверный номер и ключ до внешних API. Повтор возвращает исходный результат. */
export async function reserveServerDeal(
  requested: Omit<Deal, "number" | "createdAt"> & { createdAt?: string },
  idempotencyKey: string
): Promise<{ deal: Deal; duplicate: boolean }> {
  return db.transaction(async (tx) => {
    await tx.execute(sql`select pg_advisory_xact_lock(77194201)`);
    const existing = await tx
      .select()
      .from(dealsTable)
      .where(eq(dealsTable.idempotencyKey, idempotencyKey))
      .limit(1);
    if (existing[0]) return { deal: existing[0].data as Deal, duplicate: true };

    const maxRows = await tx.select({ max: sql<number>`coalesce(max(${dealsTable.number}), 0)` }).from(dealsTable);
    const number = Number(maxRows[0]?.max ?? 0) + 1;
    const deal: Deal = {
      ...requested,
      id: requested.id || idempotencyKey,
      idempotencyKey,
      number,
      createdAt: requested.createdAt ?? new Date().toISOString(),
      syncStatus: "pending",
    };
    await tx.insert(dealsTable).values({
      number,
      amoLeadId: deal.amoLeadId ?? null,
      msOrderId: deal.msOrderId ?? null,
      msDemandId: deal.msDemandId ?? null,
      data: deal as object,
      stage: deal.stage,
      paymentStatus: deal.paymentStatus ?? null,
      idempotencyKey,
      syncStatus: "pending",
    });
    return { deal, duplicate: false };
  });
}

export async function markSync(
  deal: Deal,
  status: "pending" | "synced" | "failed",
  error?: string
): Promise<Deal> {
  const updated = { ...deal, syncStatus: status, syncError: error };
  return persist(updated);
}

export async function updateDealFields(
  ref: string,
  patch: Partial<Deal> & { reason?: string },
  who: string
): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const { reason, ...fields } = patch;
  const nextStage = fields.stage ?? deal.stage;
  const updated: Deal = {
    ...deal,
    ...fields,
    stage: nextStage,
    history: [
      ...(deal.history ?? []),
      {
        at: new Date().toISOString(),
        who,
        action:
          nextStage !== deal.stage
            ? `Этап изменён: ${deal.stage} → ${nextStage}${reason ? ` · Причина: ${reason}` : ""}`
            : `Заявка обновлена${reason ? ` · ${reason}` : ""}`,
      },
    ],
  };
  await persist(updated);
  if (deal.amoLeadId && nextStage !== deal.stage) {
    const statusId = await resolveStatusId(deal.amoLeadId, nextStage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
  }
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

export async function updateStage(
  ref: string,
  stage: string,
  who: string,
  patch?: {
    meetingDate?: string;
    issued?: boolean;
    reason?: string;
    /** Не писать этап обратно в amo (входящий webhook). */
    skipAmoWriteback?: boolean;
    kind?: DealKind;
  }
): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const updated: Deal = {
    ...deal,
    stage,
    kind: patch?.kind ?? deal.kind,
    meetingDate: patch?.meetingDate ?? deal.meetingDate,
    issued: patch?.issued ?? deal.issued,
    history: [
      ...(deal.history ?? []),
      {
        at: new Date().toISOString(),
        who,
        action: `Этап изменён: ${deal.stage} → ${stage}${patch?.reason ? ` · Причина: ${patch.reason}` : ""}`,
      },
    ],
  };
  await persist(updated);
  if (deal.amoLeadId && !patch?.skipAmoWriteback) {
    const statusId = await resolveStatusId(deal.amoLeadId, stage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
  }
  broadcast("deal.stage_changed", { number: deal.number, stage });
  return updated;
}

export type ConvertKindPatch = {
  kind: "sale" | "company" | "rental";
  companyName?: string;
  managerName?: string;
  managerPhone?: string;
  atelierAmount?: number;
  deliveryAmount?: number;
  closingDocumentsRequired?: boolean;
  issued?: boolean;
  rentalFrom?: string;
  rentalTo?: string;
  issueDate?: string;
  returnDate?: string;
};

/**
 * Отложку или обещание проводят как продажу / компанию / аренду (п.1.1 правок 10.08).
 * Номер и общие данные сохраняются; для компании/аренды дописываются обязательные
 * поля как при создании. Аванс отложки остаётся предоплатой.
 */
export async function convertDealKind(
  ref: string,
  patch: ConvertKindPatch,
  who: string
): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const nextKind = patch.kind;
  if (deal.kind === nextKind) return deal;

  if (nextKind === "sale" || nextKind === "company") {
    if (!deal.items?.length) throw new Error("В заявке нет товаров — добавьте позиции перед сменой типа");
  }
  if (nextKind === "rental" && !deal.items?.length && !patch.rentalFrom) {
    throw new Error("Укажите комплект и сроки аренды");
  }

  let items = deal.items ?? [];
  if (nextKind === "rental" && !items.some((it) => it.productId === "rent")) {
    items = [
      { productId: "rent", name: "Аренда комплекта", price: RENTAL_SERVICE_PRICE, qty: 1 },
      ...items.map((it) => ({ ...it, noPrice: true, price: it.noPrice ? it.price : 0 })),
    ];
  }

  const stage = convertedStage(deal.stage, nextKind);
  const atelierAmount = patch.atelierAmount ?? deal.atelierAmount;
  const updated: Deal = {
    ...deal,
    kind: nextKind,
    stage,
    items,
    // Авторство (созвон 20.08): отложка консультанта остаётся его продажей —
    // непустое поле не трогаем. Отложка колл-менеджера до продажи не принадлежит
    // никому — продавцом становится тот, кто провёл конвертацию.
    consultant: deal.consultant?.trim() ? deal.consultant : who,
    reservedUntil: undefined,
    deferredUntil: undefined,
    ...(nextKind === "company"
      ? {
          companyName: patch.companyName?.trim() || deal.companyName,
          managerName: patch.managerName?.trim() || deal.managerName,
          managerPhone: patch.managerPhone?.trim() || deal.managerPhone,
          atelierAmount: atelierAmount || undefined,
          deliveryAmount: patch.deliveryAmount ?? deal.deliveryAmount,
          closingDocumentsRequired: patch.closingDocumentsRequired ?? deal.closingDocumentsRequired ?? false,
          issued: patch.issued ?? deal.issued ?? false,
          // Как при создании компании — Эдвину сразу «Выставить счет».
          invoiceStatus: deal.invoiceStatus === "paid" ? "paid" : deal.invoiceNo ? "awaiting_payment" : "draft",
          documentsStatus: deal.documentsStatus ?? "pending",
          atelierStatus:
            (atelierAmount ?? 0) > 0 ? deal.atelierStatus ?? "pending" : undefined,
        }
      : {}),
    ...(nextKind === "rental"
      ? {
          rentalFrom: patch.rentalFrom || deal.rentalFrom,
          rentalTo: patch.rentalTo || deal.rentalTo,
          issueDate: patch.issueDate || deal.issueDate,
          returnDate: patch.returnDate || deal.returnDate,
          rentalStatus:
            deal.paid >= (deal.total || 0) && (deal.total || 0) > 0
              ? "paid"
              : deal.rentalStatus ?? "reserved",
          photoAttached: true,
        }
      : {}),
    history: [
      ...(deal.history ?? []),
      {
        at: new Date().toISOString(),
        who,
        action: `Тип изменён: ${DEAL_KIND_TITLE[deal.kind] ?? deal.kind} → ${DEAL_KIND_TITLE[nextKind]}`,
      },
    ],
  };

  // Пересчёт total для аренды: услуга + комплекты без цены.
  if (nextKind === "rental") {
    const rentLine = updated.items.find((it) => it.productId === "rent");
    const rentPrice = rentLine?.price ?? 0;
    updated.total = rentPrice;
    // paid оставляем — аванс отложки идёт в оплату аренды.
  }

  await persist(updated);
  if (deal.amoLeadId && updated.stage !== deal.stage) {
    const statusId = await resolveStatusId(deal.amoLeadId, updated.stage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
    await amo
      .addLeadNote(deal.amoLeadId, `Касса: тип заявки изменён на «${DEAL_KIND_TITLE[nextKind]}»`)
      .catch(() => {});
  }
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

/**
 * Товарные этапы есть во всех целевых воронках — сохраняем.
 * Компания без товарного этапа → «Товар отложен» (как при создании + счёт Эдвину).
 */
function convertedStage(stage: string, nextKind: "sale" | "company" | "rental"): string {
  const shared = ["Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен"];
  if (shared.includes(stage)) return stage;
  return {
    sale: "Хочет прийти",
    company: "Товар отложен",
    rental: "Аренда оплачена",
  }[nextKind];
}

const DEAL_KIND_TITLE: Record<string, string> = {
  deferred: "Отложка",
  promise: "Обещание",
  sale: "Продажа",
  company: "Продажа компании",
  rental: "Аренда",
};

/**
 * Конвейер продажи компании (п.9): Эдвин ставит счёт и оплату, Миша — документы
 * и оплату ателье. Этап воронки двигается автоматически по последнему событию.
 */
export async function updateCompanyStatus(
  ref: string,
  patch: {
    invoiceNo?: string;
    invoiceDate?: string;
    invoiceStatus?: Deal["invoiceStatus"];
    documentsStatus?: Deal["documentsStatus"];
    atelierStatus?: Deal["atelierStatus"];
  },
  who: string
): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const merged: Deal = { ...deal, ...patch };
  // Дата внесения в кассу — автоматическая и неизменяемая (правки 10.08, п.8).
  if (patch.invoiceNo !== undefined && !merged.invoiceEnteredAt) {
    merged.invoiceEnteredAt = new Date().toISOString().slice(0, 10);
  }
  const nextStage =
    merged.documentsStatus === "handed"
      ? "Документы переданы"
      : merged.documentsStatus === "ready"
        ? "Документы готовы"
        : merged.invoiceStatus === "paid"
          ? "Оплачено"
          : merged.invoiceNo
            ? "Счет выставлен"
            : deal.stage;
  const actions = Object.entries(patch)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}: ${String(value)}`);
  const updated: Deal = {
    ...merged,
    stage: deal.stage === "Успех" || deal.stage === "Провал" ? deal.stage : nextStage,
    history: [
      ...(deal.history ?? []),
      { at: new Date().toISOString(), who, action: `Продажа компании — ${actions.join(", ")}` },
    ],
  };
  await persist(updated);
  if (deal.amoLeadId && updated.stage !== deal.stage) {
    const statusId = await resolveStatusId(deal.amoLeadId, updated.stage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
  }
  await advanceCompanyQueue(updated, patch, who);
  broadcast("deal.updated", { number: deal.number });
  broadcast("queue.updated", {});
  return updated;
}

/**
 * Цепочка задач по продаже компании: закрываем текущий шаг и заводим ровно
 * следующий (правки владельца 10.08.2026, пп.8–9). Одна активная задача —
 * иначе Миша видит три штуки сразу и не понимает, что делать первым.
 */
async function advanceCompanyQueue(
  deal: Deal,
  patch: {
    invoiceNo?: string;
    invoiceStatus?: Deal["invoiceStatus"];
    documentsStatus?: Deal["documentsStatus"];
    atelierStatus?: Deal["atelierStatus"];
  },
  who: string
): Promise<void> {
  const company = deal.companyName?.trim() || deal.clientName;
  const due = Math.max(0, deal.total - deal.paid);
  const common = {
    dealNumber: deal.number,
    client: company,
    status: "pending" as const,
    metadata: {
      source: "deal",
      invoiceNo: deal.invoiceNo,
      invoiceDate: deal.invoiceDate,
      invoiceEnteredAt: deal.invoiceEnteredAt,
      consultant: deal.consultant,
      companyName: deal.companyName,
      buyerName: deal.clientName,
      dealTotal: deal.total,
      dealPaid: deal.paid,
    },
  };

  // «Счёт выставлен» → сразу «Проверить оплату» (созвон 10.08: не ждать следующий день —
  // оплату могут закрыть в тот же день).
  if (patch.invoiceNo !== undefined && deal.invoiceStatus !== "paid") {
    await financeQueue.closeQueueByDeal(deal.number, "invoice", who);
    await financeQueue.addQueue({
      ...common,
      kind: "invoice_check",
      amount: due,
      destination: deal.invoiceNo ?? company,
    });
  }

  // Счёт оплачен → Мише сразу все шаги цепочки (доступны по очереди в UI).
  if (patch.invoiceStatus === "paid") {
    await financeQueue.closeQueueByDeal(deal.number, "invoice", who);
    await financeQueue.closeQueueByDeal(deal.number, "invoice_check", who);
    await financeQueue.enqueueMishaChain(deal, who);
  }

  // Закрываем текущий шаг; следующие задачи уже созданы при оплате счёта.
  if (patch.documentsStatus === "ready") {
    await financeQueue.closeQueueByDeal(deal.number, "documents", who);
  }

  if (patch.documentsStatus === "handed") {
    await financeQueue.closeQueueByDeal(deal.number, "documents_hand", who);
  }

  if (patch.atelierStatus === "paid") {
    await financeQueue.closeQueueByDeal(deal.number, "atelier", who);
  }
}

/**
 * Откат статуса заявки при возврате задачи Эдвина/Миши из «закрыто».
 * Очередь уже переоткрыта — здесь только зеркало в карточке, без advanceCompanyQueue.
 */
export async function revertCompanyStep(
  dealNumber: number,
  kind: string,
  who: string
): Promise<Deal | null> {
  const deal = await getByNumber(dealNumber);
  if (!deal) return null;

  const patch: Partial<Deal> = {};
  if (kind === "documents") patch.documentsStatus = "pending";
  else if (kind === "documents_hand") patch.documentsStatus = "ready";
  else if (kind === "atelier") patch.atelierStatus = "pending";
  else if (kind === "invoice_check") patch.invoiceStatus = "awaiting_payment";
  else if (kind === "invoice" && deal.invoiceStatus !== "paid") patch.invoiceStatus = "draft";
  else return deal;

  const merged: Deal = { ...deal, ...patch };
  const nextStage =
    merged.documentsStatus === "handed"
      ? "Документы переданы"
      : merged.documentsStatus === "ready"
        ? "Документы готовы"
        : merged.invoiceStatus === "paid"
          ? "Оплачено"
          : merged.invoiceNo
            ? "Счет выставлен"
            : deal.stage;

  const updated: Deal = {
    ...merged,
    stage: deal.stage === "Успех" || deal.stage === "Провал" ? deal.stage : nextStage,
    history: [
      ...(deal.history ?? []),
      { at: new Date().toISOString(), who, action: `Вернули задачу «${kind}» в работу` },
    ],
  };
  await persist(updated);
  if (deal.amoLeadId && updated.stage !== deal.stage) {
    const statusId = await resolveStatusId(deal.amoLeadId, updated.stage);
    if (statusId) await amo.updateLead(deal.amoLeadId, { statusId }).catch(() => {});
  }
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

/**
 * Выдача клиенту по продаже компании: товар и закрывающие документы отмечает
 * консультант из карточки заявки. Документы можно передать только после того,
 * как Миша отметил их готовыми; товар и документы часто забирают порознь.
 */
export async function handoverCompany(
  ref: string,
  patch: { issued?: boolean; documentsHanded?: boolean },
  who: string
): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const documentsStatus = patch.documentsHanded ? "handed" : deal.documentsStatus;
  const actions: string[] = [];
  if (patch.issued) actions.push("товар выдан");
  if (patch.documentsHanded) actions.push("документы переданы");
  const updated: Deal = {
    ...deal,
    issued: patch.issued ?? deal.issued,
    documentsStatus,
    history: [
      ...(deal.history ?? []),
      { at: new Date().toISOString(), who, action: `Выдача клиенту — ${actions.join(", ")}` },
    ],
  };
  await persist(updated);
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

export async function addComment(ref: string, text: string, who: string): Promise<Deal | null> {
  const deal = await resolve(ref);
  if (!deal) return null;
  const updated: Deal = {
    ...deal,
    history: [...(deal.history ?? []), { at: new Date().toISOString(), who, action: `Комментарий: ${text}` }],
  };
  await persist(updated);
  if (deal.amoLeadId) await amo.addLeadNote(deal.amoLeadId, text).catch(() => {});
  broadcast("deal.updated", { number: deal.number });
  return updated;
}

function normalizeStageName(name: string): string {
  return name.toLowerCase().trim().replace(/ё/g, "е").replace(/\s+/g, " ");
}

async function resolveStatusId(leadId: number, stageNameStr: string): Promise<number | null> {
  const lead = await amo.getLead(leadId);
  if (!lead) return null;
  const rows = await db.select().from(amoMeta).where(eq(amoMeta.kind, "status"));
  const sameP = rows.filter((r) => r.parentId === lead.pipeline_id);
  // Точное имя + алиасы (без includes — иначе легко попасть не в тот этап).
  for (const candidate of [stageNameStr, ...(STAGE_ALIASES[stageNameStr] ?? [])]) {
    const needle = normalizeStageName(candidate);
    const exact = sameP.find((r) => normalizeStageName(r.name) === needle);
    if (exact?.amoId) return exact.amoId;
  }
  return AMO_SYSTEM_STATUS_IDS[normalizeStageName(stageNameStr)] ?? null;
}

// ── Чтение из amoCRM (реальные заявки для экрана «Заявки») ───────────

type StatusRow = { amoId: number; name: string; parentId: number | null; payload: unknown };

async function statusRows(): Promise<StatusRow[]> {
  const rows = await db.select().from(amoMeta).where(eq(amoMeta.kind, "status"));
  return rows.map((r) => ({
    amoId: r.amoId,
    name: r.name,
    parentId: r.parentId,
    payload: r.payload,
  }));
}

function isJunkStatus(s: StatusRow): boolean {
  if (CLOSED_OR_JUNK_STATUS_IDS.has(s.amoId)) return true;
  const name = s.name.toLowerCase();
  if (name === "успех" || name === "провал" || name === "неразобранное") return true;
  const payload = s.payload as { type?: number } | null;
  if (payload && typeof payload === "object" && payload.type === 1) return true;
  return false;
}

function isClosedStatus(s: StatusRow): boolean {
  if (CLOSED_OR_JUNK_STATUS_IDS.has(s.amoId)) return true;
  const name = s.name.toLowerCase();
  return name === "успех" || name === "провал";
}

function stageNameFor(statusId: number, pipelineId: number, statuses: StatusRow[]): string {
  const same = statuses.filter((s) => s.parentId === pipelineId);
  return (
    same.find((s) => s.amoId === statusId)?.name ??
    statuses.find((s) => s.amoId === statusId)?.name ??
    "—"
  );
}

function storeFromLead(lead: amo.AmoLead, storeFieldId: number | null): Store {
  if (storeFieldId) {
    const raw = lead.custom_fields_values?.find((f) => f.field_id === storeFieldId)?.values?.[0]?.value;
    const v = String(raw ?? "").toLowerCase();
    if (v.includes("онлайн")) return "Онлайн-магазин";
    if (v.includes("пятниц") || v.includes("новокузнец")) return "На Пятницкой";
    if (v.includes("бауман")) return "На Бауманской";
  }
  return "На Бауманской";
}

function funnelFromStore(store: Store): FunnelType {
  return store === "Онлайн-магазин" ? "online" : "offline";
}

/**
 * Тип (онлайн/оффлайн) — по магазину.
 * Вид: жалобы/возвраты/обмены → defect; иначе sale.
 */
function funnelAndKind(pipelineId: number, store: Store): { funnel: FunnelType; kind: DealKind } {
  if (pipelineId === AMO_PIPELINE_COMPLAINTS) {
    return { funnel: funnelFromStore(store), kind: "defect" };
  }
  return { funnel: funnelFromStore(store), kind: "sale" };
}

/**
 * Имя клиента для доски: контакт amo, не название сделки.
 * Fallback: «Имя · #номер» (как пишет касса в lead.name) → «Имя».
 */
function clientFromLead(
  lead: amo.AmoLead,
  contactsById: Map<number, amo.AmoContact>
): { clientName: string; clientPhone: string } {
  const contactId = amo.mainContactId(lead);
  const contact = contactId != null ? contactsById.get(contactId) : undefined;
  if (contact?.name?.trim()) {
    return {
      clientName: contact.name.trim(),
      clientPhone: amo.phoneFromContact(contact),
    };
  }
  const fromLeadTitle = lead.name.match(/^(.*?)\s*·\s*#\d+\s*$/);
  return {
    clientName: (fromLeadTitle?.[1] ?? lead.name).trim() || lead.name,
    clientPhone: "",
  };
}

async function contactsForLeads(leads: amo.AmoLead[]): Promise<Map<number, amo.AmoContact>> {
  const ids = leads
    .map((l) => amo.mainContactId(l))
    .filter((id): id is number => id != null);
  const contacts = await amo.getContactsByIds(ids).catch(() => [] as amo.AmoContact[]);
  return new Map(contacts.map((c) => [c.id, c]));
}

function mapLeadToDeal(
  lead: amo.AmoLead,
  statuses: StatusRow[],
  storeFieldId: number | null,
  contactsById: Map<number, amo.AmoContact> = new Map()
): Deal {
  const store = storeFromLead(lead, storeFieldId);
  const { funnel, kind } = funnelAndKind(lead.pipeline_id, store);
  const createdAt = lead.created_at
    ? new Date(lead.created_at * 1000).toISOString()
    : new Date().toISOString();
  const { clientName, clientPhone } = clientFromLead(lead, contactsById);
  return {
    id: `amo-${lead.id}`,
    number: lead.id,
    createdAt,
    funnel,
    kind,
    consultant: "",
    clientName,
    clientPhone,
    store,
    storeAddress: store,
    items: [],
    payments: [],
    stage: stageNameFor(lead.status_id, lead.pipeline_id, statuses),
    total: lead.price ?? 0,
    paid: 0,
    amoLeadId: lead.id,
  };
}

function openStatusIds(pipelineId: number, statuses: StatusRow[], hideEarlySales = false): number[] {
  return statuses
    .filter((s) => {
      if (s.parentId !== pipelineId || isJunkStatus(s)) return false;
      if (hideEarlySales && HIDDEN_SALES_STAGE_NAMES.has(s.name.toLowerCase())) return false;
      return true;
    })
    .map((s) => s.amoId);
}

function closedStatusIds(pipelineId: number, statuses: StatusRow[]): number[] {
  const ids = statuses.filter((s) => s.parentId === pipelineId && isClosedStatus(s)).map((s) => s.amoId);
  // Fallback на системные 142/143, если в meta ещё нет строк воронки.
  if (ids.length === 0) return [142, 143];
  return [...new Set(ids)];
}

/**
 * Доска заявок: открытые (продажи + жалобы) + Успех/Провал с 1 июня 2026.
 * Кэш 45с + один in-flight: иначе параллельные обновления (телефон/десктоп)
 * бьют amo ~5–7с × N и сайт выглядит «мертвым».
 *
 * При росте числа сделок (~1800+) полный fetchAmoOpen занимает ~25–30с —
 * дольше, чем таймаут роута. Поэтому при устаревшем кэше отдаём последние
 * известные данные немедленно, а обновление гоняем в фоне (stale-while-
 * revalidate): устаревшие заявки лучше пустой доски. Пустой список — только
 * если кэша ещё не было вообще (холодный старт).
 */
let amoOpenCache: { at: number; deals: Deal[] } | null = null;
let amoOpenInflight: Promise<Deal[]> | null = null;
const AMO_OPEN_CACHE_MS = 45_000;

function refreshAmoOpen(): Promise<Deal[]> {
  if (amoOpenInflight) return amoOpenInflight;
  amoOpenInflight = (async () => {
    try {
      const deals = await fetchAmoOpen();
      amoOpenCache = { at: Date.now(), deals };
      return deals;
    } finally {
      amoOpenInflight = null;
    }
  })();
  return amoOpenInflight;
}

export async function listAmoOpen(): Promise<Deal[]> {
  const now = Date.now();
  if (amoOpenCache && now - amoOpenCache.at < AMO_OPEN_CACHE_MS) {
    return amoOpenCache.deals;
  }
  const refreshing = refreshAmoOpen();
  if (amoOpenCache) return amoOpenCache.deals;
  return refreshing;
}

/** Прогрев кэша в фоне (бутстрап + периодический таймер) — без ожидания. */
export function prewarmAmoOpen(): void {
  refreshAmoOpen().catch(() => {});
}

async function fetchAmoOpen(): Promise<Deal[]> {
  const statuses = await statusRows();
  const storeFieldId = await getFieldIdByName(AMO_LEAD_FIELDS.storeAddress).catch(() => null);

  const salesOpen = openStatusIds(AMO_PIPELINE_SALES, statuses, true);
  const complaintsOpen = openStatusIds(AMO_PIPELINE_COMPLAINTS, statuses, false);
  const salesClosed = closedStatusIds(AMO_PIPELINE_SALES, statuses);
  const complaintsClosed = closedStatusIds(AMO_PIPELINE_COMPLAINTS, statuses);

  const [salesLeads, complaintsLeads, salesDone, complaintsDone] = await Promise.all([
    amo.listOpenLeadsInPipeline(AMO_PIPELINE_SALES, salesOpen).catch(() => [] as amo.AmoLead[]),
    amo.listOpenLeadsInPipeline(AMO_PIPELINE_COMPLAINTS, complaintsOpen).catch(() => [] as amo.AmoLead[]),
    amo
      .listLeadsInPipelineStatuses(AMO_PIPELINE_SALES, salesClosed, { createdFrom: CLOSED_SINCE_UNIX })
      .catch(() => [] as amo.AmoLead[]),
    amo
      .listLeadsInPipelineStatuses(AMO_PIPELINE_COMPLAINTS, complaintsClosed, {
        createdFrom: CLOSED_SINCE_UNIX,
      })
      .catch(() => [] as amo.AmoLead[]),
  ]);

  const byId = new Map<number, amo.AmoLead>();
  for (const l of [...salesLeads, ...complaintsLeads, ...salesDone, ...complaintsDone]) {
    byId.set(l.id, l);
  }
  const leads = [...byId.values()];
  const contactsById = await contactsForLeads(leads);
  return leads.map((l) => mapLeadToDeal(l, statuses, storeFieldId, contactsById));
}

/** Совместимость со старыми вызовами. */
export async function listAmo(_params?: amo.ListLeadsParams): Promise<Deal[]> {
  return listAmoOpen();
}

export async function searchAmo(args: { phone?: string; name?: string; number?: number }): Promise<Deal[]> {
  const statuses = await statusRows();
  const storeFieldId = await getFieldIdByName(AMO_LEAD_FIELDS.storeAddress).catch(() => null);
  if (args.number) {
    const lead = await amo.getLead(args.number);
    if (!lead) return [];
    const contactsById = await contactsForLeads([lead]);
    return [mapLeadToDeal(lead, statuses, storeFieldId, contactsById)];
  }
  const query = args.phone ?? args.name ?? "";
  const leads = await amo.listLeads({ query, limit: 20 });
  const contactsById = await contactsForLeads(leads);
  return leads.map((l) => mapLeadToDeal(l, statuses, storeFieldId, contactsById));
}
