import type { FastifyInstance } from "fastify";
import { certificateImportSchema, redeemCertificateSchema } from "@kassa/shared";
import * as certs from "../services/certificates.js";
import { appendAudit } from "../services/audit.js";

export default async function certificatesRoutes(app: FastifyInstance) {
  app.get("/certificates", { preHandler: [app.authenticate] }, async () => certs.list());

  // Предгенерация 6-значного номера для электронного сертификата: консультант
  // видит номер до сохранения заявки и может продиктовать его клиенту.
  app.post("/certificates/next-number", { preHandler: [app.authenticate] }, async (_req, reply) => {
    try {
      return { number: await certs.generateDigitalNumber() };
    } catch (e) {
      return reply
        .code(503)
        .send({ message: e instanceof Error ? e.message : "Не удалось сгенерировать номер" });
    }
  });

  app.post("/certificates/redeem", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = redeemCertificateSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const updated = await certs.redeem(parsed.data.number, parsed.data.amount);
    if (!updated) return reply.code(409).send({ message: "Сертификат не найден, неактивен или его баланс недостаточен" });
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "certificate.redeemed",
      entityType: "certificate",
      entityId: parsed.data.number,
      after: updated,
      metadata: { amount: parsed.data.amount },
    });
    return updated;
  });

  app.post(
    "/certificates/import",
    { preHandler: [app.requireRoles(["admin"])] },
    async (req, reply) => {
      const parsed = certificateImportSchema.safeParse(req.body);
      if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
      const result = await certs.importCsv(parsed.data.csv, parsed.data.dryRun);
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: parsed.data.dryRun ? "certificate.import_dry_run" : "certificate.imported",
        entityType: "certificate_import",
        entityId: new Date().toISOString(),
        metadata: { ...result, csv: undefined },
      });
      return result;
    }
  );
}
