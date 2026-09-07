import Fastify from "fastify";
import cors from "@fastify/cors";
import websocket from "@fastify/websocket";
import { getEnv } from "./env.js";
import authPlugin from "./plugins/auth.js";
import { addClient } from "./ws/hub.js";

import healthRoutes from "./routes/health.routes.js";
import authRoutes from "./routes/auth.routes.js";
import dealsRoutes from "./routes/deals.routes.js";
import catalogRoutes from "./routes/catalog.routes.js";
import saleRoutes from "./routes/sale.routes.js";
import certificatesRoutes from "./routes/certificates.routes.js";
import queueRoutes from "./routes/queue.routes.js";
import statsRoutes from "./routes/stats.routes.js";
import webhooksRoutes from "./routes/webhooks.routes.js";
import adminRoutes from "./routes/admin.routes.js";
import contactsRoutes from "./routes/contacts.routes.js";
import auditRoutes from "./routes/audit.routes.js";
import tasksRoutes from "./routes/tasks.routes.js";
import usersRoutes from "./routes/users.routes.js";
import settingsRoutes from "./routes/settings.routes.js";
import suitsRoutes from "./routes/suits.routes.js";
import { appendAudit } from "./services/audit.js";

export async function buildServer() {
  const env = getEnv();
  const app = Fastify({
    logger:
      env.NODE_ENV === "production"
        ? { level: "info" }
        : { level: "debug", transport: { target: "pino-pretty" } },
    bodyLimit: 40 * 1024 * 1024, // до 40 МБ — несколько фотофиксаций base64 в одной заявке
  });

  await app.register(cors, { origin: true, credentials: true });
  await app.register(authPlugin);
  await app.register(websocket);
  app.addHook("onResponse", async (req, reply) => {
    if (!["POST", "PATCH", "PUT", "DELETE"].includes(req.method)) return;
    try {
      const user = req.user;
      if (!user?.sub) return;
      const params = (req.params ?? {}) as Record<string, string>;
      await appendAudit({
        actor: { id: user.sub, name: user.name, role: user.role },
        action: `http.${req.method.toLowerCase()}`,
        entityType: "api_mutation",
        entityId: params.id ?? params.ref ?? req.url.split("?")[0] ?? "unknown",
        metadata: { method: req.method, url: req.url.split("?")[0], statusCode: reply.statusCode },
      });
    } catch (error) {
      req.log.error({ err: error }, "Не удалось записать общий аудит мутации");
    }
  });

  // REST под /api (nginx проксирует /api → сюда).
  await app.register(
    async (api) => {
      await api.register(healthRoutes);
      await api.register(authRoutes);
      await api.register(dealsRoutes);
      await api.register(catalogRoutes);
      await api.register(saleRoutes);
      await api.register(certificatesRoutes);
      await api.register(queueRoutes);
      await api.register(statsRoutes);
      await api.register(webhooksRoutes);
      await api.register(adminRoutes);
      await api.register(contactsRoutes);
      await api.register(auditRoutes);
      await api.register(tasksRoutes);
      await api.register(usersRoutes);
      await api.register(settingsRoutes);
      await api.register(suitsRoutes);
    },
    { prefix: "/api" }
  );

  // WebSocket-канал на открытые кассы (токен в query).
  app.register(async (wsApp) => {
    wsApp.get("/ws", { websocket: true }, (socket, req) => {
      const token = (req.query as { token?: string } | undefined)?.token;
      try {
        if (token) wsApp.jwt.verify(token);
      } catch {
        socket.close(4001, "unauthorized");
        return;
      }
      addClient(socket);
      socket.send(JSON.stringify({ type: "ping", at: new Date().toISOString(), payload: { ok: true } }));
    });
  });

  return app;
}
