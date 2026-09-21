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


_MONTHS_GEN = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def week_range_label(week_start: date) -> str:
    """Подпись недели в сводке: 14-20 сентября."""
    end = week_start + timedelta(days=6)
    if week_start.month == end.month and week_start.year == end.year:
        return f"{week_start.day}-{end.day} {_MONTHS_GEN[end.month]}"
    if week_start.year == end.year:
        return f"{week_start.day} {_MONTHS_GEN[week_start.month]} - {end.day} {_MONTHS_GEN[end.month]}"
    return (
        f"{week_start.day} {_MONTHS_GEN[week_start.month]} {week_start.year} - "
        f"{end.day} {_MONTHS_GEN[end.month]} {end.year}"
    )


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


def last_sunday_on_or_before(d: date | None = None) -> date:
    d = d or now_msk().date()
    return d - timedelta(days=(d.weekday() + 1) % 7)


def posting_window_for_sunday(sunday: date) -> tuple[datetime, datetime]:
    """Вс 00:00 МСК → вт 00:00 МСК (покрывает вс и пн, как живой ритуал)."""
    start = datetime(sunday.year, sunday.month, sunday.day, 0, 0, 0, tzinfo=MSK)
    end = start + timedelta(days=2)
    return start, end


def digest_week_monday_on_digest_day(monday_digest: date) -> date:
    """Неделя, по которой дайджест в понедельник утром: неделя, завершившаяся вчера."""
    sunday_before = monday_digest - timedelta(days=1)
    return sunday_before - timedelta(days=6)


def monday_after_week(week_start: date) -> datetime:
    """Понедельник 00:00 МСК после закрывающего воскресенья недели."""
    return submission_deadline_msk(week_start)


def late_report_bucket(submitted_at: datetime, week_start: date) -> str:
    """sun | mon_am | mon_pm | later относительно вс 23:59 и половин понедельника."""
    dt = to_msk(submitted_at)
    monday = monday_after_week(week_start)
    noon = monday.replace(hour=12)
    tuesday = monday + timedelta(days=1)
    if dt < monday:
        return "sun"
    if dt < noon:
        return "mon_am"
    if dt < tuesday:
        return "mon_pm"
    return "later"


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
