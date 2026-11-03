import fp from "fastify-plugin";
import fastifyJwt from "@fastify/jwt";
import type { FastifyReply, FastifyRequest } from "fastify";
import { getEnv } from "../env.js";

// JWT-плагин: декораторы authenticate (любой вошедший) и requireAdmin.

const FINANCE_QUEUE_ROLES = ["finance", "rop", "admin"];

export function canAccessFinanceQueues(role: string): boolean {
  return FINANCE_QUEUE_ROLES.includes(role);
}

/** В журнал не должны попадать JWT, секрет вебхука и поисковые телефоны. */
export function safeRequestLog(req: { method: string; url: string; hostname?: string; ip?: string }) {
  return { method: req.method, url: req.url.split("?")[0], hostname: req.hostname, remoteAddress: req.ip };
}

export function completedDealEditError(stage: string, role: string, reason?: string): { status: number; message: string } | null {
  if (!["Успех", "Провал", "Не слив"].includes(stage)) return null;
  if (!["rop", "admin"].includes(role)) return { status: 403, message: "Завершённую заявку может изменить только РОП или администратор" };
  if (!reason?.trim()) return { status: 400, message: "Укажите причину изменения завершённой заявки" };
  return null;
}

declare module "fastify" {
  interface FastifyInstance {
    authenticate: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    requireAdmin: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    requireRoles: (roles: string[]) => (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    /** Очередь Эдвина / счета компаний — finance|rop|admin. */
    requireFinanceQueues: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

declare module "@fastify/jwt" {
  interface FastifyJWT {
    payload: { sub: string; role: string; name: string; mustChangePassword?: boolean };
    user: { sub: string; role: string; name: string; mustChangePassword?: boolean };
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
      if (!canAccessFinanceQueues(req.user.role)) {
        reply.code(403).send({ message: "Недостаточно прав для очереди Эдвина" });
      }
    } catch {
      reply.code(401).send({ message: "Не авторизован" });
    }
  });
});
