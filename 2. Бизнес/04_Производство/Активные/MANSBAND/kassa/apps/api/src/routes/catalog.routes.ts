import type { FastifyInstance } from "fastify";
import { catalogSearchSchema } from "@kassa/shared";
import { searchCatalog } from "../services/catalog.js";

export default async function catalogRoutes(app: FastifyInstance) {
  app.get("/catalog/search", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = catalogSearchSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: parsed.error.issues[0]?.message });
    return searchCatalog(parsed.data.q, parsed.data.limit, parsed.data.store);
  });
}
