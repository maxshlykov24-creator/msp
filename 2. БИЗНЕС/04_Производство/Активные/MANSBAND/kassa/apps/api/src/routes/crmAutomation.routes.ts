import type { FastifyInstance } from "fastify";
import { timingSafeEqual, createHash } from "node:crypto";
import { getEnv } from "../env.js";
import { sql } from "../db/index.js";

/** Read-only automation contract. Never returns contacts, amounts or task text. */
export default async function crmAutomationRoutes(app: FastifyInstance) {
  app.post<{ Params: { leadId: string }; Body: { task_id?: number; occurrence?: string } }>(
    "/internal/crm-automation/leads/:leadId/courier-completed", {
      preHandler: async (req, reply) => {
        const key = getEnv().CRM_AUTOMATION_SERVICE_KEY;
        const supplied = String(req.headers["x-automation-key"] ?? "");
        if (!key || Buffer.byteLength(key) !== Buffer.byteLength(supplied) ||
            !timingSafeEqual(Buffer.from(key), Buffer.from(supplied)))
          return reply.code(403).send({ message: "Доступ запрещён" });
      },
    }, async (req, reply) => {
      const leadId = Number(req.params.leadId), taskId = req.body?.task_id;
      const occurrence = req.body?.occurrence;
      if (!/^[1-9]\d*$/.test(req.params.leadId) || !Number.isSafeInteger(leadId) ||
          !Number.isSafeInteger(taskId) || Number(taskId) <= 0 || typeof occurrence !== "string" ||
          !/^entry:[A-Za-z0-9_-]{1,160}$/.test(occurrence))
        return reply.code(400).send({ message: "Некорректная задача" });
      if (getEnv().CRM_AUTOMATION_TASKS_ENABLED !== "true")
        return reply.code(409).send({ message: "Исполнение в amoCRM ещё не включено" });
      const amo = await import("../clients/amo.js");
      const { migrationBlocked } = await import("../services/amoRouting.js");
      const [lead, task] = await Promise.all([amo.getLead(leadId), amo.getAutomationTask(taskId!)]);
      const marker = `[MB-AUTO:${createHash("sha256").update(`courier:${leadId}:${occurrence}`).digest("hex").slice(0,24)}]`;
      if (!lead || migrationBlocked(lead) || task.id !== taskId || task.entity_id !== leadId ||
          task.entity_type !== "leads" || task.task_type_id !== 3946594 || !task.is_completed ||
          !task.text?.endsWith(marker))
        return reply.code(409).send({ message: "Завершение собственной задачи не подтверждено" });
      if (!((lead.pipeline_id === 9601214 && lead.status_id === 76655106) ||
            (lead.pipeline_id === 11482377 && lead.status_id === 89900829)))
        return reply.code(409).send({ message: "Этап сделки изменился" });
      const rows = await sql`select number, data from deals where amo_lead_id=${leadId}`;
      if (rows.length !== 1 || rows[0]!.data?.isTest || rows[0]!.data?.amoEditOutbox)
        return reply.code(409).send({ message: "Связь с кассой требует проверки" });
      const { getByNumber } = await import("../services/deals.js");
      const deal = await getByNumber(Number(rows[0]!.number));
      if (!deal || deal.amoLeadId !== leadId || deal.kind !== "delivery" || deal.stage !== "Собран")
        return reply.code(409).send({ message: "Заявка кассы не готова к передаче" });
      const { enqueueCourierHandoff } = await import("../services/taskFlow.js");
      await enqueueCourierHandoff(deal, `amoCRM: задача ${taskId}`);
      return { state: "acknowledged", lead_id: leadId, task_id: taskId, stage: deal.stage };
    });
  app.get<{ Params: { leadId: string } }>("/internal/crm-automation/leads/:leadId", {
    preHandler: async (req, reply) => {
      const key = getEnv().CRM_AUTOMATION_SERVICE_KEY;
      const supplied = String(req.headers["x-automation-key"] ?? "");
      if (!key || Buffer.byteLength(key) !== Buffer.byteLength(supplied)
          || !timingSafeEqual(Buffer.from(key), Buffer.from(supplied))) {
        return reply.code(403).send({ message: "Доступ запрещён" });
      }
    },
  }, async (req, reply) => {
    if (!/^[1-9]\d*$/.test(req.params.leadId)) return reply.code(400).send({ message: "Некорректная сделка" });
    const leadId = Number(req.params.leadId);
    if (!Number.isSafeInteger(leadId)) return reply.code(400).send({ message: "Некорректная сделка" });
    return sql.begin(async tx => {
      await tx`set transaction isolation level repeatable read, read only`;
      const bindings = await tx`
        select number, amo_lead_id as lead_id, data->>'kind' as kind,
               data->>'funnel' as funnel, stage,
               coalesce(data->'isTest' = 'true'::jsonb, false) as "isTest",
               coalesce(data->'amoEditOutbox' <> 'null'::jsonb, false) as edit_pending
        from deals where amo_lead_id = ${leadId} order by number`;
      const tasks = await tx`
        select t.id, t.deal_number, t.kind, t.status, t.assignee_role,
               t.created_at, t.completed_at
        from tasks t join deals d on d.number = t.deal_number
        where d.amo_lead_id = ${leadId} order by t.created_at, t.id`;
      const owner = getEnv().CRM_AUTOMATION_TASKS_ENABLED === "true" ? "amocrm" : "kassa";
      return { version: 1, lead_id: leadId, observed_at: new Date().toISOString(),
        execution_owners: { promise: owner, courier: owner }, bindings, tasks };
    });
  });
}
