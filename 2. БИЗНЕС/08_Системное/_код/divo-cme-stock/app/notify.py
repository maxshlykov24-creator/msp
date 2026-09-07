from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger("notify")


def send(text: str) -> None:
    token = (settings.telegram_bot_token or "").strip()
    chat = (settings.telegram_chat_id or "").strip()
    if not token or not chat:
        log.warning("Telegram не настроен, алерт пропущен: %s", text[:200])
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True},
            timeout=15.0,
        )
        if resp.status_code >= 400:
            log.warning("telegram %s: %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError:
        log.warning("telegram send failed", exc_info=True)
