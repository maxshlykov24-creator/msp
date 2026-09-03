"""Счёт покупателю: три услуги, суммы по выбранным партиям."""

from datetime import datetime, timedelta, timezone

from account import money, row_of
from db import (
    add_invoice_lot,
    get_client_by_id,
    get_lot,
    insert_invoice,
    mark_lot_billed,
    shipped_qty,
)
from ms import SERVICES, ms_meta, org_id, service_id
from net import MS_BASE, ms_headers, req

MSK = timezone(timedelta(hours=3))


def collect(lot_ids):
    """Партии одного клиента и суммы, которые ещё не выставлялись."""
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
    parts = {"storage": 0.0, "intake": 0.0, "ship": 0.0}
    detail = []
    for lot in lots:
        calc = money(lot, client)
        parts["storage"] += calc["storage"]
        parts["intake"] += calc["intake"]
        parts["ship"] += calc["ship"]
        detail.append((lot, calc))
    for key in parts:
        parts[key] = round(parts[key], 2)
    parts["total"] = round(sum(parts[k] for k in ("storage", "intake", "ship")), 2)
    return client, detail, parts


def preview(lot_ids):
    client, detail, parts = collect(lot_ids)
    return {
        "client": client["name"],
        "client_id": client["id"],
        "lines": [
            {"name": SERVICES["storage"], "sum": parts["storage"]},
            {"name": SERVICES["intake"], "sum": parts["intake"]},
            {"name": SERVICES["ship"], "sum": parts["ship"]},
        ],
        "total": parts["total"],
        "positions": [row_of(lot) for lot, _calc in detail],
    }


def description(detail):
    days = sum(c["storage"] for _l, c in detail)
    return "Фулфилмент: позиций %s, хранение %s ₽, приёмка %s ₽, отгрузка %s ₽" % (
        len(detail),
        round(days, 2),
        round(sum(c["intake"] for _l, c in detail), 2),
        round(sum(c["ship"] for _l, c in detail), 2),
    )


def create(lot_ids, author=""):
    client, detail, parts = collect(lot_ids)
    if parts["total"] <= 0:
        raise ValueError("по выбранным позициям нечего выставлять")
    if not client["ms_counterparty_id"]:
        raise ValueError("у клиента нет карточки в МойСклад")
    positions = []
    for key in ("storage", "intake", "ship"):
        if parts[key] <= 0:
            continue
        positions.append(
            {
                "quantity": 1,
                "price": int(round(parts[key] * 100)),
                "vat": 0,
                "assortment": ms_meta("service", service_id(key)),
            }
        )
    payload = {
        "organization": ms_meta("organization", client["ms_org_id"] or org_id()),
        "agent": ms_meta("counterparty", client["ms_counterparty_id"]),
        "vatEnabled": False,
        "description": description(detail),
        "positions": positions,
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
        parts["intake"],
        parts["ship"],
        parts["total"],
        len(detail),
        now,
        author,
    )
    for lot, calc in detail:
        add_invoice_lot(invoice_id, lot["id"], calc["storage"], calc["intake"], calc["ship"])
        mark_lot_billed(lot["id"], calc["days"], float(lot["qty_in"] or 0), shipped_qty(lot["id"]))
    return {
        "id": invoice_id,
        "ms_id": data.get("id"),
        "number": data.get("name") or "",
        "client": client["name"],
        "total": parts["total"],
        "positions": len(detail),
    }
