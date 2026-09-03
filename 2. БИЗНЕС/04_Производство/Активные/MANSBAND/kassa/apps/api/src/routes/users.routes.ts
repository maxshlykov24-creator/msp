import type { FastifyInstance } from "fastify";
import { createUserSchema, updateUserRoleSchema } from "@kassa/shared";
import * as auth from "../services/auth.js";
import { appendAudit } from "../services/audit.js";

export default async function usersRoutes(app: FastifyInstance) {
  app.get("/users", { preHandler: [app.requireRoles(["rop", "admin"])] }, async () => auth.listUsers());

  // Заводка учёток команды из интерфейса (созвон 20.08, п.7). Только admin.
  app.post("/users", { preHandler: [app.requireRoles(["admin"])] }, async (req, reply) => {
    const parsed = createUserSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const result = await auth.createUser(parsed.data);
    if (!result.user) return reply.code(409).send({ message: result.error });
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "user.created",
      entityType: "user",
      entityId: result.user.id,
      after: { login: result.user.login, name: result.user.name, role: result.user.role },
    });
    return result.user;
  });

  app.patch("/users/:id/role", { preHandler: [app.requireRoles(["admin"])] }, async (req, reply) => {
    const { id } = req.params as { id: string };
    const parsed = updateUserRoleSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректная роль" });
    const before = await auth.getUserById(id);
    if (!before) return reply.code(404).send({ message: "Пользователь не найден" });
    const updated = await auth.updateUserRole(id, parsed.data.role);
    await appendAudit({
      actor: { id: req.user.sub, name: req.user.name, role: req.user.role },
      action: "user.role_changed",
      entityType: "user",
      entityId: id,
      before: { role: before.role },
      after: { role: updated?.role },
    });
    return updated;
  });
}
