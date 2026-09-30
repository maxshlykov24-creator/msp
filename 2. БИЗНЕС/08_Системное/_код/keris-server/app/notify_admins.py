"""Одна карточка записи в @kerisclubadminbot.

Новая запись пишет сообщение. Перенос, отмена и неявка удаляют прошлое и пишут
новое с текущим состоянием. Повтор того же текста карточку не двоит.
Получатели — ADMIN_NOTIFY_CHAT_IDS. Список Карины эти тексты не получает.
"""
from __future__ import annotations

import hashlib
import logging
import re

from .config import settings
from .models import BookingAdminCard
from .tg_http import tg_delete_message, tg_edit_message, tg_send_message_id

log = logging.getLogger("keris.notify_admins")

_BOOKING_ID = re.compile(r"KERIS-\d+")
CARD_MENU = {"inline_keyboard": [[{"text": "Меню", "callback_data": "menu:new"}]]}


def notify(text: str) -> None:
    match = _BOOKING_ID.search(text or "")
    if not match:
        log.info("карточка без номера записи пропущена: %s", (text or "")[:120])
        return
    publish(match.group(0), text)


def publish(booking_id: str, text: str) -> None:
    token = settings.karina_bot_token
    chats = settings.admin_notify_chat_ids
    if not token or not chats:
        log.info("админ-бот не настроен — карточка пропущена: %s", text[:120])
        return
    from .db import SessionLocal

    db = SessionLocal()
    try:
        for chat_id in chats:
            _upsert(db, token, chat_id, booking_id, text)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("карточка записи %s не обновлена", booking_id, exc_info=True)
    finally:
        db.close()


def _upsert(db, token: str, chat_id: str, booking_id: str, text: str) -> None:
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    row = db.query(BookingAdminCard).filter_by(booking_id=booking_id, chat_id=str(chat_id)).one_or_none()
    if row is not None and row.fingerprint == fingerprint:
        return
    if row is None:
        message_id = tg_send_message_id(token, chat_id, text, reply_markup=CARD_MENU)
        if not message_id:
            log.warning("карточка %s не отправлена chat_id=%s", booking_id, chat_id)
            return
        db.add(BookingAdminCard(
            booking_id=booking_id,
            chat_id=str(chat_id),
            message_id=message_id,
            fingerprint=fingerprint,
        ))
        return
    if tg_delete_message(token, chat_id, row.message_id):
        message_id = tg_send_message_id(token, chat_id, text, reply_markup=CARD_MENU)
        if message_id:
            row.message_id = message_id
            row.fingerprint = fingerprint
            return
    if tg_edit_message(token, chat_id, row.message_id, text, reply_markup=CARD_MENU):
        row.fingerprint = fingerprint
        return
    message_id = tg_send_message_id(token, chat_id, text, reply_markup=CARD_MENU)
    if message_id:
        row.message_id = message_id
        row.fingerprint = fingerprint
