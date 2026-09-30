"""Сводка роли Карины: каждый день в 22:15, в воскресенье неделя, в последний день месяца месяц.

Салон до 22:00, поэтому 21:00 режет вечерние визиты.
Получатели — DIGEST_CHAT_IDS, а если список пуст, то KARINA_ROLE_IDS.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import clock
from .amocrm_client import AmoCrmError, count_pipeline_created
from .config import settings
from .day_board import period_stats, today_stats
from .models import OwnerDigest
from .tg_http import tg_send_message

log = logging.getLogger("keris.owner_digest")

SEND_HOUR = 22
SEND_MINUTE = 15


def _kennel_line(start: datetime, end: datetime) -> str:
    try:
        pipeline = int(settings.amocrm_pipeline_sales_id)
        count = count_pipeline_created(pipeline, start, end)
    except (AmoCrmError, ValueError):
        count = None
    if count is None:
        return "Питомник: не удалось спросить amoCRM"
    noun = "заявка" if count % 10 == 1 and count % 100 != 11 else "заявок"
    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        noun = "заявки"
    return f"Питомник: {count} {noun}"


def _salon_lines(stats: dict) -> list[str]:
    return [
        f"Визитов: {stats['visits']}",
        f"Выручка: {stats['revenue']} ₽",
        f"Отмены: {stats['cancelled']}",
        f"Не пришли: {stats['no_show']}",
    ]


def build_text(db: Session, moment: datetime | None = None) -> str:
    now = moment or clock.now()
    day = now.date()
    blocks = ["<b>Keris Club, сегодня</b>", *_salon_lines(today_stats(db, now=now))]
    start_today = datetime.combine(day, datetime.min.time())
    end_today = start_today + timedelta(days=1)
    blocks.append(_kennel_line(start_today, end_today))

    if day.weekday() == 6:
        week_start = day - timedelta(days=6)
        stats = period_stats(db, week_start, day + timedelta(days=1), now=now)
        blocks.append("")
        blocks.append("<b>Неделя</b>")
        blocks.extend(_salon_lines(stats))
        blocks.append(_kennel_line(datetime.combine(week_start, datetime.min.time()), end_today))

    if day.day == calendar.monthrange(day.year, day.month)[1]:
        month_start = day.replace(day=1)
        stats = period_stats(db, month_start, day + timedelta(days=1), now=now)
        blocks.append("")
        blocks.append("<b>Месяц</b>")
        blocks.extend(_salon_lines(stats))
        blocks.append(_kennel_line(datetime.combine(month_start, datetime.min.time()), end_today))

    return "\n".join(blocks)


def due(moment: datetime | None = None) -> bool:
    now = moment or clock.now()
    return (now.hour, now.minute) >= (SEND_HOUR, SEND_MINUTE)


def maybe_send(db: Session, moment: datetime | None = None, *, force: bool = False) -> dict:
    now = moment or clock.now()
    if not force and not due(now):
        return {"sent": False, "reason": "early"}
    date_iso = now.date().isoformat()
    if not force and db.get(OwnerDigest, date_iso) is not None:
        return {"sent": False, "reason": "already"}
    token = settings.karina_bot_token
    chats = settings.digest_chat_ids or settings.karina_role_ids
    if not token or not chats:
        return {"sent": False, "reason": "no_recipient"}
    text = build_text(db, now)
    delivered = 0
    for chat_id in chats:
        if tg_send_message(token, chat_id, text):
            delivered += 1
    if delivered:
        if db.get(OwnerDigest, date_iso) is None:
            db.add(OwnerDigest(date_iso=date_iso, sent_at=now))
        db.commit()
    return {"sent": delivered > 0, "delivered": delivered, "text": text}
