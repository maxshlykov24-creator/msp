import type { WsEvent } from "@kassa/shared";
import { getToken } from "./client";

// WebSocket с авто-переподключением. Токен передаём query-параметром.

type Handler = (ev: WsEvent) => void;

export function connectWs(onEvent: Handler): () => void {
  let socket: WebSocket | null = null;
  let closed = false;
  let retry = 0;

  const wsBase =
    (import.meta.env.VITE_WS_BASE as string | undefined) ??
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

  function open() {
    if (closed) return;
    const token = getToken();
    const url = token ? `${wsBase}?token=${encodeURIComponent(token)}` : wsBase;
    socket = new WebSocket(url);

    socket.onopen = () => {
      retry = 0;
    };
    socket.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data) as WsEvent);
      } catch {
        // игнорируем некорректные сообщения
      }
    };
    socket.onclose = () => {
      if (closed) return;
      retry = Math.min(retry + 1, 6);
      setTimeout(open, 1000 * retry); // backoff до 6с
    };
    socket.onerror = () => socket?.close();
  }

  open();

  return () => {
    closed = true;
    socket?.close();
  };
}
