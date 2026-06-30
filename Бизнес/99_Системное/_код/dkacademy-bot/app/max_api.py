"""Клиент MAX Bot API (platform-api.max.ru).

Синхронный (httpx) — используется из фоновых потоков без asyncio event loop.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any, Optional

import httpx

BASE = "https://platform-api.max.ru"
log = logging.getLogger(__name__)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": token.strip(), "Content-Type": "application/json"}


def send_message(
    token: str,
    user_id: int | str,
    text: str,
    *,
    buttons: Optional[list[list[dict[str, Any]]]] = None,
) -> tuple[bool, Optional[str]]:
    """POST /messages?user_id=... — отправить личное сообщение.

    buttons — двумерный список кнопок в формате MAX API.
    Возвращает (ok, error_kind) по аналогии с telegram_api.send_message_sync.
    """
    body: dict[str, Any] = {"text": text[:4000]}
    if buttons:
        body["attachments"] = [
            {"type": "inline_keyboard", "payload": {"buttons": buttons}}
        ]
    try:
        with httpx.Client(timeout=35.0) as c:
            r = c.post(
                f"{BASE}/messages",
                params={"user_id": str(user_id)},
                headers=_headers(token),
                json=body,
            )
        data: Any = {}
        try:
            data = r.json()
        except Exception:
            pass
    except Exception as exc:
        log.warning("max send_message transport error: %s", exc)
        return False, "error"

    if r.status_code >= 400:
        err_str = str(data).lower()
        log.warning("max send_message http=%s user=%s body=%s", r.status_code, user_id, str(data)[:300])
        if r.status_code in (403, 404) or "not found" in err_str or "blocked" in err_str:
            return False, "blocked"
        return False, "bad_request"

    return True, None


def get_updates(
    token: str,
    *,
    marker: Optional[int] = None,
    timeout: int = 30,
    types: Optional[list[str]] = None,
) -> tuple[list[dict[str, Any]], Optional[int]]:
    """GET /updates — Long Polling.

    Возвращает (events, next_marker).
    """
    params: dict[str, Any] = {"timeout": timeout, "limit": 100}
    if marker is not None:
        params["marker"] = marker
    if types:
        params["types"] = ",".join(types)
    try:
        with httpx.Client(timeout=timeout + 10.0) as c:
            r = c.get(f"{BASE}/updates", params=params, headers=_headers(token))
        data = r.json() if r.content else {}
    except Exception as exc:
        log.warning("max get_updates transport error: %s", exc)
        return [], marker

    if r.status_code >= 400:
        log.warning("max get_updates http=%s body=%s", r.status_code, str(data)[:200])
        return [], marker

    events = data.get("updates") or []
    next_marker = data.get("marker")
    return events, next_marker


def verify_contact_hash(token: str, vcf_info: str, received_hash: str) -> bool:
    """Проверяет, что контакт поделился своим номером через кнопку MAX.

    MAX: hash = HMAC-SHA256(key=access_token, msg=vcf_info с реальными \\r\\n).
    """
    # Заменяем экранированные переносы на реальные (если они пришли как строка)
    vcf_real = vcf_info.replace("\\r\\n", "\r\n")
    expected = hmac.new(
        token.strip().encode(),
        vcf_real.encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, received_hash)
    except Exception:
        return False


def extract_phone_from_vcf(vcf_info: str) -> str:
    """Извлекает номер телефона из VCF-строки."""
    vcf_real = vcf_info.replace("\\r\\n", "\r\n")
    for line in vcf_real.splitlines():
        if line.upper().startswith("TEL"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                phone = re.sub(r"[^\d+]", "", parts[1])
                return phone
    return ""
