"""Партии FIFO, дни на складе и деньги к выставлению."""

from datetime import datetime, timedelta, timezone

from db import add_lot_move, get_client_by_id, list_lots, list_lots_fifo, shipped_qty
from ms import tracking_code

MSK = timezone(timedelta(hours=3))


def parse_when(raw):
    if not raw:
        return datetime.now(MSK)
    text = str(raw).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(MSK)
    if when.tzinfo is None:
        when = when.replace(tzinfo=MSK)
    return when.astimezone(MSK)


def days_on_stock(received_at):
    rec = parse_when(received_at).date()
    return max(0, (datetime.now(MSK).date() - rec).days)


def money(lot, client):
    """Считаем только то, что ещё не попало в счёт."""
    liters = float(lot["liters"] or 0)
    qty_left = float(lot["qty_left"] or 0)
    qty_in = float(lot["qty_in"] or 0)
    out = shipped_qty(lot["id"])
    days = days_on_stock(lot["received_at"])
    store_t = float(client["tariff_storage"] or 0) if client else 0
    in_t = float(client["tariff_intake"] or 0) if client else 0
    out_t = float(client["tariff_ship"] or 0) if client else 0
    free_days = max(0.0, days - float(lot["billed_days"] or 0))
    storage = round(free_days * liters * qty_left * store_t, 2)
    intake = round(max(0.0, qty_in - float(lot["billed_in"] or 0)) * in_t, 2)
    ship = round(max(0.0, out - float(lot["billed_out"] or 0)) * out_t, 2)
    return {
        "days": days,
        "shipped": out,
        "storage": storage,
        "intake": intake,
        "ship": ship,
        "total": round(storage + intake + ship, 2),
    }


def consume_fifo(client_id, ms_product_id, qty, ref):
    left = float(qty or 0)
    if left <= 0:
        return 0
    now = datetime.now(MSK).isoformat()
    for lot in list_lots_fifo(client_id, ms_product_id):
        take = min(float(lot["qty_left"]), left)
        if take <= 0:
            continue
        add_lot_move(lot["id"], take, "ship", ref, now)
        left -= take
        if left <= 0:
            break
    return left


def row_of(lot):
    client = get_client_by_id(lot["client_id"])
    calc = money(lot, client)
    liters = float(lot["liters"] or 0)
    qty_left = float(lot["qty_left"] or 0)
    return {
        "id": lot["id"],
        "client_id": lot["client_id"],
        "client": client["name"] if client else "",
        "article": lot["article"] or "",
        "barcode": lot["barcode"] or "",
        "gtin": lot["gtin"] or "",
        "name": lot["name"] or "",
        "tracking": lot["tracking_type"] or "",
        "liters": liters,
        "qty": qty_left,
        "qty_in": float(lot["qty_in"] or 0),
        "shipped": calc["shipped"],
        "volume": round(liters * qty_left, 3),
        "received": parse_when(lot["received_at"]).strftime("%d.%m.%Y"),
        "days": calc["days"],
        "storage": calc["storage"],
        "intake": calc["intake"],
        "ship": calc["ship"],
        "total": calc["total"],
    }


def is_marked(tracking):
    return tracking_code(tracking) != "NOT_TRACKED"


def lot_rows(client_id=None, query="", marked=None, only_open=True):
    text = (query or "").strip().lower()
    rows = []
    for lot in list_lots(only_open=only_open):
        if client_id and lot["client_id"] != int(client_id):
            continue
        row = row_of(lot)
        if marked is True and not is_marked(row["tracking"]):
            continue
        if marked is False and is_marked(row["tracking"]):
            continue
        if text:
            hay = " ".join(
                str(row[k]).lower() for k in ("article", "barcode", "gtin", "name", "client")
            )
            if text not in hay:
                continue
        rows.append(row)
    return rows


def totals(rows):
    return {
        "positions": len(rows),
        "qty": round(sum(r["qty"] for r in rows), 3),
        "volume": round(sum(r["volume"] for r in rows), 2),
        "money": round(sum(r["total"] for r in rows), 2),
        "aged": sum(1 for r in rows if r["days"] > 90),
    }
