import type { FastifyInstance } from "fastify";
import { loginSchema, changePasswordSchema } from "@kassa/shared";
import * as auth from "../services/auth.js";

export default async function authRoutes(app: FastifyInstance) {
  app.post("/auth/login", async (req, reply) => {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректные данные" });
    const user = await auth.verifyCredentials(parsed.data.login, parsed.data.password);
    if (!user) return reply.code(401).send({ message: "Неверный логин или пароль" });
    const token = app.jwt.sign({ sub: user.id, role: user.role, name: user.name });
    return { token, user };
  });

  app.get("/auth/me", { preHandler: [app.authenticate] }, async (req, reply) => {
    const user = await auth.getUserById(req.user.sub);
    if (!user) return reply.code(401).send({ message: "Не авторизован" });
    return user;
  });

  app.post("/auth/change-password", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = changePasswordSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    const res = await auth.changePassword(
      req.user.sub,
      parsed.data.currentPassword,
      parsed.data.newPassword
    );
    if (!res.ok) return reply.code(400).send({ message: res.error });
    return { ok: true };
  });
}
