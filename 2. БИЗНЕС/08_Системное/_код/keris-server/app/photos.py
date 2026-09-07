"""Фото до/после: хранение у нас и отчёт клиенту.

Постоянное место фото — кабинет «Мой Keris»: клиент открывает любой прошлый визит
и видит, как было. Отправка в бота клиента — дубль-уведомление, а не единственное
место, где фото существует. Поэтому файл сразу скачивается из Telegram в
PHOTOS_DIR, а в базе лежит относительный путь.
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, max_bind, max_http, telegram_bind
from .config import settings
from .models import Booking, BookingPhoto
from .tg_http import tg_get_file_bytes, tg_send_photos

log = logging.getLogger("keris.photos")

KINDS = ("before", "after")
KIND_LABELS = {"before": "до", "after": "после"}


class PhotoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def root() -> Path:
    return Path(settings.photos_dir)


def absolute_path(photo: BookingPhoto) -> Path:
    return root() / photo.path


def url_for(photo: BookingPhoto) -> str:
    return f"{settings.media_base_url}/{photo.path}"


def absolute_url(photo: BookingPhoto) -> str:
    """Ссылка, открываемая вне кабинета — например из карточки сделки amoCRM."""
    url = url_for(photo)
    if url.startswith("http") or not settings.public_base_url:
        return url
    return f"{settings.public_base_url}{url}"


def for_booking(db: Session, booking_id: str) -> list[BookingPhoto]:
    rows = db.execute(
        select(BookingPhoto)
        .where(BookingPhoto.booking_id == booking_id)
        .order_by(BookingPhoto.added_at, BookingPhoto.id)
    ).scalars().all()
    # before раньше after независимо от порядка загрузки — так читается отчёт.
    return sorted(rows, key=lambda p: (0 if p.kind == "before" else 1, p.added_at or clock.now(), p.id))


def as_dicts(db: Session, booking_id: str) -> list[dict]:
    return [
        {"kind": p.kind, "url": url_for(p), "added_at": p.added_at.isoformat() if p.added_at else ""}
        for p in for_booking(db, booking_id)
    ]


def by_booking_ids(db: Session, booking_ids: list[str]) -> dict[str, list[dict]]:
    """Фото сразу по всем визитам клиента — чтобы /api/client не делал запрос на визит."""
    if not booking_ids:
        return {}
    rows = db.execute(
        select(BookingPhoto)
        .where(BookingPhoto.booking_id.in_(booking_ids))
        .order_by(BookingPhoto.added_at, BookingPhoto.id)
    ).scalars().all()
    out: dict[str, list[dict]] = {}
    for p in sorted(rows, key=lambda r: (0 if r.kind == "before" else 1, r.id)):
        out.setdefault(p.booking_id, []).append({
            "kind": p.kind,
            "url": url_for(p),
            "added_at": p.added_at.isoformat() if p.added_at else "",
        })
    return out


def save_bytes(
    db: Session,
    booking: Booking,
    kind: str,
    data: bytes,
    *,
    added_by: str = "",
    ext: str = ".jpg",
) -> BookingPhoto:
    if kind not in KINDS:
        raise PhotoError("kind: before | after", 422)
    if not data:
        raise PhotoError("Пустой файл", 422)
    folder = root() / booking.id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{kind}_{clock.now():%Y%m%d%H%M%S}_{secrets.token_hex(3)}{ext}"
    (folder / name).write_bytes(data)
    row = BookingPhoto(
        booking_id=booking.id,
        kind=kind,
        path=f"{booking.id}/{name}",
        added_at=clock.now(),
        added_by=str(added_by or ""),
    )
    db.add(row)
    db.commit()
    log.info("фото %s добавлено к %s (%s, %d КБ)", kind, booking.id, added_by or "—", len(data) // 1024)
    return row


def save_from_telegram(
    db: Session,
    booking: Booking,
    kind: str,
    file_id: str,
    *,
    bot_token: str = "",
    added_by: str = "",
) -> BookingPhoto:
    token = bot_token or settings.staff_bot_token
    if not token:
        raise PhotoError("STAFF_BOT_TOKEN не задан — фото скачать нечем", 503)
    data = tg_get_file_bytes(token, file_id)
    if not data:
        raise PhotoError("Не удалось скачать фото из Telegram", 502)
    return save_bytes(db, booking, kind, data, added_by=added_by)


def report_caption(booking: Booking, service_name: str) -> str:
    pet = booking.pet_name or "ваш питомец"
    when = booking.starts_at.strftime("%d.%m")
    return (
        f"🐾 <b>Фото-отчёт: {pet}</b>\n\n"
        f"Визит {when}, {service_name}.\n"
        "Слева — до, справа — после.\n\n"
        "Отчёт сохранён в кабинете «Мой Keris»: любой прошлый визит можно открыть и посмотреть снова."
    )


def send_report(db: Session, booking: Booking, service_name: str) -> dict:
    """Отчёт клиенту в Telegram и MAX. Фото уже сохранены, отправка — уведомление.

    Клиент ещё не в боте — не ошибка: возвращаем `pending`, фото остаются в
    кабинете, а администратор видит подсказку перевести клиента в бота.
    """
    rows = for_booking(db, booking.id)
    if not rows:
        raise PhotoError("К записи ещё не добавлено ни одного фото", 404)

    payload: list[tuple[str, bytes]] = []
    for p in rows:
        path = absolute_path(p)
        if not path.exists():
            log.warning("файл фото пропал: %s", path)
            continue
        payload.append((f"{KIND_LABELS.get(p.kind, p.kind)}_{p.id}.jpg", path.read_bytes()))
    if not payload:
        raise PhotoError("Файлы фото не найдены на диске", 500)

    caption = report_caption(booking, service_name)
    channels: list[str] = []

    chat_id = booking.telegram_chat_id or telegram_bind.chat_id_for_phone(db, booking.owner_phone)
    if chat_id and settings.client_bot_token:
        if tg_send_photos(settings.client_bot_token, chat_id, payload, caption=caption):
            channels.append("telegram")

    user_id = max_bind.user_id_for_phone(db, booking.owner_phone)
    if user_id and settings.max_bot_token:
        if max_http.send_photos(int(user_id), caption, payload):
            channels.append("max")
        elif max_http.send_message(int(user_id), caption):
            channels.append("max_text")

    if channels:
        now = clock.now()
        for p in rows:
            p.sent_at = now
        db.commit()

    _push_report_to_amocrm(db, booking, rows, channels)
    return {
        "booking_id": booking.id,
        "photos": len(rows),
        "channels": channels,
        "pending": not channels,
        "links": {"telegram": settings.telegram_bot_url, "max": settings.max_bot_url},
    }


def _push_report_to_amocrm(db: Session, booking: Booking, rows: list[BookingPhoto],
                            channels: list[str]) -> None:
    """Отчёт в карточке сделки: ссылка в поле и все фото списком в ленте.

    Менеджер должен видеть результат визита в amoCRM, не заходя в кабинет.
    """
    from . import sync  # локальный импорт: sync тянет пол-приложения

    after = next((p for p in rows if p.kind == "after"), rows[-1])
    lines = [f"{KIND_LABELS.get(p.kind, p.kind)}: {absolute_url(p)}" for p in rows]
    delivery = ", ".join(channels) if channels else "клиент не в боте, отчёт ждёт в кабинете"
    sync.sync_booking_to_amocrm(
        db, booking,
        note="Фото-отчёт отправлен (" + delivery + "):\n" + "\n".join(lines),
        extra_fields={"Фото-отчёт": absolute_url(after)},
    )


def send_pending_reports(db: Session, phone: str) -> int:
    """Клиент только что привязал бота — досылаем отчёты, которые его не догнали."""
    from .models import Service

    normalized = (phone or "").strip()
    if not normalized:
        return 0
    rows = db.execute(
        select(BookingPhoto)
        .join(Booking, Booking.id == BookingPhoto.booking_id)
        .where(Booking.owner_phone == normalized, BookingPhoto.sent_at.is_(None))
    ).scalars().all()
    sent = 0
    for booking_id in dict.fromkeys(r.booking_id for r in rows):
        booking = db.get(Booking, booking_id)
        if booking is None:
            continue
        service = db.get(Service, booking.service_id)
        try:
            result = send_report(db, booking, service.name if service else booking.service_id)
        except PhotoError:
            continue
        if result["channels"]:
            sent += 1
    return sent
