from datetime import date, datetime

from app.day_board import prev_week_range, week_range


def test_week_runs_monday_through_sunday():
    wednesday = datetime(2026, 9, 30, 11, 0)
    assert week_range(wednesday) == (date(2026, 9, 28), date(2026, 10, 5))
    assert prev_week_range(wednesday) == (date(2026, 9, 21), date(2026, 9, 28))

    monday = datetime(2026, 9, 28, 0, 1)
    assert week_range(monday) == (date(2026, 9, 28), date(2026, 10, 5))

    sunday = datetime(2026, 10, 4, 23, 0)
    assert week_range(sunday) == (date(2026, 9, 28), date(2026, 10, 5))
    assert prev_week_range(sunday) == (date(2026, 9, 21), date(2026, 9, 28))
