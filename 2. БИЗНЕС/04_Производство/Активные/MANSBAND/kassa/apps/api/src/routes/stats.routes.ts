import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { summary } from "../services/stats.js";

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const periodSchema = z.object({ from: day.optional(), to: day.optional() });

export default async function statsRoutes(app: FastifyInstance) {
  app.get("/stats/summary", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = periodSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный период" });
    // Роль решает, чьи цифры отдаём: консультанту — только его заявки.
    return summary(parsed.data, { name: req.user.name, role: req.user.role });
  });
}
