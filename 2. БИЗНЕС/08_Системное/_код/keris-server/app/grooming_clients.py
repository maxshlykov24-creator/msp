"""Воронка «Клиенты груминга»: одна открытая сделка на питомца после визита.

Визит живёт в воронке «Груминг». Когда клиент уже пришёл, здесь открывается
сопровождение от даты этого визита. Этап зависит от того, сколько дней прошло.
Новый визит того же питомца закрывает прежнюю сделку в «Пришел повторно»
и открывает новую. Два питомца одного владельца — две сделки.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import amocrm_client, clock
from .amocrm_stages import STAGE_ARRIVED, STAGE_DONE
from .models import Booking, BookingStatus

log = logging.getLogger("keris.grooming.clients")

PIPE_CLIENTS = 11339218
BOOKING_NO = 2072903
PET = 2072913
VISIT_AT = 2072905

# Верхняя граница дней, не включая её. Дальше 180 дней остаётся «6 месяцев».
STAGES = (
    (7, 88875102, "Посетил груминг"),
    (14, 88875106, "1 неделя с посещения"),
    (21, 88875206, "2 недели с посещения"),
    (30, 88875210, "3 недели с посещения"),
    (60, 88875214, "1 месяц с посещения"),
    (90, 88875218, "2 месяца с посещения"),
    (180, 88875222, "3 месяца с посещения"),
)
STAGE_HALF_YEAR = 88875226
SUCCESS = 142


def stage_for_days(days: int) -> tuple[int, str]:
    for limit, sid, name in STAGES:
        if days < limit:
            return sid, name
    return STAGE_HALF_YEAR, "6 месяцев с посещения"


def _visited(booking: Booking, today: date) -> bool:
    if booking.starts_at.date() > today:
        return False
    if booking.status in (BookingStatus.cancelled, BookingStatus.no_show):
        return False
    if booking.status == BookingStatus.completed:
        return True
    return booking.amocrm_stage in (STAGE_ARRIVED, STAGE_DONE)


def _pet_key(booking: Booking) -> tuple:
    if booking.amocrm_company_id:
        return ("co", booking.amocrm_company_id)
    return ("pet", booking.owner_phone, (booking.pet_name or "").strip().lower())


def _load_open(index: dict[str, dict]) -> None:
    page = 1
    while page <= 20:
        data = amocrm_client._request(
            "GET", "/api/v4/leads",
            params={
                "filter[pipeline_id]": PIPE_CLIENTS,
                "with": "contacts",
                "page": page,
                "limit": 250,
            },
        )
        rows = (data.get("_embedded") or {}).get("leads") or []
        if not rows:
            return
        for lead in rows:
            if lead.get("status_id") in (SUCCESS, 143):
                continue
            number = ""
            for row in lead.get("custom_fields_values") or []:
                if row.get("field_id") == BOOKING_NO:
                    vals = row.get("values") or []
                    number = str(vals[0].get("value") or "") if vals else ""
            if number:
                index[number] = lead
        if len(rows) < 250:
            return
        page += 1


def _links(booking: Booking) -> tuple[int | None, int | None]:
    if not booking.amocrm_lead_id:
        return None, booking.amocrm_company_id
    data = amocrm_client._request(
        "GET", f"/api/v4/leads/{int(booking.amocrm_lead_id)}",
        params={"with": "contacts,companies"},
    )
    emb = (data or {}).get("_embedded") or {}
    contacts = [c.get("id") for c in emb.get("contacts") or [] if c.get("id")]
    companies = [c.get("id") for c in emb.get("companies") or [] if c.get("id")]
    return (contacts[0] if contacts else None, companies[0] if companies else booking.amocrm_company_id)


def _ensure(booking: Booking, days: int, existing: dict | None) -> None:
    sid, _name = stage_for_days(days)
    if existing:
        if existing.get("status_id") != sid:
            amocrm_client._request(
                "PATCH", f"/api/v4/leads/{existing['id']}",
                json={"status_id": sid, "pipeline_id": PIPE_CLIENTS},
            )
        return
    contact_id, company_id = _links(booking)
    embedded: dict = {}
    if contact_id:
        embedded["contacts"] = [{"id": int(contact_id)}]
    if company_id:
        embedded["companies"] = [{"id": int(company_id)}]
    fields = [
        {"field_id": BOOKING_NO, "values": [{"value": str(booking.id)}]},
    ]
    if booking.pet_name:
        fields.append({"field_id": PET, "values": [{"value": booking.pet_name}]})
    fields.append({
        "field_id": VISIT_AT,
        "values": [{"value": int(booking.starts_at.timestamp())}],
    })
    item = {
        "name": booking.pet_name or str(booking.id),
        "pipeline_id": PIPE_CLIENTS,
        "status_id": sid,
        "price": int(booking.price or 0),
        "custom_fields_values": fields,
    }
    if embedded:
        item["_embedded"] = embedded
    amocrm_client._request("POST", "/api/v4/leads", json=[item])


def run_once(db: Session, now: datetime | None = None) -> dict:
    if not amocrm_client.settings.amocrm_ready or amocrm_client.blocked():
        return {"opened": 0, "moved": 0, "closed": 0}
    now = now or clock.now().replace(tzinfo=None)
    today = now.date()
    bookings = [
        b for b in db.execute(select(Booking)).scalars().all()
        if _visited(b, today)
    ]
    groups: dict[tuple, list[Booking]] = {}
    for booking in bookings:
        groups.setdefault(_pet_key(booking), []).append(booking)
    index: dict[str, dict] = {}
    _load_open(index)
    opened = moved = closed = 0
    for items in groups.values():
        items.sort(key=lambda b: b.starts_at)
        latest = items[-1]
        for old in items[:-1]:
            row = index.get(str(old.id))
            if not row:
                continue
            amocrm_client._request(
                "PATCH", f"/api/v4/leads/{row['id']}",
                json={"status_id": SUCCESS, "pipeline_id": PIPE_CLIENTS},
            )
            closed += 1
            index.pop(str(old.id), None)
        days = (today - latest.starts_at.date()).days
        before = index.get(str(latest.id))
        _ensure(latest, days, before)
        if before:
            moved += 1
        else:
            opened += 1
    if opened or closed:
        log.info("клиенты груминга: открыто %s, этап %s, в успех %s", opened, moved, closed)
    return {"opened": opened, "moved": moved, "closed": closed}
