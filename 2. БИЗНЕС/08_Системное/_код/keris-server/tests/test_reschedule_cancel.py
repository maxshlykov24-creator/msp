"""Перенос и отмена записи клиентом: права, окно 24 ч, слоты, абонемент.

Идёт через TestClient на отдельной sqlite-базе (как smoke_api), потому что
эндпоинты собирают вместе БД, правила из сида и sync (без токенов — no-op).
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.booking_logic import has_overlap
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Booking, BookingStatus, Subscription
from app.seed import apply_seed, booking_rules

PHONE = "+79990000123"
ADMIN_KEY = "test-admin"
ADMIN = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture()
def sessions():
    """Своя in-memory база на тест: StaticPool — чтобы её видел и поток TestClient."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, future=True)
    seed = factory()
    apply_seed(seed)
    seed.close()
    return factory


@pytest.fixture()
def client(sessions, monkeypatch):
    # Settings — frozen dataclass, поэтому подменяем объект целиком: без ключа
    # админский обход прав не проверить.
    monkeypatch.setattr("app.main.settings", dataclasses.replace(settings, admin_api_key=ADMIN_KEY))

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Без контекстного менеджера: startup поднял бы боевой engine и цикл напоминаний.
    yield TestClient(app)
    app.dependency_overrides.clear()


def free_slot(client, day_offset: int) -> tuple[str, str, str]:
    """(дата, мастер, время) первого свободного слота, начиная с day_offset дней."""
    for extra in range(8):
        day = (date.today() + timedelta(days=day_offset + extra)).isoformat()
        slots = client.get("/api/slots", params={"date_iso": day, "service_id": "dog_complex_cut",
                                                "size": "XS", "addon_ids": ""}).json()
        found = next(((mid, s) for mid, s in (slots.get("masters") or {}).items() if s), None)
        if found:
            master_id, free = found
            return day, master_id, free[0]
    raise AssertionError("нет свободных слотов у активных мастеров")


def make_booking(client, day_offset: int = 5, phone: str = PHONE) -> dict:
    day, master_id, time_hhmm = free_slot(client, day_offset)
    r = client.post("/api/bookings", json={
        "owner_name": "Ольга", "owner_phone": phone, "pet_name": "Тедди",
        "pet_type": "dog", "pet_size": "XS", "service_id": "dog_complex_cut",
        "addon_ids": [], "master_id": master_id, "date_iso": day, "time_hhmm": time_hhmm,
        "personal_data_consent": True, "marketing_consent": True, "media_consent": True,
    })
    assert r.status_code == 200, r.text
    return r.json()


def db_booking(sessions, booking_id: str) -> Booking:
    db = sessions()
    try:
        return db.get(Booking, booking_id)
    finally:
        db.close()


def test_complex_booking_does_not_attach_unselected_addons(client, sessions):
    """Комплекс уже включает когти и уши: в запись не дописываем чужие допы."""
    created = make_booking(client, day_offset=5)
    booking = db_booking(sessions, created["booking_id"])
    assert booking.addon_ids == []
    assert "dog_mask_hydra" not in (booking.free_addon_ids or [])
    assert "dog_nails" not in (booking.free_addon_ids or [])
    assert "dog_ears" not in (booking.free_addon_ids or [])


def test_reschedule_moves_the_same_booking(client, sessions):
    booking = make_booking(client, day_offset=5)
    old_starts = datetime.fromisoformat(booking["starts_at"])
    new_day, _, new_time = free_slot(client, 7)

    r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})
    assert r.status_code == 200, r.text
    assert r.json()["starts_at"].startswith(f"{new_day}T{new_time}")

    db = sessions()
    try:
        # запись одна: новой не появилось, старый слот освободился
        assert db.query(Booking).count() == 1
        moved = db.get(Booking, booking["booking_id"])
        assert moved.status == BookingStatus.confirmed
        assert moved.starts_at != old_starts
        assert not has_overlap(db, moved.master_id, old_starts,
                               old_starts + (moved.ends_at - moved.starts_at))
    finally:
        db.close()


def test_reschedule_keeps_duration_and_resets_reminders(client, sessions):
    booking = make_booking(client, day_offset=5)
    db = sessions()
    try:
        b = db.get(Booking, booking["booking_id"])
        b.reminders_sent = [24]
        duration = b.ends_at - b.starts_at
        db.commit()
    finally:
        db.close()

    new_day, _, new_time = free_slot(client, 8)
    client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})

    moved = db_booking(sessions, booking["booking_id"])
    assert moved.ends_at - moved.starts_at == duration
    assert moved.reminders_sent == []  # напоминания посчитаются от нового времени


def test_reschedule_requires_owner_phone(client):
    booking = make_booking(client)
    new_day, _, new_time = free_slot(client, 7)
    base = {"date_iso": new_day, "time_hhmm": new_time}

    assert client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                       json=base).status_code == 403
    assert client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                       json={**base, "phone": "+79995550000"}).status_code == 403
    # админский ключ (бот Карины) телефон не требует
    assert client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                       json=base, headers=ADMIN).status_code == 200


def test_reschedule_accepts_any_owner_phone_format(client):
    booking = make_booking(client)
    new_day, _, new_time = free_slot(client, 7)
    r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": "8 999 000-01-23"})
    assert r.status_code == 200, r.text


def test_reschedule_blocked_inside_free_window(client, sessions):
    """Ближе чем за 24 ч до визита перенос — только через мастера."""
    booking = make_booking(client, day_offset=5)
    hours = booking_rules()["free_reschedule_hours"]
    db = sessions()
    try:
        b = db.get(Booking, booking["booking_id"])
        duration = b.ends_at - b.starts_at
        b.starts_at = datetime.now() + timedelta(hours=hours - 1)
        b.ends_at = b.starts_at + duration
        db.commit()
    finally:
        db.close()

    new_day, _, new_time = free_slot(client, 7)
    r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})
    assert r.status_code == 422
    assert "мастера" in r.json()["detail"]
    # админ переносит и в этом окне
    assert client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                       json={"date_iso": new_day, "time_hhmm": new_time},
                       headers=ADMIN).status_code == 200


def test_reschedule_rejects_taken_slot(client, sessions):
    first = make_booking(client, day_offset=5)
    second = make_booking(client, day_offset=6, phone="+79995550001")
    second_db = db_booking(sessions, second["booking_id"])
    # переносим первую ровно на слот второй записи к тому же мастеру
    db = sessions()
    try:
        b = db.get(Booking, first["booking_id"])
        b.master_id = second_db.master_id
        db.commit()
    finally:
        db.close()

    r = client.post(f"/api/bookings/{first['booking_id']}/reschedule",
                    json={"date_iso": second_db.starts_at.date().isoformat(),
                          "time_hhmm": second_db.starts_at.strftime("%H:%M"), "phone": PHONE})
    assert r.status_code == 409


def test_reschedule_rejects_past_and_far_dates(client):
    booking = make_booking(client)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    far = (date.today() + timedelta(days=booking_rules()["horizon_days"] + 1)).isoformat()
    for day in (yesterday, far):
        r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                        json={"date_iso": day, "time_hhmm": "12:00", "phone": PHONE})
        assert r.status_code == 422, day


def test_cancel_frees_slot_and_is_idempotent(client, sessions):
    booking = make_booking(client, day_offset=5)
    r = client.post(f"/api/bookings/{booking['booking_id']}/cancel", json={"phone": PHONE})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    cancelled = db_booking(sessions, booking["booking_id"])
    db = sessions()
    try:
        assert not has_overlap(db, cancelled.master_id, cancelled.starts_at, cancelled.ends_at)
    finally:
        db.close()
    # повторная отмена не ошибка
    assert client.post(f"/api/bookings/{booking['booking_id']}/cancel",
                       json={"phone": PHONE}).status_code == 200


def test_cancel_requires_owner_phone(client, sessions):
    booking = make_booking(client)
    assert client.post(f"/api/bookings/{booking['booking_id']}/cancel",
                       json={"phone": "+79995550000"}).status_code == 403
    assert db_booking(sessions, booking["booking_id"]).status == BookingStatus.confirmed


def test_cancelled_booking_cannot_be_rescheduled(client):
    booking = make_booking(client)
    client.post(f"/api/bookings/{booking['booking_id']}/cancel", json={"phone": PHONE})
    new_day, _, new_time = free_slot(client, 7)
    r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})
    assert r.status_code == 409


def test_no_show_booking_cannot_be_rescheduled_by_client(client, sessions):
    booking = make_booking(client)
    db = sessions()
    try:
        db.get(Booking, booking["booking_id"]).status = BookingStatus.no_show
        db.commit()
    finally:
        db.close()
    new_day, _, new_time = free_slot(client, 7)
    r = client.post(f"/api/bookings/{booking['booking_id']}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})
    assert r.status_code == 409


def test_unknown_booking_is_404(client, sessions):
    r = client.post("/api/bookings/KERIS-0/cancel", json={"phone": PHONE})
    assert r.status_code == 404


def subscription_booking(client, sessions) -> tuple[str, int]:
    """Запись по абонементу: (booking_id, subscription_id)."""
    db = sessions()
    try:
        sub = Subscription(owner_phone=PHONE, plan_id="habit", size="S",
                           visits_total=6, visits_used=0,
                           expires_at=datetime.now() + timedelta(days=180))
        db.add(sub)
        db.commit()
        sub_id = sub.id
    finally:
        db.close()

    day, master_id, time_hhmm = free_slot(client, 5)
    r = client.post("/api/bookings", json={
        "owner_name": "Ольга", "owner_phone": PHONE, "pet_name": "Тедди",
        "pet_type": "dog", "pet_size": "XS", "service_id": "dog_complex_cut",
        "addon_ids": [], "master_id": master_id, "date_iso": day, "time_hhmm": time_hhmm,
        "personal_data_consent": True, "use_subscription": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["booking_id"], sub_id


def visits_used(sessions, subscription_id: int) -> float:
    db = sessions()
    try:
        return db.get(Subscription, subscription_id).visits_used
    finally:
        db.close()


def test_cancel_returns_subscription_visit(client, sessions):
    booking_id, sub_id = subscription_booking(client, sessions)
    charged = visits_used(sessions, sub_id)
    assert charged > 0

    r = client.post(f"/api/bookings/{booking_id}/cancel", json={"phone": PHONE})
    assert r.json()["visits_refunded"] == charged
    assert visits_used(sessions, sub_id) == 0
    # повторная отмена не возвращает визит второй раз
    client.post(f"/api/bookings/{booking_id}/cancel", json={"phone": PHONE})
    assert visits_used(sessions, sub_id) == 0


def test_no_show_burns_subscription_visit(client, sessions):
    """Неявка — санкция по оферте §2.4: визит абонемента не возвращается."""
    booking_id, sub_id = subscription_booking(client, sessions)
    charged = visits_used(sessions, sub_id)
    db = sessions()
    try:
        db.get(Booking, booking_id).status = BookingStatus.no_show
        db.commit()
    finally:
        db.close()
    assert visits_used(sessions, sub_id) == charged


def test_reschedule_keeps_subscription_charge(client, sessions):
    booking_id, sub_id = subscription_booking(client, sessions)
    charged = visits_used(sessions, sub_id)
    new_day, _, new_time = free_slot(client, 8)
    r = client.post(f"/api/bookings/{booking_id}/reschedule",
                    json={"date_iso": new_day, "time_hhmm": new_time, "phone": PHONE})
    assert r.status_code == 200, r.text
    assert visits_used(sessions, sub_id) == charged  # перенос не списывает визит повторно
