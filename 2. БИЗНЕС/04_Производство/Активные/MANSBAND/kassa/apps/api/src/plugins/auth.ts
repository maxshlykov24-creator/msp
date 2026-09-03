import fp from "fastify-plugin";
import fastifyJwt from "@fastify/jwt";
import type { FastifyReply, FastifyRequest } from "fastify";
import { getEnv } from "../env.js";

// JWT-плагин: декораторы authenticate (любой вошедший) и requireAdmin.

const FINANCE_QUEUE_ROLES = ["finance", "rop", "admin"];

/** Как на фронте canSeeFinanceQueues: финансы + владелец (Максим), даже с ролью logist. */
function canAccessFinanceQueues(role: string, name?: string): boolean {
  if (FINANCE_QUEUE_ROLES.includes(role)) return true;
  const who = (name ?? "").toLowerCase();
  return who.includes("max") || who.includes("максим");
}

declare module "fastify" {
  interface FastifyInstance {
    authenticate: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    requireAdmin: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    requireRoles: (roles: string[]) => (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    /** Очередь Эдвина / счета компаний — finance|rop|admin или Максим. */
    requireFinanceQueues: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
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

  app.decorate("requireRoles", (roles: string[]) => {
    return async (req: FastifyRequest, reply: FastifyReply) => {
      try {
        await req.jwtVerify();
        if (!roles.includes(req.user.role)) {
          reply.code(403).send({ message: `Недостаточно прав. Разрешённые роли: ${roles.join(", ")}` });
        }
      } catch {
        reply.code(401).send({ message: "Не авторизован" });
      }
    };
  });

  app.decorate("requireFinanceQueues", async (req: FastifyRequest, reply: FastifyReply) => {
    try {
      await req.jwtVerify();
      if (!canAccessFinanceQueues(req.user.role, req.user.name)) {
        reply.code(403).send({ message: "Недостаточно прав для очереди Эдвина" });
      }
    } catch {
      reply.code(401).send({ message: "Не авторизован" });
    }
  });
});
