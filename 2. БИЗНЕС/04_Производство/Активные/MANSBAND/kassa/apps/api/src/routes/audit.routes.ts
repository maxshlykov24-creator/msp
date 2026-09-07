import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { listAudit } from "../services/audit.js";

const querySchema = z.object({
  entityType: z.string().optional(),
  entityId: z.string().optional(),
  /** Фильтр «авто / ручное» (созвон 20.08). */
  source: z.enum(["manual", "auto"]).optional(),
  limit: z.coerce.number().int().min(1).max(500).default(100),
});

export default async function auditRoutes(app: FastifyInstance) {
  app.get(
    "/audit",
    { preHandler: [app.requireRoles(["rop", "admin"])] },
    async (req, reply) => {
      const parsed = querySchema.safeParse(req.query);
      if (!parsed.success) return reply.code(400).send({ message: "Некорректный фильтр аудита" });
      return listAudit(parsed.data);
    }
  );

  app.get(
    "/deals/:ref/audit",
    { preHandler: [app.requireRoles(["rop", "admin"])] },
    async (req) => {
      const { ref } = req.params as { ref: string };
      return listAudit({ entityType: "deal", entityId: ref, limit: 500 });
    }
  );
}
