import type { FastifyInstance } from "fastify";
import { sql } from "../db/index.js";
import { clientCount } from "../ws/hub.js";

export default async function healthRoutes(app: FastifyInstance) {
  app.get("/health", async (_request, reply) => {
    let dbOk = false;
    try {
      await sql`select 1`;
      dbOk = true;
    } catch {
      dbOk = false;
    }
    return reply.code(dbOk ? 200 : 503).header("Cache-Control", "no-store").send({
      ok: dbOk, db: dbOk, wsClients: clientCount(), ts: new Date().toISOString(),
    });
  });
}
