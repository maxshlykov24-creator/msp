"""Связка телефон ↔ Telegram и уведомление при записи не из Mini App."""
from datetime import datetime, timedelta

from app import reminders, telegram_bind
from app.models import Booking, BookingStatus, PetType, TelegramClient
from app.telegram_bind import bind_client, chat_id_for_phone, resolve_chat_id


def test_bind_normalizes_phone(db_session):
    row, attached = bind_client(db_session, "8 999 368-98-71", 555001)
    assert row is not None
    assert row.phone == "+79993689871"
    assert row.chat_id == 555001
    assert attached == []
    assert chat_id_for_phone(db_session, "79993689871") == 555001


def test_bind_rejects_garbage_phone(db_session):
    row, attached = bind_client(db_session, "123", 1)
    assert row is None and attached == []


def test_resolve_uses_directory_when_no_explicit_chat(db_session):
    bind_client(db_session, "+79990000001", 777)
    assert resolve_chat_id(db_session, "+79990000001", None) == 777
    assert resolve_chat_id(db_session, "+79990000002", None) is None


def test_resolve_explicit_chat_also_saves_directory(db_session):
    assert resolve_chat_id(db_session, "+79990000003", 888) == 888
    db_session.commit()
    assert chat_id_for_phone(db_session, "+79990000003") == 888


def test_bind_attaches_future_booking_without_chat_id(db_session):
    starts = datetime(2026, 9, 1, 12, 0)
    b = Booking(
        id="KERIS-8801", owner_name="Ольга", owner_phone="+79991112233", pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_hygiene",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=5500,
        status=BookingStatus.confirmed, personal_data_consent=True,
    )
    db_session.add(b)
    db_session.commit()
    row, attached = bind_client(db_session, "+79991112233", 999001)
    assert attached == ["KERIS-8801"]
    db_session.refresh(b)
    assert b.telegram_chat_id == 999001
    assert row.chat_id == 999001


def test_bind_skips_past_and_done_bookings(db_session):
    past = datetime(2026, 1, 1, 10, 0)
    done_starts = datetime(2026, 9, 1, 12, 0)
    for bid, starts, status in (
        ("KERIS-8803", past, BookingStatus.confirmed),
        ("KERIS-8804", done_starts, BookingStatus.completed),
        ("KERIS-8805", done_starts, BookingStatus.cancelled),
    ):
        db_session.add(Booking(
            id=bid, owner_name="Ольга", owner_phone="+79991112244", pet_name="Моня",
            pet_type=PetType.dog, pet_size="XS", service_id="dog_hygiene",
            addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
            starts_at=starts, ends_at=starts + timedelta(minutes=90), price=5500,
            status=status, personal_data_consent=True,
        ))
    db_session.commit()
    _, attached = bind_client(db_session, "+79991112244", 999002)
    assert attached == []


def test_bind_sends_thanks_for_active_bookings(monkeypatch, db_session):
    starts = datetime(2026, 9, 1, 12, 0)
    b = Booking(
        id="KERIS-8806", owner_name="Ольга", owner_phone="+79991112255", pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_hygiene",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=5500,
        status=BookingStatus.confirmed, personal_data_consent=True,
    )
    db_session.add(b)
    db_session.commit()
    sent = []
    monkeypatch.setattr(reminders, "send_booking_created", lambda booking, name, **kw: sent.append((booking.id, name)) or True)
    _, attached = bind_client(db_session, "+79991112255", 999003)
    assert attached == ["KERIS-8806"]
    assert sent == [("KERIS-8806", "Гигиена")]
    sent.clear()
    bind_client(db_session, "+79991112255", 999003)
    assert sent == []


def test_same_chat_new_phone_updates_row(db_session):
    bind_client(db_session, "+79990000010", 42)
    bind_client(db_session, "+79990000011", 42)
    rows = db_session.query(TelegramClient).all()
    assert len(rows) == 1
    assert rows[0].phone == "+79990000011"


def test_send_booking_created_after_resolve(monkeypatch, db_session):
    bind_client(db_session, "+79990000009", 555)
    starts = datetime(2026, 9, 2, 12, 0)
    b = Booking(
        id="KERIS-8802", owner_name="Тест", owner_phone="+79990000009", pet_name="Барс",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.confirmed, personal_data_consent=True,
        telegram_chat_id=resolve_chat_id(db_session, "+79990000009"),
    )
    sent = []
    monkeypatch.setattr(reminders, "send_to_client", lambda chat_id, text, keyboard=None: sent.append((chat_id, text)) or True)
    assert reminders.send_booking_created(b, "Гигиена") is True
    assert sent[0][0] == 555 and "Спасибо" in sent[0][1]


def test_yclients_create_keeps_bigint_telegram_chat_id(db_session, monkeypatch):
    """chat_id Telegram часто > 2^31-1; колонка должна принять BIGINT, не INTEGER."""
    from tests.test_yclients_webhook import mapped, record_event
    from app.sync import handle_yclients_webhook

    mapped(db_session)
    bind_client(db_session, "+79990000781", 7251008149)
    monkeypatch.setattr(reminders, "send_to_client", lambda *a, **kw: True)
    result = handle_yclients_webhook(db_session, record_event())
    assert result["status"] == "created"
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.telegram_chat_id == 7251008149


# ── HTTP: bind + запись с сайта подхватывает chat_id ─────────────────────────
import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.seed import apply_seed


@pytest.fixture()
def api_sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, future=True)
    seed = factory()
    apply_seed(seed)
    seed.close()
    return factory


@pytest.fixture()
def api_client(api_sessions, monkeypatch):
    monkeypatch.setattr("app.main.settings", dataclasses.replace(settings, admin_api_key="test-admin"))

    def override_get_db():
        db = api_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_website_booking_sends_thank_you_after_bind(api_client, api_sessions, monkeypatch):
    sent = []
    monkeypatch.setattr("app.reminders.send_to_client", lambda *a, **kw: sent.append(a) or True)
    bind = api_client.post("/api/telegram/bind", json={"phone": "89990000123", "chat_id": 111222})
    assert bind.status_code == 200, bind.text
    assert bind.json()["phone"] == "+79990000123"

    from datetime import date, timedelta
    master_id = free = day = None
    for extra in range(8):
        day = (date.today() + timedelta(days=5 + extra)).isoformat()
        slots = api_client.get("/api/slots", params={"date_iso": day, "service_id": "dog_complex_cut",
                                                    "size": "XS", "addon_ids": ""}).json()
        found = next(((mid, s) for mid, s in (slots.get("masters") or {}).items() if s), None)
        if found:
            master_id, free = found
            break
    assert master_id and free, "нет свободных слотов у активных мастеров"
    r = api_client.post("/api/bookings", json={
        "owner_name": "Ольга", "owner_phone": "+79990000123", "pet_name": "Тедди",
        "pet_type": "dog", "pet_size": "XS", "service_id": "dog_complex_cut",
        "addon_ids": [], "master_id": master_id, "date_iso": day, "time_hhmm": free[0],
        "source": "website", "personal_data_consent": True,
    })
    assert r.status_code == 200, r.text
    db = api_sessions()
    booking = db.get(Booking, r.json()["booking_id"])
    assert booking.telegram_chat_id == 111222
    assert booking.source.value == "website"
    assert sent and sent[0][0] == 111222
    db.close()


def test_max_bind_http_normalizes_and_dedupes(api_client):
    first = api_client.post("/api/max/bind", json={"phone": "8 999 000-01-23", "user_id": 900001})
    assert first.status_code == 200, first.text
    assert first.json()["phone"] == "+79990000123"
    second = api_client.post("/api/max/bind", json={"phone": "мой номер +7 (999) 000-01-23", "user_id": 900001})
    assert second.status_code == 200
    assert second.json()["phone"] == "+79990000123"
    bad = api_client.post("/api/max/bind", json={"phone": "нет", "user_id": 1})
    assert bad.status_code == 422
