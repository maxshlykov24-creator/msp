import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { movementTaskSchema, taskSchema } from "@kassa/shared";
import * as taskService from "../services/tasks.js";
import { appendAudit } from "../services/audit.js";

const querySchema = z.object({
  role: z.string().optional(),
  status: z.string().optional(),
  store: z.string().optional(),
});

export default async function tasksRoutes(app: FastifyInstance) {
  app.get("/tasks", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный фильтр задач" });
    return taskService.listTasks(parsed.data);
  });

  // Включая CDEK: задачи создаются только этим ручным endpoint, без авто-событий.
  app.post("/tasks", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = taskSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    // createdBy = ФИО из JWT; в metadata дублируем userId — чтобы отличить одноимённых.
    const task = await taskService.createTask(
      {
        ...parsed.data,
        metadata: {
          ...(parsed.data.metadata ?? {}),
          createdByUserId: req.user.sub,
        },
      },
      req.user.name
    );
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "task.created",
      entityType: "task",
      entityId: task.id,
      after: task,
    });
    return task;
  });

  app.post("/tasks/movement", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = movementTaskSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const authorRole =
      req.user.role === "supply" || req.user.role === "logist"
        ? "logist"
        : req.user.role === "callmanager" || req.user.role === "crm"
          ? "crm"
          : "consultant";
    try {
      const task = await taskService.createMovement(
        {
          ...parsed.data,
          metadata: {
            ...(parsed.data.metadata ?? {}),
            createdByUserId: req.user.sub,
            authorRole,
          },
        },
        req.user.name
      );
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "movement.created",
        entityType: "task",
        entityId: task.id,
        after: task,
      });
      return task;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось создать перемещение";
      return reply.code(400).send({ message });
    }
  });

  app.patch("/tasks/:id/complete", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { id } = req.params as { id: string };
    try {
      const task = await taskService.completeTask(id, req.user.name);
      if (!task) return reply.code(404).send({ message: "Задача не найдена" });
      await appendAudit({
        actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
        action: "task.completed",
        entityType: "task",
        entityId: id,
        after: task,
      });
      return task;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось закрыть задачу";
      return reply.code(400).send({ message });
    }
  });
}
