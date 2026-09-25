"""Сверка поля «Источник записи» по живым записям.

Mini App и Сайт остаются как есть. Записи, которые вебхук YCLIENTS раньше
складывал в «Яндекс.Карты / 2ГИС», разбираем по признакам самой записи:
online, форма, ссылка, record_from — это карты. Без них — журнал.
Сделку без нашей записи не угадываем.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import amocrm_client, amocrm_metrics, yclients_client
from .config import settings
from .models import Booking, BookingSource
from .sync import yclients_record_source

log = logging.getLogger("keris.source")

GROOMING_PIPE = 11225138
FIELD_NAME = "Источник записи"
DROP_VALUE = "Админ-бот"


def _record_data(record_id: int) -> dict | None:
    raw = yclients_client.get_record(record_id)
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    return data if isinstance(data, dict) else None


def reclassify_journal(db: Session) -> dict:
    """Карты остаются картами. Журнал, который лежал в картах, переносим."""
    rows = list(db.execute(
        select(Booking).where(
            Booking.source == BookingSource.yclients_maps,
            Booking.yclients_record_id.isnot(None),
        )
    ).scalars().all())
    journal = maps = unread = 0
    for booking in rows:
        try:
            data = _record_data(int(booking.yclients_record_id))
        except Exception:
            log.warning("запись YCLIENTS %s не прочитана", booking.yclients_record_id)
            unread += 1
            continue
        if data is None:
            unread += 1
            continue
        source = yclients_record_source(data)
        if source == BookingSource.yclients_journal:
            booking.source = source
            journal += 1
        else:
            maps += 1
    db.commit()
    return {"checked": len(rows), "journal": journal, "maps": maps, "unread": unread}


def _select_value(lead: dict) -> str:
    for field in lead.get("custom_fields_values") or []:
        if str(field.get("field_name") or "") != FIELD_NAME:
            continue
        values = field.get("values") or []
        if values:
            return str(values[0].get("value") or "")
    return ""


def _booking_number(lead: dict) -> str:
    for field in lead.get("custom_fields_values") or []:
        if str(field.get("field_name") or "") != "Номер записи":
            continue
        values = field.get("values") or []
        if values:
            return str(values[0].get("value") or "").strip()
    return ""


def _leads() -> list[dict]:
    out: list[dict] = []
    page = 1
    while page <= 40:
        data = amocrm_client._request(
            "GET", "/api/v4/leads",
            params={
                "filter[pipeline_id]": int(settings.amocrm_pipeline_grooming_id or GROOMING_PIPE),
                "limit": 250,
                "page": page,
            },
        )
        batch = ((data or {}).get("_embedded") or {}).get("leads") or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 250:
            break
        page += 1
        time.sleep(0.2)
    return out


def push_sources(db: Session) -> dict:
    """Проставить поле на каждой сделке груминга, для которой есть запись."""
    bookings = {
        booking.id: booking
        for booking in db.execute(select(Booking)).scalars().all()
    }
    by_lead = {
        int(booking.amocrm_lead_id): booking
        for booking in bookings.values()
        if booking.amocrm_lead_id
    }
    leads = _leads()
    updated = same = skipped = unmatched = 0
    by_label: dict[str, int] = {}
    touched_status: dict[str, int] = {}
    for lead in leads:
        lead_id = int(lead["id"])
        booking = by_lead.get(lead_id)
        if booking is None:
            number = _booking_number(lead)
            booking = bookings.get(number)
        if booking is None:
            prefix = str(lead.get("name") or "").split(":", 1)[0].strip()
            booking = bookings.get(prefix)
        if booking is None:
            unmatched += 1
            continue
        label = amocrm_metrics.SOURCE_TO_LEAD_FIELD.get(booking.source, "")
        if not label:
            skipped += 1
            continue
        status = str(lead.get("status_id") or "")
        if _select_value(lead) == label:
            same += 1
            by_label[label] = by_label.get(label, 0) + 1
            touched_status[status] = touched_status.get(status, 0) + 1
            continue
        amocrm_client.update_grooming_lead(lead_id, fields={FIELD_NAME: label})
        updated += 1
        by_label[label] = by_label.get(label, 0) + 1
        touched_status[status] = touched_status.get(status, 0) + 1
        time.sleep(0.15)
    closed = touched_status.get("142", 0) + touched_status.get("143", 0)
    return {
        "leads": len(leads),
        "updated": updated,
        "already": same,
        "skipped": skipped,
        "unmatched": unmatched,
        "closed_with_source": closed,
        "open_with_source": sum(touched_status.values()) - closed,
        "by_label": by_label,
    }


def drop_admin_bot() -> str:
    return amocrm_client.drop_select_value("leads", FIELD_NAME, DROP_VALUE)


def run(db: Session) -> dict:
    if not settings.amocrm_ready or not settings.yclients_ready:
        return {"status": "not_ready"}
    classified = reclassify_journal(db)
    pushed = push_sources(db)
    removed = drop_admin_bot()
    return {"classified": classified, "pushed": pushed, "admin_bot": removed}
