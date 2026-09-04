"""Счёт покупателю: хранение по тарифу каждой позиции.

Период считает система: от дня после прошлого счёта (или от прихода) по date_to.
Начало руками не задаём — иначе неоплаченные сутки молча выпадут из счёта.
"""

from datetime import datetime, timedelta, timezone

from account import as_day, money, row_of, today
from db import (
    add_invoice_lot,
    get_client_by_id,
    get_lot,
    insert_invoice,
    mark_lot_billed,
    moves_by_day,
    shipped_qty,
)
from ms import SERVICES, ms_meta, org_id, service_id
from net import MS_BASE, ms_headers, req

MSK = timezone(timedelta(hours=3))


def collect(lot_ids, date_to=""):
    """Партии одного клиента и суммы, которые ещё не выставлялись."""
    end = as_day(date_to) or today()
    lots = []
    client = None
    for raw in lot_ids:
        lot = get_lot(int(raw))
        if not lot:
            continue
        if client is None:
            client = get_client_by_id(lot["client_id"])
        elif lot["client_id"] != client["id"]:
            raise ValueError("в одном счёте только один контрагент")
        lots.append(lot)
    if not lots:
        raise ValueError("не выбрано ни одной позиции")
    moves = moves_by_day()
    detail = []
    total = 0.0
    for lot in lots:
        calc = money(lot, client, None, end, moves.get(lot["id"], {}))
        total += calc["storage"]
        detail.append((lot, calc))
    parts = {"storage": round(total, 2), "intake": 0.0, "ship": 0.0}
    parts["total"] = parts["storage"]
    return client, detail, parts, end


def span_of(detail, end):
    starts = [c["bill_from"] for _l, c in detail if c["bill_days"] > 0]
    return (min(starts) if starts else end.isoformat()), end.isoformat()


def preview(lot_ids, date_to=""):
    client, detail, parts, end = collect(lot_ids, date_to)
    moves = moves_by_day()
    start, stop = span_of(detail, end)
    return {
        "client": client["name"],
        "client_id": client["id"],
        "period_from": start,
        "period_to": stop,
        "lines": [{"name": SERVICES["storage"], "sum": parts["storage"]}],
        "liter_days": round(sum(c["liter_days"] for _l, c in detail), 2),
        "total": parts["total"],
        "positions": [
            row_of(lot, client=client, ship_days=moves.get(lot["id"], {}), date_to=end)
            for lot, _calc in detail
        ],
    }


def ru_day(iso):
    day = as_day(iso)
    return day.strftime("%d.%m.%Y") if day else str(iso)


def description(detail, start, stop):
    return "Фулфилмент, хранение %s — %s: позиций %s, литро-суток %s" % (
        ru_day(start),
        ru_day(stop),
        len(detail),
        round(sum(c["liter_days"] for _l, c in detail), 2),
    )


def create(lot_ids, author="", date_to=""):
    client, detail, parts, end = collect(lot_ids, date_to)
    if parts["total"] <= 0:
        raise ValueError("по выбранным позициям нечего выставлять")
    if not client["ms_counterparty_id"]:
        raise ValueError("у клиента нет карточки в МойСклад")
    start, stop = span_of(detail, end)
    payload = {
        "organization": ms_meta("organization", client["ms_org_id"] or org_id()),
        "agent": ms_meta("counterparty", client["ms_counterparty_id"]),
        "vatEnabled": False,
        "description": description(detail, start, stop),
        "positions": [
            {
                "quantity": 1,
                "price": int(round(parts["storage"] * 100)),
                "vat": 0,
                "assortment": ms_meta("service", service_id("storage")),
            }
        ],
    }
    r = req("POST", MS_BASE + "/entity/invoiceout", headers=ms_headers(), json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError("счёт %s %s" % (r.status_code, (r.text or "")[:200]))
    data = r.json()
    now = datetime.now(MSK).isoformat()
    invoice_id = insert_invoice(
        client["id"],
        data.get("id"),
        data.get("name") or "",
        parts["storage"],
        0,
        0,
        parts["total"],
        len(detail),
        now,
        author,
        start,
        stop,
    )
    for lot, calc in detail:
        add_invoice_lot(
            invoice_id,
            lot["id"],
            calc["storage"],
            calc["bill_days"],
            calc["liter_days"],
            calc["bill_from"],
            calc["bill_to"],
        )
        mark_lot_billed(
            lot["id"],
            stop,
            calc["bill_days"],
            float(lot["qty_in"] or 0),
            shipped_qty(lot["id"]),
        )
    return {
        "id": invoice_id,
        "ms_id": data.get("id"),
        "number": data.get("name") or "",
        "client": client["name"],
        "total": parts["total"],
        "period_from": start,
        "period_to": stop,
        "positions": len(detail),
    }
