#!/usr/bin/env python3
"""Полная пересборка локального среза 2MY. Только GET к внешним системам."""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lib
import shift_roster

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("DASHBOARD_SNAPSHOT", ROOT / "дашборд" / "snapshot.local.json"))
GAPS = Path(os.environ.get("DASHBOARD_GAPS", ROOT / "дашборд" / "ПРОБЕЛЫ_ДАННЫХ.local.md"))
TZ = ZoneInfo("Europe/Moscow")
FIELD = {
    "paid": "ed14770a-dc0d-11ef-0a80-10cd00226b09",
    "confirm": "ed14761e-dc0d-11ef-0a80-10cd00226b08",
    "sent": "ed14796a-dc0d-11ef-0a80-10cd00226b0c",
    "stock": "ed147544-dc0d-11ef-0a80-0d360015ef35",
    "delivery": "ed14744d-dc0d-11ef-0a80-10cd00226b06",
    "promo": "f11cca54-ed68-11ef-0a80-084e000137c7",
    "utm": "6d95a799-f849-11f0-0a80-009c000b6c8a",
    "utm_medium": "6d95a996-f849-11f0-0a80-009c000b6c8b",
    "utm_campaign": "6d95aa5b-f849-11f0-0a80-009c000b6c8c",
}


def required_rows(ms: lib.MS, path: str, params: dict | None = None,
                  keep: tuple[str, ...] | None = None) -> list[dict]:
    params = dict(params or {})
    params["limit"] = 1000
    out = []
    offset = 0
    while True:
        params["offset"] = offset
        for attempt in range(8):
            status, body = ms.req(path, params)
            if status not in (429, 500, 502, 503, 504):
                break
            time.sleep(min(30, 2 * (attempt + 1)))
        if status != 200:
            raise RuntimeError(f"GET {path}: HTTP {status}")
        chunk = body.get("rows", [])
        if keep is None:
            out.extend(chunk)
        else:
            out.extend({key: row[key] for key in keep if key in row} for row in chunk)
        offset += len(chunk)
        if not chunk or offset >= body.get("meta", {}).get("size", offset):
            return out


def required_get(ms: lib.MS, path: str) -> dict:
    status, body = ms.req(path)
    if status != 200:
        raise RuntimeError(f"GET {path.split('/entity/')[-1].split('/')[0]}: HTTP {status}")
    return body


def attrs(row: dict) -> dict:
    return {a.get("id"): a.get("value") for a in row.get("attributes") or []}


def mid(ref: dict | None) -> str:
    return lib.href_id((ref or {}).get("meta"))


def rub(value) -> float:
    return round(float(value or 0) / 100, 2)


def dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # МойСклад отдаёт локальное время кабинета; кабинет 2MY в Москве.
        return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=TZ)
    except ValueError:
        return None


def phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return "+" + digits if 10 <= len(digits) <= 15 else ""


def city(address: str, delivery: str) -> str:
    text = (address + " " + delivery).lower().replace("ё", "е")
    if "самовывоз" in text:
        return "Москва, самовывоз"
    names = [("Санкт-Петербург", r"санкт.петербург|\bспб\b|\bпитер\b"),
             ("Москва", r"\bмоскв\w*|\bмск\b"),
             ("Казань", r"\bказан\w*"), ("Екатеринбург", r"екатеринбург"),
             ("Краснодар", r"краснодар"), ("Новосибирск", r"новосибирск"),
             ("Нижний Новгород", r"нижн\w* новгород"),
             ("Ростов-на-Дону", r"ростов.на.дону"),
             ("Сочи", r"\bсочи\b"), ("Самара", r"\bсамар\w*"),
             ("Уфа", r"\bуфа\b"), ("Пермь", r"\bперм\w*"),
             ("Тюмень", r"\bтюмен\w*"), ("Челябинск", r"челябинск"),
             ("Воронеж", r"\bворонеж\w*"), ("Омск", r"\bомск\b")]
    for label, pattern in names:
        if re.search(pattern, text):
            return label
    generic = re.search(r"(?:\bг\.?|\bгород)\s*([А-ЯЁ][а-яё]+(?:[ -][А-ЯЁ][а-яё]+)?)", address)
    if generic:
        return generic.group(1)
    return "не распознано"


def cf_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def get_amo() -> tuple[list[dict], dict]:
    amo = lib.Amo()
    def pages(path, key):
        result = []
        for page in range(1, 501):
            for attempt in range(6):
                status, body = amo.req("GET", f"{path}{'&' if '?' in path else '?'}limit=250&page={page}")
                if status not in (429, 500, 502, 503, 504):
                    break
                time.sleep(2 * (attempt + 1))
            if status == 204:
                return result
            if status != 200:
                raise RuntimeError(f"amo {key} page {page}: HTTP {status}")
            batch = (body.get("_embedded") or {}).get(key) or []
            result.extend(batch)
            if len(batch) < 250:
                if len({row["id"] for row in result}) != len(result):
                    raise RuntimeError(f"amo {key}: duplicate IDs during pagination")
                return result
        raise RuntimeError(f"amo {key}: pagination limit reached")

    leads = pages(f"/api/v4/leads?filter[pipeline_id]={lib.PIPELINE_SALES_NEW}", "leads")
    status, pipeline = amo.req("GET", f"/api/v4/leads/pipelines/{lib.PIPELINE_SALES_NEW}")
    if status != 200:
        raise RuntimeError(f"amo pipeline: HTTP {status}")
    stages = sorted(pipeline.get("_embedded", {}).get("statuses", []), key=lambda s: s["sort"])
    sales_leads = [lead for lead in leads if lead.get("pipeline_id") == lib.PIPELINE_SALES_NEW
                   and lead.get("responsible_user_id") != lib.USER_POLINA]
    tasks = []
    lead_ids = {lead["id"] for lead in sales_leads}
    tasks = [t for t in pages("/api/v4/tasks", "tasks")
             if t.get("entity_type") == "leads" and t.get("entity_id") in lead_ids]
    result = []
    for lead in sales_leads:
        result.append({
            "id": lead["id"], "created": datetime.fromtimestamp(lead["created_at"], TZ).date().isoformat(),
            "status": lead.get("status_id"), "manager": lead.get("responsible_user_id"),
            "won": lead.get("status_id") == 142,
        })
    return result, {"open": sum(t.get("is_completed") is False for t in tasks), "sampled": len(tasks),
                    "excluded_marketing_owner": len(leads) - len(sales_leads),
                    "pipeline_name": pipeline["name"], "pipeline_id": pipeline["id"],
                    "source_total": len(leads), "collected_at": datetime.now(TZ).isoformat(),
                    "stages": [{"id": s["id"], "name": s["name"]} for s in stages]}


def response_rules() -> dict | None:
    path = os.environ.get("DASHBOARD_RESPONSE_RULES")
    return json.loads(Path(path).read_text()) if path else None


def working_minutes(start: int, end: int, rules: dict | None) -> float | None:
    if not rules:
        return None
    first, last = datetime.fromtimestamp(start, TZ), datetime.fromtimestamp(end, TZ)
    if first.date().isoformat() < rules["valid_from"] or last.date().isoformat() > rules.get("valid_to", "9999-12-31"):
        return None
    total = 0.0
    day = first.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        periods = rules.get("dates", {}).get(day.date().isoformat(), rules["weekdays"].get(str(day.isoweekday()), []))
        for opening, closing in periods:
            oh, om = map(int, opening.split(":")); ch, cm = map(int, closing.split(":"))
            left = max(first, day.replace(hour=oh, minute=om))
            right = min(last, day.replace(hour=ch, minute=cm))
            total += max(0, (right - left).total_seconds() / 60)
        day += timedelta(days=1)
    return round(total, 2)


def get_responses(leads: list[dict]) -> dict:
    """Паузы входящее → первый исходящий в беседе, без текстов и контактов."""
    amo = lib.Amo()
    now = datetime.now(TZ)
    since = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
    lead_ids = {l["id"] for l in leads}
    events = {}
    for page in range(1, 2001):
        path = ("/api/v4/events?filter[type]=incoming_chat_message,outgoing_chat_message"
                f"&filter[created_at][from]={int(since.timestamp())}"
                f"&filter[created_at][to]={int(now.timestamp())}&limit=100&page={page}")
        for attempt in range(6):
            status, body = amo.req("GET", path)
            if status not in (429, 500, 502, 503, 504):
                break
            time.sleep(2 * (attempt + 1))
        if status == 204:
            break
        if status != 200:
            raise RuntimeError(f"amo response events page {page}: HTTP {status}")
        rows = body.get("_embedded", {}).get("events", [])
        for row in rows:
            if row.get("entity_type") == "lead" and row.get("entity_id") in lead_ids:
                events[row["id"]] = row
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("amo response events: pagination limit reached")
    names = {lib.USER_TANYA: "Таня", lib.USER_KRISTINA: "Кристина", lib.USER_OKSANA: "Оксана", lib.USER_MAXIM: "Максим"}
    pending, samples, unknown_rows = {}, [], []
    rules = response_rules()
    current_managers = {l["id"]: names.get(l.get("manager"), "Не определён") for l in leads}
    unknown = 0
    for row in sorted(events.values(), key=lambda r: (r["created_at"], r["type"] != "incoming_chat_message", r["id"])):
        values = row.get("value_after") or []
        msg = values[0].get("message", {}) if values else {}
        if not msg.get("talk_id"):
            continue
        key = (msg.get("origin"), msg["talk_id"])
        if row["type"] == "incoming_chat_message":
            pending.setdefault(key, (row["created_at"], row["entity_id"]))
        elif key in pending:
            start, entity_id = pending.pop(key)
            manager = names.get(row.get("created_by"))
            if manager:
                samples.append({"date": datetime.fromtimestamp(start, TZ).date().isoformat(),
                                "manager": manager, "minutes": round((row["created_at"] - start) / 60, 2),
                                "start_at": start, "end_at": row["created_at"],
                                "working_minutes": working_minutes(start, row["created_at"], rules)})
            else:
                unknown += 1
                unknown_rows.append({"date": datetime.fromtimestamp(start, TZ).date().isoformat()})
    pending_rows = [{"date": datetime.fromtimestamp(start, TZ).date().isoformat(), "start_at": start,
                     "minutes": round((now.timestamp() - start) / 60, 2),
                     "working_minutes": working_minutes(start, int(now.timestamp()), rules),
                     "manager": current_managers.get(entity_id, "Не определён")} for start, entity_id in pending.values()]
    return {"status": "ok", "source": "amoCRM events", "from": since.date().isoformat(),
            "to": now.isoformat(), "samples": samples, "messages": len(events),
            "unknown_author": unknown, "unanswered": len(pending), "unknown_rows": unknown_rows,
            "pending": pending_rows, "rules": rules, "rules_status": "configured" if rules else "not_configured"}


def positions_for(order_id: str) -> tuple[str, list[dict]]:
    client = lib.MS()
    path = f"/entity/customerorder/{order_id}/positions"
    for attempt in range(6):
        try:
            return order_id, required_rows(client, path,
                                           keep=("assortment", "quantity", "price", "discount"))
        except RuntimeError:
            if attempt == 5:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def positions_in_order(order_ids: list[str]):
    """Держим в памяти не больше трёх ответов и не бьём лимит API."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        source = iter(order_ids)
        pending = deque()
        for _ in range(min(3, len(order_ids))):
            order_id = next(source)
            pending.append((order_id, pool.submit(positions_for, order_id)))
        for expected_id in order_ids:
            order_id, future = pending.popleft()
            if order_id != expected_id:
                raise RuntimeError("Нарушен порядок загрузки позиций")
            loaded_id, rows = future.result()
            if loaded_id != expected_id:
                raise RuntimeError("Позиции не того заказа")
            next_id = next(source, None)
            if next_id:
                pending.append((next_id, pool.submit(positions_for, next_id)))
            yield rows


def atomic_json(path: Path, value: dict, mode=0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".next")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    tmp.chmod(mode)
    tmp.replace(path)


def enrich_accounting(snap: dict, ms=None) -> None:
    """История наблюдённых цен и отдельные отрицательные события возвратов."""
    ms = ms or lib.MS()
    observed = datetime.now(TZ).isoformat()
    history_path = Path(os.environ.get("DASHBOARD_COST_HISTORY", GAPS.parent / "cost-history.local.json"))
    history = json.loads(history_path.read_text()) if history_path.exists() else {"versions": {}, "locked": {}, "started": observed}
    products = {r["id"]: r for r in required_rows(ms, "/entity/product", keep=("id", "buyPrice"))}
    variants = {r["id"]: mid(r.get("product")) for r in required_rows(ms, "/entity/variant", keep=("id", "product"))}
    for pid, product in products.items():
        price = rub((product.get("buyPrice") or {}).get("value"))
        versions = history["versions"].setdefault(pid, [])
        if not versions or versions[-1]["unit"] != price:
            versions.append({"observed_from": observed, "unit": price})
    previous = json.loads(OUT.read_text()) if OUT.exists() else snap
    for order in previous.get("orders", []):
        for line in order.get("lines", []):
            if not line.get("delivery") and line.get("qty"):
                history["locked"].setdefault(order["id"] + ":" + line["id"],
                    {"unit": line["cost"] / line["qty"], "basis": "baseline_estimate", "fixed_at": observed})
    for order in snap["orders"]:
        for line in order.get("lines", []):
            if line.get("delivery"):
                continue
            key = order["id"] + ":" + line["id"]
            if key not in history["locked"]:
                pid = variants.get(line["id"], line["id"])
                paid = order.get("paid_at") or ((order.get("paid_date") or "") + "T00:00:00+03:00")
                vs = [v for v in history["versions"].get(pid, []) if v["observed_from"] <= paid]
                history["locked"][key] = {"unit": vs[-1]["unit"] if vs else line["cost"] / line["qty"] if line["qty"] else 0,
                    "basis": "observed_price" if vs else "baseline_estimate", "fixed_at": observed}
            fixed = history["locked"][key]
            line["cost"] = round(fixed["unit"] * line["qty"], 2)
            line["cost_basis"] = fixed["basis"]
    atomic_json(history_path, history)
    by_id = {o["id"]: o for o in snap["orders"]}
    demands = {r["id"]: mid(r.get("customerOrder")) for r in required_rows(ms, "/entity/demand", keep=("id", "customerOrder"))}
    returns, unresolved = [], []
    for ret in required_rows(ms, "/entity/salesreturn", keep=("id", "name", "moment", "sum", "applicable", "demand")):
        if ret.get("applicable") is not True:
            continue
        date = (dt(ret.get("moment")) or None)
        original = by_id.get(demands.get(mid(ret.get("demand"))))
        reason = None
        if not date: reason = "Нет даты возврата"
        elif not original: reason = "Нет связи с заказом"
        elif not original.get("buy") or not original.get("paid_date"): reason = "Не подтверждена признанная продажа"
        elif date.date().isoformat() < original["paid_date"]: reason = "Возврат раньше признания продажи"
        if reason:
            unresolved.append({"date": date.date().isoformat() if date else None, "sum": rub(ret.get("sum")), "reason": reason,
                               "impact": "outside_revenue" if original and not original.get("buy") else "unresolved"})
            continue
        originals = defaultdict(list)
        for line in original["lines"]:
            originals[line["id"]].append(line)
        lines = []
        for pos in required_rows(ms, f"/entity/salesreturn/{ret['id']}/positions"):
            aid = mid(pos.get("assortment"))
            candidates = originals.get(aid, [])
            if not candidates:
                reason = "Позиция возврата не найдена в заказе"
                break
            costs = {round(x["cost"] / x["qty"], 6) for x in candidates if x["qty"]}
            if len(costs) != 1:
                reason = "Неоднозначная закупочная стоимость возврата"
                break
            line = dict(candidates[0]);quantity = float(pos.get("quantity") or 0)
            gross = rub(pos.get("price")) * quantity
            line.update(qty=-quantity, rev=-round(gross * (1 - float(pos.get("discount") or 0) / 100), 2),
                        list=-gross, cost=-round(next(iter(costs)) * quantity, 2))
            lines.append(line)
        if reason or abs(sum(l["rev"] for l in lines) + rub(ret.get("sum"))) > 0.02:
            unresolved.append({"date": date.date().isoformat(), "sum": rub(ret.get("sum")), "reason": reason or "Сумма позиций не совпадает с документом"})
            continue
        event = {k: original.get(k) for k in ("channel", "source", "city", "phone", "client", "manager")}
        event.update(id=ret["id"], number=ret.get("name"), original_order=original["id"], return_event=True,
                     paid_date=date.date().isoformat(), sum=-rub(ret.get("sum")), lines=lines)
        returns.append(event)
    snap.update(returns=returns, return_gaps=unresolved,
                accounting={"cost_history_started": history["started"], "returns_checked_at": observed})


def main() -> None:
    ms = lib.MS()
    started = datetime.now(TZ)
    print("МойСклад: заказы, платежи, номенклатура, контрагенты", flush=True)
    orders = required_rows(ms, "/entity/customerorder", {"order": "moment,desc"},
                           keep=("id", "name", "moment", "sum", "payedSum", "attributes", "agent",
                                 "salesChannel", "shipmentAddress", "shipmentAddressFull"))
    order_count = len(orders)
    paymentins = required_rows(ms, "/entity/paymentin", keep=("moment", "created", "sum", "operations", "applicable"))
    payments = paymentins + required_rows(ms, "/entity/cashin", keep=("moment", "operations", "applicable"))
    payment_daily = defaultdict(float)
    for payment in paymentins:
        if payment.get("applicable") is not True:
            continue
        created = dt(payment.get("created"))
        if created:
            payment_daily[created.date().isoformat()] += rub(payment.get("sum"))
    invoices = {i["id"]: mid(i.get("customerOrder")) for i in required_rows(
        ms, "/entity/invoiceout", keep=("id", "customerOrder"))}
    order_payments = defaultdict(list)
    for payment in payments:
        if payment.get("applicable") is not True:
            continue
        for operation in payment.get("operations") or []:
            meta = operation.get("meta") or {}
            target_id = mid(operation)
            order_id = target_id if meta.get("type") == "customerorder" else invoices.get(target_id) if meta.get("type") == "invoiceout" else None
            if order_id:
                order_payments[order_id].append((payment.get("moment"), rub(operation.get("linkedSum"))))
    del payments, paymentins, invoices
    products = {p["id"]: p for p in required_rows(ms, "/entity/product",
                                                   keep=("id", "name", "pathName", "buyPrice"))}
    variants = {v["id"]: v for v in required_rows(ms, "/entity/variant",
                                                   keep=("id", "name", "product", "characteristics"))}
    services = {s["id"]: s for s in required_rows(ms, "/entity/service", keep=("id", "name"))}
    agents = {a["id"]: a for a in required_rows(ms, "/entity/counterparty",
                                                 keep=("id", "name", "phone"))}
    channels = {c["id"]: c["name"] for c in required_rows(ms, "/entity/saleschannel",
                                                       keep=("id", "name"))}
    returns = required_rows(ms, "/entity/salesreturn", keep=("id", "name", "demand", "description"))
    demands = {d["id"]: d for d in required_rows(ms, "/entity/demand",
                                                  keep=("id", "customerOrder"))}
    return_orders = defaultdict(list)
    for ret in returns:
        demand = demands.get(mid(ret.get("demand")))
        if demand:
            return_orders[mid(demand.get("customerOrder"))].append(ret)
    del demands, returns
    print(f"справочники готовы; заказов {len(orders)}", flush=True)
    paid_ids = [o["id"] for o in orders if float(o.get("sum") or 0) > 0
                and float(o.get("payedSum") or 0) >= float(o.get("sum") or 0)]
    print(f"позиции оплаченных заказов: {len(paid_ids)}", flush=True)
    position_rows = positions_in_order(paid_ids)
    paid_processed = 0
    gaps = defaultdict(list)
    result = []
    for index, o in enumerate(orders, 1):
        number = o.get("name", "")
        a = attrs(o)
        total = rub(o.get("sum"))
        paid = rub(o.get("payedSum"))
        is_buy = total > 0 and paid + 0.009 >= total
        if total > 0 and paid == 0:
            gaps["Оплачено пустое"].append(number)
        if bool(a.get(FIELD["paid"])) != is_buy:
            gaps["Галка Оплачен не совпадает с платежом"].append(number)
        accumulated = 0.0
        closed = None
        for moment, linked_sum in sorted(order_payments.get(o["id"], []), key=lambda row: row[0] or ""):
            accumulated += linked_sum
            if closed is None and accumulated + 0.009 >= total:
                closed = dt(moment)
        if is_buy and not closed:
            gaps["Оплачено закрыто, дата платежа не найдена"].append(number)
        agent = agents.get(mid(o.get("agent")), {})
        ph = phone(agent.get("phone"))
        if is_buy and not ph:
            gaps["Нет телефона у контрагента"].append(number)
        channel = channels.get(mid(o.get("salesChannel")), "не указано")
        address = str(o.get("shipmentAddress") or "")
        full = o.get("shipmentAddressFull") or {}
        structured_city = ""
        if isinstance(full, dict):
            structured_city = str(full.get("city") or "").strip()
            address += " " + " ".join(str(v) for k, v in full.items() if k != "meta" and isinstance(v, str))
        delivery = cf_name(a.get(FIELD["delivery"]))
        geo = city(address, delivery)
        if geo == "не распознано" and structured_city:
            geo = structured_city
        if geo == "не распознано":
            gaps["Город не распознан"].append(number)
        utm = str(a.get(FIELD["utm"]) or "").strip()
        if channel == "Сайт" and (not utm or utm.lower() == "utm"):
            gaps["Сайт без utm_source"].append(number)
        ret_list = return_orders.get(o["id"], [])
        if ret_list:
            gaps["Возврат без причины"].extend(
                str(r.get("name") or r.get("id")) for r in ret_list
                if not str(r.get("description") or "").strip()
            )
        lines = []
        if is_buy:
            pos = next(position_rows)
            paid_processed += 1
            if paid_processed % 250 == 0:
                print(f"позиций заказов: {paid_processed}/{len(paid_ids)}", flush=True)
            for x in pos:
                aid = mid(x.get("assortment"))
                kind = ((x.get("assortment") or {}).get("meta") or {}).get("type")
                variant = variants.get(aid, {}) if kind == "variant" else {}
                product = products.get(mid(variant.get("product")), {}) if variant else products.get(aid, {})
                service = services.get(aid, {}) if kind == "service" else {}
                item = product or service or variant
                name = item.get("name") or variant.get("name") or "не указано"
                if kind == "service" and name.strip().upper() == "ДОСТАВКА":
                    delivery_line = True
                else:
                    delivery_line = False
                chars = {c.get("name", "").lower(): c.get("value") for c in variant.get("characteristics") or []}
                size = str(chars.get("размер") or "").strip()
                color = str(chars.get("цвет") or "").strip()
                if not color:
                    match = re.search(r"(бордов\w*|черн\w*|чёрн\w*|бел\w*|молочн\w*|красн\w*|розов\w*|син\w*|голуб\w*|бежев\w*|зелен\w*|зелён\w*|сер\w*)", name, re.I)
                    color = match.group(1) if match else ""
                qty = float(x.get("quantity") or 0)
                gross = rub(x.get("price")) * qty
                rev = round(gross * (1 - float(x.get("discount") or 0) / 100), 2)
                cost = rub((product.get("buyPrice") or {}).get("value")) * qty
                if rev > 0 and cost == 0 and not delivery_line:
                    gaps["Позиция без закупочной цены"].append(number)
                lines.append({"id": aid, "name": name, "category": product.get("pathName") or "не указано",
                              "size": size, "color": color, "qty": qty, "rev": rev,
                              "list": gross, "cost": round(cost, 2), "delivery": delivery_line})
        result.append({
            "number": number, "id": o["id"], "created": (dt(o.get("moment")) or started).date().isoformat(),
            "paid_date": closed.date().isoformat() if closed else None,
            "paid_at": closed.isoformat() if closed else None,
            "sum": total, "paid": paid, "buy": bool(is_buy),
            "partial": bool(0 < paid < total), "channel": channel,
            "source": utm or "не указано", "utm_medium": a.get(FIELD["utm_medium"]) or "",
            "utm_campaign": a.get(FIELD["utm_campaign"]) or "",
            "city": geo, "address": address.strip(), "delivery": delivery, "manager": "",
            "phone": ph, "client": agent.get("name") or "",
            "return": bool(ret_list), "return_reason": next((str(r.get("description")) for r in ret_list if r.get("description")), ""),
            "confirmed_at": (dt(a.get(FIELD["confirm"])) or None).isoformat() if a.get(FIELD["confirm"]) else None,
            "sent_at": (dt(a.get(FIELD["sent"])) or None).isoformat() if a.get(FIELD["sent"]) else None,
            "await_stock": bool(a.get(FIELD["stock"])), "promo": a.get(FIELD["promo"]) or "",
            "lines": lines,
        })
        if index % 500 == 0:
            print(f"обработано {index}/{len(orders)}", flush=True)
    position_rows.close()
    del orders, order_payments, products, variants, services, agents, channels, return_orders
    print("amoCRM: новая воронка и задачи", flush=True)
    leads, tasks = get_amo()
    responses = get_responses(leads)
    try:
        roster_rows = shift_roster._rows(force=True)
        roster_status = "ok"
    except Exception as exc:
        roster_rows = {}
        roster_status = f"недоступен: {type(exc).__name__}"
    for row in result:
        paid_date = row["paid_date"]
        flags = roster_rows.get(datetime.fromisoformat(paid_date).date()) if paid_date else None
        if flags == (True, False):
            row["manager"] = "Кристина"
        elif flags == (False, True):
            row["manager"] = "Таня"
        else:
            row["manager"] = ""
    if os.environ.get("DASHBOARD_STRIP_ADDRESS"):
        for row in result:
            row.pop("address", None)
    snap = {"generated": datetime.now(TZ).isoformat(timespec="seconds"), "timezone": "Europe/Moscow",
            "orders": result, "leads": leads, "tasks": tasks,
            "roster": {"status": roster_status},
            "payment_daily": [{"date": day, "sum": round(amount, 2)} for day, amount in sorted(payment_daily.items())],
            "responses": responses,
            "wazzup": {"status": "метрика ответа рассчитана по событиям amoCRM"}}
    enrich_accounting(snap)
    # Атомарная замена: при ошибке предыдущий срез не получает новый timestamp.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    GAPS.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    report = [f"# Пробелы данных 2MY", "", f"Срез: {snap['generated']}. Источник: МойСклад.", ""]
    for label, numbers in gaps.items():
        report += [f"## {label}: {len(numbers)}", "", ", ".join(numbers) if numbers else "Нет", ""]
    GAPS.write_text("\n".join(report), encoding="utf-8")
    print(f"готово: {order_count} заказов, {len(leads)} сделок новой воронки; пробелы: {dict((k,len(v)) for k,v in gaps.items())}", flush=True)


if __name__ == "__main__":
    main()
