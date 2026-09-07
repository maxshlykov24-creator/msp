"""Партии FIFO, дни на складе и деньги к выставлению.

Две метрики, а не одна колонка «тариф». Хранение считаем как в таблице Глеба:
у каждых суток свой остаток, формула дня — остаток на утро × литраж × ставка.
Ставка хранения одна на всех, 0,15 ₽ за литр в сутки: она не зависит ни от
товара, ни от клиента. Разная только сборка — напильник и игрушка стоят
по-разному, поэтому ставка сборки живёт у позиции, в ₽ за штуку.

Счётчик стартует от фактической приёмки на склад, а не от заведения карточки:
пока в lots.accepted_at пусто, партия ничего не должна.
"""

from datetime import date, datetime, timedelta, timezone
from os import environ

from db import (
    add_lot_move,
    get_client_by_id,
    list_clients,
    list_lots,
    list_lots_fifo,
    moves_by_day,
)
from ms import tracking_code

MSK = timezone(timedelta(hours=3))
MAX_DAYS = 1500
STORAGE_RATE = 0.15


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


def today():
    return datetime.now(MSK).date()


def as_day(raw, fallback=None):
    text = str(raw or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return fallback


def days_on_stock(received_at):
    return max(0, (today() - parse_when(received_at).date()).days)


def storage_rate():
    """Ставка хранения одна на всех. Меняется переменной, а не в карточке."""
    raw = environ.get("FF_STORAGE_RATE")
    if not raw:
        return STORAGE_RATE
    try:
        return float(str(raw).replace(",", "."))
    except ValueError:
        return STORAGE_RATE


def pick_rate_of(lot, client):
    """Сборка, ₽ за штуку: у позиции своя, иначе ставка клиента по умолчанию."""
    if "pick_rate" in lot.keys() and lot["pick_rate"] is not None:
        return float(lot["pick_rate"])
    if client and "tariff_pick" in client.keys() and client["tariff_pick"] is not None:
        return float(client["tariff_pick"] or 0)
    return 0.0


def accepted_day(lot):
    """День фактической приёмки. Пусто — партия ещё не встала на счётчик."""
    if "accepted_at" not in lot.keys():
        return parse_when(lot["received_at"]).date()
    raw = lot["accepted_at"]
    if not raw:
        return None
    return parse_when(raw).date()


def billed_until(lot):
    if "billed_until" not in lot.keys():
        return None
    return as_day(lot["billed_until"])


def window(lot, date_from=None, date_to=None):
    """Что ещё не выставлено: со дня после прошлого счёта по date_to включительно."""
    start = accepted_day(lot)
    if start is None:
        return None, None
    closed = billed_until(lot)
    if closed and closed >= start:
        start = closed + timedelta(days=1)
    if date_from and date_from > start:
        start = date_from
    end = date_to or today()
    return start, end


def storage_of(lot, client, date_from=None, date_to=None, ship_days=None):
    """Дни периода с остатком на каждое утро и деньги за них."""
    liters = float(lot["liters"] or 0)
    tariff = storage_rate()
    start, end = window(lot, date_from, date_to)
    if start is None:
        return {"from": "", "to": "", "days": 0, "liter_days": 0.0, "storage": 0.0, "by_day": []}
    out = ship_days if ship_days is not None else moves_by_day(lot["id"]).get(lot["id"], {})
    qty = float(lot["qty_in"] or 0) - sum(q for day, q in out.items() if as_day(day, date.max) < start)
    days = []
    cur = start
    guard = 0
    while cur <= end and guard < MAX_DAYS:
        qty = max(0.0, qty)
        days.append({"day": cur.isoformat(), "qty": round(qty, 3), "sum": round(qty * liters * tariff, 2)})
        qty -= out.get(cur.isoformat(), 0)
        cur += timedelta(days=1)
        guard += 1
    # партия уехала посреди периода: пустой хвост в счёт не пишем
    while days and days[-1]["qty"] <= 0:
        days.pop()
    liter_days = round(sum(d["qty"] for d in days) * liters, 3)
    return {
        "from": start.isoformat(),
        "to": days[-1]["day"] if days else start.isoformat(),
        "days": len(days),
        "liter_days": liter_days,
        "storage": round(liter_days * tariff, 2),
        "by_day": days,
    }


def pick_of(lot, client, date_from=None, date_to=None, ship_days=None):
    """Сборка за период: сколько штук уехало × ставка сборки этой позиции."""
    start, end = window(lot, date_from, date_to)
    if start is None:
        return {"qty": 0.0, "rate": pick_rate_of(lot, client), "sum": 0.0}
    out = ship_days if ship_days is not None else moves_by_day(lot["id"]).get(lot["id"], {})
    qty = sum(q for day, q in out.items() if start <= as_day(day, date.max) <= end)
    rate = pick_rate_of(lot, client)
    return {"qty": round(qty, 3), "rate": rate, "sum": round(qty * rate, 2)}


def money(lot, client, date_from=None, date_to=None, ship_days=None):
    out = ship_days if ship_days is not None else moves_by_day(lot["id"]).get(lot["id"], {})
    calc = storage_of(lot, client, date_from, date_to, out)
    pick = pick_of(lot, client, date_from, date_to, out)
    return {
        "days": days_on_stock(lot["received_at"]),
        "bill_days": calc["days"],
        "bill_from": calc["from"],
        "bill_to": calc["to"],
        "liter_days": calc["liter_days"],
        "shipped": round(sum(out.values()), 3),
        "storage": calc["storage"],
        "pick_qty": pick["qty"],
        "pick": pick["sum"],
        "intake": 0.0,
        "ship": pick["sum"],
        "total": round(calc["storage"] + pick["sum"], 2),
    }


def consume_fifo(client_id, ms_product_id, qty, ref, when=None):
    """Списываем по FIFO датой отгрузки с площадки, а не временем воркера."""
    left = float(qty or 0)
    if left <= 0:
        return 0
    stamp = when or datetime.now(MSK).isoformat()
    for lot in list_lots_fifo(client_id, ms_product_id):
        take = min(float(lot["qty_left"]), left)
        if take <= 0:
            continue
        add_lot_move(lot["id"], take, "ship", ref, stamp)
        left -= take
        if left <= 0:
            break
    return left


def row_of(lot, client=None, ship_days=None, date_from=None, date_to=None):
    if client is None:
        client = get_client_by_id(lot["client_id"])
    out = ship_days if ship_days is not None else moves_by_day(lot["id"]).get(lot["id"], {})
    calc = money(lot, client, date_from, date_to, out)
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
        "dims": (lot["dims"] if "dims" in lot.keys() else "") or "",
        "rate": storage_rate(),
        "pick_rate": pick_rate_of(lot, client),
        "liters": liters,
        "qty": qty_left,
        "qty_in": float(lot["qty_in"] or 0),
        "shipped": round(sum(out.values()), 3),
        "volume": round(liters * qty_left, 3),
        "received": parse_when(lot["received_at"]).strftime("%d.%m.%Y"),
        "accepted": (accepted_day(lot).strftime("%d.%m.%Y") if accepted_day(lot) else ""),
        "days": calc["days"],
        "bill_days": calc["bill_days"],
        "bill_from": calc["bill_from"],
        "bill_to": calc["bill_to"],
        "liter_days": calc["liter_days"],
        "storage": calc["storage"],
        "pick_qty": calc["pick_qty"],
        "pick": calc["pick"],
        "intake": 0.0,
        "ship": calc["ship"],
        "total": calc["total"],
    }


def is_marked(tracking):
    return tracking_code(tracking) != "NOT_TRACKED"


def lot_rows(client_id=None, query="", marked=None, only_open=True, date_from=None, date_to=None):
    text = (query or "").strip().lower()
    clients = {c["id"]: c for c in list_clients()}
    moves = moves_by_day()
    rows = []
    for lot in list_lots(only_open=only_open):
        if client_id and lot["client_id"] != int(client_id):
            continue
        row = row_of(
            lot,
            client=clients.get(lot["client_id"]),
            ship_days=moves.get(lot["id"], {}),
            date_from=date_from,
            date_to=date_to,
        )
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
        "liter_days": round(sum(r["liter_days"] for r in rows), 2),
        "money": round(sum(r["total"] for r in rows), 2),
        "aged": sum(1 for r in rows if r["days"] > 90),
    }


def calendar(client_id=None, query="", date_from=None, date_to=None):
    """Сетка как в таблице Глеба: строка — позиция, колонка — сутки."""
    end = date_to or today()
    start = date_from or (end - timedelta(days=13))
    if start > end:
        start = end
    days = []
    cur = start
    while cur <= end and len(days) < MAX_DAYS:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    clients = {c["id"]: c for c in list_clients()}
    moves = moves_by_day()
    rows = []
    for lot in list_lots(only_open=False):
        if client_id and lot["client_id"] != int(client_id):
            continue
        client = clients.get(lot["client_id"])
        out = moves.get(lot["id"], {})
        got = accepted_day(lot)
        if got is None or got > end:
            continue
        liters = float(lot["liters"] or 0)
        tariff = storage_rate()
        qty = float(lot["qty_in"] or 0) - sum(
            q for day, q in out.items() if as_day(day, date.max) < max(start, got)
        )
        cells = []
        for day in days:
            here = as_day(day)
            if here < got:
                cells.append({"qty": None, "sum": 0, "ship": 0})
                continue
            cells.append({
                "qty": round(qty, 3),
                "sum": round(qty * liters * tariff, 2),
                "ship": round(out.get(day, 0), 3),
            })
            qty -= out.get(day, 0)
        text = (query or "").strip().lower()
        hay = " ".join(str(lot[k] or "").lower() for k in ("article", "barcode", "gtin", "name"))
        if text and text not in hay and text not in (client["name"].lower() if client else ""):
            continue
        rows.append({
            "id": lot["id"],
            "client": client["name"] if client else "",
            "article": lot["article"] or "",
            "barcode": lot["barcode"] or "",
            "name": lot["name"] or "",
            "liters": liters,
            "rate": tariff,
            "received": got.isoformat(),
            "cells": cells,
            "sum": round(sum(c["sum"] for c in cells), 2),
            "shipped": round(sum(c["ship"] for c in cells), 3),
        })
    per_day = [
        {
            "day": day,
            "sum": round(sum(r["cells"][i]["sum"] for r in rows), 2),
            "ship": round(sum(r["cells"][i]["ship"] for r in rows), 3),
            "qty": round(sum(r["cells"][i]["qty"] or 0 for r in rows), 3),
        }
        for i, day in enumerate(days)
    ]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days,
        "rows": rows,
        "per_day": per_day,
        "total": round(sum(r["sum"] for r in rows), 2),
    }
