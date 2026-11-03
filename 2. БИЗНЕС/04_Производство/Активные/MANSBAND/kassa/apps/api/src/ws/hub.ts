import type { FastifyInstance } from "fastify";
import type { WebSocket } from "@fastify/websocket";
import type { WsEvent, WsEventType } from "@kassa/shared";

// Простой broadcast-хаб: бэкенд шлёт события всем открытым кассам.

const clients = new Set<WebSocket>();

export function addClient(ws: WebSocket) {
  clients.add(ws);
  ws.on("close", () => clients.delete(ws));
  ws.on("error", () => clients.delete(ws));
}

export function broadcast<T>(type: WsEventType, payload: T) {
  const event: WsEvent<T> = { type, at: new Date().toISOString(), payload };
  const data = JSON.stringify(event);
  for (const ws of clients) {
    try {
      ws.send(data);
    } catch {
      clients.delete(ws);
    }
  }
}

export function clientCount(): number {
  return clients.size;
}

/** Без JWT соединение никогда не попадает в список получателей событий. */
export function registerRealtime(app: FastifyInstance) {
  app.register(async (wsApp) => {
    wsApp.get("/ws", { websocket: true }, (socket, req) => {
      const protocols = req.headers["sec-websocket-protocol"]?.split(",").map(value => value.trim()) ?? [];
      const token = protocols.find(value => value.startsWith("bearer."))?.slice(7);
      try {
        if (!token) throw new Error("Missing token");
        const claims = wsApp.jwt.verify<{ exp?: number }>(token);
        if (!claims.exp) throw new Error("Missing expiration");
        const timer = setTimeout(() => socket.close(4001, "expired"), Math.max(0, claims.exp * 1000 - Date.now()));
        timer.unref();
        socket.once("close", () => clearTimeout(timer));
      } catch {
        socket.close(4001, "unauthorized");
        return;
      }
      addClient(socket);
      socket.send(JSON.stringify({ type: "ping", at: new Date().toISOString(), payload: { ok: true } }));
    });
  });
}
