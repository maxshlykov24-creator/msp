"""Срез дня салона из Postgres. Тот же журнал, что отдаёт админ-бот.

Кнопка в боте читает уже собранный срез. Сбор раз в 10 минут, не в момент нажатия.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Booking, BookingStatus

CACHE_SEC = 600
_today: dict | None = None
_today_at: datetime | None = None
_lists: dict[tuple[str, int], tuple[datetime, list]] = {}


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())


def period_stats(db: Session, start: date, end: date, *, now: datetime | None = None) -> dict:
    """end не входит. Визиты — не отмена и не неявка. Сумма по ним."""
    moment = now or clock.now()
    start_at, end_at = _bounds(start, end)
    rows = db.execute(
        select(Booking).where(Booking.starts_at >= start_at, Booking.starts_at < end_at)
    ).scalars().all()
    visits = [b for b in rows if b.status not in (BookingStatus.cancelled, BookingStatus.no_show)]
    started = [b for b in visits if b.starts_at <= moment]
    from . import photos
    shots = photos.by_booking_ids(db, [b.id for b in started])
    missing = 0
    for booking in started:
        kinds = {item["kind"] for item in shots.get(booking.id, [])}
        if "before" not in kinds or "after" not in kinds:
            missing += 1
    return {
        "visits": len(visits),
        "revenue": sum(int(b.price or 0) for b in visits),
        "cancelled": sum(1 for b in rows if b.status == BookingStatus.cancelled),
        "no_show": sum(1 for b in rows if b.status == BookingStatus.no_show),
        "photos_missing": missing,
    }


def today_stats(db: Session, *, now: datetime | None = None) -> dict:
    moment = now or clock.now()
    return period_stats(db, moment.date(), moment.date() + timedelta(days=1), now=moment)


def cache_enabled() -> bool:
    return os.environ.get("KERIS_BOARD_CACHE", "1") != "0"


def peek_today() -> dict | None:
    if not cache_enabled() or _today is None or _today_at is None:
        return None
    if (clock.now() - _today_at).total_seconds() > CACHE_SEC:
        return None
    return _today


def store_today(payload: dict, *, at: datetime | None = None) -> dict:
    global _today, _today_at
    _today = payload
    _today_at = at or clock.now()
    return payload


def refresh_today(db: Session) -> dict:
    return store_today(today_stats(db))


def peek_bookings(when: str, for_photos: int) -> list | None:
    if not cache_enabled():
        return None
    row = _lists.get((when, int(for_photos)))
    if row is None:
        return None
    stored_at, rows = row
    if (clock.now() - stored_at).total_seconds() > CACHE_SEC:
        return None
    return rows


def store_bookings(when: str, for_photos: int, rows: list, *, at: datetime | None = None) -> None:
    _lists[(when, int(for_photos))] = (at or clock.now(), rows)


def drop_lists() -> None:
    _lists.clear()
