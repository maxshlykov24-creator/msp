"""Фото до/после: хранение у нас, история в кабинете, отчёт в бота."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from app import max_bind, photos, telegram_bind
from app.config import settings
from app.models import Booking, BookingPhoto, BookingStatus, PetType

PHONE = "+79991112233"


@pytest.fixture()
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(
        photos, "settings",
        dataclasses.replace(settings, photos_dir=str(tmp_path), media_base_url="/media"),
    )
    return tmp_path


def make_booking(db, bid="KERIS-7001", phone=PHONE, chat_id=None, max_id=None):
    starts = datetime(2026, 8, 18, 12, 0)
    b = Booking(
        id=bid, owner_name="Ольга", owner_phone=phone, pet_name="Моня",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut",
        addon_ids=[], free_addon_ids=[], applied_promos=[], master_id="svetlana",
        starts_at=starts, ends_at=starts + timedelta(minutes=90), price=7800,
        status=BookingStatus.completed, personal_data_consent=True,
        telegram_chat_id=chat_id, max_user_id=max_id,
    )
    db.add(b)
    db.commit()
    return b


def test_save_bytes_keeps_file_and_returns_url(db_session, storage):
    booking = make_booking(db_session)
    row = photos.save_bytes(db_session, booking, "before", b"\xff\xd8jpeg", added_by="555")

    saved = storage / row.path
    assert saved.read_bytes() == b"\xff\xd8jpeg"
    assert photos.url_for(row) == "/media/" + row.path
    assert row.sent_at is None


def test_before_goes_first_regardless_of_upload_order(db_session, storage):
    booking = make_booking(db_session)
    photos.save_bytes(db_session, booking, "after", b"after")
    photos.save_bytes(db_session, booking, "before", b"before")

    assert [p["kind"] for p in photos.as_dicts(db_session, booking.id)] == ["before", "after"]


def test_rejects_unknown_kind(db_session, storage):
    booking = make_booking(db_session)
    with pytest.raises(photos.PhotoError):
        photos.save_bytes(db_session, booking, "middle", b"x")


def test_send_report_marks_sent_and_goes_to_telegram(monkeypatch, db_session, storage):
    booking = make_booking(db_session, chat_id=555000111222)
    photos.save_bytes(db_session, booking, "before", b"before")
    photos.save_bytes(db_session, booking, "after", b"after")
    monkeypatch.setattr(
        photos, "settings",
        dataclasses.replace(
            settings, photos_dir=str(storage), media_base_url="/media", client_bot_token="tg",
        ),
    )
    sent = []
    monkeypatch.setattr(
        photos, "tg_send_photos",
        lambda token, chat_id, files, caption="": sent.append((chat_id, len(files), caption)) or True,
    )

    result = photos.send_report(db_session, booking, "Комплекс")

    assert result["channels"] == ["telegram"]
    assert result["pending"] is False
    assert sent == [(555000111222, 2, sent[0][2])]
    assert "Моня" in sent[0][2]
    assert all(p.sent_at is not None for p in photos.for_booking(db_session, booking.id))


def test_send_report_without_bot_stays_pending(monkeypatch, db_session, storage):
    """Клиент не в боте — фото уже в кабинете, отчёт ждёт привязки."""
    booking = make_booking(db_session)
    photos.save_bytes(db_session, booking, "before", b"before")

    result = photos.send_report(db_session, booking, "Комплекс")

    assert result["pending"] is True
    assert result["channels"] == []
    assert result["links"]["telegram"].startswith("https://t.me/")
    assert photos.for_booking(db_session, booking.id)[0].sent_at is None


def test_report_reaches_client_after_bind(monkeypatch, db_session, storage):
    booking = make_booking(db_session)
    photos.save_bytes(db_session, booking, "before", b"before")
    photos.send_report(db_session, booking, "Комплекс")  # некуда — ждёт

    monkeypatch.setattr(
        photos, "settings",
        dataclasses.replace(
            settings, photos_dir=str(storage), media_base_url="/media", client_bot_token="tg",
        ),
    )
    sent = []
    monkeypatch.setattr(
        photos, "tg_send_photos",
        lambda token, chat_id, files, caption="": sent.append(chat_id) or True,
    )
    telegram_bind.upsert_client(db_session, PHONE, 900001)
    db_session.commit()

    assert photos.send_pending_reports(db_session, PHONE) == 1
    assert sent == [900001]
    # Повторно тот же отчёт не уходит.
    assert photos.send_pending_reports(db_session, PHONE) == 0


def test_send_report_requires_photos(db_session, storage):
    booking = make_booking(db_session)
    with pytest.raises(photos.PhotoError):
        photos.send_report(db_session, booking, "Комплекс")


def test_max_gets_text_when_upload_fails(monkeypatch, db_session, storage):
    """Картинку в MAX загрузить не удалось — клиент всё равно узнаёт про отчёт."""
    booking = make_booking(db_session)
    photos.save_bytes(db_session, booking, "after", b"after")
    max_bind.upsert_client(db_session, PHONE, 775149185013)
    db_session.commit()
    monkeypatch.setattr(
        photos, "settings",
        dataclasses.replace(
            settings, photos_dir=str(storage), media_base_url="/media", max_bot_token="max",
        ),
    )
    monkeypatch.setattr(photos.max_http, "send_photos", lambda *a, **kw: False)
    texts = []
    monkeypatch.setattr(
        photos.max_http, "send_message",
        lambda user_id, text, buttons=None: texts.append(user_id) or True,
    )

    result = photos.send_report(db_session, booking, "Комплекс")

    assert result["channels"] == ["max_text"]
    assert texts == [775149185013]


def test_client_profile_returns_photos_per_visit(monkeypatch, db_session, storage):
    """Фото приходят в кабинет вместе с визитом — по любой прошлой записи."""
    booking = make_booking(db_session)
    photos.save_bytes(db_session, booking, "before", b"before")
    photos.save_bytes(db_session, booking, "after", b"after")

    grouped = photos.by_booking_ids(db_session, [booking.id, "KERIS-НЕТ"])

    assert [p["kind"] for p in grouped[booking.id]] == ["before", "after"]
    assert "KERIS-НЕТ" not in grouped
    assert db_session.query(BookingPhoto).count() == 2
