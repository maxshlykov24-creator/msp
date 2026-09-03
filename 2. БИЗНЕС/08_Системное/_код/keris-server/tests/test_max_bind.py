from datetime import datetime, timedelta

from app import max_bind, reminders
from app.max_bind import bind_client, resolve_user_id, user_id_for_phone
from app.models import Booking, BookingStatus, MaxClient, PetType


def test_bind_normalizes_phone_and_dedupes_formats(db_session):
    row, attached = bind_client(db_session, "8 999 368-98-71", 700001)
    assert row is not None
    assert row.phone == "+79993689871"
    assert row.user_id == 700001
    assert attached == []
    again, _ = bind_client(db_session, "+7 (999) 368-98-71", 700001)
    assert again.phone == "+79993689871"
    assert db_session.query(MaxClient).count() == 1
    assert user_id_for_phone(db_session, "79993689871") == 700001


def test_bind_extracts_phone_from_surrounding_text(db_session):
    row, _ = bind_client(db_session, "мой номер 89990001122 спасибо", 700002)
    assert row is not None
    assert row.phone == "+79990001122"


def test_bind_rejects_garbage_phone(db_session):
    row, attached = bind_client(db_session, "привет", 1)
    assert row is None and attached == []


def test_same_user_new_phone_updates_row(db_session):
    bind_client(db_session, "+79990000010", 42)
    bind_client(db_session, "+79990000011", 42)
    rows = db_session.query(MaxClient).all()
    assert len(rows) == 1
    assert rows[0].phone == "+79990000011"


def test_bind_attaches_future_booking(db_session):
    starts = datetime(2026, 9, 1, 12, 0)
    b = Booking(
        id="KERIS-9901", owner_name="Ольга", owner_phone="+79991112233", pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_hygiene",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=5500,
        status=BookingStatus.confirmed, personal_data_consent=True,
    )
    db_session.add(b)
    db_session.commit()
    row, attached = bind_client(db_session, "8 999 111-22-33", 800001)
    assert attached == ["KERIS-9901"]
    db_session.refresh(b)
    assert b.max_user_id == 800001
    assert row.user_id == 800001
    assert b.telegram_chat_id is None


def test_resolve_uses_directory(db_session):
    bind_client(db_session, "+79990000001", 777)
    assert resolve_user_id(db_session, "+79990000001", None) == 777
    assert resolve_user_id(db_session, "+79990000002", None) is None


def test_reminder_due_for_max_only_booking(db_session):
    now = datetime(2026, 8, 10, 12, 0)
    starts = now + timedelta(hours=23, minutes=45)
    b = Booking(
        id="KERIS-9902", owner_name="Тест", owner_phone="+79990000009", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True,
        max_user_id=555001,
    )
    db_session.add(b)
    db_session.commit()
    due = reminders.due_reminders(db_session, now)
    assert [(row.id, h) for row, h in due] == [("KERIS-9902", 24)]


def test_run_once_sends_max_confirm_button(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    starts = now + timedelta(hours=23, minutes=45)
    b = Booking(
        id="KERIS-9903", owner_name="Тест", owner_phone="+79990000019", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True,
        max_user_id=555002,
    )
    db_session.add(b)
    db_session.commit()
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: False)
    monkeypatch.setattr(reminders, "send_to_max", lambda uid, text, buttons=None: sent.append(buttons) or True)
    assert reminders.run_once(db_session, now) == 1
    assert sent == [reminders.confirm_buttons_max(db_session.query(Booking).filter_by(id="KERIS-9903").one())]


def test_notify_client_sends_telegram_and_max_together(monkeypatch, db_session):
    """Один телефон в обоих ботах: «спасибо» и напоминание уходят сразу в оба."""
    from app.telegram_bind import bind_client as bind_tg

    bind_tg(db_session, "+79990000077", 111)
    bind_client(db_session, "+79990000077", 222)
    starts = datetime(2026, 8, 11, 12, 0)
    b = Booking(
        id="KERIS-9904", owner_name="Тест", owner_phone="+79990000077", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True,
        telegram_chat_id=111, max_user_id=222,
    )
    db_session.add(b)
    db_session.commit()
    tg, mx = [], []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: tg.append(a[0]) or True)
    monkeypatch.setattr(reminders, "send_to_max", lambda *a, **kw: mx.append(a[0]) or True)
    assert reminders.send_booking_created(b, "Гигиена") is True
    assert tg == [111] and mx == [222]


def test_max_bind_thanks_only_max_not_telegram(monkeypatch, db_session):
    """После авторизации в MAX не дублируем «спасибо» в уже привязанный Telegram."""
    from app.telegram_bind import bind_client as bind_tg

    bind_tg(db_session, "+79990000088", 111)
    starts = datetime(2026, 9, 1, 12, 0)
    b = Booking(
        id="KERIS-9905", owner_name="Ольга", owner_phone="+79990000088", pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_hygiene",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=5500,
        status=BookingStatus.confirmed, personal_data_consent=True,
        telegram_chat_id=111,
    )
    db_session.add(b)
    db_session.commit()
    tg, mx = [], []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: tg.append(a[0]) or True)
    monkeypatch.setattr(reminders, "send_to_max", lambda *a, **kw: mx.append(a[0]) or True)
    _, attached = bind_client(db_session, "+79990000088", 222)
    assert attached == ["KERIS-9905"]
    assert mx == [222] and tg == []


def test_run_once_sends_both_channels(monkeypatch, db_session):
    now = datetime(2026, 8, 10, 12, 0)
    starts = now + timedelta(hours=23, minutes=45)
    b = Booking(
        id="KERIS-9906", owner_name="Тест", owner_phone="+79990000066", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True,
        telegram_chat_id=111, max_user_id=222,
    )
    db_session.add(b)
    db_session.commit()
    tg, mx = [], []
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: tg.append(a[0]) or True)
    monkeypatch.setattr(reminders, "send_to_max", lambda *a, **kw: mx.append(a[0]) or True)
    assert reminders.run_once(db_session, now) == 1
    assert tg == [111] and mx == [222]
