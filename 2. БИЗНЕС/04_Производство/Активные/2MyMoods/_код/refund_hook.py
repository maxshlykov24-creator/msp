#!/usr/bin/env python3
"""Приём вебхуков МойСклад для возврата денег. Секрет только в пути и в окружении."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import refund_flow

HOST = "127.0.0.1"
PORT = int(os.environ.get("REFUND_HOOK_PORT", "19122"))


class Handler(BaseHTTPRequestHandler):
    server_version = "2my-refund"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _text(self, code: int, text: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not refund_flow.authorized(self.path):
            self._text(404, "")
            return
        self._text(200, "ok")

    def do_POST(self) -> None:
        if not refund_flow.authorized(self.path):
            self._text(404, "")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            self._text(400, "")
            return
        try:
            refund_flow.handle_payload(payload)
        except Exception as exc:
            print(f"ошибка вебхука: {type(exc).__name__}", flush=True)
            self._text(500, "")
            return
        self._text(200, "ok")


def main() -> None:
    if not os.environ.get("REFUND_HOOK_TOKEN", "").strip():
        raise SystemExit("Нет токена вебхука")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"refund hook {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
