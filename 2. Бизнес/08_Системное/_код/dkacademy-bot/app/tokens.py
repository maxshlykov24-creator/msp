from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AmoOAuthToken
from app.oauth_amocrm import refresh_tokens, token_expires_at

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_valid_access_token(db: Session) -> str:
    settings = get_settings()
    row = db.execute(select(AmoOAuthToken).where(AmoOAuthToken.id == 1)).scalar_one_or_none()
    if row and row.access_token:
        if row.expires_at and row.refresh_token and row.expires_at <= _now() + timedelta(minutes=2):
            try:
                data = refresh_tokens(refresh_token=row.refresh_token)
                row.access_token = data.get("access_token", row.access_token)
                row.refresh_token = data.get("refresh_token", row.refresh_token) or row.refresh_token
                row.expires_at = token_expires_at(data)
                row.extra = {**(row.extra or {}), "last_refresh": _now().isoformat()}
                db.commit()
            except Exception as e:
                log.warning("Refresh token failed: %s", e)
        if row.expires_at is None or row.expires_at > _now():
            return row.access_token
    if settings.amo_long_lived_token:
        return settings.amo_long_lived_token.strip()
    raise RuntimeError("Нет access_token: выполните OAuth /oauth/callback или задайте AMO_LONG_LIVED_TOKEN")
