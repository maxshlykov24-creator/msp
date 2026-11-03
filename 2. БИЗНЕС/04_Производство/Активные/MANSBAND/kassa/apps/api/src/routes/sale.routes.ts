import { withOperationLock } from "../lib/idempotency.js";
import { validateReturnItems } from "./deals.routes.js";
import type { FastifyInstance } from "fastify";
import { createSaleSchema, financialDealError, cartLineTotal } from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import * as sale from "../services/sale.js";
import { notifyDeal } from "../services/notify.js";
import * as deals from "../services/deals.js";

// Явный эндпоинт проведения продажи (schema-validated, идемпотентный, с фото).
// Альтернатива POST /deals; полезен для интеграций и строгого контракта.

export default async function saleRoutes(app: FastifyInstance) {
  app.post("/sale", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = createSaleSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const input = parsed.data;

    const total = input.items.reduce((acc, i) => acc + cartLineTotal(i) * (i.isReturn || input.kind === "refund" ? -1 : 1), 0) + (input.atelierAmount ?? 0) + (input.deliveryAmount ?? 0) - (input.saryBonus ?? 0);
    const paid = input.payments.reduce((acc, p) => acc + p.amount, 0);

    const deal: Deal = {
      id: input.idempotencyKey,
      number: 0,
      createdAt: new Date().toISOString(),
      funnel: input.funnel,
      kind: input.kind as Deal["kind"],
      consultant: input.consultant,
      referredBy: input.referredBy,
      callManager: input.callManager,
      clientName: input.clientName,
      clientPhone: input.clientPhone ?? "",
      email: input.email || undefined,
      store: input.store,
      channel: input.channel,
      purpose: input.purpose,
      comment: input.comment,
      saryPhone: input.saryPhone,
      saryClient: input.saryClient,
      saryBonus: input.saryBonus,
      items: input.items,
      payments: input.payments,
      stage: input.fulfill ? "Успех" : "Новая заявка",
      guestName: input.guestName,
      recipientPhone: input.recipientPhone,
      certificateGreeting: input.certificateGreeting,
      certificateNumber: input.certificateNumber,
      rentalFrom: input.rentalFrom,
      rentalTo: input.rentalTo,
      reservedUntil: input.reservedUntil,
      linkedDealNumber: input.linkedDealNumber,
      companyName: input.companyName,
      invoiceNo: input.invoiceNo,
      invoiceDate: input.invoiceDate,
      invoiceStatus: input.invoiceStatus,
      atelierAmount: input.atelierAmount,
      deliveryAmount: input.deliveryAmount,
      closingDocumentsRequired: input.closingDocumentsRequired,
      issued: input.issued,
      meetingDate: input.meetingDate,
      rentalStatus: input.rentalStatus,
      rentalIssuedAt: input.rentalIssuedAt,
      rentalReturnedAt: input.rentalReturnedAt,
      rentalDeposit: input.rentalDeposit,
      idempotencyKey: input.idempotencyKey,
      total,
      paid,
    };

    if (deal.kind === "company" && input.fulfill && !deal.issued) {
      return reply.code(400).send({ message: "Продажа компании проводится только после фактической выдачи" });
    }
    const source = deal.linkedDealNumber ? (await deals.getByNumber(deal.linkedDealNumber)) ?? (await deals.getByAmoLeadId(deal.linkedDealNumber)) : null;
    return withOperationLock(["refund", "exchange"].includes(deal.kind) && source ? `return-source:${source.id}` : `create:${deal.id}`, async () => {
    const financialError = financialDealError(deal, input.photos?.length ?? 0);
    if (financialError) return reply.code(400).send({ message: financialError });
    const returnError = await validateReturnItems(deal);
    if (returnError) return reply.code(400).send({ message: returnError });
    const { number: _number, createdAt, ...requested } = deal;
    const reserved = await deals.reserveServerDeal({ ...requested, createdAt }, input.idempotencyKey);
    if (reserved.duplicate) return sale.synchronizeFinancialDeal(reserved.deal, req.user.name, input.photos);
    try {
      const saved = await sale.processSale(reserved.deal, {
        fulfill: input.fulfill,
        photos: input.photos,
        who: req.user.name,
      });
      const synced = saved.amoLeadId
        ? await deals.markSync(saved, "synced")
        : await deals.markSync(saved, "failed", "amoCRM: сделка не создана — см. логи api");
      await notifyDeal(synced, synced.stage === "Успех" ? "success" : "created").catch(() => {});
      return synced;
    } catch (error) {
      const message = (error as Error).message;
      await deals.markSync((await deals.getByNumber(reserved.deal.number)) ?? reserved.deal, "failed", message).catch(() => {});
      return reply.code(502).send({ message, number: reserved.deal.number, syncStatus: "failed" });
    }
    });
  });
}
