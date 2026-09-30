"""Срез дня салона из Postgres. Тот же журнал, что отдаёт админ-бот."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Booking, BookingStatus


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
    missing = 0
    for booking in visits:
        if booking.starts_at > moment:
            continue
        from . import photos
        if not photos.has_pair(db, booking.id):
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
