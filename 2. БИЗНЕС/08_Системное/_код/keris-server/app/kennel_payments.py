"""Оплата воронки «Питомник». Раз в 5 минут из reminders_loop.

Сумму пишут в «Внесение платежа (₽)». Проход переносит её в «Оплачено (₽)»,
очищает внесение, ставит статус и остаток (бюджет сделки минус оплачено).
Рассрочка: следующая дата = якорь плюс месяц, даже если платёж пришёл позже.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from . import amocrm_client

log = logging.getLogger("keris.kennel.payments")

PIPE = 11036674
PAID, STATUS, TYPE = 1820831, 1820833, 1820839
NEXT, DEBT, ENTRY, INST = 1820847, 1820849, 2109839, 2109841
ANCHOR_NAME = "Якорь платежа"

PART, FULL, REFUND = 3087125, 3087127, 3265421
TYPE_FULL, TYPE_INST = 3087143, 3087145
INST_OK, INST_LATE = 3265417, 3265419
MSK = timezone(timedelta(hours=3))


def _today() -> date:
    return datetime.now(MSK).date()


def _add_month(d: date) -> date:
    month = d.month + 1
    year = d.year + (month == 13)
    if month == 13:
        month = 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _to_date(value) -> date | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), MSK).date()
    return None


def _unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=MSK).timestamp())


def _num(value) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def _cf(lead: dict) -> dict:
    out = {}
    for row in lead.get("custom_fields_values") or []:
        vals = row.get("values") or []
        if not vals:
            continue
        val = vals[0]
        out[row["field_id"]] = val.get("enum_id") or val.get("value")
    return out


def _anchor_id() -> int:
    page = 1
    while page <= 5:
        data = amocrm_client._request(
            "GET", "/api/v4/leads/custom_fields", params={"page": page, "limit": 250}
        )
        rows = (data.get("_embedded") or {}).get("custom_fields") or []
        for field in rows:
            if field.get("name") == ANCHOR_NAME:
                return field["id"]
        if len(rows) < 250:
            break
        page += 1
    data = amocrm_client._request(
        "POST", "/api/v4/leads/custom_fields", json=[{"name": ANCHOR_NAME, "type": "date"}]
    )
    created = (data.get("_embedded") or {}).get("custom_fields") or []
    return created[0]["id"]


def _plan(lead: dict, anchor_id: int, now: date) -> dict | None:
    fields = _cf(lead)
    pay_type = fields.get(TYPE)
    entry = _num(fields.get(ENTRY))
    if pay_type not in (TYPE_FULL, TYPE_INST) and entry <= 0:
        return None
    paid = _num(fields.get(PAID))
    price = _num(lead.get("price"))
    status = fields.get(STATUS)
    inst = fields.get(INST)
    nxt = _to_date(fields.get(NEXT))
    anchor = _to_date(fields.get(anchor_id))
    consumed = False
    if entry > 0:
        paid += entry
        consumed = True
        if pay_type == TYPE_INST:
            nxt = _add_month(anchor or nxt or now)
            anchor = nxt
    if pay_type == TYPE_INST and anchor is None and nxt is not None and not consumed:
        anchor = nxt
    debt = max(price - paid, 0) if price > 0 else None
    new_status = status
    if status != REFUND:
        if price > 0 and paid >= price:
            new_status = FULL
        elif paid > 0 and status != FULL:
            new_status = PART
    new_inst = inst
    if pay_type == TYPE_INST:
        if price > 0 and paid >= price:
            new_inst = INST_OK
        elif nxt is not None and now > nxt:
            new_inst = INST_LATE
        elif nxt is not None:
            new_inst = INST_OK
    patch = {}
    if consumed:
        patch[ENTRY] = None
        patch[PAID] = paid
    if debt is not None and debt != _num(fields.get(DEBT)):
        patch[DEBT] = debt
    if new_status != status and new_status is not None:
        patch[STATUS] = new_status
    if pay_type == TYPE_INST:
        if nxt != _to_date(fields.get(NEXT)):
            patch[NEXT] = nxt
        if anchor != _to_date(fields.get(anchor_id)):
            patch[anchor_id] = anchor
        if new_inst != inst and new_inst is not None:
            patch[INST] = new_inst
    return patch or None


def _cell(field_id: int, value, anchor_id: int) -> dict:
    if value is None:
        if field_id in (NEXT, anchor_id):
            return {"field_id": field_id, "values": None}
        return {"field_id": field_id, "values": [{"value": None}]}
    if field_id in (STATUS, INST):
        return {"field_id": field_id, "values": [{"enum_id": value}]}
    if isinstance(value, date):
        return {"field_id": field_id, "values": [{"value": _unix(value)}]}
    return {"field_id": field_id, "values": [{"value": value}]}


def apply_lead(lead_id: int) -> bool:
    """Разобрать одну сделку. Пустое внесение и чужая воронка ничего не меняют,
    поэтому повторный вебхук от нашего же PATCH не зацикливается."""
    if not amocrm_client.settings.amocrm_ready or amocrm_client.blocked():
        return False
    data = amocrm_client._request("GET", f"/api/v4/leads/{int(lead_id)}")
    if not data or int(data.get("pipeline_id") or 0) != PIPE:
        return False
    patch = _plan(data, _anchor_id(), _today())
    if not patch:
        return False
    anchor_id = _anchor_id()
    amocrm_client._request(
        "PATCH",
        "/api/v4/leads",
        json=[{
            "id": int(lead_id),
            "custom_fields_values": [
                _cell(fid, val, anchor_id) for fid, val in patch.items()
            ],
        }],
    )
    log.info("оплата питомника: сделка %s разобрана", lead_id)
    return True


def run_once() -> int:
    if not amocrm_client.settings.amocrm_ready or amocrm_client.blocked():
        return 0
    anchor_id = _anchor_id()
    now = _today()
    changed = 0
    page = 1
    while page <= 40:
        data = amocrm_client._request(
            "GET",
            "/api/v4/leads",
            params={"filter[pipeline_id]": PIPE, "page": page, "limit": 250},
        )
        rows = (data.get("_embedded") or {}).get("leads") or []
        if not rows:
            break
        for lead in rows:
            patch = _plan(lead, anchor_id, now)
            if not patch:
                continue
            amocrm_client._request(
                "PATCH",
                "/api/v4/leads",
                json=[{
                    "id": lead["id"],
                    "custom_fields_values": [
                        _cell(fid, val, anchor_id) for fid, val in patch.items()
                    ],
                }],
            )
            changed += 1
        if len(rows) < 250:
            break
        page += 1
    if changed:
        log.info("оплата питомника: обновлено сделок %s", changed)
    return changed
