from __future__ import annotations

import logging

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

log = logging.getLogger("auth")

COOKIE_NAME = "keris_pulse"
MAX_AGE = 60 * 60 * 24 * 30

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="keris-analytics")


def verify_credentials(login: str, password: str) -> bool:
    if login.strip().lower() != settings.auth_login.strip().lower():
        return False
    if not settings.auth_password_hash:
        log.error("AUTH_PASSWORD_HASH не задан — вход невозможен")
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), settings.auth_password_hash.encode("utf-8")
        )
    except ValueError:
        log.error("Некорректный AUTH_PASSWORD_HASH")
        return False


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
    except (BadSignature, Exception):  # noqa: B014
        return False
    return data.get("login") == settings.auth_login


def set_session_cookie(response, login: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _serializer.dumps({"login": login}),
        max_age=MAX_AGE,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path=settings.auth_cookie_path,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path=settings.auth_cookie_path)


def verify_pulse_entry(token: str) -> str | None:
    """Короткая подпись с сервера. Сама по себе сессию не подделывает: вход ставит свою куку."""
    import hashlib
    import hmac
    import json
    import time
    from base64 import urlsafe_b64decode

    secret = settings.pulse_entry_secret
    if not secret or "." not in (token or ""):
        return None
    raw, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        payload = json.loads(urlsafe_b64decode(raw + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    chat_id = str(payload.get("chat_id") or "")
    return chat_id or None
