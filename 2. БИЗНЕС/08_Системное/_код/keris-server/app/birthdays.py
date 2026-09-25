"""Воронка «Дни рождения».

В день рождения открывается сделка: питомец на «ДР щенка», клиент на «ДР клиента».
Название: имя и дата рождения. «Отправлен подарок» двигают руками. Дальше само:
через 7 дней «1 неделя», через 14 «2 недели». Ещё неделя без новой записи на
груминг — провал, запись была — успех. Запись с любого этапа тоже успех.
«ДР щенка» и «ДР клиента» дольше месяца без сдвига — провал.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import amocrm_client
from .booking_logic import normalize_phone
from .models import Booking, BookingStatus

log = logging.getLogger("keris.birthdays")

PIPE = 11339450
PET, CLIENT = 88876538, 88876542
GIFT, WEEK1, WEEK2 = 88876710, 88876714, 88876738
SUCCESS, FAIL = 142, 143
PET_BIRTH, CLIENT_BIRTH = 1820893, 1820897
MSK = timezone(timedelta(hours=3))
SKIP_NAMES = {"", "собака", "кошка", "питомец"}
_opened_on: date | None = None


def next_status(status: int, created: date, gift_on: date | None,
                today: date, booked: bool) -> int | None:
    """Куда перевести открытую сделку. None — оставить как есть."""
    if status in (SUCCESS, FAIL):
        return None
    if booked:
        return SUCCESS
    if status in (PET, CLIENT):
        return FAIL if (today - created).days > 30 else None
    if gift_on is None or status not in (GIFT, WEEK1, WEEK2):
        return None
    days = (today - gift_on).days
    if days >= 21:
        return FAIL
    if days >= 14 and status in (GIFT, WEEK1):
        return WEEK2
    if days >= 7 and status == GIFT:
        return WEEK1
    return None


def is_birthday(day: int, month: int, today: date) -> bool:
    if (day, month) == (today.day, today.month):
        return True
    return (
        (day, month) == (29, 2)
        and today.month == 3 and today.day == 1
        and not calendar.isleap(today.year)
    )


def deal_name(who: str, day: int, month: int, year: int | None) -> str:
    stamp = f"{day:02d}.{month:02d}.{year}" if year else f"{day:02d}.{month:02d}"
    return f"{who.strip()} · {stamp}"


def _today() -> date:
    return datetime.now(MSK).date()


def _to_date(value) -> date | None:
    if isinstance(value, (int, float)) and value:
        return datetime.fromtimestamp(int(value), MSK).date()
    return None


def _parts(value) -> tuple[int, int, int | None] | None:
    parsed = _to_date(value)
    if parsed is None or parsed.year < 1900:
        return None
    year = parsed.year if parsed.year > 1900 else None
    return parsed.day, parsed.month, year


def _cf(item: dict, field_id: int):
    for row in item.get("custom_fields_values") or []:
        if row.get("field_id") != field_id:
            continue
        vals = row.get("values") or []
        if vals:
            return vals[0].get("value")
    return None


def _pages(path: str, key: str, params: dict) -> list[dict]:
    rows = []
    page = 1
    while page <= 40:
        query = dict(params)
        query["page"] = page
        query["limit"] = 250
        data = amocrm_client._request("GET", path, params=query) or {}
        chunk = ((data.get("_embedded") or {}).get(key)) or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 250:
            break
        page += 1
    return rows


def _phone(contact: dict) -> str:
    for field in contact.get("custom_fields_values") or []:
        if (field or {}).get("field_code") != "PHONE":
            continue
        for value in field.get("values") or []:
            phone = normalize_phone(str((value or {}).get("value") or ""))
            if phone.startswith("+7") and len(phone) == 12:
                return phone
    return ""


def _lead_contact_id(lead: dict) -> int | None:
    contacts = ((lead.get("_embedded") or {}).get("contacts")) or []
    if not contacts:
        return None
    cid = contacts[0].get("id")
    return int(cid) if cid else None


def _gift_date(lead_id: int) -> date | None:
    data = amocrm_client._request(
        "GET", "/api/v4/events",
        params={
            "filter[entity]": "lead",
            "filter[entity_id]": lead_id,
            "filter[type]": "lead_status_changed",
            "limit": 50,
        },
    ) or {}
    events = ((data.get("_embedded") or {}).get("events")) or []
    moments = []
    for event in events:
        after = event.get("value_after") or []
        for item in after:
            status = (item or {}).get("lead_status") or {}
            if status.get("id") == GIFT and event.get("created_at"):
                moments.append(int(event["created_at"]))
    if not moments:
        return None
    return datetime.fromtimestamp(min(moments), MSK).date()


def _move(lead_id: int, status_id: int) -> None:
    amocrm_client._request(
        "PATCH", f"/api/v4/leads/{int(lead_id)}",
        json={"status_id": status_id, "pipeline_id": PIPE},
    )


def _create(name: str, status_id: int, contact_id: int | None, company_id: int | None) -> None:
    embedded: dict = {}
    if contact_id:
        embedded["contacts"] = [{"id": int(contact_id), "is_main": True}]
    if company_id:
        embedded["companies"] = [{"id": int(company_id)}]
    item: dict = {"name": name, "pipeline_id": PIPE, "status_id": status_id}
    if embedded:
        item["_embedded"] = embedded
    amocrm_client._request("POST", "/api/v4/leads", json=[item])


def _year_leads(today: date) -> list[dict]:
    start = int(datetime(today.year, 1, 1, tzinfo=MSK).timestamp())
    return _pages("/api/v4/leads", "leads", {
        "filter[pipeline_id]": PIPE,
        "filter[created_at][from]": start,
        "with": "contacts",
    })


def _booked_phones(db: Session, since: datetime) -> set[str]:
    rows = db.execute(
        select(Booking).where(
            Booking.created_at >= since.replace(tzinfo=None),
            Booking.status.notin_((BookingStatus.cancelled, BookingStatus.no_show)),
        )
    ).scalars().all()
    return {normalize_phone(b.owner_phone) for b in rows if b.owner_phone}


def _advance(db: Session, leads: list[dict], today: date) -> int:
    open_leads = [lead for lead in leads if lead.get("status_id") not in (SUCCESS, FAIL)]
    if not open_leads:
        return 0
    earliest = min(int(lead.get("created_at") or 0) for lead in open_leads)
    since = datetime.fromtimestamp(earliest, timezone.utc)
    booked = _booked_phones(db, since)
    phones: dict[int, str] = {}
    moved = 0
    for lead in open_leads:
        created = datetime.fromtimestamp(int(lead["created_at"]), MSK).date()
        cid = _lead_contact_id(lead)
        phone = ""
        if cid:
            phone = phones.get(cid, "")
            if cid not in phones:
                try:
                    phone = _phone(amocrm_client.get_contact(cid))
                except amocrm_client.AmoCrmError:
                    phone = ""
                phones[cid] = phone
        has_booking = bool(phone and phone in booked and _booking_after(db, phone, int(lead["created_at"])))
        gift_on = None
        if lead.get("status_id") in (GIFT, WEEK1, WEEK2) and not has_booking:
            gift_on = _gift_date(int(lead["id"])) or created
        target = next_status(int(lead["status_id"]), created, gift_on, today, has_booking)
        if target is None or target == lead.get("status_id"):
            continue
        _move(int(lead["id"]), target)
        moved += 1
    return moved


def _booking_after(db: Session, phone: str, created_at: int) -> bool:
    moment = datetime.fromtimestamp(created_at, timezone.utc).replace(tzinfo=None)
    row = db.execute(
        select(Booking.id).where(
            Booking.owner_phone == phone,
            Booking.created_at >= moment,
            Booking.status.notin_((BookingStatus.cancelled, BookingStatus.no_show)),
        ).limit(1)
    ).first()
    return row is not None


def _open_new(db: Session, leads: list[dict], today: date) -> int:
    taken = {((lead.get("name") or "").strip()) for lead in leads}
    made = 0
    made += _open_companies(today, taken)
    made += _open_booking_pets(db, today, taken)
    made += _open_clients(today, taken)
    return made


def _open_companies(today: date, taken: set[str]) -> int:
    made = 0
    for company in _pages("/api/v4/companies", "companies", {"with": "contacts"}):
        parts = _parts(_cf(company, PET_BIRTH))
        if parts is None or not is_birthday(parts[0], parts[1], today):
            continue
        who = (company.get("name") or "").strip()
        if who.lower() in SKIP_NAMES:
            continue
        name = deal_name(who, parts[0], parts[1], parts[2])
        if name in taken:
            continue
        contacts = ((company.get("_embedded") or {}).get("contacts")) or []
        cid = contacts[0].get("id") if contacts else None
        _create(name, PET, int(cid) if cid else None, int(company["id"]))
        taken.add(name)
        made += 1
    return made


def _open_booking_pets(db: Session, today: date, taken: set[str]) -> int:
    rows = db.execute(
        select(Booking).where(Booking.pet_birth_date != "").order_by(Booking.starts_at)
    ).scalars().all()
    latest: dict[tuple[str, str], Booking] = {}
    for booking in rows:
        pet = (booking.pet_name or "").strip()
        if pet.lower() in SKIP_NAMES:
            continue
        latest[(booking.owner_phone, pet.lower())] = booking
    made = 0
    for booking in latest.values():
        raw = (booking.pet_birth_date or "").strip()
        parsed = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None or not is_birthday(parsed.day, parsed.month, today):
            continue
        year = parsed.year if parsed.year > 1900 else None
        name = deal_name(booking.pet_name.strip(), parsed.day, parsed.month, year)
        if name in taken:
            continue
        contact = amocrm_client.find_contact_by_phone(booking.owner_phone)
        if not contact:
            continue
        _create(name, PET, int(contact["id"]), booking.amocrm_company_id)
        taken.add(name)
        made += 1
    return made


def _open_clients(today: date, taken: set[str]) -> int:
    made = 0
    for contact in _pages("/api/v4/contacts", "contacts", {}):
        parts = _parts(_cf(contact, CLIENT_BIRTH))
        if parts is None or not is_birthday(parts[0], parts[1], today):
            continue
        who = (contact.get("name") or "").strip()
        if not who:
            continue
        name = deal_name(who, parts[0], parts[1], parts[2])
        if name in taken:
            continue
        _create(name, CLIENT, int(contact["id"]), None)
        taken.add(name)
        made += 1
    return made


def run_once(db: Session) -> None:
    global _opened_on
    if not amocrm_client.settings.amocrm_ready:
        return
    today = _today()
    leads = _year_leads(today)
    moved = _advance(db, leads, today)
    opened = 0
    if _opened_on != today:
        opened = _open_new(db, leads, today)
        _opened_on = today
    if moved or opened:
        log.info("дни рождения: открыто %s, переведено %s", opened, moved)
