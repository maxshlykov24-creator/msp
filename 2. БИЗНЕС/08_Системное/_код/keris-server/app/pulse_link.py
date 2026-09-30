"""Короткая ссылка в пульс. Пароль в кнопку не кладём."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64encode

import requests

from .config import settings

ENTRY_TTL_SEC = 12 * 60 * 60
_health_ok_until = 0.0


def sign(chat_id: str) -> str:
    payload = {"chat_id": str(chat_id), "exp": int(time.time()) + ENTRY_TTL_SEC}
    raw = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(settings.pulse_entry_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def issue(chat_id: str) -> str:
    if str(chat_id) not in {str(item) for item in settings.karina_role_ids}:
        raise PermissionError("этот чат не в роли Карины")
    if not settings.pulse_entry_secret:
        raise RuntimeError("PULSE_ENTRY_SECRET не задан")
    global _health_ok_until
    if time.time() >= _health_ok_until:
        url = settings.pulse_public_url.rstrip("/") + "/health"
        try:
            resp = requests.get(url, timeout=3)
            body = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError("пульс не ответил") from exc
        if body.get("status") != "ok":
            raise RuntimeError("пульс ещё не готов")
        _health_ok_until = time.time() + 600
    token = sign(str(chat_id))
    return f"{settings.pulse_public_url.rstrip('/')}/enter?token={token}"
