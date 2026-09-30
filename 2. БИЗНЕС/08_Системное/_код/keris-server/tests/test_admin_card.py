"""Одна карточка записи: повтор того же текста не шлёт второе сообщение."""
from __future__ import annotations

import dataclasses

from app import notify_admins
from app.config import settings
from app.models import BookingAdminCard


def test_same_card_is_not_sent_twice(monkeypatch, db_session):
    monkeypatch.setattr(
        notify_admins, "settings",
        dataclasses.replace(settings, karina_bot_token="admin", admin_notify_chat_ids=["435207481"]),
    )
    monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
    sent = []
    deleted = []
    monkeypatch.setattr(notify_admins, "tg_send_message_id", lambda *args, **kwargs: sent.append(args[2]) or 41)
    monkeypatch.setattr(notify_admins, "tg_delete_message", lambda *args, **kwargs: deleted.append(args) or True)

    notify_admins.notify("<b>Новая KERIS-501</b>\nОльга")
    db_session.commit()
    notify_admins.notify("<b>Новая KERIS-501</b>\nОльга")

    assert sent == ["<b>Новая KERIS-501</b>\nОльга"]
    assert deleted == []
    assert db_session.query(BookingAdminCard).count() == 1


def test_changed_card_deletes_previous(monkeypatch, db_session):
    monkeypatch.setattr(
        notify_admins, "settings",
        dataclasses.replace(settings, karina_bot_token="admin", admin_notify_chat_ids=["435207481"]),
    )
    monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
    sent = []
    deleted = []
    ids = iter([41, 42])
    monkeypatch.setattr(
        notify_admins, "tg_send_message_id",
        lambda *args, **kwargs: sent.append(args[2]) or next(ids),
    )
    monkeypatch.setattr(notify_admins, "tg_delete_message", lambda *args, **kwargs: deleted.append(args[2]) or True)

    notify_admins.notify("<b>Новая KERIS-502</b>")
    db_session.commit()
    notify_admins.notify("<b>Перенос KERIS-502</b>")
    db_session.commit()

    assert deleted == [41]
    assert sent[1].startswith("<b>Перенос")
    row = db_session.query(BookingAdminCard).filter_by(booking_id="KERIS-502").one()
    assert row.message_id == 42
