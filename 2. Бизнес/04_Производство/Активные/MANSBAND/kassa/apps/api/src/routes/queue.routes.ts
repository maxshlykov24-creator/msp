import type { FastifyInstance } from "fastify";
import { queueItemSchema } from "@kassa/shared";
import * as q from "../services/queue.js";

export default async function queueRoutes(app: FastifyInstance) {
  // Очередь Эдвина
  app.get("/queue", { preHandler: [app.authenticate] }, async () => q.listQueue());

  app.post("/queue", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = queueItemSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return q.addQueue(parsed.data);
  });

  app.patch("/queue/:id/issue", { preHandler: [app.authenticate] }, async (req) => {
    const { id } = req.params as { id: string };
    await q.issueQueue(id);
    return { ok: true };
  });

  // Сары
  app.get("/sary", { preHandler: [app.authenticate] }, async () => q.listSary());

  app.patch("/sary/:id/sent", { preHandler: [app.authenticate] }, async (req) => {
    const { id } = req.params as { id: string };
    await q.markSarySent(id);
    return { ok: true };
  });
}
