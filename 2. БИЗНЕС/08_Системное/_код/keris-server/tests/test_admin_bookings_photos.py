"""Список записей для фото-бота: вчерашний день и счётчики фото.

Бот показывает администратору только визиты, где фото до/после ещё не полные,
поэтому эндпоинт обязан отдавать `when=yesterday` и счётчики по каждой записи.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import clock, photos
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Booking, BookingStatus, PetType
from app.seed import apply_seed

ADMIN_KEY = "test-admin"
ADMIN = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, future=True)
    seed = factory()
    apply_seed(seed)
    seed.close()
    return factory


@pytest.fixture()
def client(sessions, monkeypatch, tmp_path):
    monkeypatch.setattr("app.main.settings", dataclasses.replace(settings, admin_api_key=ADMIN_KEY))
    monkeypatch.setattr(
        photos, "settings",
        dataclasses.replace(settings, photos_dir=str(tmp_path), media_base_url="/media"),
    )

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_booking(sessions, bid: str, hour: int, day_offset: int) -> None:
    db = sessions()
    starts = datetime.combine(clock.today() + timedelta(days=day_offset),
                              datetime.min.time()) + timedelta(hours=hour)
    db.add(Booking(
        id=bid, owner_name="Ольга", owner_phone="+79991112233", pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.completed, personal_data_consent=True,
    ))
    db.commit()
    db.close()


def attach(sessions, bid: str, *kinds: str) -> None:
    db = sessions()
    booking = db.get(Booking, bid)
    for kind in kinds:
        photos.save_bytes(db, booking, kind, b"\xff\xd8jpeg")
    db.close()


def test_yesterday_list_carries_photo_counts(client, sessions):
    add_booking(sessions, "KERIS-9001", 11, -1)
    add_booking(sessions, "KERIS-9002", 13, -1)
    attach(sessions, "KERIS-9002", "before", "after")

    rows = client.get("/admin/bookings", params={"when": "yesterday"}, headers=ADMIN).json()

    counts = {r["id"]: (r["photos_before"], r["photos_after"]) for r in rows}
    assert counts == {"KERIS-9001": (0, 0), "KERIS-9002": (1, 1)}


def test_unknown_when_rejected(client):
    r = client.get("/admin/bookings", params={"when": "позавчера"}, headers=ADMIN)
    assert r.status_code == 422
