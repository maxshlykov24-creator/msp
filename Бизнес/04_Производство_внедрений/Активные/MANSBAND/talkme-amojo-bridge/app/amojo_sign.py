from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional

# Подпись исходящих в amojo (новая схема с 2019 г.)


def content_md5_hex(body: bytes) -> str:
    return hashlib.md5(body).hexdigest()


def build_amojo_signature(
    *,
    channel_secret: str,
    method: str,
    content_type: str,
    body: bytes,
    path: str,
    rfc2822_date: Optional[str] = None,
) -> tuple[str, str, str]:
    """
    Возвращает (rfc2822_date, md5_hex, signature_hex).
    path — полный путь с ведущим слэшем, без query.
    """
    if rfc2822_date is None:
        rfc2822_date = format_datetime(datetime.now(timezone.utc))
    md5_hex = content_md5_hex(body)
    sign_str = "\n".join(
        [
            method.upper(),
            md5_hex,
            content_type,
            rfc2822_date,
            path,
        ]
    )
    sig = hmac.new(
        channel_secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return rfc2822_date, md5_hex, sig


def verify_amojo_webhook_body(*, channel_secret: str, raw_body: bytes, x_signature_header: str) -> bool:
    """
    Входящий вебхук v2: X-Signature = HMAC-SHA1(body) в hex.
    """
    expected = hmac.new(
        channel_secret.encode("utf-8"),
        raw_body,
        hashlib.sha1,
    ).hexdigest()
    got = (x_signature_header or "").strip().lower()
    return hmac.compare_digest(expected.lower(), got)
