"""Состав онлайн-записи берётся из YCLIENTS, а не из ручного seed."""
from datetime import date, datetime

from sqlalchemy import select

from app import yclients_client
from app.booking_logic import slots_for_master
from app.models import Master, MasterShift
from app.seed import apply_seed
from app.sync import sync_staff_from_yclients


def test_sync_staff_activates_scheduled_masters(db_session, monkeypatch):
    people = [
        {"id": 5824353, "name": "Светлана", "fired": 0, "hidden": 0, "is_bookable": 1,
         "has_schedule": 1, "specialization": "грумер", "services_links": [1]},
        {"id": 5824356, "name": "Алла", "fired": 0, "hidden": 0, "is_bookable": 1,
         "has_schedule": 1, "specialization": "грумер", "services_links": [1]},
        {"id": 5927391, "name": "Анастасия", "fired": 0, "hidden": 0, "is_bookable": 0,
         "has_schedule": 1, "specialization": "грумер", "services_links": []},
        {"id": 5869176, "name": "Максим", "fired": 0, "hidden": 0, "is_bookable": 0,
         "has_schedule": 0, "specialization": "Администратор", "services_links": []},
    ]
    called = {}

    def fake_ensure(ids, catalog=None):
        called["ids"] = list(ids)
        return {"updated": 1}

    monkeypatch.setattr(yclients_client, "get_staff", lambda: people)
    monkeypatch.setattr(yclients_client, "ensure_staff_on_services", fake_ensure)

    result = sync_staff_from_yclients(db_session)

    assert db_session.get(Master, "svetlana").active
    assert db_session.get(Master, "alla").active
    nastya = db_session.get(Master, "anastasiya")
    assert nastya is not None
    assert nastya.active
    assert nastya.yclients_staff_id == 5927391
    assert db_session.execute(select(Master).where(Master.name == "Максим")).scalars().first() is None
    assert 5927391 in result["need_link"]
    assert 5927391 in called["ids"]


def test_seed_does_not_disable_yclients_master(db_session):
    alla = db_session.get(Master, "alla")
    alla.yclients_staff_id = 5824356
    alla.active = True
    db_session.commit()
    apply_seed(db_session)
    assert db_session.get(Master, "alla").active


def test_yclients_shift_limits_hours(db_session):
    master = db_session.get(Master, "svetlana")
    day = date(2026, 8, 10)
    db_session.add(MasterShift(
        master_id="svetlana", date_iso=day.isoformat(), starts_at="17:00", ends_at="19:00",
    ))
    db_session.commit()
    now = datetime(2026, 8, 9, 9, 0)
    slots = slots_for_master(db_session, master, day, 90, now=now)
    assert "10:00" not in slots
    assert "17:00" in slots
    assert "17:30" in slots
    assert "18:00" not in slots


def test_empty_yclients_shift_is_day_off(db_session):
    master = db_session.get(Master, "svetlana")
    day = date(2026, 8, 10)
    db_session.add(MasterShift(
        master_id="svetlana", date_iso=day.isoformat(), starts_at="", ends_at="",
    ))
    db_session.commit()
    now = datetime(2026, 8, 9, 9, 0)
    assert slots_for_master(db_session, master, day, 90, now=now) == []
