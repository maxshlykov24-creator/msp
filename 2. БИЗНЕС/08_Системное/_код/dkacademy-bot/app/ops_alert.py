"""Алерты в ops Telegram-бот при нераспознанных статусах LiveInform."""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.delivery_semantics import collect_classify_debug
from app.models import ProcessedEvent
from app.telegram_api import send_message_sync

log = logging.getLogger(__name__)


def _ops_dedup_key(tracking_norm: str, debug: dict[str, str]) -> str:
    raw = "|".join(
        [
            tracking_norm,
            debug.get("code", ""),
            debug.get("status_line", ""),
            debug.get("track_blob", ""),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"li_ops:{tracking_norm}:{digest}"[:511]


def format_unclassified_alert(
    *,
    tracking: str,
    liveinform_id: str = "",
    debug: dict[str, str],
) -> str:
    lines = [
        "⚠️ Нераспознанный статус доставки",
        "",
        "Клиенту НЕ отправлено (серый статус — не в whitelist и не явный отказ).",
        "Если этот статус нужен — добавь алиас в delivery_semantics.py и задеплой.",
        "",
        f"Трек: {tracking or '—'}",
    ]
    if liveinform_id:
        lines.append(f"liveinform_id: {liveinform_id}")
    lines.extend(
        [
            f"Код доставки: {debug.get('code') or '—'}",
            f"status (сырой): {debug.get('status') or '—'}",
            f"track_status: {debug.get('track_status') or '—'}",
            f"delivery (ТК): {debug.get('delivery') or '—'}",
            "",
            f"Поле CRM / status_line:\n{(debug.get('status_line') or '—')[:800]}",
            "",
            f"Текст трека (blob):\n{(debug.get('track_blob') or '—')[:800]}",
        ]
    )
    return "\n".join(lines)


def maybe_alert_unclassified(
    db: Session,
    *,
    tracking_norm: str,
    tracking_raw: str,
    liveinform_id: str,
    payload: dict[str, Any],
    status_line: str,
) -> None:
    """Шлёт алерт в ops-бот один раз на уникальный (трек + текст статуса)."""
    s = get_settings()
    token = (s.ops_alert_bot_token or "").strip()
    chat_id = (s.ops_alert_chat_id or "").strip()
    if not token or not chat_id:
        log.info(
            "ops_alert: пропуск (нет OPS_ALERT_BOT_TOKEN или OPS_ALERT_CHAT_ID)"
        )
        return

    debug = collect_classify_debug(payload, status_line=status_line)
    dedup_key = _ops_dedup_key(tracking_norm, debug)
    exists = db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == "liveinform",
            ProcessedEvent.dedup_key == dedup_key,
        )
    ).scalar_one_or_none()
    if exists:
        log.info("ops_alert: уже слали, skip %s", dedup_key[:100])
        return

    text = format_unclassified_alert(
        tracking=tracking_raw or tracking_norm,
        liveinform_id=liveinform_id or "",
        debug=debug,
    )
    ok, err = send_message_sync(token, chat_id, text)
    if not ok:
        log.warning("ops_alert: send failed err=%s chat=%s", err, chat_id)
        return

    try:
        db.add(ProcessedEvent(source="liveinform", dedup_key=dedup_key))
        db.commit()
    except IntegrityError:
        db.rollback()
    log.info("ops_alert: отправлено track=%s", tracking_norm)
