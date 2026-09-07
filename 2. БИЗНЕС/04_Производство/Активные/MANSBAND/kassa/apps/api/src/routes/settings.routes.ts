import type { FastifyInstance } from "fastify";
import { appSettingsSchema, payrollQuerySchema, payrollSettingsSchema } from "@kassa/shared";
import * as settings from "../services/settings.js";
import * as payroll from "../services/payroll.js";

/**
 * Настройки кассы (порог САР) и мотивации + расчёт ЗП (созвон 20.08).
 * Читать настройки может любой залогиненный (порог нужен форме продажи),
 * менять — только rop / admin.
 */
export default async function settingsRoutes(app: FastifyInstance) {
  app.get("/settings", { preHandler: [app.authenticate] }, async () => settings.getAppSettings());

  app.put("/settings", { preHandler: [app.requireRoles(["rop", "admin"])] }, async (req, reply) => {
    const parsed = appSettingsSchema.partial().safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return settings.updateAppSettings(parsed.data, {
      id: req.user.sub,
      name: req.user.name,
      role: req.user.role,
    });
  });

  app.get(
    "/payroll/settings",
    { preHandler: [app.requireRoles(["rop", "admin", "finance"])] },
    async () => payroll.getPayrollSettings()
  );

  app.put(
    "/payroll/settings",
    { preHandler: [app.requireRoles(["rop", "admin"])] },
    async (req, reply) => {
      const parsed = payrollSettingsSchema.safeParse(req.body);
      if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
      return payroll.savePayrollSettings(parsed.data, {
        id: req.user.sub,
        name: req.user.name,
        role: req.user.role,
      });
    }
  );

  app.get(
    "/payroll",
    { preHandler: [app.requireRoles(["rop", "admin", "finance"])] },
    async (req, reply) => {
      const parsed = payrollQuerySchema.safeParse(req.query);
      if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
      if (parsed.data.from > parsed.data.to) {
        return reply.code(400).send({ message: "Начало периода позже конца" });
      }
      return payroll.payrollReport(parsed.data.from, parsed.data.to);
    }
  );

  // Ручной расчёт за выбранный период: задача Эдвину «Выдать зарплату»
  // (созвон 04.09 — не ждать вторничной автоматики).
  app.post(
    "/payroll/enqueue",
    { preHandler: [app.requireRoles(["rop", "admin"])] },
    async (req, reply) => {
      const parsed = payrollQuerySchema.safeParse(req.body);
      if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
      if (parsed.data.from > parsed.data.to) {
        return reply.code(400).send({ message: "Начало периода позже конца" });
      }
      const res = await payroll.enqueueSalaryForPeriod(
        parsed.data.from,
        parsed.data.to,
        req.user.name
      );
      if (!res.created && res.consultants === 0) {
        return reply.code(400).send({ message: "За этот период нет заявок консультантов" });
      }
      return res;
    }
  );
}
