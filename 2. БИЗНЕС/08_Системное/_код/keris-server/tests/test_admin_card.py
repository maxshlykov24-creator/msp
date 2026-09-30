"""Одна карточка записи: повтор того же текста не шлёт второе сообщение."""
from __future__ import annotations

import dataclasses

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import notify_admins
from app.config import settings
from app.db import Base
from app.models import BookingAdminCard


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def test_same_card_is_not_sent_twice(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(
        notify_admins, "settings",
        dataclasses.replace(settings, karina_bot_token="admin", admin_notify_chat_ids=["435207481"]),
    )
    monkeypatch.setattr("app.db.SessionLocal", factory)
    sent = []
    monkeypatch.setattr(notify_admins, "tg_send_message_id", lambda *args, **kwargs: sent.append(args[2]) or 41)
    monkeypatch.setattr(notify_admins, "tg_delete_message", lambda *args, **kwargs: False)

    notify_admins.notify("<b>Новая KERIS-501</b>\nОльга")
    notify_admins.notify("<b>Новая KERIS-501</b>\nОльга")

    assert sent == ["<b>Новая KERIS-501</b>\nОльга"]
    db = factory()
    assert db.query(BookingAdminCard).count() == 1


def test_changed_card_deletes_previous(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(
        notify_admins, "settings",
        dataclasses.replace(settings, karina_bot_token="admin", admin_notify_chat_ids=["435207481"]),
    )
    monkeypatch.setattr("app.db.SessionLocal", factory)
    sent = []
    deleted = []
    ids = iter([41, 42])
    monkeypatch.setattr(
        notify_admins, "tg_send_message_id",
        lambda *args, **kwargs: sent.append(args[2]) or next(ids),
    )
    monkeypatch.setattr(notify_admins, "tg_delete_message", lambda *args, **kwargs: deleted.append(args[2]) or True)

    notify_admins.notify("<b>Новая KERIS-502</b>")
    notify_admins.notify("<b>Перенос KERIS-502</b>")

    assert deleted == [41]
    assert sent[1].startswith("<b>Перенос")
    db = factory()
    row = db.query(BookingAdminCard).filter_by(booking_id="KERIS-502").one()
    assert row.message_id == 42
