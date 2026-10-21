from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional

log = logging.getLogger(__name__)

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
    Пробуем несколько вариантов: raw тело, stripped тело, sha1= префикс.
    """
    got_raw = (x_signature_header or "").strip()
    # amoCRM может слать "sha1=<hex>" или просто "<hex>"
    got = got_raw.lower()
    if got.startswith("sha1="):
        got = got[5:]

    # два варианта тела: raw и stripped (amo иногда trim-ит тело до подписи)
    for body_variant in (raw_body, raw_body.strip()):
        expected = hmac.new(
            channel_secret.encode("utf-8"),
            body_variant,
            hashlib.sha1,
        ).hexdigest()
        if got and hmac.compare_digest(expected.lower(), got):
            return True

    # диагностика при провале — логируем только длины и первые символы (без секрета и тела)
    log.warning(
        "AMOJO_SIG_FAIL header_present=%s header_prefix=%r body_len=%d",
        bool(got_raw),
        got_raw[:10] if got_raw else "",
        len(raw_body),
    )
    return False
