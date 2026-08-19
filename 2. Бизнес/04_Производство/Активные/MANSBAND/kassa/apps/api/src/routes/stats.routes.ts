import type { FastifyInstance } from "fastify";
import { summary } from "../services/stats.js";

export default async function statsRoutes(app: FastifyInstance) {
  app.get("/stats/summary", { preHandler: [app.authenticate] }, async () => summary());
}
