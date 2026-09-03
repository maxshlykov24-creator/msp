#!/usr/bin/env python3
"""Keris Club — Telegram Mini App бот для прототипа онлайн-записи на груминг.

Long polling, только stdlib. Токен и URL — из окружения / .env на сервере.
Не путать с витриной щенков (@kerisclubbot).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("keris-booking-bot")

# IPv6: api.telegram.org с RU по IPv4 таймаутится; зарубежный бот-VPS — IPv6-only.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv6

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/") + "/"
MENU_TEXT = os.environ.get("MENU_TEXT", "Запись")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан")
if not WEBAPP_URL or WEBAPP_URL == "/":
    raise SystemExit("WEBAPP_URL не задан")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _http(method: str, url: str, data: Optional[dict] = None, timeout: float = 35.0) -> tuple[int, Any]:
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"description": raw}
        return e.code, payload
    except Exception as e:
        return 0, {"description": str(e)}


def api(method: str, payload: Optional[dict] = None) -> dict:
    status, data = _http("POST", f"{TG_API}/{method}", payload or {})
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        log.warning("%s -> %s %s", method, status, str(data)[:300])
    return data if isinstance(data, dict) else {}


def webapp_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✂️ Записаться на груминг", "web_app": {"url": WEBAPP_URL}}],
        ]
    }


def reply_webapp_kb() -> dict:
    return {
        "keyboard": [[{"text": "Записаться", "web_app": {"url": WEBAPP_URL}}]],
        "resize_keyboard": True,
    }


def set_menu_button() -> None:
    api(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": MENU_TEXT,
                "web_app": {"url": WEBAPP_URL},
            }
        },
    )


def send(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    api("sendMessage", payload)


GREETING = (
    "🐾 <b>Keris Club — онлайн-запись на груминг</b>\n\n"
    "Откройте мини-приложение: выберите питомца, услугу, мастера и время.\n"
    "Подтверждение сразу — без звонков.\n\n"
    "Это демо-прототип записи."
)


def handle_start(chat_id: int) -> None:
    send(chat_id, GREETING, webapp_kb())
    send(chat_id, "Кнопка ниже тоже открывает запись:", reply_webapp_kb())


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if text.startswith("/start") or text.startswith("/booking") or text.startswith("/menu"):
        handle_start(chat_id)
    elif text:
        handle_start(chat_id)


def main() -> None:
    me = api("getMe")
    uname = (me.get("result") or {}).get("username", "?")
    log.info("booking-bot @%s webapp=%s", uname, WEBAPP_URL)
    api("deleteWebhook", {"drop_pending_updates": True})
    set_menu_button()
    offset = 0
    allowed = urllib.parse.quote('["message"]')
    while True:
        try:
            status, data = _http(
                "GET",
                f"{TG_API}/getUpdates?offset={offset}&timeout=25&allowed_updates={allowed}",
                None,
                timeout=35.0,
            )
            if status != 200 or not isinstance(data, dict) or not data.get("ok"):
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                if "message" in upd:
                    handle_message(upd["message"])
        except Exception:
            log.warning("poll error", exc_info=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
