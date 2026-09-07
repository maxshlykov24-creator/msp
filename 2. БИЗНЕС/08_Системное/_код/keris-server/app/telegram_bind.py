"""Связка телефон ↔ Telegram chat_id для уведомлений о записи.

Сайт и журнал YCLIENTS не знают chat_id. Клиент один раз делится номером в боте
(или записывается через Mini App) — дальше «спасибо», 24ч и 3ч уходят в этот чат.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .booking_logic import extract_phone_from_text
from .models import Booking, BookingStatus, TelegramClient


def chat_id_for_phone(db: Session, phone: str) -> int | None:
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return None
    row = db.execute(select(TelegramClient).where(TelegramClient.phone == normalized)).scalars().first()
    return int(row.chat_id) if row else None


def upsert_client(db: Session, phone: str, chat_id: int) -> TelegramClient | None:
    """Сохраняет связку без commit. None — телефон не нормализовался."""
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return None
    chat_id = int(chat_id)
    by_chat = db.execute(select(TelegramClient).where(TelegramClient.chat_id == chat_id)).scalars().first()
    by_phone = db.execute(select(TelegramClient).where(TelegramClient.phone == normalized)).scalars().first()
    if by_chat is not None and by_phone is not None and by_chat.phone != by_phone.phone:
        db.delete(by_phone)
        db.flush()
        by_chat.phone = normalized
        by_chat.pd_consent = True
        return by_chat
    if by_chat is not None:
        if by_chat.phone != normalized:
            conflict = db.execute(
                select(TelegramClient).where(TelegramClient.phone == normalized)
            ).scalars().first()
            if conflict is not None:
                db.delete(conflict)
                db.flush()
            by_chat.phone = normalized
        by_chat.pd_consent = True
        return by_chat
    if by_phone is not None:
        by_phone.chat_id = chat_id
        by_phone.pd_consent = True
        return by_phone
    row = TelegramClient(phone=normalized, chat_id=chat_id, pd_consent=True)
    db.add(row)
    return row


def attach_future_bookings(db: Session, phone: str, chat_id: int) -> list[str]:
    """Проставляет chat_id будущим записям этого телефона, у которых его ещё не было."""
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return []
    now = clock.now()
    rows = db.execute(
        select(Booking).where(
            Booking.owner_phone == normalized,
            Booking.telegram_chat_id.is_(None),
            Booking.status.in_((BookingStatus.pending, BookingStatus.confirmed)),
            Booking.starts_at > now,
        )
    ).scalars().all()
    ids = []
    for b in rows:
        b.telegram_chat_id = int(chat_id)
        ids.append(b.id)
    return ids


def bind_client(db: Session, phone: str, chat_id: int) -> tuple[TelegramClient | None, list[str]]:
    row = upsert_client(db, phone, chat_id)
    if row is None:
        return None, []
    attached = attach_future_bookings(db, row.phone, row.chat_id)
    db.commit()
    from . import photos, reminders, sms_otp
    reminders.send_thanks_for_attached(db, attached, only="telegram")
    # Клиент мог прийти сюда прямо со страницы входа в кабинет: там код выдать
    # было некуда, теперь есть куда — присылаем его тем же сообщением-цепочкой.
    sms_otp.deliver_pending(db, row.phone)
    # И тот же случай после визита: фото загрузили, отправить было некуда.
    photos.send_pending_reports(db, row.phone)
    # В amoCRM отмечаем «Подписан на TG-бот»: менеджер видит, дойдёт ли до
    # клиента напоминание или его надо звать в бота руками.
    from . import amocrm_metrics
    amocrm_metrics.refresh_for_phone(db, row.phone)
    return row, attached


def resolve_chat_id(db: Session, phone: str, explicit: int | None = None) -> int | None:
    """chat_id для новой записи: из Mini App или по телефону из telegram_clients.
    Если Mini App прислал id — запоминаем связку, чтобы следующие записи с сайта тоже находились."""
    if explicit:
        upsert_client(db, phone, explicit)
        return int(explicit)
    return chat_id_for_phone(db, phone)
