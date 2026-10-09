"""Счёт покупателю: хранение по общей ставке плюс сборка по ставке позиции.

Период считает система: от дня после прошлого счёта (или от приёмки) по date_to.
Начало руками не задаём — иначе неоплаченные сутки молча выпадут из счёта.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from account import accepted_day, as_day, calculation_end, money, row_of, today
from db import (
    get_setting,
    set_setting,
    save_invoice_snapshot,
    list_invoices,
    get_invoice,
    list_invoice_positions,
    get_client_by_id,
    get_lot,
    moves_by_day,
    run_lock,
)
from ms import SERVICES, ms_meta, org_id, service_id
from net import MS_BASE, ms_headers, req

MSK = timezone(timedelta(hours=3))


def collect(lot_ids, date_to=""):
    """Партии одного клиента и суммы, которые ещё не выставлялись."""
    end = calculation_end(date_to)
    lots = []
    client = None
    waiting = 0
    for raw in dict.fromkeys(int(value) for value in lot_ids):
        lot = get_lot(int(raw))
        if not lot:
            raise ValueError("выбранная партия не найдена: %s" % raw)
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
    if waiting:
        raise ValueError("позиции ещё не приняты на склад: их %s. Убери их из выбора." % waiting)
    if not lots:
        raise ValueError("не выбрано ни одной позиции")
    moves = moves_by_day()
    detail = []
    storage = 0.0
    pick = 0.0
    for lot in lots:
        calc = row_of(lot, client, moves.get(lot["id"], {}), date_to=end)
        if calc["bill_days"] == 0 and calc["pick_qty"] == 0:
            continue
        if calc["liters"] <= 0 or calc["pick_rate"] < 0:
            raise ValueError("проверь литраж и ставку сборки партии %s" % lot["id"])
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
    with run_lock(name="billing"):
        return _create(lot_ids, author, date_to)


def _create(lot_ids, author="", date_to=""):
    # A lost HTTP response must recover the same document and the same calculation.
    pending = json.loads(get_setting("billing:pending") or "null")
    request_key = {"lots": sorted(set(int(x) for x in lot_ids)), "to": calculation_end(date_to).isoformat()}
    if pending:
        if pending["request"] != request_key:
            raise ValueError("предыдущий счёт ещё не подтверждён. Повтори создание для тех же партий и даты: %s" % pending["request"]["to"])
        return _send_pending(pending)
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
        "syncId": str(uuid.uuid4()),
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
    pending = {"request": request_key, "client_id": client["id"], "client": client["name"],
               "payload": payload, "parts": parts, "detail": [(dict(l), c) for l, c in detail],
               "start": start, "stop": stop, "author": author, "now": datetime.now(MSK).isoformat()}
    set_setting("billing:pending", json.dumps(pending, ensure_ascii=False))
    return _send_pending(pending)


def _send_pending(pending):
    r = req("POST", MS_BASE + "/entity/invoiceout", headers=ms_headers(), json=pending["payload"], retry_safe=False)
    if r.status_code not in (200, 201):
        if 400 <= r.status_code < 500 and r.status_code not in (408, 409, 429):
            set_setting("billing:pending", "null")
        raise RuntimeError("МойСклад не подтвердил счёт (HTTP %s). Повтори с теми же партиями и датой." % r.status_code)
    data = r.json()
    if not data.get("id"):
        raise RuntimeError("МойСклад не вернул номер документа. Повтори с теми же партиями и датой.")
    if "sum" in data and round(float(data["sum"]) / 100, 2) != pending["parts"]["total"]:
        raise RuntimeError("сумма МойСклад отличается от расчёта. Нужна сверка счёта, период не закрыт.")
    iid = save_invoice_snapshot(pending["client_id"], data, pending["parts"], pending["detail"], pending["start"], pending["stop"], pending["author"], pending["now"])
    set_setting("billing:pending", "null")
    return {"id": iid, "ms_id": data["id"], "number": data.get("name") or "", "client": pending["client"],
            "total": pending["parts"]["total"], "period_from": pending["start"], "period_to": pending["stop"], "positions": len(pending["detail"])}


def payment_state(inv):
    data = json.loads(get_setting("invoice:ms:%s" % inv["id"]) or "{}")
    paid = data.get("paid")
    total = data.get("total")
    label = "Не проверено"
    if data.get("deleted"):
        label = "Удалён в МойСклад"
    elif total is not None and paid is not None:
        label = "Оплачен" if paid >= total else ("Частично оплачен" if paid > 0 else "Не оплачен")
        if not data.get("applicable"):
            label = "Не проведён"
    return {**{k:v for k,v in data.items() if k != "total"}, "ms_total": total, "payment_status": label, "due": max(0, round(total-paid, 2)) if total is not None and paid is not None and not data.get("deleted") else None}


def sync_invoices(client_id=None):
    checked = 0
    errors = 0
    with run_lock("invoice-sync", blocking=False):
        for inv in list_invoices(client_id=client_id):
            key = "invoice:ms:%s" % inv["id"]
            old = json.loads(get_setting(key) or "{}")
            try:
                r = req("GET", MS_BASE + "/entity/invoiceout/" + inv["ms_invoice_id"], headers=ms_headers())
                if r.status_code != 200:
                    raise ValueError("МойСклад: HTTP %s" % r.status_code)
                d = r.json()
                if "sum" not in d or "payedSum" not in d:
                    raise ValueError("МойСклад не вернул суммы счёта и оплаты")
                old = {"total": round(float(d["sum"])/100,2), "paid": round(float(d["payedSum"])/100,2),
                       "applicable": bool(d.get("applicable")), "deleted": bool(d.get("deleted")),
                       "checked_at": datetime.now(MSK).isoformat(timespec="seconds"), "error": ""}
                if old["total"] != round(inv["total"], 2):
                    old["error"] = "Сумма в МойСклад изменена; детализация сохранена на момент создания"
                checked += 1
            except Exception as exc:
                old["error"] = str(exc) if isinstance(exc, ValueError) else "Не удалось получить счёт из МойСклад"
                errors += 1
            set_setting(key, json.dumps(old, ensure_ascii=False))
    return {"checked": checked, "errors": errors}


def invoice_data(invoice_id):
    inv = get_invoice(invoice_id)
    if not inv:
        raise ValueError("счёт не найден")
    positions = []
    for r in list_invoice_positions(invoice_id):
        snap = json.loads(r["snapshot_json"] or "{}")
        row = {k: snap.get(k, r[k]) for k in ("article", "barcode", "gtin", "name", "liters", "qty_in")}
        row.update({"period_from": r["period_from"] or "", "period_to": r["period_to"] or "", "days": r["days"],
                    "liter_days": r["liter_days"], "storage": r["storage"], "intake": r["intake"], "pick": r["ship"],
                    "pick_qty": snap.get("pick_qty"), "total": round(r["storage"]+r["intake"]+r["ship"],2),
                    "snapshot": bool(snap)})
        positions.append(row)
    return {"invoice": {"id": inv["id"], "client": inv["client_name"], "number": inv["ms_number"], "ms_id": inv["ms_invoice_id"],
                        "period_from": inv["period_from"] or "", "period_to": inv["period_to"] or "",
                        "storage": inv["storage"], "intake": inv["intake"], "pick": inv["ship"], "total": inv["total"],
                        "positions": inv["lots_count"], "liter_days": round(sum(r["liter_days"] for r in positions),3),
                        "created": (inv["created_at"] or "")[:16].replace("T"," "), "author": inv["author"], **payment_state(inv)},
            "positions": positions}
