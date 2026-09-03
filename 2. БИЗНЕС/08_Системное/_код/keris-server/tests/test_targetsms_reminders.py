"""SMS (targetsms) в напоминаниях и в «спасибо за запись» — параллельно Telegram/MAX,
всем клиентам, независимо от привязки бота и от источника записи (см. reminders.py)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from app import reminders, sync
from app.config import settings
from app.models import Booking, BookingStatus, PetType
from app.reminders import (
    run_once,
    send_booking_created,
    sms_booking_created_text,
    sms_reminder_text,
)

from .test_yclients_webhook import mapped, record_event


def make_booking(db, starts_at, chat_id=None, sent=None, bid="KERIS-9101",
                  phone="+79990000009", master_id="svetlana"):
    b = Booking(
        id=bid, owner_name="Тест", owner_phone=phone, pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id=master_id,
        starts_at=starts_at, ends_at=starts_at + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True, telegram_chat_id=chat_id,
        reminders_sent=sent or [],
    )
    db.add(b)
    db.commit()
    return b


def _sms_enabled(monkeypatch):
    monkeypatch.setattr(
        reminders, "settings",
        dataclasses.replace(
            settings,
            targetsms_enabled=True,
            targetsms_token="test-token",
            targetsms_sender="KerisClub",
        ),
    )


def _capture_sms(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        reminders.targetsms_client, "send_sms",
        lambda phone, text: calls.append((phone, text)) or {"id_sms": "1", "action": "send"},
    )
    return calls


# --- тексты -------------------------------------------------------------------------


def test_sms_booking_created_text_format(db_session):
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0))
    text = sms_booking_created_text(b, "Светлана")
    assert text == "Keris Club. Спасибо за запись. Мастер Светлана, 17.08 20:00."


def test_sms_reminder_text_24h_format(db_session):
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0))
    text = sms_reminder_text(b, "Светлана", 24)
    assert text == "Keris Club. Завтра ждём вас. Мастер Светлана, 20:00."


def test_sms_reminder_text_3h_format(db_session):
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0))
    text = sms_reminder_text(b, "Светлана", 3)
    assert text == "Keris Club. Уже скоро ждём вас. Мастер Светлана, 20:00."


def test_sms_texts_fit_one_part(db_session):
    """70 символов — предел одной SMS на кириллице, дальше цена удваивается."""
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0))
    for text in (
        sms_booking_created_text(b, "Александра"),
        sms_reminder_text(b, "Александра", 24),
        sms_reminder_text(b, "Александра", 3),
    ):
        assert len(text) <= 70, text


# --- send_booking_created -------------------------------------------------------------


def test_send_booking_created_sends_sms_even_without_telegram(monkeypatch, db_session):
    _sms_enabled(monkeypatch)
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0), chat_id=None)
    calls = _capture_sms(monkeypatch)
    assert send_booking_created(b, "Гигиена") is True
    assert len(calls) == 1
    assert calls[0][0] == "+79990000009"
    assert "Спасибо за запись. Мастер Светлана" in calls[0][1]


def test_send_booking_created_sms_disabled_by_default(monkeypatch, db_session):
    """Без TARGETSMS_ENABLED канал молчит — не должен даже пытаться дернуть API."""
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0), chat_id=None)
    calls = _capture_sms(monkeypatch)
    assert send_booking_created(b, "Гигиена") is False
    assert calls == []


def test_send_booking_created_sms_failure_does_not_break_telegram(monkeypatch, db_session):
    _sms_enabled(monkeypatch)
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0), chat_id=777)
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: True)

    def failing_send(phone, text):
        from app.targetsms_client import TargetSMSError
        raise TargetSMSError("Недостаточно средств для отправки SMS")

    monkeypatch.setattr(reminders.targetsms_client, "send_sms", failing_send)
    assert send_booking_created(b, "Гигиена") is True


def test_send_booking_created_only_telegram_skips_sms_resend(monkeypatch, db_session):
    """`only=` — досылка в один только что привязанный канал, SMS уже была отправлена
    при первой попытке и не должна дублироваться."""
    _sms_enabled(monkeypatch)
    b = make_booking(db_session, datetime(2026, 8, 17, 20, 0), chat_id=777)
    calls = _capture_sms(monkeypatch)
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: True)
    send_booking_created(b, "Гигиена", only="telegram")
    assert calls == []


def test_booking_from_yclients_sends_sms(monkeypatch, db_session):
    """Запись из журнала YCLIENTS (Яндекс.Карты, 2ГИС, администратор) — тот же SMS,
    что и с сайта: «любой источник» держится на общем send_booking_created."""
    _sms_enabled(monkeypatch)
    mapped(db_session)
    calls = _capture_sms(monkeypatch)
    monkeypatch.setattr(sync, "push_booking_to_yclients", lambda *a, **kw: None)

    result = sync.handle_yclients_webhook(db_session, record_event())

    assert result["status"] == "created"
    assert len(calls) == 1
    assert calls[0][0] == "+79990000781"
    assert "Спасибо за запись" in calls[0][1]


# --- run_once (24ч/3ч) ------------------------------------------------------------------


def test_run_once_sends_sms_to_client_without_telegram(monkeypatch, db_session):
    _sms_enabled(monkeypatch)
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), chat_id=None)
    calls = _capture_sms(monkeypatch)
    assert run_once(db_session, now) == 1
    assert len(calls) == 1
    assert "Завтра ждём вас. Мастер Светлана" in calls[0][1]
    booking = db_session.get(Booking, "KERIS-9101")
    assert booking.reminders_sent == [24]


def test_run_once_marks_not_sent_when_all_channels_fail(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), chat_id=None)
    # SMS выключены (по умолчанию), telegram не привязан — обоих каналов нет.
    assert run_once(db_session, now) == 0
    booking = db_session.get(Booking, "KERIS-9101")
    assert booking.reminders_sent == []


def test_run_once_sends_sms_in_addition_to_telegram(monkeypatch, db_session):
    _sms_enabled(monkeypatch)
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=2, minutes=50), chat_id=777, sent=[24])
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: True)
    sms_calls = _capture_sms(monkeypatch)
    assert run_once(db_session, now) == 1
    assert len(sms_calls) == 1
    assert "Уже скоро ждём вас" in sms_calls[0][1]
