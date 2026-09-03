import { and, eq, sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { ledger, stock } from "../db/schema.js";
import * as amo from "../clients/amo.js";
import * as ms from "../clients/ms.js";
import * as deals from "./deals.js";
import { attachPhotos } from "./files.js";
import { resolveAssortment } from "./catalog.js";
import { getMsRef, getWarehouseForStore, getStatusId, extractIdFromHref } from "./bootstrap.js";
import { buildLeadCustomFields, ensureLeadCompany, paymentStatusLabel } from "./amoMapping.js";
import { withIdempotency } from "../lib/idempotency.js";
import { toKopecks } from "../lib/money.js";
import { AMO_PIPELINE_SALES } from "@kassa/shared";
import type { Deal, Payment } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

export interface PhotoInput {
  filename: string;
  contentBase64: string;
  // Не задан → фото уходит в сделку amoCRM (нет складского документа МойСклад,
  // напр. аренда/брак/сертификат). Задан → к конкретному документу МС при fulfill.
  target?: "shipment" | "order" | "return";
}

// Классификация способа оплаты по methodId (наличные / безнал / счёт / сертификат).
function paymentKind(methodId: string): "cash" | "card" | "account" | "certificate" {
  const m = methodId.toLowerCase();
  if (m.startsWith("cash") || m.includes("налич")) return "cash";
  if (m.startsWith("rs") || m.includes("account") || m.includes("счет") || m.includes("счёт")) return "account";
  if (m.startsWith("cert")) return "certificate";
  return "card";
}

function dealItemsToPositions(deal: Deal) {
  return deal.items
    .filter((i) => i.qty > 0)
    .map((i) => ({ productId: i.productId, qty: i.qty, price: toKopecks(i.price) }));
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

    // 2. МойСклад (только при полной оплате / fulfill)
    if (opts.fulfill) {
      await fulfillMoysklad(enriched, opts.photos ?? [], opts.who);
    } else if (amoLeadId && opts.photos?.length) {
      // Нет складского документа (аренда/брак и т.п.) — фотофиксация уходит в сделку amoCRM.
      await attachPhotosToLead(amoLeadId, opts.photos);
    }

    // 3. writeback статуса оплаты + ссылок в amo
    if (amoLeadId) await writeback(amoLeadId, enriched);

    // 4. локальное зеркало
    await deals.persist(enriched);
    broadcast("deal.created", { number: enriched.number });
    return enriched;
  });
  return result;
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

    const statusId = (await getStatusId(AMO_PIPELINE_SALES, deal.stage)) ?? undefined;
    const customFields = await buildLeadCustomFields(deal).catch(() => []);
    const lead = await amo.createLead({
      name: `${deal.clientName} · #${deal.number}`,
      price: deal.total,
      pipelineId: AMO_PIPELINE_SALES,
      statusId,
      contactId,
      companyId,
      customFields,
    });
    return lead.id;
  } catch {
    return null; // amo недоступен — не блокируем продажу (локально сохраним, синхронизируем позже)
  }
}

async function attachPhotosToLead(amoLeadId: number, photos: PhotoInput[]): Promise<void> {
  for (const p of photos) {
    await amo.attachPhotoToLead(amoLeadId, p.filename, p.contentBase64).catch(() => {
      // сеть/лимиты amo — фото не критично для проведения заявки
    });
  }
}

async function fulfillMoysklad(deal: Deal, photos: PhotoInput[], who: string): Promise<void> {
  const org = await getMsRef("organization", "default");
  if (!org) throw new Error("МойСклад: не резолвлена организация (запусти bootstrap)");

  // Контрагент: розница (если нет телефона) или по имени.
  const agentRef = await getMsRef("counterparty", "retail");
  let agent = agentRef;
  if (deal.clientPhone) {
    const found = await ms.findCounterpartyByName(deal.clientName).catch(() => null);
    agent = found ?? (await ms.createCounterparty(deal.clientName, deal.clientPhone).catch(() => agentRef));
  }
  if (!agent) throw new Error("МойСклад: не резолвлен контрагент");

  const warehouse = await getWarehouseForStore(deal.store);
  if (!warehouse) throw new Error(`МойСклад: не найден склад для шоурума «${deal.store}»`);

  // Позиции из каталога (только то, что есть в МойСклад).
  const positions: ms.SalePosition[] = [];
  const stockDecrements: { productMsId: string; qty: number }[] = [];
  for (const item of dealItemsToPositions(deal)) {
    const a = await resolveAssortment(item.productId);
    if (!a) continue; // услуга/неизвестный товар — пропускаем в складских позициях
    positions.push({ assortmentHref: a.href, assortmentType: a.type, quantity: item.qty, price: item.price });
    const productMsId = extractIdFromHref(a.href);
    if (productMsId) stockDecrements.push({ productMsId, qty: item.qty });
  }

  // Заказ → отгрузка
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

  // Оптимистичное списание локального зеркала остатков (МС — источник истины,
  // сверка МС → сервер раз в STOCK_SYNC_INTERVAL_MIN подменит любой дрейф).
  // Не роняем продажу при ошибке — остаток самолечится при ближайшей сверке.
  await decrementLocalStock(warehouse.meta.href, stockDecrements).catch(() => {});

  // Платежи по способам
  await processPayments(deal, org.meta, agent.meta, order.meta, who);

  // Фотофиксации → к нужному документу
  for (const target of ["shipment", "order", "return"] as const) {
    const group = photos.filter((p) => p.target === target);
    if (group.length === 0) continue;
    const entityType = target === "order" ? "customerorder" : "demand";
    const entityId = target === "order" ? order.id : demand.id;
    await attachPhotos(entityType, entityId, group);
  }
}

// Локальный декремент остатков по складу продажи. GREATEST(...,0) — не уходим в минус
// в зеркале; если строки ещё нет (товар не синкнулся) — UPDATE ничего не тронет,
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
      .set({ quantity: sql`GREATEST(${stock.quantity} - ${it.qty}, 0)`, updatedAt: new Date() })
      .where(and(eq(stock.productMsId, it.productMsId), eq(stock.warehouseMsId, warehouseMsId)));
  }
}

async function processPayments(
  deal: Deal,
  org: ms.MsMeta,
  agent: ms.MsMeta,
  orderMeta: ms.MsMeta,
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
        const doc = await ms.createCashIn({ organization: org, agent, sum, orderMeta, description: `Касса #${deal.number} (${p.methodId})` });
        msPaymentId = doc.id;
      } else if (kind === "certificate") {
        // оплата сертификатом гасится в ledger/сертификатах, не как денежный платёж МС
      } else {
        const doc = await ms.createPaymentIn({
          organization: org,
          agent,
          sum,
          organizationAccount: account?.meta,
          orderMeta,
          description: `Касса #${deal.number} (${p.methodId})`,
        });
        msPaymentId = doc.id;
      }
    } catch {
      // платёж в МС не прошёл — фиксируем в ledger, разберём при сверке
    }

    await recordLedger(deal.number, p, kind, msPaymentId, who);
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
