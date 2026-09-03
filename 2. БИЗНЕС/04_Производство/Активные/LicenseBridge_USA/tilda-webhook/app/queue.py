"""Идемпотентная постановка входящих событий в inbox."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InboxEvent

log = logging.getLogger("queue")


def enqueue(
    s: Session,
    source: str,
    event_type: str,
    event_key: str,
    payload: dict[str, Any],
    phone: str | None = None,
) -> tuple[int | None, bool]:
    """Кладёт событие в inbox. Возвращает (id, created). Дубликат по event_key
    не создаёт вторую запись (идемпотентность вебхуков)."""
    existing = s.scalar(select(InboxEvent).where(InboxEvent.event_key == event_key))
    if existing:
        log.info("duplicate event_key=%s (inbox id=%s)", event_key, existing.id)
        return existing.id, False
    ev = InboxEvent(
        source=source, event_type=event_type, event_key=event_key,
        payload=payload, phone=phone, status="pending",
    )
    s.add(ev)
    s.flush()
    return ev.id, True
