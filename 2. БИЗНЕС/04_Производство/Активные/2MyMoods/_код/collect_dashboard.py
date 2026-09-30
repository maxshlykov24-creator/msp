#!/usr/bin/env python3
"""Полная пересборка локального среза 2MY. Только GET к внешним системам."""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lib

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


def required_rows(ms: lib.MS, path: str, params: dict | None = None) -> list[dict]:
    params = dict(params or {})
    params["limit"] = 1000
    out = []
    offset = 0
    while True:
        params["offset"] = offset
        status, body = ms.req(path, params)
        if status != 200:
            raise RuntimeError(f"GET {path}: HTTP {status}")
        chunk = body.get("rows", [])
        out += chunk
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
    leads = amo.iter_leads(f"?filter[pipeline_id]={lib.PIPELINE_SALES_NEW}", pages=500)
    # iter_leads молча прекращает обход при ошибке; проверяем размер первой выборки.
    status, body = amo.req("GET", f"/api/v4/leads?filter[pipeline_id]={lib.PIPELINE_SALES_NEW}&limit=1&page=1")
    if status not in (200, 204):
        raise RuntimeError(f"amo leads: HTTP {status}")
    tasks = []
    lead_ids = {lead["id"] for lead in leads if lead.get("pipeline_id") == lib.PIPELINE_SALES_NEW}
    for page in range(1, 501):
        status, tasks_body = amo.req("GET", f"/api/v4/tasks?limit=250&page={page}")
        if status == 204:
            break
        if status != 200:
            raise RuntimeError(f"amo tasks: HTTP {status}")
        batch = (tasks_body.get("_embedded") or {}).get("tasks") or []
        tasks.extend(t for t in batch if t.get("entity_type") == "leads" and t.get("entity_id") in lead_ids)
        if len(batch) < 250:
            break
    result = []
    for lead in leads:
        if lead.get("pipeline_id") != lib.PIPELINE_SALES_NEW:
            continue
        result.append({
            "id": lead["id"], "created": datetime.fromtimestamp(lead["created_at"], TZ).date().isoformat(),
            "status": lead.get("status_id"), "manager": lead.get("responsible_user_id"),
            "won": lead.get("status_id") == 142,
        })
    return result, {"open": sum(t.get("is_completed") is False for t in tasks), "sampled": len(tasks)}


def positions_for(order_id: str) -> tuple[str, list[dict]]:
    client = lib.MS()
    path = f"/entity/customerorder/{order_id}/positions"
    for attempt in range(6):
        try:
            return order_id, required_rows(client, path)
        except RuntimeError:
            if attempt == 5:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> None:
    ms = lib.MS()
    started = datetime.now(TZ)
    print("МойСклад: заказы, платежи, номенклатура, контрагенты", flush=True)
    orders = required_rows(ms, "/entity/customerorder", {"order": "moment,desc"})
    payments = {p["id"]: p for p in required_rows(ms, "/entity/paymentin")}
    products = {p["id"]: p for p in required_rows(ms, "/entity/product")}
    variants = {v["id"]: v for v in required_rows(ms, "/entity/variant")}
    services = {s["id"]: s for s in required_rows(ms, "/entity/service")}
    agents = {a["id"]: a for a in required_rows(ms, "/entity/counterparty")}
    channels = {c["id"]: c["name"] for c in required_rows(ms, "/entity/saleschannel")}
    returns = required_rows(ms, "/entity/salesreturn")
    demands = {d["id"]: d for d in required_rows(ms, "/entity/demand")}
    return_orders = defaultdict(list)
    for ret in returns:
        demand = demands.get(mid(ret.get("demand")))
        if demand:
            return_orders[mid(demand.get("customerOrder"))].append(ret)
    print(f"справочники готовы; заказов {len(orders)}", flush=True)
    paid_ids = [o["id"] for o in orders if float(o.get("sum") or 0) > 0
                and float(o.get("payedSum") or 0) >= float(o.get("sum") or 0)]
    positions = {}
    print(f"позиции оплаченных заказов: {len(paid_ids)}", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(positions_for, oid) for oid in paid_ids]
        for count, future in enumerate(as_completed(futures), 1):
            oid, rows = future.result()
            positions[oid] = rows
            if count % 250 == 0:
                print(f"позиций заказов: {count}/{len(paid_ids)}", flush=True)
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
        pay_refs = o.get("payments") or []
        accumulated = 0.0
        closed = None
        for ref in sorted(pay_refs, key=lambda r: (payments.get(mid(r), {}).get("moment") or "")):
            p = payments.get(mid(ref))
            if not p:
                continue
            accumulated += rub(ref.get("linkedSum"))
            if closed is None and accumulated + 0.009 >= total:
                closed = dt(p.get("moment"))
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
            pos = positions[o["id"]]
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
                lines.append({"id": aid, "name": name, "category": product.get("pathName") or "не указано",
                              "size": size, "color": color, "qty": qty, "rev": rev,
                              "list": gross, "cost": round(cost, 2), "delivery": delivery_line})
        result.append({
            "number": number, "id": o["id"], "created": (dt(o.get("moment")) or started).date().isoformat(),
            "paid_date": closed.date().isoformat() if closed else None,
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
    print("amoCRM: новая воронка и задачи", flush=True)
    leads, tasks = get_amo()
    if os.environ.get("DASHBOARD_STRIP_ADDRESS"):
        for row in result:
            row.pop("address", None)
    snap = {"generated": datetime.now(TZ).isoformat(timespec="seconds"), "timezone": "Europe/Moscow",
            "orders": result, "leads": leads, "tasks": tasks,
            "wazzup": {"messages": 0, "first_response_minutes": 0, "status": "нет проверенной выгрузки сообщений"}}
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
    print(f"готово: {len(orders)} заказов, {len(leads)} сделок новой воронки; пробелы: {dict((k,len(v)) for k,v in gaps.items())}", flush=True)


if __name__ == "__main__":
    main()
