from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
_warned = False


def send(text: str) -> bool:
    """Сообщение в Telegram. Без токена возвращает False и пишет в лог один раз."""
    global _warned
    settings = get_settings()
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        if not _warned:
            log.warning("Telegram для коллтрекинга не настроен")
            _warned = True
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = httpx.post(url, json={"chat_id": chat_id, "text": (text or "")[:4000]}, timeout=10.0)
    except Exception:
        log.exception("telegram send failed")
        return False
    if r.status_code >= 300:
        log.error("telegram send http=%s", r.status_code)
        return False
    return True
