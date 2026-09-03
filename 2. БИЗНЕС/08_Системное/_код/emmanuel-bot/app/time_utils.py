"""Неделя отчёта и дедлайны в часовом поясе Europe/Moscow."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    return datetime.now(MSK)


def to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def week_start_from_date(d: date) -> date:
    """Понедельник недели, за которую пишется отчёт (неделя «замыкается» в воскресенье)."""
    wd = d.weekday()  # Mon=0 ... Sun=6
    if wd == 6:
        sunday = d
    else:
        sunday = d - timedelta(days=weekday_index_to_days_since_sunday(wd))
    return sunday - timedelta(days=6)


def weekday_index_to_days_since_sunday(wd: int) -> int:
    """Сколько дней назад было воскресенье (не включая текущее воскресенье)."""
    return wd + 1


def submission_deadline_msk(week_start: date) -> datetime:
    """Дедлайн «вовремя»: понедельник 00:00 МСК после закрывающего воскресенья недели."""
    sunday = week_start + timedelta(days=6)
    monday_after = sunday + timedelta(days=1)
    return datetime(
        monday_after.year,
        monday_after.month,
        monday_after.day,
        0,
        0,
        0,
        tzinfo=MSK,
    )


def digest_week_monday_on_digest_day(monday_digest: date) -> date:
    """Неделя, по которой дайджест в понедельник утром: неделя, завершившаяся вчера."""
    sunday_before = monday_digest - timedelta(days=1)
    return sunday_before - timedelta(days=6)


def submitted_on_time(submitted_at: datetime, week_start: date) -> bool:
    deadline = submission_deadline_msk(week_start)
    return to_msk(submitted_at) < deadline


def streak_after_submit(
    previous_streak: int,
    last_week_start: date | None,
    new_week_start: date,
    on_time: bool,
) -> int:
    if last_week_start is None:
        return 1 if on_time else 0
    expected = last_week_start + timedelta(days=7)
    consecutive = new_week_start == expected
    if not consecutive:
        return 1 if on_time else 0
    return (previous_streak + 1) if on_time else 0
