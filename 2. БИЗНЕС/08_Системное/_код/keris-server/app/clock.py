"""Единое «сейчас» в часовом поясе салона.

Слоты и `starts_at` хранятся как naive-время салона (Москва), поэтому сравнивать
их с `datetime.utcnow()` нельзя: на VPS в UTC лид-тайм 24 ч и напоминания
поехали бы на 3 часа. Всё время в бизнес-логике берётся отсюда.
"""
from __future__ import annotations

import os
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.environ.get("SALON_TZ", "Europe/Moscow"))
except Exception:  # pragma: no cover — нет tzdata: работаем по времени системы
    _TZ = None


def now() -> datetime:
    """Текущее время салона, naive — в одной системе координат с starts_at."""
    if _TZ is None:
        return datetime.now()
    return datetime.now(_TZ).replace(tzinfo=None)


def today() -> date:
    return now().date()


def to_salon_naive(dt: datetime) -> datetime:
    """Время из внешнего API (с offset, напр. `2026-08-09T17:00:00+03:00`) —
    в ту же систему координат, что starts_at."""
    if dt.tzinfo is None:
        return dt
    if _TZ is None:
        return dt.replace(tzinfo=None)
    return dt.astimezone(_TZ).replace(tzinfo=None)
