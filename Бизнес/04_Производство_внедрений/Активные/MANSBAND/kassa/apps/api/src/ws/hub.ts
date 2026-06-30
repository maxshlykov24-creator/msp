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
