"""ДР питомца и возврат «давно не был» — то, что иначе делают руками."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from app import reminders
from app.config import settings
from app.models import Booking, BookingStatus, ClientOutreach, PetType

NOW = datetime(2026, 8, 20, 11, 0)


def make_booking(db, bid, phone, starts_at, birth_date="", pet="Моня",
                 status=BookingStatus.completed):
    b = Booking(
        id=bid, owner_name="Ольга", owner_phone=phone, pet_name=pet,
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts_at, ends_at=starts_at + timedelta(minutes=90), price=7800,
        status=status, personal_data_consent=True, pet_birth_date=birth_date,
    )
    db.add(b)
    db.commit()
    return b


def _channel(monkeypatch, sent):
    monkeypatch.setattr(reminders, "notify_phone", lambda db, phone, text, sms_text="": sent.append((phone, text)) or ["telegram"])


def test_parses_both_date_formats():
    assert reminders.parse_pet_birth_date("2021-08-20") == (20, 8)
    assert reminders.parse_pet_birth_date("20.08.2021") == (20, 8)
    assert reminders.parse_pet_birth_date("") is None
    assert reminders.parse_pet_birth_date("когда-то летом") is None


def test_birthday_greeting_sent_once_per_year(monkeypatch, db_session):
    make_booking(db_session, "KERIS-8001", "+79991110001", NOW - timedelta(days=30),
                 birth_date="2021-08-20")
    sent = []
    _channel(monkeypatch, sent)

    assert reminders.send_birthday_greetings(db_session, NOW) == 1
    assert "Моня" in sent[0][1]
    # Пятиминутный цикл не дублирует.
    assert reminders.send_birthday_greetings(db_session, NOW + timedelta(minutes=5)) == 0
    assert db_session.query(ClientOutreach).count() == 1

    # Следующий год — поздравляем снова.
    assert reminders.send_birthday_greetings(db_session, NOW.replace(year=2027)) == 1


def test_birthday_ignores_other_days_and_night(monkeypatch, db_session):
    make_booking(db_session, "KERIS-8002", "+79991110002", NOW - timedelta(days=30),
                 birth_date="2021-08-21")
    sent = []
    _channel(monkeypatch, sent)

    assert reminders.send_birthday_greetings(db_session, NOW) == 0
    # День тот, но ночь: сообщение ждёт рабочих часов салона.
    assert reminders.send_birthday_greetings(db_session, NOW.replace(day=21, hour=4)) == 0
    assert reminders.send_birthday_greetings(db_session, NOW.replace(day=21, hour=11)) == 1


def test_reactivation_after_n_days(monkeypatch, db_session):
    monkeypatch.setattr(
        reminders, "settings",
        dataclasses.replace(settings, reactivation_days=60, reactivation_enabled=True),
    )
    make_booking(db_session, "KERIS-8003", "+79991110003", NOW - timedelta(days=70))
    sent = []
    _channel(monkeypatch, sent)

    assert reminders.send_reactivations(db_session, NOW) == 1
    assert "60" in sent[0][1]
    assert reminders.send_reactivations(db_session, NOW + timedelta(days=1)) == 0


def test_reactivation_skips_recent_and_those_with_future_visit(monkeypatch, db_session):
    monkeypatch.setattr(
        reminders, "settings",
        dataclasses.replace(settings, reactivation_days=60, reactivation_enabled=True),
    )
    make_booking(db_session, "KERIS-8004", "+79991110004", NOW - timedelta(days=10))
    make_booking(db_session, "KERIS-8005", "+79991110005", NOW - timedelta(days=90))
    make_booking(db_session, "KERIS-8006", "+79991110005", NOW + timedelta(days=3),
                 status=BookingStatus.confirmed)
    sent = []
    _channel(monkeypatch, sent)

    assert reminders.send_reactivations(db_session, NOW) == 0
    assert sent == []


def test_reactivation_repeats_after_new_visit(monkeypatch, db_session):
    """Пришёл и снова пропал — сообщение уходит заново, тег другой."""
    monkeypatch.setattr(
        reminders, "settings",
        dataclasses.replace(settings, reactivation_days=60, reactivation_enabled=True),
    )
    make_booking(db_session, "KERIS-8007", "+79991110007", NOW - timedelta(days=200))
    sent = []
    _channel(monkeypatch, sent)
    assert reminders.send_reactivations(db_session, NOW) == 1

    make_booking(db_session, "KERIS-8008", "+79991110007", NOW - timedelta(days=61))
    assert reminders.send_reactivations(db_session, NOW) == 1
    assert db_session.query(ClientOutreach).count() == 2


def test_run_once_includes_outreach(monkeypatch, db_session):
    make_booking(db_session, "KERIS-8009", "+79991110009", NOW - timedelta(days=30),
                 birth_date="2020-08-20")
    monkeypatch.setattr(
        reminders, "settings",
        dataclasses.replace(settings, reactivation_days=0, reactivation_enabled=False),
    )
    sent = []
    _channel(monkeypatch, sent)

    assert reminders.run_once(db_session, NOW) == 1
    assert len(sent) == 1
