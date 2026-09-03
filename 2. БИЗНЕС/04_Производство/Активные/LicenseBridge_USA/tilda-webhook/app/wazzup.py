"""Клиент Wazzup24: отправка сообщения в WhatsApp мимо Salesbot.

Первое сообщение клиенту раньше отправлял Salesbot внутри Kommo. Это оказалось
слабым местом: бота выключили в UI, и заявки молча остались без ответа — снаружи
это выглядело как «Pleep не пишет первым» (26.08–03.09.2026). Хаб отправляет сам,
и каждая попытка видна в журнале решений.

Ключ и канал — в `.env` на сервере (`WAZZUP_API_KEY`, `WAZZUP_CHANNEL_ID`).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("wazzup")

BASE = "https://api.wazzup24.com/v3"
_channels: dict[str, Any] = {"items": None, "ts": 0.0}
_channels_lock = threading.Lock()
_CHANNELS_TTL = 600.0


class WazzupError(Exception):
    """Wazzup не принял запрос: нет ключа, канал не активен, номер не в WhatsApp."""


def _headers() -> dict[str, str]:
    if not settings.wazzup_api_key:
        raise WazzupError("WAZZUP_API_KEY не задан")
    return {"Authorization": f"Bearer {settings.wazzup_api_key}",
            "Content-Type": "application/json"}


def channels(refresh: bool = False) -> list[dict[str, Any]]:
    """Каналы аккаунта. Кэш на 10 минут: список меняется раз в месяцы."""
    with _channels_lock:
        fresh = (_channels["items"] is not None
                 and time.monotonic() - float(_channels["ts"]) < _CHANNELS_TTL)
        if fresh and not refresh:
            return _channels["items"]  # type: ignore[return-value]
    resp = httpx.get(f"{BASE}/channels", headers=_headers(), timeout=15.0)
    if resp.status_code != 200:
        raise WazzupError(f"channels -> {resp.status_code}: {resp.text[:200]}")
    items = resp.json() or []
    with _channels_lock:
        _channels["items"] = items
        _channels["ts"] = time.monotonic()
    return items


def whatsapp_channel_id() -> str:
    """Канал для отправки. Задан в `.env` — берём его, иначе ищем активный."""
    if settings.wazzup_channel_id:
        return settings.wazzup_channel_id
    for ch in channels():
        if ch.get("transport") == "whatsapp" and ch.get("state") == "active":
            return str(ch["channelId"])
    raise WazzupError("активного канала WhatsApp в аккаунте нет")


def send_text(phone: str, text: str, channel_id: str = "") -> str:
    """Отправить текст в WhatsApp. Возвращает id сообщения Wazzup.

    `chatId` — номер без «+»: с плюсом Wazzup отвечает 400."""
    if not text.strip():
        raise WazzupError("пустой текст сообщения")
    chat_id = "".join(ch for ch in str(phone) if ch.isdigit())
    if not chat_id:
        raise WazzupError(f"телефон не годится для WhatsApp: {phone!r}")
    payload = {
        "channelId": channel_id or whatsapp_channel_id(),
        "chatType": "whatsapp",
        "chatId": chat_id,
        "text": text,
    }
    resp = httpx.post(f"{BASE}/message", headers=_headers(), json=payload, timeout=30.0)
    if resp.status_code not in (200, 201):
        raise WazzupError(f"message -> {resp.status_code}: {resp.text[:300]}")
    data = resp.json() if resp.text else {}
    message_id = str(data.get("messageId") or data.get("id") or "")
    log.info("wazzup sent to %s: message=%s", chat_id, message_id or "?")
    return message_id
