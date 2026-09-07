import { and, eq, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { ledger, stock } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import * as ms from "../clients/ms.js";
import * as deals from "./deals.js";
import { attachPhotos } from "./files.js";
import * as catalog from "./catalog.js";
import { resolveAssortment } from "./catalog.js";
import { getMsRef, getWarehouseForStore, getStatusId, extractIdFromHref, getFieldIdByName } from "./bootstrap.js";
import { buildLeadCustomFields, ensureLeadCompany, invalidateLeadFieldCache, paymentStatusLabel } from "./amoMapping.js";
import { withIdempotency } from "../lib/idempotency.js";
import { toKopecks } from "../lib/money.js";
import {
  AMO_LEAD_FIELDS,
  AMO_PIPELINE_COMPLAINTS,
  AMO_PIPELINE_SALES,
  isSuitCategory,
  mansbandPayoutAmount,
  paymentKindOf as paymentKind,
  paymentMethodLabel,
  SARY_BONUS,
  selfPayouts,
  taskTitle,
} from "@kassa/shared";
import type { Deal, DealKind, Payment, Payout } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";
import * as certs from "./certificates.js";
import { getAppSettings } from "./settings.js";
import * as financeQueue from "./queue.js";
import * as taskService from "./tasks.js";
import * as taskFlow from "./taskFlow.js";
import { noteSuitBreaks } from "./suitBreaks.js";

export interface PhotoInput {
  filename: string;
  contentBase64: string;
  // Не задан → фото уходит в сделку amoCRM (нет складского документа МойСклад,
  // напр. аренда/брак/сертификат). Задан → к конкретному документу МС при fulfill.
  target?: "shipment" | "order" | "return";
}

/** Виды заявок → воронка Жалобы (всегда новая сделка, не схлопываем в Продажи). */
const COMPLAINTS_KINDS = new Set<DealKind>([
  "defect",
  "drycleaning",
  "resew",
  "wrong_size",
  "wrong_label",
  "refund",
  "exchange",
]);

const CLOSED_OR_JUNK_STATUS_IDS = new Set([142, 143]);

function dealItemsToPositions(deal: Deal) {
  return deal.items
    .filter((i) => i.qty > 0)
    .map((i) => ({ productId: i.productId, qty: i.qty, price: toKopecks(Math.abs(i.price)) }));
}

function returnLineItems(deal: Deal) {
  if (deal.kind === "refund") return deal.items.filter((i) => i.qty > 0);
  return deal.items.filter((i) => i.isReturn && i.qty > 0);
}

function saleLineItems(deal: Deal) {
  if (deal.kind === "refund") return [];
  if (deal.kind === "exchange") return deal.items.filter((i) => !i.isReturn && i.qty > 0);
  return deal.items.filter((i) => i.qty > 0);
}

// Полное проведение продажи: amo (контакт+сделка+Успех) + МойСклад (заказ+отгрузка+платежи)
// + фотофиксации + writeback в amo. Идемпотентно по deal.id.
export async function processSale(
  deal: Deal,
  opts: { fulfill: boolean; photos?: PhotoInput[]; who: string }
): Promise<Deal> {
  const { result } = await withIdempotency(`sale:${deal.id}`, "sale", async () => {
    const enriched = { ...deal };
    enriched.paymentStatus = paymentStatusLabel(enriched);

    // 1. amoCRM: контакт + сделка
    const amoLeadId = await ensureAmoLead(enriched);
    enriched.amoLeadId = amoLeadId ?? undefined;
    await enqueueManualChecks(enriched, opts.who);
    await enqueueOperationalTasks(enriched, opts.who);
    // Очередь Эдвина (сдача / чаевые / возврат / счёт компании) — до МойСклад:
    // отгрузка/возврат МС может упасть, а выплату клиенту всё равно нужно поставить.
    // addQueue дедуплицирует по заявке/виду/сумме/реквизитам.
    await enqueueEdwin(enriched);

    // 2. МойСклад
    if (opts.fulfill) {
      if (enriched.kind === "refund" || enriched.kind === "exchange") {
        await fulfillReturnOrExchange(enriched, opts.photos ?? [], opts.who);
      } else {
        await fulfillMoysklad(enriched, opts.photos ?? [], opts.who);
      }
    } else if (amoLeadId && opts.photos?.length) {
      await attachPhotosToLead(amoLeadId, opts.photos);
    }

    // 3. writeback статуса оплаты + ссылок в amo
    if (amoLeadId) await writeback(amoLeadId, enriched);

    // 4. Флажки на ИСХОДНОЙ сделке amo (не меняем её этап)
    if (
      (enriched.kind === "refund" || enriched.kind === "exchange") &&
      enriched.linkedDealNumber &&
      enriched.stage === "Успех"
    ) {
      await markOriginalReturnFlags(enriched.linkedDealNumber, enriched.kind).catch(() => {});
    }

    // 4b. Продажа сертификата → реестр (иначе в оплате не находится как действующий)
    await issueCertificateIfNeeded(enriched);

    // 4c. Оплата сертификатом → списание баланса
    await redeemCertificatesFromPayments(enriched);

    // 4d. Полупарк от этой продажи — в журнал разбитых костюмов.
    await noteSuitBreaks(enriched).catch(() => {});

    await enqueueSary(enriched, opts.who).catch(() => {});
    await noteSaryInAmo(enriched, opts.who).catch(() => {});

    // 5. локальное зеркало
    await deals.persist(enriched);
    broadcast("deal.created", { number: enriched.number });
    return enriched;
  });
  return result;
}

/** Сдача / чаевые / возврат / счёт — в очередь Эдвина. Идемпотентно (можно звать повторно). */
export async function enqueueEdwin(deal: Deal): Promise<void> {
  const common = {
    dealNumber: deal.number,
    client: deal.clientName,
    status: "pending" as const,
  };
  // В очередь Эдвина уходит только та часть выплаты, где выбрана строка
  // «Переведёт Mansband». Остальное консультант выдал сам из кассы.
  const change = Math.max(0, deal.paid - deal.total - (deal.tips ?? 0));
  const changeViaMansband = deal.changePayouts
    ? mansbandPayoutAmount(deal.changePayouts)
    : deal.changeStatus === "pending"
      ? change
      : 0;
  if (changeViaMansband > 0) {
    await financeQueue.addQueue({
      ...common,
      kind: "change",
      amount: changeViaMansband,
      destination: deal.changeDestination ?? "",
      metadata: { source: "deal", consultant: deal.consultant },
    });
  }
  const tipsViaMansband = deal.tipsPayouts
    ? mansbandPayoutAmount(deal.tipsPayouts)
    : (deal.tips ?? 0) > 0 && deal.tipsDestination
      ? deal.tips!
      : 0;
  if (tipsViaMansband > 0) {
    await financeQueue.addQueue({
      ...common,
      kind: "tips",
      amount: tipsViaMansband,
      destination: deal.tipsDestination ?? deal.consultant,
      metadata: { source: "deal", consultant: deal.consultant },
    });
  }
  const refundAmount = refundClientAmount(deal);
  const refundViaMansband = deal.returnPayouts?.length
    ? mansbandPayoutAmount(deal.returnPayouts)
    : deal.returnStatus === "pending"
      ? refundAmount
      : 0;
  if (refundViaMansband > 0) {
    await financeQueue.addQueue({
      ...common,
      kind: "refund",
      amount: refundViaMansband,
      destination: deal.returnDestination ?? "",
      metadata: { source: "deal", linkedDealNumber: deal.linkedDealNumber },
    });
  }
  await enqueueCompanyChain(deal, common);
}

/** Сколько денег должны вернуть клиенту (возврат или обмен с минусом). */
export function refundClientAmount(deal: Deal): number {
  if (deal.kind === "refund") return Math.max(0, Math.abs(deal.total ?? 0));
  if (deal.kind === "exchange" && (deal.total ?? 0) < 0) return Math.abs(deal.total);
  if (deal.returnPayouts?.length) {
    return deal.returnPayouts.reduce((s, r) => s + Math.max(0, r.amount || 0), 0);
  }
  return 0;
}

/**
 * Продажа компании: в очередь падает ровно один текущий шаг цепочки
 * (правки владельца 10.08.2026, пп.8–9). Дальше её двигает
 * `deals.updateCompanyStatus` по мере закрытия задач.
 */
/**
 * После смены типа отложки/обещания → компания / продажа / аренда:
 * снимаем задачи резерва, ставим цепочку компании (счёт Эдвину), задачи этапа.
 */
export async function afterKindConverted(
  deal: Deal,
  who: string,
  photos?: PhotoInput[]
): Promise<void> {
  await taskFlow.cancelOnKindConvert(deal.number);
  if (deal.kind === "company") {
    await enqueueCompanyChain(deal, {
      dealNumber: deal.number,
      client: deal.companyName?.trim() || deal.clientName,
      status: "pending",
    });
    if (deal.amoLeadId) {
      const companyId = await ensureLeadCompany(deal).catch(() => null);
      if (companyId) {
        await amo.linkCompanyToLead(deal.amoLeadId, companyId).catch(() => {});
      }
      const customFields = await buildLeadCustomFields(deal).catch(() => []);
      if (customFields.length) {
        await amo.updateLead(deal.amoLeadId, { customFields }).catch(() => {});
      }
    }
  }
  if (photos?.length && deal.amoLeadId) {
    await attachPhotosToLead(deal.amoLeadId, photos).catch(() => {});
  }
  if (deal.stage !== "Успех" && deal.stage !== "Провал") {
    await taskFlow.onDealStage(deal, who);
  }
  broadcast("queue.updated", {});
}

async function enqueueCompanyChain(
  deal: Deal,
  common: { dealNumber: number; client: string; status: "pending" }
): Promise<void> {
  if (deal.kind !== "company") return;
  const company = deal.companyName?.trim() || deal.clientName;
  const due = Math.max(0, deal.total - deal.paid);
  const meta = {
    source: "deal",
    invoiceNo: deal.invoiceNo,
    invoiceDate: deal.invoiceDate,
    invoiceEnteredAt: deal.invoiceEnteredAt,
    invoiceStatus: deal.invoiceStatus ?? "draft",
    consultant: deal.consultant,
    companyName: deal.companyName,
    buyerName: deal.clientName,
    dealTotal: deal.total,
    dealPaid: deal.paid,
  };

  if (deal.invoiceStatus !== "paid") {
    // Счёт ещё не оплачен: либо Эдвин его выставляет, либо уже ждёт оплату.
    const awaiting = deal.invoiceStatus === "awaiting_payment" || Boolean(deal.invoiceNo);
    await financeQueue.addQueue({
      ...common,
      client: company,
      kind: awaiting ? "invoice_check" : "invoice",
      amount: due,
      destination: deal.invoiceNo || company,
      metadata: meta,
    });
    return;
  }

  // Счёт уже оплачен при создании — сразу вся цепочка Миши.
  await financeQueue.enqueueMishaChain(deal, "Касса (авто)");
}

function phoneDigits(value: string | undefined | null): string {
  return (value ?? "").replace(/\D/g, "").slice(-10);
}

/**
 * Сколько костюмов в чеке — столько САР (созвон 04.09). Костюм определяется
 * группой МойСклад из настройки sarySuitGroups; подарки и возвраты не в счёт.
 */
async function countSuitsInDeal(deal: Deal, suitGroups: string[]): Promise<number> {
  const lines = deal.items.filter((item) => !item.isReturn && item.qty > 0 && item.productId);
  if (lines.length === 0) return 0;
  const categories = await catalog.productCategoriesByMsIds(lines.map((i) => i.productId));
  return lines.reduce((sum, item) => {
    const category = categories.get(item.productId);
    return isSuitCategory(category, suitGroups) ? sum + item.qty : sum;
  }, 0);
}

/**
 * Сарафан (правки владельца 10.08.2026, пп.3, 7; количество — созвон 04.09).
 * Источник «Сарафан» + телефон друга + этап «Успех», дальше по количеству:
 * костюмы в чеке — столько же САР; костюмов нет, но чек не ниже порога
 * (настройка saryMinCheck, сумма до вычета бонуса) — одна САР.
 * Сколько бонусов консультант применил в чеке, столько записей «учтено в чеке»
 * (in_check) без задачи; остальные — задача колл-менеджеру перевести 1000 ₽.
 * Телефон друга не нашёлся в базе — САР всё равно проходит, но с меткой
 * «не найдено», чтобы колл-менеджер проверил номер перед переводом.
 * Вызывать и при создании в «Успех», и при смене этапа на «Успех».
 */
export async function enqueueSary(deal: Deal, who: string): Promise<void> {
  const phone = deal.saryPhone?.trim();
  if (!phone || deal.stage !== "Успех") return;
  if (phoneDigits(phone) === phoneDigits(deal.clientPhone)) return; // самореферал

  const settings = await getAppSettings();
  const suits = await countSuitsInDeal(deal, settings.sarySuitGroups);
  // Порог по сумме чека: сравниваем с чеком до вычета бонуса САР, иначе
  // сам бонус −1000 ₽ выбивал бы чек 20 500 ₽ из программы.
  const checkAmount = (deal.total ?? 0) + (deal.saryBonus ?? 0);
  const target = suits > 0 ? suits : checkAmount >= settings.saryMinCheck ? 1 : 0;
  if (target === 0) return;

  // Заявку могли провести повторно (правка чека, смена этапа) — добираем недостающие.
  const already = await financeQueue.countSaryForDeal(deal.number);
  if (already >= target) return;

  // Бонусов в чеке столько, сколько раз консультант нажал «использовать»:
  // сумма бонуса кратна 1000 ₽ и не может превышать число САР.
  const bonusCount = Math.min(Math.round((deal.saryBonus ?? 0) / SARY_BONUS), target);
  const friend = deal.saryClient?.trim() || undefined;
  const phoneFound = Boolean(friend);

  for (let seq = already + 1; seq <= target; seq += 1) {
    const inCheck = seq <= bonusCount;
    const ofTarget = target > 1 ? ` (${seq} из ${target})` : "";
    await financeQueue.createSary({
      client: friend || "Друг клиента",
      phone,
      amount: SARY_BONUS,
      reason: inCheck
        ? `Сарафан учтён в продаже #${deal.number}${ofTarget}`
        : `Перевести ${SARY_BONUS} ₽ · заявка #${deal.number}${ofTarget}`,
      refDealNumber: deal.number,
      seq,
      phoneFound,
      status: inCheck ? "in_check" : "pending",
    });
    if (inCheck) continue;

    await taskService.createTask(
      {
        kind: "sary_send",
        dealNumber: deal.number,
        store: deal.store,
        assigneeRole: "crm",
        title: `${friend || phone}${phoneFound ? "" : " · не найдено"}${ofTarget}`,
        idempotencyKey: `sary-send:${deal.id}:${seq}`,
        metadata: {
          phone,
          client: friend,
          phoneFound,
          amount: SARY_BONUS,
          buyer: deal.clientName,
          dealKindLabel: "Сарафан",
        },
      },
      who
    );
  }
}

async function enqueueManualChecks(deal: Deal, who: string): Promise<void> {
  if (deal.kind !== "refund" && deal.kind !== "exchange") return;
  const unresolved = deal.items.filter((item) => item.isReturn && !item.barcode?.trim());
  if (unresolved.length === 0) return;
  await taskService.createTask({
    kind: "manual_check",
    dealNumber: deal.number,
    store: deal.store,
    assigneeRole: "consultant",
    title: taskTitle("manual_check", deal.number, `${unresolved.length} поз. без штрихкода`),
    idempotencyKey: `manual-check:${deal.id}`,
    metadata: { positions: unresolved.map((item) => item.name) },
  }, who);
}

async function enqueueOperationalTasks(deal: Deal, who: string): Promise<void> {
  // Задачи по этапу заявки («Ждет товар» → перемещение, «Товар в магазине» →
  // отложка, доставка → СДЭК) ставит движок формулы по реестру TASK_FLOW.
  if (deal.stage !== "Успех" && deal.stage !== "Провал") {
    await taskFlow.onDealStage(deal, who);
  }

  const withoutBarcode = deal.items.filter(
    (item) =>
      !item.barcode?.trim() &&
      item.productId !== "cert" &&
      item.productId !== "rent"
  );
  if (withoutBarcode.length > 0) {
    await taskService.createTask({
      kind: "barcode",
      dealNumber: deal.number,
      store: deal.store,
      assigneeRole: "consultant",
      title: taskTitle("barcode", deal.number, `${withoutBarcode.length} поз.`),
      idempotencyKey: `barcode:${deal.id}`,
      metadata: { positions: withoutBarcode.map((item) => item.name), route: deal.store },
    }, who);
  }
}

const CERT_ISSUE_STAGES = new Set(["Сертификат оплачен", "Сертификат продан", "Успех"]);

async function issueCertificateIfNeeded(deal: Deal): Promise<void> {
  if (deal.kind !== "cert_plastic" && deal.kind !== "cert_digital") return;
  if (!CERT_ISSUE_STAGES.has(deal.stage)) return;
  let number = deal.certificateNumber?.trim();
  // Номинал: явная строка «Сертификат №…» или total заявки.
  const certLine = deal.items.find((i) => i.productId === "cert" || /^Сертификат\s*№/i.test(i.name));
  const nominal = Math.max(0, certLine?.price ?? deal.total ?? 0);
  if (nominal <= 0) return;

  const buildCertificate = (certificateNumber: string) => ({
    number: certificateNumber,
    nominal,
    balance: nominal,
    status: "active" as const,
    type: deal.kind === "cert_digital" ? "digital" as const : "plastic" as const,
    guestName: deal.guestName,
    buyerDealNumber: deal.number,
    issuedAt: (deal.receivedAt || deal.createdAt || new Date().toISOString()).slice(0, 10),
    validUntil: (deal.validUntil || "").slice(0, 10) || addYearFromToday(),
  });
  if (deal.kind === "cert_digital") {
    // Номер, сгенерированный кассой заранее (кнопка в форме), сохраняем как есть —
    // консультант мог уже продиктовать его клиенту. При коллизии подбираем новый.
    let issued = number ? await certs.issue(buildCertificate(number)) : false;
    for (let attempt = 0; attempt < 30 && !issued; attempt++) {
      number = await certs.generateDigitalNumber();
      issued = await certs.issue(buildCertificate(number));
    }
    if (!issued || !number) throw new Error("Не удалось выпустить уникальный электронный сертификат");
    deal.certificateNumber = number;
  } else {
    if (!number) throw new Error("Не указан номер пластикового сертификата");
    if (!(await certs.issue(buildCertificate(number)))) {
      throw new Error(`Сертификат №${number} уже существует`);
    }
  }
  broadcast("certificate.updated", { number });
}

async function redeemCertificatesFromPayments(deal: Deal): Promise<void> {
  const redemptions = deal.payments
    .filter((payment) => paymentKind(payment.methodId) === "certificate")
    .map((payment) => ({
      number: payment.certificateNumber?.trim() ?? "",
      amountRub: payment.amount,
    }));
  if (redemptions.some((row) => !row.number || row.amountRub <= 0)) {
    throw new Error("Для оплаты сертификатом нужны номер и положительная сумма");
  }
  if (redemptions.length > 0) await certs.redeemMany(redemptions);
}

function addYearFromToday(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() + 1);
  return d.toISOString().slice(0, 10);
}

/** Проставляет «Был возврат» / «Был обмен» на исходной сделке amo (только API-поле). */
async function markOriginalReturnFlags(
  linkedNumber: number,
  kind: "refund" | "exchange"
): Promise<void> {
  const local = await deals.getByNumber(linkedNumber);
  const leadId = local?.amoLeadId ?? linkedNumber;
  invalidateLeadFieldCache();
  const fieldName =
    kind === "refund" ? AMO_LEAD_FIELDS.wasRefund : AMO_LEAD_FIELDS.wasExchange;
  const fieldId = await getFieldIdByName(fieldName);
  if (!fieldId) return;
  await amo.updateLead(leadId, {
    customFields: [{ field_id: fieldId, values: [{ value: true }] }],
  });
}

/**
 * Сделка и её этап важнее кастом-полей: если amoCRM отбивает значение поля
 * (чужой справочник, новое поле), повторяем запрос без полей — иначе Успех
 * не уезжает в CRM из-за одного некорректного значения.
 */
async function createLeadResilient(input: amo.CreateLeadInput): Promise<amo.AmoLead> {
  try {
    return await amo.createLead(input);
  } catch (err) {
    if (!input.customFields?.length) throw err;
    console.warn(`[amo] createLead без кастом-полей после отказа: ${(err as Error).message}`);
    return amo.createLead({ ...input, customFields: [] });
  }
}

async function updateLeadResilient(
  leadId: number,
  patch: { statusId?: number; price?: number; customFields?: Array<{ field_id: number; values: Array<{ value: unknown }> }> }
): Promise<void> {
  try {
    await amo.updateLead(leadId, patch);
  } catch (err) {
    if (!patch.customFields?.length) throw err;
    console.warn(`[amo] updateLead #${leadId} без кастом-полей после отказа: ${(err as Error).message}`);
    await amo.updateLead(leadId, { ...patch, customFields: [] });
  }
}

async function ensureAmoLead(deal: Deal): Promise<number | null> {
  try {
    let contactId: number | undefined;
    if (deal.clientPhone) {
      const existing = await amo.findContactByPhone(deal.clientPhone);
      const contact = existing ?? (await amo.createContact(deal.clientName, deal.clientPhone, deal.email));
      contactId = contact.id;
    } else if (deal.clientName && deal.clientName !== "—") {
      const contact = await amo.createContact(deal.clientName);
      contactId = contact.id;
    }

    // Компания (kind=company): найти/создать сущность и привязать к сделке.
    const companyId = (await ensureLeadCompany(deal).catch(() => null)) ?? undefined;

    const pipelineId = COMPLAINTS_KINDS.has(deal.kind) ? AMO_PIPELINE_COMPLAINTS : AMO_PIPELINE_SALES;
    const statusId = (await getStatusId(pipelineId, deal.stage)) ?? undefined;
    const customFields = await buildLeadCustomFields(deal).catch(() => []);
    const leadName = `${deal.clientName} · #${deal.number}`;

    // Жалобы / возвраты / обмены — всегда новая сделка.
    if (pipelineId === AMO_PIPELINE_COMPLAINTS) {
      const lead = await createLeadResilient({
        name: leadName,
        price: Math.abs(deal.total),
        pipelineId,
        statusId,
        contactId,
        companyId,
        customFields,
      });
      return lead.id;
    }

    // Продажи: при наличии телефона + контакта — обновить единственную открытую сделку,
    // кроме оплаченных сертификатов, иначе создать новую.
    if (contactId && deal.clientPhone) {
      const certStatusIds = (
        await Promise.all([
          getStatusId(AMO_PIPELINE_SALES, "Сертификат оплачен"),
          getStatusId(AMO_PIPELINE_SALES, "Сертификат продан"),
        ])
      ).filter((id): id is number => Boolean(id));
      const leadIds = await amo.listLeadIdsByContact(contactId);
      const linked = await amo.getLeadsByIds(leadIds);
      const open = amo.pickOpenSalesLead(linked, AMO_PIPELINE_SALES, {
        excludeStatusIds: certStatusIds,
        closedOrJunkIds: CLOSED_OR_JUNK_STATUS_IDS,
      });
      if (open) {
        const matching = linked.filter(
          (l) =>
            l.pipeline_id === AMO_PIPELINE_SALES &&
            !CLOSED_OR_JUNK_STATUS_IDS.has(l.status_id) &&
            !certStatusIds.includes(l.status_id)
        );
        if (matching.length > 1) {
          console.warn(
            `[ensureAmoLead] контакт ${contactId}: ${matching.length} открытых в Продажах, берём #${open.id} (updated_at)`
          );
        }
        await updateLeadResilient(open.id, { statusId, price: deal.total, customFields });
        if (companyId) {
          await amo.linkCompanyToLead(open.id, companyId).catch(() => {});
        }
        await amo
          .addLeadNote(open.id, `Касса #${deal.number} · ${deal.kind} · ${deal.stage}`)
          .catch(() => {});
        return open.id;
      }
    }

    const lead = await createLeadResilient({
      name: leadName,
      price: deal.total,
      pipelineId: AMO_PIPELINE_SALES,
      statusId,
      contactId,
      companyId,
      customFields,
    });
    return lead.id;
  } catch (err) {
    // amo недоступен — не блокируем продажу (локально сохраним, синхронизируем позже),
    // но причину пишем в лог: молчаливый null раньше маскировал отказы синка.
    console.error(
      `[ensureAmoLead] заявка #${deal.number}: ${(err as Error).message}`,
      (err as { body?: unknown }).body ?? ""
    );
    return null;
  }
}

/**
 * Примечание в сделке amoCRM по сарафану: кто направил, телефон, что стало
 * с бонусом 1000 ₽ (списан в чеке или уходит переводом). Пишется один раз —
 * отметка `saryNotedAt` в заявке защищает от дублей при повторных сохранениях.
 */
export async function noteSaryInAmo(deal: Deal, who: string): Promise<void> {
  const phone = deal.saryPhone?.trim();
  const bonus = deal.saryBonus ?? 0;
  if (!phone && bonus <= 0) return;
  if (deal.saryNotedAt || !deal.amoLeadId) return;

  const friend = deal.saryClient?.trim();
  const lines = [
    `Касса #${deal.number}: источник «Сарафан»`,
    friend && phone ? `Направил: ${friend} · ${phone}` : friend || phone ? `Направил: ${friend || phone}` : null,
    bonus > 0
      ? `Бонус ${bonus} ₽ списан из чека — к оплате ${deal.total} ₽ вместо ${deal.subtotal ?? deal.total} ₽`
      : `Бонус ${SARY_BONUS} ₽ — задача колл-менеджеру на перевод`,
    `Провёл: ${who} · ${new Date().toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}`,
  ].filter(Boolean) as string[];

  await amo.addLeadNote(deal.amoLeadId, lines.join("\n"));
  deal.saryNotedAt = new Date().toISOString();
  await deals.persist(deal);
}

/**
 * Ручной перезалив заявки в amoCRM (POST /admin/resync/deal/:number) — для тех
 * заявок, что застряли локально, пока синк падал.
 */
export async function resyncToAmo(number: number, who: string): Promise<Deal | null> {
  const deal = await deals.getByNumber(number);
  if (!deal) return null;
  // Уже привязанную сделку не ищем заново: её этап (Успех/Провал) закрыт,
  // поиск открытых лидов создал бы дубль.
  const amoLeadId = deal.amoLeadId ?? (await ensureAmoLead(deal));
  if (!amoLeadId) {
    return deals.markSync(deal, "failed", "amoCRM: сделка не создана — см. логи api");
  }
  deal.amoLeadId = amoLeadId;
  const pipelineId = COMPLAINTS_KINDS.has(deal.kind) ? AMO_PIPELINE_COMPLAINTS : AMO_PIPELINE_SALES;
  const statusId = (await getStatusId(pipelineId, deal.stage)) ?? undefined;
  const customFields = await buildLeadCustomFields(deal).catch(() => []);
  await updateLeadResilient(amoLeadId, { statusId, price: Math.abs(deal.total), customFields });
  await noteSaryInAmo(deal, who).catch(() => {});
  return deals.markSync(deal, "synced");
}

async function attachPhotosToLead(amoLeadId: number, photos: PhotoInput[]): Promise<void> {
  for (const p of photos) {
    await amo.attachPhotoToLead(amoLeadId, p.filename, p.contentBase64).catch(() => {
      // сеть/лимиты amo — фото не критично для проведения заявки
    });
  }
}

async function resolveMsParty(deal: Deal): Promise<{
  org: { meta: ms.MsMeta };
  agent: { meta: ms.MsMeta };
  warehouse: { meta: ms.MsMeta };
}> {
  const org = await getMsRef("organization", "default");
  if (!org) throw new Error("МойСклад: не резолвлена организация (запусти bootstrap)");

  const agentRef = await getMsRef("counterparty", "retail");
  let agent = agentRef;
  if (deal.clientPhone) {
    const found = await ms.findCounterpartyByName(deal.clientName).catch((error) => {
      if (ms.isMoySkladAuthError(error)) throw error;
      return null;
    });
    agent = found ?? (await ms.createCounterparty(deal.clientName, deal.clientPhone).catch((error) => {
      if (ms.isMoySkladAuthError(error)) throw error;
      return agentRef;
    }));
  }
  if (!agent) throw new Error("МойСклад: не резолвлен контрагент");

  const warehouse = await getWarehouseForStore(deal.store);
  if (!warehouse) throw new Error(`МойСклад: не найден склад для шоурума «${deal.store}»`);

  return { org, agent, warehouse };
}

async function buildMsPositions(
  items: { productId: string; qty: number; price: number }[]
): Promise<{ positions: ms.SalePosition[]; stockDeltas: { productMsId: string; qty: number }[] }> {
  const positions: ms.SalePosition[] = [];
  const stockDeltas: { productMsId: string; qty: number }[] = [];
  for (const item of items) {
    const a = await resolveAssortment(item.productId);
    if (!a) continue;
    positions.push({
      assortmentHref: a.href,
      assortmentType: a.type,
      quantity: item.qty,
      price: item.price,
    });
    const productMsId = extractIdFromHref(a.href);
    if (productMsId) stockDeltas.push({ productMsId, qty: item.qty });
  }
  return { positions, stockDeltas };
}

async function fulfillMoysklad(deal: Deal, photos: PhotoInput[], who: string): Promise<void> {
  const { org, agent, warehouse } = await resolveMsParty(deal);

  const { positions, stockDeltas } = await buildMsPositions(dealItemsToPositions(deal));

  const order = await ms.createCustomerOrder({
    organization: org.meta,
    agent: agent.meta,
    store: warehouse.meta,
    name: `Касса #${deal.number}`,
    description: `Продажа из кассы MANSBAND, шоурум ${deal.store}`,
    positions,
  });
  deal.msOrderId = order.id;

  const demand = await ms.createDemand({
    organization: org.meta,
    agent: agent.meta,
    store: warehouse.meta,
    orderMeta: order.meta,
    positions,
    description: `Отгрузка по заказу #${deal.number}`,
  });
  deal.msDemandId = demand.id;

  await decrementLocalStock(warehouse.meta.href, stockDeltas).catch(() => {});
  await processPayments(deal, org.meta, agent.meta, order.meta, who);
  await processCashOuts(deal, org.meta, agent.meta, who);

  for (const target of ["shipment", "order", "return"] as const) {
    const group = photos.filter((p) => p.target === target);
    if (group.length === 0) continue;
    const entityType = target === "order" ? "customerorder" : "demand";
    const entityId = target === "order" ? order.id : demand.id;
    await attachPhotos(entityType, entityId, group);
  }
}

/** Возврат → salesreturn; обмен → salesreturn + отгрузка (demand). Исходную сделку не трогаем. */
async function fulfillReturnOrExchange(deal: Deal, photos: PhotoInput[], who: string): Promise<void> {
  const { org, agent, warehouse } = await resolveMsParty(deal);

  const toPos = (items: Deal["items"]) =>
    items.map((i) => ({ productId: i.productId, qty: i.qty, price: toKopecks(Math.abs(i.price)) }));

  const ret = await buildMsPositions(toPos(returnLineItems(deal)));
  let returnId: string | undefined;

  if (ret.positions.length > 0) {
    const doc = await ms.createSalesReturn({
      organization: org.meta,
      agent: agent.meta,
      store: warehouse.meta,
      positions: ret.positions,
      description: `${deal.kind === "exchange" ? "Обмен" : "Возврат"} касса #${deal.number}` +
        (deal.linkedDealNumber ? ` ← заявка #${deal.linkedDealNumber}` : ""),
    });
    returnId = doc.id;
    deal.msOrderId = doc.id; // зеркало: id возврата МС (отдельного поля нет)
    await incrementLocalStock(warehouse.meta.href, ret.stockDeltas).catch(() => {});
  }

  if (deal.kind === "exchange") {
    const sale = await buildMsPositions(toPos(saleLineItems(deal)));
    if (sale.positions.length > 0) {
      const demand = await ms.createDemand({
        organization: org.meta,
        agent: agent.meta,
        store: warehouse.meta,
        positions: sale.positions,
        description: `Обмен · отгрузка касса #${deal.number}`,
      });
      deal.msDemandId = demand.id;
      await decrementLocalStock(warehouse.meta.href, sale.stockDeltas).catch(() => {});
      await processPayments(deal, org.meta, agent.meta, demand.meta, who);
    }
  }

  // Возврат наличкой / картой консультанта — в расход кассы (и для чистого возврата, и для обмена).
  await processCashOuts(deal, org.meta, agent.meta, who);

  for (const p of photos) {
    const target = p.target ?? "return";
    if (target === "return" && returnId) {
      await attachPhotos("salesreturn", returnId, [p]);
    } else if ((target === "shipment" || target === "order") && deal.msDemandId) {
      await attachPhotos("demand", deal.msDemandId, [p]);
    } else if (deal.amoLeadId) {
      await attachPhotosToLead(deal.amoLeadId, [p]);
    }
  }
}

// Локальный декремент остатков по складу продажи. Минус сохраняем и показываем:
// это сигнал расхождения для консультанта, а не значение, которое можно скрыть.
// Если строки ещё нет (товар не синкнулся) — UPDATE ничего не тронет,
// ближайшая сверка с МойСклад подтянет корректное значение.
async function decrementLocalStock(
  warehouseHref: string,
  items: { productMsId: string; qty: number }[]
): Promise<void> {
  const warehouseMsId = extractIdFromHref(warehouseHref);
  if (!warehouseMsId || items.length === 0) return;
  for (const it of items) {
    if (it.qty <= 0) continue;
    await db
      .update(stock)
      .set({ quantity: sql`${stock.quantity} - ${it.qty}`, updatedAt: new Date() })
      .where(and(eq(stock.productMsId, it.productMsId), eq(stock.warehouseMsId, warehouseMsId)));
  }
}

async function incrementLocalStock(
  warehouseHref: string,
  items: { productMsId: string; qty: number }[]
): Promise<void> {
  const warehouseMsId = extractIdFromHref(warehouseHref);
  if (!warehouseMsId || items.length === 0) return;
  for (const it of items) {
    if (it.qty <= 0) continue;
    await db
      .update(stock)
      .set({ quantity: sql`${stock.quantity} + ${it.qty}`, updatedAt: new Date() })
      .where(and(eq(stock.productMsId, it.productMsId), eq(stock.warehouseMsId, warehouseMsId)));
  }
}

async function processPayments(
  deal: Deal,
  org: ms.MsMeta,
  agent: ms.MsMeta,
  linkMeta: ms.MsMeta | undefined,
  who: string
): Promise<void> {
  const account = await getMsRef("account", "default");
  for (const p of deal.payments) {
    const kind = paymentKind(p.methodId);
    const sum = toKopecks(p.amount);
    if (sum <= 0) continue;

    let msPaymentId: string | undefined;
    try {
      if (kind === "cash") {
        const doc = await ms.createCashIn({
          organization: org,
          agent,
          sum,
          orderMeta: linkMeta,
          description: `Касса #${deal.number} (${paymentMethodLabel(p.methodId)})`,
        });
        msPaymentId = doc.id;
      } else if (kind === "certificate") {
        // оплата сертификатом гасится в ledger/сертификатах, не как денежный платёж МС
      } else {
        const doc = await ms.createPaymentIn({
          organization: org,
          agent,
          sum,
          organizationAccount: account?.meta,
          orderMeta: linkMeta,
          description: `Касса #${deal.number} (${paymentMethodLabel(p.methodId)})`,
        });
        msPaymentId = doc.id;
      }
    } catch (error) {
      if (ms.isMoySkladAuthError(error)) throw error;
      // платёж в МС не прошёл — фиксируем в ledger, разберём при сверке
    }

    await recordLedger(deal.number, p, kind, msPaymentId, who);
  }
}

/**
 * Списание налички магазина в МойСклад (cashout) + ledger:
 * - сдача «Выдано клиенту» → сумма (сдача − чаевые);
 * - чаевые «Забрал наличкой» (tips без tipsDestination) → сумма чаевых.
 * «Получить от Mansband» не трогает кассу — перевод через Эдвина.
 */
async function processCashOuts(
  deal: Deal,
  org: ms.MsMeta,
  agent: ms.MsMeta,
  who: string
): Promise<void> {
  const change = Math.max(0, (deal.paid ?? 0) - (deal.total ?? 0));
  const tips = deal.tips ?? 0;
  const toClient = Math.max(0, change - tips);

  // Раскладка выплат по способам (правки 05.08, п.5). Старые заявки без
  // массивов читаются как одна строка по прежним changeMethodId / tipsMethodId.
  const changeRows = payoutRows(
    deal.changePayouts,
    deal.changeStatus === "issued" ? toClient : 0,
    deal.changeMethodId
  );
  const tipsRows = payoutRows(
    deal.tipsPayouts,
    tips > 0 && !deal.tipsDestination ? tips : 0,
    deal.tipsMethodId
  );
  // Возврат клиенту: часть могли отдать из кассы, часть переводит Эдвин.
  const refundRows = payoutRows(
    deal.returnPayouts,
    deal.returnStatus === "issued" ? refundClientAmount(deal) : 0,
    undefined
  );

  let cashOutRub = 0;
  const parts: string[] = [];
  for (const row of changeRows) {
    if (paymentKind(row.methodId) !== "cash") continue;
    cashOutRub += row.amount;
    parts.push(`сдача клиенту ${row.amount}₽`);
  }
  for (const row of tipsRows) {
    if (paymentKind(row.methodId) !== "cash") continue;
    cashOutRub += row.amount;
    parts.push(`чаевые наличкой ${row.amount}₽`);
  }
  for (const row of refundRows) {
    if (paymentKind(row.methodId) !== "cash") continue;
    cashOutRub += row.amount;
    parts.push(`возврат клиенту ${row.amount}₽`);
  }
  await recordNonCashPayouts(deal, who, {
    change: changeRows.filter((row) => paymentKind(row.methodId) !== "cash"),
    tips: tipsRows.filter((row) => paymentKind(row.methodId) !== "cash"),
    refund: refundRows.filter((row) => paymentKind(row.methodId) !== "cash"),
  });
  if (cashOutRub <= 0) return;

  const sum = toKopecks(cashOutRub);
  let msPaymentId: string | undefined;
  try {
    const doc = await ms.createCashOut({
      organization: org,
      agent,
      sum,
      description: `Касса #${deal.number}: ${parts.join(", ")}`,
    });
    msPaymentId = doc.id;
  } catch (error) {
    if (ms.isMoySkladAuthError(error)) throw error;
    // не блокируем продажу — зафиксируем в ledger без ms id
  }

  await db
    .insert(ledger)
    .values({
      dealNumber: deal.number,
      methodId: "cash_out",
      kind: "cash_out",
      amount: -sum, // минус = уход из кассы
      msPaymentId: msPaymentId ?? null,
      idempotencyKey: `${deal.number}:cash_out:${cashOutRub}`,
      createdBy: who,
    })
    .onConflictDoNothing();
}

/**
 * Строки выплаты, которые фактически уходят из кассы магазина. Строка
 * «Переведёт Mansband» здесь не участвует — она попадает в очередь Эдвина.
 * Старые заявки без массива читаются как одна строка.
 */
function payoutRows(
  rows: Payout[] | undefined,
  legacyAmount: number,
  legacyMethodId: string | undefined
): Payout[] {
  if (rows && rows.length > 0) return selfPayouts(rows);
  if (legacyAmount <= 0) return [];
  return [{ methodId: legacyMethodId ?? "cash", amount: legacyAmount }];
}

/**
 * Сдача или чаевые, которые консультант выдал переводом, а не из ящика:
 * в кассу МС это не попадает, но в ledger должно быть видно для сверки.
 */
async function recordNonCashPayouts(
  deal: Deal,
  who: string,
  payout: { change: Payout[]; tips: Payout[]; refund: Payout[] }
): Promise<void> {
  const rows = [
    ...payout.change.map((row) => ({ kind: "change_out", ...row })),
    ...payout.tips.map((row) => ({ kind: "tips_out", ...row })),
    ...payout.refund.map((row) => ({ kind: "refund_out", ...row })),
  ].filter((r) => r.amount > 0);
  for (const row of rows) {
    await db
      .insert(ledger)
      .values({
        dealNumber: deal.number,
        methodId: row.methodId,
        kind: row.kind,
        amount: -toKopecks(row.amount),
        idempotencyKey: `${deal.number}:${row.kind}:${row.methodId}:${row.amount}`,
        createdBy: who,
      })
      .onConflictDoNothing();
  }
}

async function recordLedger(
  dealNumber: number,
  payment: Payment,
  kind: string,
  msPaymentId: string | undefined,
  who: string
): Promise<void> {
  await db
    .insert(ledger)
    .values({
      dealNumber,
      methodId: payment.methodId,
      kind,
      amount: toKopecks(payment.amount),
      msPaymentId: msPaymentId ?? null,
      idempotencyKey: `${dealNumber}:${payment.id}`,
      createdBy: who,
    })
    .onConflictDoNothing();
}

// Полный writeback: пишет ВСЕ значимые поля заявки в сделку amoCRM (не только
// статус оплаты/ссылки МойСклад) — см. Фазу B плана «MANSBAND касса фиксы».
async function writeback(amoLeadId: number, deal: Deal): Promise<void> {
  try {
    const customFields = await buildLeadCustomFields(deal);
    if (customFields.length) await amo.updateLead(amoLeadId, { customFields });
  } catch {
    // writeback не критичен для проведения; повторим при следующей синхронизации
  }
}
