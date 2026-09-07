"""Связка телефон ↔ MAX user_id для уведомлений о записи.

Параллельно telegram_bind: один клиент может быть и в Telegram, и в MAX.
Телефон нормализуется в +7XXXXXXXXXX, поэтому 8 999… и +7 999… не плодят дубли.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .booking_logic import extract_phone_from_text
from .models import Booking, BookingStatus, MaxClient


def user_id_for_phone(db: Session, phone: str) -> int | None:
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return None
    row = db.execute(select(MaxClient).where(MaxClient.phone == normalized)).scalars().first()
    return int(row.user_id) if row else None


def upsert_client(db: Session, phone: str, user_id: int) -> MaxClient | None:
    """Сохраняет связку без commit. None — телефон не распознан."""
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return None
    user_id = int(user_id)
    by_user = db.execute(select(MaxClient).where(MaxClient.user_id == user_id)).scalars().first()
    by_phone = db.execute(select(MaxClient).where(MaxClient.phone == normalized)).scalars().first()
    if by_user is not None and by_phone is not None and by_user.phone != by_phone.phone:
        db.delete(by_phone)
        db.flush()
        by_user.phone = normalized
        by_user.pd_consent = True
        return by_user
    if by_user is not None:
        if by_user.phone != normalized:
            conflict = db.execute(
                select(MaxClient).where(MaxClient.phone == normalized)
            ).scalars().first()
            if conflict is not None:
                db.delete(conflict)
                db.flush()
            by_user.phone = normalized
        by_user.pd_consent = True
        return by_user
    if by_phone is not None:
        by_phone.user_id = user_id
        by_phone.pd_consent = True
        return by_phone
    row = MaxClient(phone=normalized, user_id=user_id, pd_consent=True)
    db.add(row)
    return row


def attach_future_bookings(db: Session, phone: str, user_id: int) -> list[str]:
    """Проставляет max_user_id будущим записям этого телефона, у которых его ещё не было."""
    normalized = extract_phone_from_text(phone)
    if not normalized:
        return []
    now = clock.now()
    rows = db.execute(
        select(Booking).where(
            Booking.owner_phone == normalized,
            Booking.max_user_id.is_(None),
            Booking.status.in_((BookingStatus.pending, BookingStatus.confirmed)),
            Booking.starts_at > now,
        )
    ).scalars().all()
    ids = []
    for b in rows:
        b.max_user_id = int(user_id)
        ids.append(b.id)
    return ids


def bind_client(db: Session, phone: str, user_id: int) -> tuple[MaxClient | None, list[str]]:
    row = upsert_client(db, phone, user_id)
    if row is None:
        return None, []
    attached = attach_future_bookings(db, row.phone, row.user_id)
    db.commit()
    from . import photos, reminders, sms_otp
    reminders.send_thanks_for_attached(db, attached, only="max")
    # Как и в Telegram: если клиент ждёт код входа в кабинет — досылаем в MAX.
    sms_otp.deliver_pending(db, row.phone)
    photos.send_pending_reports(db, row.phone)
    # Отметка «Подписан на Max-бот» на контакте в amoCRM.
    from . import amocrm_metrics
    amocrm_metrics.refresh_for_phone(db, row.phone)
    return row, attached


def resolve_user_id(db: Session, phone: str, explicit: int | None = None) -> int | None:
    if explicit:
        upsert_client(db, phone, explicit)
        return int(explicit)
    return user_id_for_phone(db, phone)
