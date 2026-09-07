"""График 4/3 у Светланы и Аллы: смены не совпадают, салон открыт каждый день."""
from datetime import date, datetime, time, timedelta

from app.booking_logic import shift_day_off, slots_for_master
from app.models import Master


def masters(db):
    return db.get(Master, "svetlana"), db.get(Master, "alla")


def test_shift_cycle_is_four_on_three_off(db_session):
    sveta, _ = masters(db_session)
    assert (sveta.shift_on, sveta.shift_off) == (4, 3)
    anchor = date.fromisoformat(sveta.shift_start)
    work = [not shift_day_off(sveta, anchor + timedelta(days=i)) for i in range(7)]
    assert work == [True, True, True, True, False, False, False]


def test_cycle_repeats_next_week(db_session):
    sveta, _ = masters(db_session)
    anchor = date.fromisoformat(sveta.shift_start)
    for i in range(21):
        day = anchor + timedelta(days=i)
        assert shift_day_off(sveta, day) == shift_day_off(sveta, day + timedelta(days=7))


def test_every_day_has_at_least_one_master(db_session):
    sveta, alla = masters(db_session)
    start = date.fromisoformat(sveta.shift_start)
    for i in range(60):
        day = start + timedelta(days=i)
        assert not (shift_day_off(sveta, day) and shift_day_off(alla, day)), f"{day} — никто не работает"


def test_day_off_gives_no_slots(db_session):
    sveta, _ = masters(db_session)
    anchor = date.fromisoformat(sveta.shift_start)
    now = datetime.combine(anchor, time(9, 0))
    assert slots_for_master(db_session, sveta, anchor + timedelta(days=4), 90, now=now) == []
    assert slots_for_master(db_session, sveta, anchor + timedelta(days=1), 90, now=now)


def test_master_without_shift_works_every_day(db_session):
    free = Master(id="tmp", name="Без графика", work_start="10:00", work_end="22:00")
    for i in range(10):
        assert not shift_day_off(free, date(2026, 8, 10) + timedelta(days=i))
