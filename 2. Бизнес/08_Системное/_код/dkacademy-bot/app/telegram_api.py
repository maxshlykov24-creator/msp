from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


def send_message_sync(
    bot_token: str,
    chat_id: str | int,
    text: str,
    *,
    reply_markup: Optional[dict[str, Any]] = None,
) -> tuple[bool, Optional[str]]:
    """Синхронная отправка (для фоновых задач без event loop aiogram)."""
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000]}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        with httpx.Client(timeout=35.0) as c:
            r = c.post(url, json=payload)
        data = r.json() if r.content else {}
    except Exception as e:
        log.warning("telegram sync send transport: %s", e)
        return False, "error"
    if r.status_code >= 400:
        desc = ""
        if isinstance(data, dict):
            desc = str((data.get("description") or data.get("ok")) or "")
        low = desc.lower()
        log.warning("telegram sync send http=%s chat=%s %s", r.status_code, chat_id, desc[:400])
        if "blocked" in low or r.status_code == 403:
            return False, "blocked"
        if "chat not found" in low or "deactivated" in low:
            return False, "blocked"
        return False, "bad_request"
    ok = isinstance(data, dict) and data.get("ok") is True
    if ok:
        return True, None
    return False, "error"
