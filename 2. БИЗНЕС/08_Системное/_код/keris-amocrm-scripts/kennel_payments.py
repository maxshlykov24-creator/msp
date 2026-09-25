#!/usr/bin/env python3
"""Оплата сделок воронки «Питомник».

Человек пишет сумму в «Внесение платежа (₽)». Проход забирает её в «Оплачено (₽)»,
очищает внесение, ставит статус оплаты и остаток долга. Для рассрочки сдвигает
дату ближайшего платежа на месяц от якоря, а не от дня оплаты.

Запуск: python3 kennel_payments.py          применить
        python3 kennel_payments.py --dry    только показать
"""
from __future__ import annotations

import calendar
import sys
import time
from datetime import date, datetime, timedelta, timezone

from actualize_sales import amo, amo_pages

PIPE = 11036674
PAID = 1820831
STATUS = 1820833
TYPE = 1820839
NEXT = 1820847
DEBT = 1820849
ENTRY = 2109839
INST = 2109841
ANCHOR_NAME = "Якорь платежа"

PART, FULL, REFUND = 3087125, 3087127, 3265421
TYPE_FULL, TYPE_INST = 3087143, 3087145
INST_OK, INST_LATE = 3265417, 3265419

MSK = timezone(timedelta(hours=3))


def today() -> date:
    return datetime.now(MSK).date()


def add_month(d: date) -> date:
    month = d.month + 1
    year = d.year
    if month == 13:
        month = 1
        year += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def to_date(value) -> date | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), MSK).date()
    return None


def to_unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=MSK).timestamp())


def num(value) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def cf_map(lead: dict) -> dict:
    out = {}
    for row in lead.get("custom_fields_values") or []:
        vals = row.get("values") or []
        if not vals:
            continue
        val = vals[0]
        out[row["field_id"]] = val.get("enum_id") if val.get("enum_id") else val.get("value")
    return out


def ensure_anchor() -> int:
    for field in amo_pages("/api/v4/leads/custom_fields", key="custom_fields"):
        if field.get("name") == ANCHOR_NAME:
            return field["id"]
    code, body = amo("POST", "/api/v4/leads/custom_fields", [{"name": ANCHOR_NAME, "type": "date"}])
    if not (200 <= code < 300):
        sys.exit(f"не создал поле {ANCHOR_NAME}: {code} {body}")
    created = (body.get("_embedded") or {}).get("custom_fields") or []
    return created[0]["id"]


def plan(lead: dict, anchor_id: int, now: date) -> dict | None:
    fields = cf_map(lead)
    pay_type = fields.get(TYPE)
    entry = num(fields.get(ENTRY))
    if pay_type not in (TYPE_FULL, TYPE_INST) and entry <= 0:
        return None

    paid = num(fields.get(PAID))
    price = num(lead.get("price"))
    status = fields.get(STATUS)
    inst = fields.get(INST)
    nxt = to_date(fields.get(NEXT))
    anchor = to_date(fields.get(anchor_id))
    consumed = False

    if entry > 0:
        paid += entry
        consumed = True
        if pay_type == TYPE_INST:
            base = anchor or nxt or now
            nxt = add_month(base)
            anchor = nxt

    if pay_type == TYPE_INST and anchor is None and nxt is not None and not consumed:
        anchor = nxt

    debt = None
    if price > 0:
        debt = max(price - paid, 0)

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
    if debt is not None and debt != num(fields.get(DEBT)):
        patch[DEBT] = debt
    if new_status != status and new_status is not None:
        patch[STATUS] = new_status
    if pay_type == TYPE_INST:
        if nxt != to_date(fields.get(NEXT)):
            patch[NEXT] = nxt
        if anchor != to_date(fields.get(anchor_id)):
            patch[anchor_id] = anchor
        if new_inst != inst and new_inst is not None:
            patch[INST] = new_inst
    return patch or None


def value_of(field_id: int, value):
    if field_id in (STATUS, TYPE, INST):
        return {"enum_id": value}
    if field_id in (NEXT,) or field_id not in (PAID, DEBT, ENTRY, STATUS, TYPE, INST):
        if value is None:
            return {"value": None}
        if isinstance(value, date):
            return {"value": to_unix(value)}
    if value is None:
        return {"value": None}
    return {"value": value}


def apply_one(lead_id: int, patch: dict, anchor_id: int) -> tuple[int, dict]:
    body = [{
        "id": lead_id,
        "custom_fields_values": [
            {"field_id": fid, "values": [value_of(fid, val)]}
            for fid, val in patch.items()
        ],
    }]
    # якорь тоже дата
    for row in body[0]["custom_fields_values"]:
        if row["field_id"] == anchor_id and row["values"][0].get("value") not in (None,):
            if not isinstance(row["values"][0]["value"], int):
                row["values"][0] = {"value": to_unix(row["values"][0]["value"])}
    return amo("PATCH", "/api/v4/leads", body)


def main() -> None:
    dry = "--dry" in sys.argv
    anchor_id = ensure_anchor()
    now = today()
    changed = 0
    for lead in amo_pages("/api/v4/leads", {"filter[pipeline_id]": str(PIPE)}):
        patch = plan(lead, anchor_id, now)
        if not patch:
            continue
        changed += 1
        print(lead["id"], lead.get("name"), patch)
        if dry:
            continue
        code, body = apply_one(lead["id"], patch, anchor_id)
        print(" ", code)
        if not (200 <= code < 300):
            print(" ", body)
        time.sleep(0.15)
    print("anchor", anchor_id, "changed", changed, "dry" if dry else "applied")


if __name__ == "__main__":
    main()
