from datetime import datetime, timedelta

from app import reminders, yclients_client
from app.models import Booking, BookingStatus, Master, PetType, Service
from app.reminders import (
    booking_created_client_text,
    confirm_booking,
    confirm_keyboard,
    due_reminders,
    is_confirmed,
    reminder_text,
    retry_pending_thanks,
    run_once,
    send_booking_created,
)


def make_booking(db, starts_at, chat_id=555, sent=None, status=BookingStatus.confirmed, bid="KERIS-9001"):
    b = Booking(
        id=bid, owner_name="Тест", owner_phone="+79990000009", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts_at, ends_at=starts_at + timedelta(minutes=90), price=7800,
        status=status, personal_data_consent=True, telegram_chat_id=chat_id,
        reminders_sent=sent or [],
    )
    db.add(b)
    db.commit()
    return b


def test_reminder_due_24h(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45))
    due = due_reminders(db_session, now)
    assert [(b.id, h) for b, h in due] == [("KERIS-9001", 24)]


def test_reminder_not_due_yet(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=30))
    assert due_reminders(db_session, now) == []


def test_reminder_not_repeated(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), sent=[24])
    assert due_reminders(db_session, now) == []


def test_reminder_3h(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=2, minutes=50), sent=[24])
    due = due_reminders(db_session, now)
    assert [h for _, h in due] == [3]


def test_cancelled_booking_gets_no_reminder(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), status=BookingStatus.cancelled)
    assert due_reminders(db_session, now) == []


def test_no_show_booking_gets_no_reminder(db_session):
    """«Не пришел» в журнале ставят и как отмену — напоминать о таком визите нельзя."""
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), status=BookingStatus.no_show)
    assert due_reminders(db_session, now) == []


def test_booking_without_telegram_still_due_for_sms(db_session):
    """С подключением SMS (targetsms) напоминание актуально даже без привязанного бота —
    run_once сам решит, что реально удастся отправить (см. test_targetsms_reminders.py)."""
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45), chat_id=None)
    due = due_reminders(db_session, now)
    assert [(b.id, h) for b, h in due] == [("KERIS-9001", 24)]


def test_reminder_text_mentions_gifts(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    b = make_booking(db_session, now + timedelta(hours=23))
    b.free_addon_ids = ["dog_mask_hydra"]
    text = reminder_text(b, "Комплекс со стрижкой", 24)
    assert "подарки" in text and "Комплекс со стрижкой" in text
    assert "<b>" in text  # HTML-разметка для Telegram


def test_reminder_text_cta_only_when_ask_confirm(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    b = make_booking(db_session, now + timedelta(hours=23))
    plain = reminder_text(b, "Гигиена", 24, ask_confirm=False)
    with_cta = reminder_text(b, "Гигиена", 24, ask_confirm=True)
    assert "кнопкой ниже" not in plain
    assert "кнопкой ниже" in with_cta


# ── «Спасибо, что записались» сразу после оформления ────────────────────────

def test_booking_created_text_mentions_pet_service_price(db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    text = booking_created_client_text(b, "Гигиена")
    assert "Барс" in text and "Гигиена" in text and "7800 ₽" in text
    assert b.id in text
    assert "Спасибо, что записались" in text


def test_booking_created_text_gifts_and_subscription(db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.free_addon_ids = ["dog_mask_hydra"]
    b.subscription_id = 1
    b.visits_charged = 1.0
    b.price = 500
    text = booking_created_client_text(b, "Комплекс")
    assert "подарки" in text
    assert "По абонементу (списано визитов: 1)" in text and "доплата 500 ₽" in text


def test_send_booking_created_skipped_without_chat_id(monkeypatch, db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0), chat_id=None)
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: sent.append(a) or True)
    assert send_booking_created(b, "Гигиена") is False
    assert sent == []


def test_send_booking_created_sent_with_chat_id(monkeypatch, db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0), chat_id=777)
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda chat_id, text, keyboard=None: sent.append((chat_id, text)) or True)
    assert send_booking_created(b, "Гигиена") is True
    assert sent[0][0] == 777 and "Спасибо" in sent[0][1]
    assert "telegram" in (b.thanks_channels or [])
    assert b.thanks_pending is False


def test_send_booking_created_marks_pending_when_telegram_fails(monkeypatch, db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0), chat_id=777)
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: False)
    assert send_booking_created(b, "Гигиена") is False
    assert b.thanks_pending is True
    assert "telegram" not in (b.thanks_channels or [])


def test_retry_pending_thanks_sends_only_missing_telegram(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    b = make_booking(db_session, now + timedelta(days=2), chat_id=777)
    b.thanks_pending = True
    db_session.commit()
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda chat_id, text, keyboard=None: sent.append(chat_id) or True)
    assert retry_pending_thanks(db_session, now) == 1
    assert sent == [777]
    db_session.refresh(b)
    assert b.thanks_pending is False
    assert "telegram" in (b.thanks_channels or [])


def test_retry_pending_thanks_skips_when_flag_off(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(days=2), chat_id=777)
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: sent.append(1) or True)
    assert retry_pending_thanks(db_session, now) == 0
    assert sent == []


# ── Кнопка «Подтверждаю» и её отражение в YCLIENTS ──────────────────────────

def test_is_confirmed_false_without_flag_or_record(db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    assert is_confirmed(db_session, b) is False


def test_is_confirmed_true_after_local_flag(db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.client_confirmed_at = datetime(2026, 8, 9, 10, 0)
    db_session.commit()
    assert is_confirmed(db_session, b) is True


def test_is_confirmed_checks_yclients_attendance(monkeypatch, db_session):
    """Админ подтвердил вручную в журнале YCLIENTS (attendance=2) — у нас локального
    флага ещё нет, но is_confirmed должен это увидеть и закэшировать."""
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.yclients_record_id = 555
    db_session.commit()
    monkeypatch.setattr(yclients_client, "get_record",
                         lambda record_id, company_id=None: {"data": {"attendance": 2}})
    assert is_confirmed(db_session, b) is True
    assert b.client_confirmed_at is not None  # закэшировано, повторно YCLIENTS не спросим


def test_is_confirmed_false_when_yclients_attendance_not_set(monkeypatch, db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.yclients_record_id = 555
    db_session.commit()
    monkeypatch.setattr(yclients_client, "get_record",
                         lambda record_id, company_id=None: {"data": {"attendance": 0}})
    assert is_confirmed(db_session, b) is False


def test_confirm_booking_sets_flag_and_pushes_yclients(monkeypatch, db_session):
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.yclients_record_id = 777
    db_session.commit()
    # YCLIENTS-маппинг мастера/услуги — так же, как в test_yclients_webhook.mapped()
    db_session.get(Master, "svetlana").yclients_staff_id = 9001
    db_session.get(Service, "dog_complex_cut").yclients_service_ids = {"XS": 9101}
    db_session.commit()
    calls = []
    monkeypatch.setattr(yclients_client, "confirm_record",
                         lambda record_id, payload, company_id=None: calls.append((record_id, payload)))
    confirm_booking(db_session, b)
    assert b.client_confirmed_at is not None
    assert len(calls) == 1 and calls[0][0] == 777


def test_confirm_booking_does_not_crash_when_not_mapped_to_yclients(monkeypatch, db_session):
    """Мастер/услуга ещё не сопоставлены с YCLIENTS — подтверждение всё равно
    сохраняется локально, push просто пропускается (см. push_confirm_to_yclients)."""
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    b.yclients_record_id = 777
    db_session.commit()
    confirm_booking(db_session, b)
    assert b.client_confirmed_at is not None


def test_confirm_booking_ok_without_yclients_record(db_session):
    """Запись ещё не ушла в YCLIENTS — подтверждение всё равно фиксируется локально."""
    b = make_booking(db_session, datetime(2026, 8, 10, 12, 0))
    confirm_booking(db_session, b)
    assert b.client_confirmed_at is not None


def test_run_once_sends_confirm_button_when_not_confirmed(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    make_booking(db_session, now + timedelta(hours=23, minutes=45))
    sent = []
    monkeypatch.setattr(reminders, "send_to_client",
                         lambda chat_id, text, keyboard=None: sent.append(keyboard) or True)
    assert run_once(db_session, now) == 1
    assert sent == [confirm_keyboard(db_session.query(Booking).one())]


def test_run_once_omits_button_when_already_confirmed(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    b = make_booking(db_session, now + timedelta(hours=23, minutes=45))
    b.client_confirmed_at = datetime(2026, 8, 9, 9, 0)
    db_session.commit()
    sent = []
    monkeypatch.setattr(reminders, "send_to_client",
                         lambda chat_id, text, keyboard=None: sent.append(keyboard) or True)
    assert run_once(db_session, now) == 1
    assert sent == [None]
