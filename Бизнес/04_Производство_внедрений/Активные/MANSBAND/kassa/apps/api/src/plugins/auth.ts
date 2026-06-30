import fp from "fastify-plugin";
import fastifyJwt from "@fastify/jwt";
import type { FastifyReply, FastifyRequest } from "fastify";
import { getEnv } from "../env.js";

// JWT-плагин: декораторы authenticate (любой вошедший) и requireAdmin.

declare module "fastify" {
  interface FastifyInstance {
    authenticate: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    requireAdmin: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

declare module "@fastify/jwt" {
  interface FastifyJWT {
    payload: { sub: string; role: string; name: string };
    user: { sub: string; role: string; name: string };
  }
}

export default fp(async (app) => {
  const env = getEnv();
  await app.register(fastifyJwt, {
    secret: env.JWT_SECRET,
    sign: { expiresIn: env.JWT_EXPIRES_IN },
  });

  app.decorate("authenticate", async (req: FastifyRequest, reply: FastifyReply) => {
    try {
      // Поддержка токена и в заголовке, и в query (для WebSocket).
      const q = req.query as { token?: string } | undefined;
      if (q?.token && !req.headers.authorization) {
        req.headers.authorization = `Bearer ${q.token}`;
      }
      await req.jwtVerify();
    } catch {
      reply.code(401).send({ message: "Не авторизован" });
    }
  });

  app.decorate("requireAdmin", async (req: FastifyRequest, reply: FastifyReply) => {
    try {
      await req.jwtVerify();
      if (req.user.role !== "admin") {
        reply.code(403).send({ message: "Доступ только для администратора" });
      }
    } catch {
      reply.code(401).send({ message: "Не авторизован" });
    }
  });
});
