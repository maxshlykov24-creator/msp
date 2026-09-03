from __future__ import annotations

import logging

import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

log = logging.getLogger("auth")

COOKIE_NAME = "dka_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 дней

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="dka-auth")


def verify_credentials(login: str, password: str) -> bool:
    if login != settings.auth_login:
        return False
    if not settings.auth_password_hash:
        log.error("AUTH_PASSWORD_HASH не задан")
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), settings.auth_password_hash.encode("utf-8")
        )
    except ValueError:
        log.error("Некорректный AUTH_PASSWORD_HASH")
        return False


def make_session_token(login: str) -> str:
    return _serializer.dumps({"login": login})


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
    except BadSignature:
        return False
    except Exception:  # noqa: BLE001 (просроченная/битая кука)
        return False
    return data.get("login") == settings.auth_login


def set_session_cookie(response, login: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(login),
        max_age=MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_auth_redirect(request: Request) -> RedirectResponse | None:
    if is_authenticated(request):
        return None
    return RedirectResponse(url="/login", status_code=302)
