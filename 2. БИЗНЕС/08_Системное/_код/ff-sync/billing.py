"""Счёт покупателю: хранение по общей ставке плюс сборка по ставке позиции.

Период считает система: от дня после прошлого счёта (или от приёмки) по date_to.
Начало руками не задаём — иначе неоплаченные сутки молча выпадут из счёта.
"""

from datetime import datetime, timedelta, timezone

from account import accepted_day, as_day, money, row_of, today
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
    waiting = 0
    for raw in lot_ids:
        lot = get_lot(int(raw))
        if not lot:
            continue
        if client is None:
            client = get_client_by_id(lot["client_id"])
        elif lot["client_id"] != client["id"]:
            raise ValueError("в одном счёте только один контрагент")
        if accepted_day(lot) is None:
            # партию не приняли на склад: закрыть её счётом нельзя, иначе дни
            # до приёмки молча выпадут из следующего периода
            waiting += 1
            continue
        lots.append(lot)
    if not lots:
        if waiting:
            raise ValueError("позиции ещё не приняты на склад: их %s" % waiting)
        raise ValueError("не выбрано ни одной позиции")
    moves = moves_by_day()
    detail = []
    storage = 0.0
    pick = 0.0
    for lot in lots:
        calc = money(lot, client, None, end, moves.get(lot["id"], {}))
        storage += calc["storage"]
        pick += calc["pick"]
        detail.append((lot, calc))
    parts = {"storage": round(storage, 2), "intake": 0.0, "ship": round(pick, 2)}
    parts["total"] = round(parts["storage"] + parts["ship"], 2)
    return client, detail, parts, end


def lines_of(parts):
    """В счёт идут только непустые услуги: хранение и сборка считаются раздельно."""
    out = []
    for key in ("storage", "ship"):
        if parts.get(key):
            out.append({"name": SERVICES[key], "sum": parts[key]})
    return out


def span_of(detail, end):
    starts = [c["bill_from"] for _l, c in detail if c["bill_from"] and (c["bill_days"] > 0 or c["pick"] > 0)]
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
        "lines": lines_of(parts),
        "liter_days": round(sum(c["liter_days"] for _l, c in detail), 2),
        "pick_qty": round(sum(c["pick_qty"] for _l, c in detail), 3),
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
    return "Фулфилмент %s — %s: позиций %s, литро-суток %s, собрано %s шт" % (
        ru_day(start),
        ru_day(stop),
        len(detail),
        round(sum(c["liter_days"] for _l, c in detail), 2),
        round(sum(c["pick_qty"] for _l, c in detail), 3),
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
                "price": int(round(parts[key] * 100)),
                "vat": 0,
                "assortment": ms_meta("service", service_id(key)),
            }
            for key in ("storage", "ship")
            if parts[key]
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
        parts["ship"],
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
            calc["ship"],
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
