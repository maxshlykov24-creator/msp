import type { FastifyInstance } from "fastify";
import { redeemCertificateSchema } from "@kassa/shared";
import * as certs from "../services/certificates.js";

export default async function certificatesRoutes(app: FastifyInstance) {
  app.get("/certificates", { preHandler: [app.authenticate] }, async () => certs.list());

  app.post("/certificates/redeem", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = redeemCertificateSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const updated = await certs.redeem(parsed.data.number, parsed.data.amount);
    if (!updated) return reply.code(404).send({ message: "Сертификат не найден" });
    return updated;
  });
}
