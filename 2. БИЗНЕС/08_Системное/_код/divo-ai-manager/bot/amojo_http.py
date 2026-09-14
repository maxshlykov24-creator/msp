"""Публичный HTTPS-хук amo Chats API.

Слушает только localhost. Наружу его открывает nginx на
https://divomotors-analytics.ru/amojo/...
Пока ТП не выдала секрет канала, подпись не проверяем, отвечаем 200
быстрее 5 секунд. Иначе amo потом глушит канал.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.config import settings

log = logging.getLogger("amojo")

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
<circle cx="7" cy="7" r="6.5" fill="#0B1F3A"/>
<path fill="#FFFFFF" d="M4.2 3.15h3.05c2.05 0 3.35 1.18 3.35 3.85S9.3 10.85 7.25 10.85H4.2V3.15zm1.65 1.45v4.8h1.35c1.18 0 1.85-.72 1.85-2.4 0-1.68-.67-2.4-1.85-2.4H5.85z"/>
</svg>
"""


def _log_path() -> Path:
    path = settings.state_dir / "_логи"
    path.mkdir(parents=True, exist_ok=True)
    return path / "amojo.log"


def _append_log(line: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip() + "\n")
    except OSError:
        log.exception("не записал amojo.log")


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    header_blob = await reader.readuntil(b"\r\n\r\n")
    head, _ = header_blob.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    if not lines:
        raise ValueError("empty request")
    parts = lines[0].split()
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, target = parts[0].upper(), parts[1]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    body = b""
    if length > 0:
        body = await reader.readexactly(min(length, 2_000_000))
    return method, target, headers, body


def _response(status: int, body: bytes, content_type: str) -> bytes:
    reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}.get(status, "OK")
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n"
        "\r\n"
    ).encode("ascii") + body


def route(method: str, target: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
    path = target.split("?", 1)[0]
    if method == "GET" and path in {"/amojo/health", "/health"}:
        payload = json.dumps(
            {"ok": True, "service": "divo-amojo", "account": "divomotors"},
            ensure_ascii=False,
        ).encode()
        return 200, payload, "application/json; charset=utf-8"
    if method == "GET" and path in {"/amojo/icon.svg", "/icon.svg"}:
        return 200, ICON_SVG.encode("utf-8"), "image/svg+xml; charset=utf-8"
    if method == "POST" and (
        path.startswith("/amojo/v2/hooks/") or path.startswith("/v2/hooks/")
    ):
        scope = path.rstrip("/").rsplit("/", 1)[-1]
        preview = body[:800].decode("utf-8", "replace").replace("\n", " ")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _append_log(f"{stamp}\t{scope}\t{len(body)}\t{preview}")
        log.info("хук amojo scope=%s bytes=%s", scope, len(body))
        return 200, b'{"ok":true}', "application/json; charset=utf-8"
    if method == "GET" and (
        path.startswith("/amojo/v2/hooks/") or path.startswith("/v2/hooks/")
    ):
        return 200, b'{"ok":true,"hint":"POST only"}', "application/json; charset=utf-8"
    return 404, b'{"ok":false}', "application/json; charset=utf-8"


async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, target, headers, body = await asyncio.wait_for(
            _read_request(reader), timeout=4
        )
        status, payload, ctype = route(method, target, headers, body)
        writer.write(_response(status, payload, ctype))
        await writer.drain()
    except Exception as exc:
        log.warning("amojo http: %s", exc)
        try:
            writer.write(_response(200, b'{"ok":true}', "application/json"))
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def serve() -> None:
    host = settings.amojo_http_host
    port = settings.amojo_http_port
    server = await asyncio.start_server(_client, host, port)
    log.info("amojo http %s:%s", host, port)
    async with server:
        await server.serve_forever()
