import type { FastifyInstance } from "fastify";
import { certificateImportSchema, redeemCertificateSchema } from "@kassa/shared";
import * as certs from "../services/certificates.js";
import { reservedNumbers } from "../services/certificateRegistry.js";
import { appendAudit } from "../services/audit.js";

export default async function certificatesRoutes(app: FastifyInstance) {
  app.get("/certificates/reserved", { preHandler: [app.authenticate] }, async () => reservedNumbers());
  app.get("/certificates", { preHandler: [app.authenticate] }, async () => certs.list());

  app.get("/certificates/:number/usages", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { number } = req.params as { number: string };
    const needle = decodeURIComponent(number ?? "").trim();
    if (!needle) return reply.code(400).send({ message: "Нет номера сертификата" });
    return certs.listUsages(needle);
  });

  // Предгенерация 6-значного номера для электронного сертификата: консультант
  // видит номер до сохранения заявки и может продиктовать его клиенту.
  app.post("/certificates/next-number", { preHandler: [app.authenticate] }, async (_req, reply) => {
    try {
      return reply.code(409).send({ message: "Номер закрепляется сервером после оплаты" });
    } catch (e) {
      return reply
        .code(503)
        .send({ message: e instanceof Error ? e.message : "Не удалось сгенерировать номер" });
    }
  });

  app.post("/certificates/:number/cancel",{preHandler:[app.requireAdmin]},async(req,reply)=>{
    const reason=(req.body as {reason?:unknown})?.reason;
    if(typeof reason!=="string"||reason.trim().length<3||reason.length>500)return reply.code(400).send({message:"Укажи причину аннулирования"});
    const number=(req.params as {number:string}).number;
    const updated=await certs.cancel(number);
    if(!updated)return reply.code(409).send({message:"Сертификат уже использован или недоступен. Требуется сверка."});
    await appendAudit({actor:{id:req.user.sub,name:req.user.name,role:req.user.role},action:"certificate.cancelled",entityType:"certificate",entityId:updated.number,after:updated,metadata:{reason:reason.trim()}});
    return updated;
  });
  app.post("/certificates/redeem", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = redeemCertificateSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const updated = await certs.redeem(parsed.data.number, parsed.data.amount, parsed.data.expectedBalance);
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
