from __future__ import annotations

import logging

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

log = logging.getLogger("auth")

COOKIE_NAME = "lb_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 дней

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="lb-analytics")


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
    except (BadSignature, Exception):  # noqa: B014 — просроченная/битая кука
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
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME)
