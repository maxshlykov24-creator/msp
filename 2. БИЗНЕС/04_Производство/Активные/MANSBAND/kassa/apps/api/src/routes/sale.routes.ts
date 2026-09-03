import type { FastifyInstance } from "fastify";
import { createSaleSchema } from "@kassa/shared";
import type { Deal } from "@kassa/shared";
import * as sale from "../services/sale.js";
import { notifySale } from "../services/notify.js";

// Явный эндпоинт проведения продажи (schema-validated, идемпотентный, с фото).
// Альтернатива POST /deals; полезен для интеграций и строгого контракта.

export default async function saleRoutes(app: FastifyInstance) {
  app.post("/sale", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = createSaleSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const input = parsed.data;

    const total = input.items.reduce((acc, i) => acc + i.price * i.qty, 0);
    const paid = input.payments.reduce((acc, p) => acc + p.amount, 0);

    const deal: Deal = {
      id: input.idempotencyKey,
      number: Date.now() % 1_000_000,
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
      items: input.items,
      payments: input.payments,
      stage: input.fulfill ? "Успех" : "Новая заявка",
      guestName: input.guestName,
      certificateNumber: input.certificateNumber,
      rentalFrom: input.rentalFrom,
      rentalTo: input.rentalTo,
      reservedUntil: input.reservedUntil,
      linkedDealNumber: input.linkedDealNumber,
      companyName: input.companyName,
      invoiceNo: input.invoiceNo,
      total,
      paid,
    };

    const saved = await sale.processSale(deal, {
      fulfill: input.fulfill,
      photos: input.photos,
      who: req.user.name,
    });
    if (input.fulfill) await notifySale(saved);
    return saved;
  });
}
