from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def token_url(settings: Settings) -> str:
    return f"https://{settings.amo_subdomain}.{settings.amo_base_domain}/oauth2/access_token"


def api_base(settings: Settings) -> str:
    return f"https://{settings.amo_subdomain}.{settings.amo_base_domain}"


def exchange_code_for_tokens(
    *,
    code: str,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    body = {
        "client_id": settings.amo_client_id,
        "client_secret": settings.amo_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(token_url(settings), json=body)
        r.raise_for_status()
        return r.json()


def refresh_tokens(
    *,
    refresh_token: str,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    body = {
        "client_id": settings.amo_client_id,
        "client_secret": settings.amo_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": settings.redirect_uri,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(token_url(settings), json=body)
        r.raise_for_status()
        return r.json()


def token_expires_at(data: dict[str, Any]) -> Optional[datetime]:
    exp = data.get("expires_in")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(exp))


def fetch_account_amojo_id(*, access_token: str, settings: Optional[Settings] = None) -> str:
    settings = settings or get_settings()
    url = f"{api_base(settings)}/api/v4/account"
    params = {"with": "amojo_id"}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
    amojo = (data or {}).get("amojo_id")
    if not amojo:
        raise ValueError("Ответ account не содержит amojo_id")
    return str(amojo)
