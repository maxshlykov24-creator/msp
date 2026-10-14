"""Партии FIFO, дни на складе и деньги к выставлению.

Две метрики, а не одна колонка «тариф». Хранение считаем как в таблице Глеба:
у каждых суток свой остаток, формула дня — остаток на утро × литраж × ставка.
Ставка хранения одна на всех, 0,15 ₽ за литр в сутки: она не зависит ни от
товара, ни от клиента. Разная только сборка — напильник и игрушка стоят
по-разному, поэтому ставка сборки живёт у позиции, в ₽ за штуку.

Хранение стартует на следующий день после фактической приёмки и включает день отгрузки:
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
from ms import tracking_code, TRACKING

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


def calculation_end(raw=""):
    if raw and as_day(raw) is None:
        raise ValueError("неверная дата расчёта")
    end = as_day(raw) or today()
    if end > today():
        raise ValueError("нельзя выставлять хранение за будущие дни")
    return end


def storage_rate():
    """Подтверждённая ставка хранения: 0,15 рубля за литр в сутки для всех."""
    return STORAGE_RATE


def pick_rate_of(lot, client):
    """Сборка из допполя товара; тариф клиента сохранён только для старых архивных партий."""
    if "pick_rate" in lot.keys() and lot["pick_rate"] is not None:
        return float(lot["pick_rate"])
    if not ("source_key" in lot.keys() and lot["source_key"]) and client and "tariff_pick" in client.keys() and client["tariff_pick"] is not None:
        return float(client["tariff_pick"] or 0)
    return 0.0


def pick_rate_known(lot, client):
    return ("pick_rate" in lot.keys() and lot["pick_rate"] is not None) or bool(
        not ("source_key" in lot.keys() and lot["source_key"]) and client and "tariff_pick" in client.keys() and client["tariff_pick"] is not None
    )


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


def window(lot, date_from=None, date_to=None, storage=True):
    """Что ещё не выставлено: со дня после прошлого счёта по date_to включительно."""
    start = accepted_day(lot)
    if start is None:
        return None, None
    if storage:
        start += timedelta(days=1)
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
    if start is None or lot["ledger_kind"] == "assembly":
        return {"from": "", "to": "", "days": 0, "liter_days": 0.0, "storage": 0.0, "by_day": []}
    out = ship_days if ship_days is not None else moves_by_day(lot["id"]).get(lot["id"], {})
    qty = float(lot["qty_in"] or 0) - sum(q for day, q in out.items() if as_day(day, date.max) < start)
    if (end - start).days >= MAX_DAYS:
        raise ValueError("слишком длинный период расчёта: максимум %s дней" % MAX_DAYS)
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
    start, end = window(lot, date_from, date_to, storage=False)
    if start is None or lot["ledger_kind"] == "receipt":
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
        "days": (0 if lot["ledger_kind"] == "assembly" or not accepted_day(lot) else max(0, ((min(today(), as_day(max(out))) if float(lot["qty_left"] or 0) <= 0 and out else today()) - accepted_day(lot)).days)),
        "bill_days": calc["days"],
        "bill_from": (max(accepted_day(lot), (billed_until(lot) + timedelta(days=1)) if billed_until(lot) else accepted_day(lot)).isoformat() if lot["ledger_kind"] == "assembly" else calc["from"]),
        "bill_to": ((date_to or today()).isoformat() if lot["ledger_kind"] == "assembly" else calc["to"]),
        "liter_days": calc["liter_days"],
        "shipped": (0.0 if lot["ledger_kind"] == "assembly" else round(sum(out.values()), 3)),
        "storage": calc["storage"],
        "pick_qty": pick["qty"],
        "pick": pick["sum"],
        "intake": 0.0,
        "ship": pick["sum"],
        "total": round(calc["storage"] + pick["sum"], 2),
    }


def consume_fifo(client_id, ms_product_id, qty, ref, when=None):
    """Списываем по FIFO датой отгрузки с площадки, а не временем воркера."""
    from db import get_setting
    if get_setting("ledger:enabled") == "1":
        raise ValueError("Остаток учёта изменяют только документы МойСклад")
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
    errors = []
    if calc["bill_days"] and liters <= 0:
        errors.append("не заполнен литраж")
    if calc["pick_qty"] and not pick_rate_known(lot, client):
        errors.append("не заполнена ставка сборки")
    return {
        "id": lot["id"],
        "ms_product_id": lot["ms_product_id"],
        "kind": lot["ledger_kind"],
        "source_document": lot["source_document"] or "",
        "client_id": lot["client_id"],
        "client": client["name"] if client else "",
        "article": lot["article"] or "",
        "barcode": lot["barcode"] or "",
        "gtin": lot["gtin"] or "",
        "name": lot["name"] or "",
        "tracking": next((label for label, code in TRACKING.items() if code == lot["tracking_type"]), lot["tracking_type"] or ""),
        "dims": (lot["dims"] if "dims" in lot.keys() else "") or "",
        "rate": storage_rate(),
        "pick_rate": pick_rate_of(lot, client),
        "pick_rate_known": pick_rate_known(lot, client),
        "billing_errors": errors,
        "liters": liters,
        "qty": qty_left,
        "qty_in": float(lot["qty_in"] or 0),
        "shipped": (0.0 if lot["ledger_kind"] == "assembly" else round(sum(out.values()), 3)),
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
    # МойСклад возвращает enum (LP_CLOTHES и др.), старые партии хранили русскую подпись.
    if tracking and tracking == tracking.upper():
        return tracking != "NOT_TRACKED"
    return tracking_code(tracking) != "NOT_TRACKED"


def _lot_rows(client_id=None, query="", marked=None, only_open=True, date_from=None, date_to=None):
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
        "incomplete": sum(bool(r.get("billing_errors")) for r in rows),
        "positions": len(rows),
        "qty": round(sum(r["qty"] for r in rows), 3),
        "volume": (None if any(r["qty"] > 0 and not r.get("volume_known", bool(r["liters"])) for r in rows) else round(sum(r["volume"] for r in rows), 2)),
        "liter_days": round(sum(r["liter_days"] for r in rows), 2),
        "money": round(sum(r["total"] for r in rows), 2),
        "aged": sum(1 for r in rows if r["days"] > 90),
    }


def group_rows(rows):
    """Одна строка артикула у клиента; исходные партии остаются для FIFO и закрытия счёта."""
    from collections import OrderedDict
    grouped = OrderedDict()
    for raw in [leaf for r in rows for leaf in (dict(r).get("details") or [r])]:
        row = dict(raw)
        article = str(row.get("article") or "").strip()
        key = (row.get("client_id"), article or ("product:" + str(row.get("ms_product_id") or row.get("id"))))
        grouped.setdefault(key, []).append(row)
    result = []
    money_fields = ("storage", "intake", "pick", "ship", "total")
    quantity_fields = ("qty", "qty_in", "shipped", "pick_qty", "volume", "liter_days")
    for members in grouped.values():
        row = dict(members[0])
        row["details"] = members
        row["lot_ids"] = list(dict.fromkeys(x["id"] for x in members if x.get("id") is not None))
        if row["lot_ids"]:
            row["id"] = min(row["lot_ids"])
        row["product_ids"] = list(dict.fromkeys(x.get("ms_product_id") for x in members if x.get("ms_product_id")))
        row["edit_lot_id"] = row.get("id") if len(row["product_ids"]) <= 1 else None
        for field in money_fields + quantity_fields:
            # A missing historical assembly quantity stays unknown, rather than becoming zero.
            if field == "pick_qty" and any(x.get("pick") and x.get(field) is None for x in members):
                row[field] = None
            else:
                row[field] = round(sum(float(x.get(field) or 0) for x in members), 2 if field in money_fields else 3)
        row["billing_errors"] = list(dict.fromkeys(e for x in members for e in x.get("billing_errors", [])))
        row["volume_known"] = all(x.get("liters", 0) and x["liters"] > 0 for x in members if x.get("qty", 0) > 0)
        for field, mixed in (("liters", "mixed_liters"), ("pick_rate", "mixed_pick_rates")):
            values = {x.get(field) for x in members}
            row[mixed] = len(values) > 1
            row[field] = next(iter(values)) if len(values) == 1 else None
        row["pick_rate_known"] = all(x.get("pick_rate_known", x.get("pick_rate") is not None) for x in members)
        rates = OrderedDict()
        for x in members:
            if not x.get("pick"):
                continue
            rate = x.get("pick_rate")
            values = rates.setdefault(rate, {"rate": rate, "qty": 0.0, "sum": 0.0})
            values["qty"] += float(x.get("pick_qty") or 0)
            values["sum"] = round(values["sum"] + float(x["pick"]), 2)
        row["pick_rates"] = list(rates.values())
        # The assembly unit price belongs to charged movements, not to unrelated receipt rows.
        if len(rates) == 1:
            row["pick_rate"] = next(iter(rates))
            row["mixed_pick_rates"] = False
        row["barcodes"] = list(dict.fromkeys(x.get("barcode") for x in members if x.get("barcode")))
        row["gtins"] = list(dict.fromkeys(x.get("gtin") for x in members if x.get("gtin")))
        row["barcode"] = ", ".join(row["barcodes"])
        row["gtin"] = ", ".join(row["gtins"])
        row["names"] = list(dict.fromkeys(x.get("name") for x in members if x.get("name")))
        row["name"] = "; ".join(row["names"])
        row["documents"] = list(dict.fromkeys(x.get("source_document") for x in members if x.get("source_document")))
        row["source_document"] = "\n".join(row["documents"])
        for field in ("accepted", "received"):
            dates = sorted({x[field] for x in members if x.get(field)})
            row[field] = (dates[0] if len(dates) == 1 else " — ".join((dates[0], dates[-1]))) if dates else ""
        receipt_days = [int(x.get("days") or 0) for x in members if x.get("kind") != "assembly"]
        row["days_min"] = min(receipt_days, default=0)
        row["days"] = max(receipt_days, default=0)
        charged_days = set()
        for x in members:
            if x.get("kind") == "assembly" or not (x.get("bill_days") or x.get("storage")):
                continue
            start = as_day(x.get("bill_from") or x.get("period_from"))
            end = as_day(x.get("bill_to") or x.get("period_to"))
            if start and end:
                charged_days.update((start + timedelta(days=n)).isoformat() for n in range(max(0, (end-start).days+1)))
        row["bill_days"] = len(charged_days)
        for start_key, end_key in (("bill_from", "bill_to"), ("period_from", "period_to")):
            starts = [x[start_key] for x in members if x.get(start_key)]
            stops = [x[end_key] for x in members if x.get(end_key)]
            row[start_key] = min(starts, default="")
            row[end_key] = max(stops, default="")
        row["snapshot"] = all(x.get("snapshot", True) for x in members)
        result.append(row)
    return result


def _calendar(client_id=None, query="", date_from=None, date_to=None):
    """Сетка как в таблице Глеба: строка — позиция, колонка — сутки."""
    end = date_to or today()
    start = date_from or (end - timedelta(days=13))
    if start > end:
        raise ValueError("конец периода раньше начала")
    if (end - start).days >= MAX_DAYS:
        raise ValueError("слишком длинный период календаря")
    days = []
    cur = start
    while cur <= end and len(days) < MAX_DAYS:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    clients = {c["id"]: c for c in list_clients()}
    moves = moves_by_day()
    rows = []
    for lot in list_lots(only_open=False):
        if lot["ledger_kind"] == "assembly":
            continue
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
                "sum": (0 if here == got else (round(qty * liters * tariff, 2) if liters > 0 or qty == 0 else None)),
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
            "sum": (None if any(c["sum"] is None for c in cells) else round(round(sum(c["qty"] or 0 for i,c in enumerate(cells) if as_day(days[i]) > got) * liters, 3) * tariff, 2)),
            "shipped": round(sum(c["ship"] for c in cells), 3),
        })
    per_day = [
        {
            "day": day,
            "sum": (None if any(r["cells"][i]["sum"] is None for r in rows) else round(sum(r["cells"][i]["sum"] for r in rows), 2)),
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
        "total": (None if any(r["sum"] is None for r in rows) else round(sum(r["sum"] for r in rows), 2)),
    }


def lot_rows(*args, **kwargs):
    from db import run_lock
    with run_lock('billing'):
        return _lot_rows(*args, **kwargs)


def calendar(*args, **kwargs):
    from db import run_lock
    with run_lock('billing'):
        return _calendar(*args, **kwargs)


def require_current_ledger():
    from db import get_setting
    if get_setting('ledger:enabled') != '1':
        return
    if get_setting('ledger:last_error'):
        raise ValueError('Последняя синхронизация учёта не завершена. Обнови учёт перед счётом или клиентским отчётом.')
    raw = get_setting('ledger:last_success')
    if not raw or (datetime.now(MSK) - datetime.fromisoformat(raw)).total_seconds() > 1800:
        raise ValueError('Данные учёта требуют обновления. Нажми «Обновить учёт».')


PRODUCT_FIELDS = {
    'liters': ('Литраж, л', 'ATTR_LITERS_ID'),
    'pick_rate': ('Стоимость сборки, ₽', 'ATTR_PICK_RATE_ID'),
}


def _product_for_lot(lot_id):
    from db import connect
    conn = connect()
    try:
        lot = conn.execute('SELECT ms_product_id FROM lots WHERE id=? AND source_active=1', (lot_id,)).fetchone()
    finally:
        conn.close()
    if not lot or not lot['ms_product_id']:
        raise ValueError('Позиция больше не участвует в учёте. Обнови таблицу.')
    # UUID берём из сверенной партии, а не из запроса браузера.
    from uuid import UUID
    try:
        return str(UUID(lot['ms_product_id']))
    except ValueError:
        raise ValueError('У позиции нет корректной ссылки на карточку МойСклад.')


def _product_card(pid):
    from accept import canonical, ms_get, number, ref_id
    from db import connect, get_setting
    import hashlib
    p = ms_get('/entity/product/' + pid)
    attrs = {ref_id(a): a for a in p.get('attributes', [])}
    fields = {}
    for key, (name, setting) in PRODUCT_FIELDS.items():
        aid = get_setting(setting)
        if not aid:
            raise ValueError('Не настроено допполе МойСклад «%s».' % name)
        attr = attrs.get(aid)
        if attr and attr.get('name') != name:
            raise ValueError('Изменено допполе МойСклад «%s». Нужна проверка настройки.' % name)
        fields[key] = number(attr.get('value') if attr else None, name, True)
    conn = connect()
    try:
        rows = conn.execute('SELECT DISTINCT c.name FROM lots l JOIN clients c ON c.id=l.client_id WHERE l.ms_product_id=? AND l.source_active=1 ORDER BY c.name', (pid,)).fetchall()
    finally:
        conn.close()
    version = dict(fields, updated=p.get('updated'), product_id=pid,
                   attributes={key: get_setting(setting) for key, (_, setting) in PRODUCT_FIELDS.items()})
    return dict(fields, product_id=pid, name=p.get('name', ''), article=p.get('article', ''),
                clients=[r['name'] for r in rows], archived=bool(p.get('archived')),
                revision=hashlib.sha256(canonical(version).encode()).hexdigest())


def lot_product(lot_id):
    return _product_card(_product_for_lot(lot_id))


def save_lot_product(lot_id, data, actor):
    """Обновить только введённые допполя; пересчитать весь источник под замками."""
    from accept import apply_plan, build_plan, canonical, fetch_snapshot, ms_get, number, stamp
    from db import get_setting, run_lock, set_setting
    from ms import attr_value
    from net import MS_BASE, ms_headers, req
    from intake import parse_liters
    import json
    import math

    # Пустое поле сохраняет прежнее значение. Ноль сборки — явная бесплатная сборка.
    desired = {}
    for key, (name, _) in PRODUCT_FIELDS.items():
        raw = data.get(key)
        if raw is None or str(raw).strip() == '':
            continue
        if isinstance(raw, bool):
            raise ValueError('Некорректное поле ' + name)
        if key == 'liters':
            value, _ = parse_liters(raw)
            if value is None or not math.isfinite(value) or value <= 0:
                raise ValueError('Литраж должен быть больше нуля. Можно ввести габариты в сантиметрах: 8×8×120.')
        else:
            value = number(raw, name)
        desired[key] = value
    if not desired:
        raise ValueError('Введи литраж или стоимость сборки.')
    with run_lock('ledger', blocking=False), run_lock('billing', blocking=False):
        if json.loads(get_setting('billing:pending') or 'null'):
            raise ValueError('Есть неподтверждённый счёт. Сначала заверши его, затем изменяй товар.')
        pid = _product_for_lot(lot_id)
        current = _product_card(pid)
        if data.get('revision') != current['revision']:
            raise ValueError('Карточка изменилась после открытия. Закрой окно и открой товар заново.')
        changed = {key: value for key, value in desired.items() if current[key] != value}
        if changed:
            metadata = ms_get('/entity/product/metadata/attributes')
            known = {a['id']: a for a in metadata.get('rows', [])}
            attrs = []
            for key, value in changed.items():
                name, setting = PRODUCT_FIELDS[key]
                a = known.get(get_setting(setting))
                if not a or a.get('name') != name or a.get('type') != 'double':
                    raise ValueError('Не найдено числовое допполе МойСклад «%s».' % name)
                attrs.append(attr_value('product', name, setting, value))
            # Если процесс прервётся после записи, счета останутся заблокированы до сверки.
            set_setting('ledger:last_error', 'Обновляется карточка товара. Требуется завершить сверку учёта.')
            try:
                response = req('PUT', MS_BASE + '/entity/product/' + pid, headers=ms_headers(),
                               json={'attributes': attrs}, retry_safe=False)
                if response.status_code != 200:
                    raise ValueError('МойСклад не подтвердил сохранение: HTTP %s.' % response.status_code)
            except Exception:
                # Ответ мог потеряться после успешной записи: проверяем саму карточку.
                verified = _product_card(pid)
                if any(verified[key] != value for key, value in changed.items()):
                    raise ValueError('Сохранение не подтверждено. Открой товар заново и проверь значения перед повтором.')
            current = _product_card(pid)
            if any(current[key] != value for key, value in changed.items()):
                raise ValueError('МойСклад не сохранил введённые значения. Открой товар заново и проверь карточку.')
            print('Учёт: допполя товара %s, пользователь %s, %s' % (pid, actor, canonical(changed)))
        try:
            groups = json.loads(get_setting('ledger:groups') or '[]')
            if not groups:
                raise ValueError('Не настроены подтверждённые группы контрагентов для учёта.')
            set_setting('ledger:last_attempt', stamp())
            snapshot = fetch_snapshot()
            if any(snapshot['products'][pid][key] != value for key, value in desired.items()):
                raise ValueError('Карточка изменилась во время сверки. Проверь её значения.')
            result = apply_plan(snapshot, build_plan(snapshot, groups), groups)
        except Exception as exc:
            set_setting('ledger:last_error', str(exc)[:500])
            return {'ok': True, 'saved': True, 'synced': False, 'product': current,
                    'msg': 'Данные карточки сохранены в МойСклад. Учёт требует обновления: ' + str(exc)}
        return {'ok': True, 'saved': True, 'synced': True, 'product': current, 'last_sync': result['last_sync']}
