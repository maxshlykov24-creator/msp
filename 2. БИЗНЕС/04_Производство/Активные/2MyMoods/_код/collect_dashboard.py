#!/usr/bin/env python3
"""Полная пересборка локального среза 2MY. Чтение МойСклад и amoCRM, плюс запись даты полной оплаты."""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
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
    "full_paid": "015b915a-c21c-11f1-0a80-1e7500283344",
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


def full_paid_stamp(moment: datetime) -> str:
    return moment.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")


def absorb_payment(payment: dict, order_payments: dict, invoices: dict, seen: set[str], replace: bool = False) -> None:
    """Связь проведённого платежа с заказом. Повтор того же платежа не удваивает сумму."""
    pid = payment.get("id")
    if pid and pid in seen and not replace:
        return
    if pid and replace:
        for bucket in order_payments.values():
            bucket[:] = [item for item in bucket if item[0] != pid]
    if payment.get("applicable") is not True:
        if pid:
            seen.add(pid)
        return
    if pid:
        seen.add(pid)
    for operation in payment.get("operations") or []:
        meta = operation.get("meta") or {}
        target_id = mid(operation)
        order_id = target_id if meta.get("type") == "customerorder" else invoices.get(target_id) if meta.get("type") == "invoiceout" else None
        if order_id:
            order_payments[order_id].append((pid, payment.get("moment"), rub(operation.get("linkedSum"))))


def closing_payment(order_id: str, total: float, order_payments: dict) -> datetime | None:
    accumulated = 0.0
    closed = None
    for _pid, moment, linked_sum in sorted(order_payments.get(order_id, []), key=lambda row: row[1] or ""):
        accumulated += linked_sum
        if closed is None and accumulated + 0.009 >= total:
            closed = dt(moment)
    return closed


def stamp_full_paid(ms: lib.MS, order_id: str, moment: datetime) -> bool:
    """Пишет в заказ дату, когда связанные платежи впервые закрыли сумму.
    Одно доп. поле. Остальные поля заказа не передаются.
    """
    href = f"{lib.MS_BASE}/entity/customerorder/metadata/attributes/{FIELD['full_paid']}"
    status, _body = ms.send("PUT", f"/entity/customerorder/{order_id}", {"attributes": [{
        "meta": {"href": href, "type": "attributemetadata", "mediaType": "application/json"},
        "value": full_paid_stamp(moment),
    }]})
    return 200 <= status < 300


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


_SIZE = re.compile(r"^(xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|os|one size|onesize|\d+)$")


def _norm_piece(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[‐‑‒–—]", "-", text)
    return re.sub(r"\s*-\s*", "-", text)


def _color_token(raw: str) -> str:
    text = _norm_piece(raw)
    if not text:
        return ""
    text = re.sub(r"\s*\((?:one size|onesize|os|xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|\d+)\)\s*", "", text)
    text = re.sub(r"[,/]\s*(?:one size|onesize|os|xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|\d+)\s*$", "", text)
    text = re.sub(r"\s+(?:xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|os|one size|onesize|\d+)\s*$", "", text).strip()
    if not text or _SIZE.match(text) or not re.search(r"[а-я]", text):
        return ""
    return text


def color_from_name(name: str) -> str:
    """Цвет из хвоста названия: скобки или часть после запятой.
    Размер и артикул в конце пропускаются. Слова внутри названия не считаются цветом:
    иначе «бисера» становится «сера», а «Красноречивое молчание» становится цветом.
    """
    text = name or ""
    for group in reversed(re.findall(r"\(([^)]*)\)", text)):
        for part in reversed(group.split(",")):
            token = _color_token(part)
            if token:
                return token
    parts = text.split(",")
    for part in reversed(parts[1:]):
        token = _color_token(part)
        if token:
            return token
    return ""


_SIZE_CANON = {
    "one size": "one size", "onesize": "one size", "os": "one size",
    "xs-s": "XS-S", "s-m": "S-M", "m-l": "M-L", "l-xl": "L-XL",
    "xxxs": "XXXS", "xxs": "XXS", "xs": "XS", "s": "S", "m": "M", "l": "L",
    "xl": "XL", "xxl": "XXL", "xxxl": "XXXL",
}
_SIZE_FIND = re.compile(
    r"(?:^|[\s,(])(one\s*size|onesize|xxxs|xxs|xs-s|s-m|m-l|l-xl|xxxl|xxl|xs|xl|os|s|m|l)(?=$|[\s),])"
)


def canon_size(raw: str) -> str:
    text = _norm_piece(raw)
    if not text:
        return ""
    if re.fullmatch(r"one\s*size|onesize|os", text):
        return "one size"
    return _SIZE_CANON.get(text, (raw or "").strip())


def size_from_name(name: str) -> str:
    """Размер из названия, если в характеристике пусто.
    one size, OS, S-M и буквенные размеры. Кардиган альпаки 2MY x Vlada Nazina без размера в названии — one size.
    """
    text = _norm_piece(name)
    found = _SIZE_FIND.findall(text)
    if found:
        return canon_size(found[-1])
    if "кардиган из альпаки 2my x vlada nazina" in text:
        return "one size"
    return ""


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
            "order": amo.cf(lead, lib.FIELD_MS_ORDER_NUM).strip(),
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


def _shift_manager(flags) -> str | None:
    if flags == (True, False):
        return "Кристина"
    if flags == (False, True):
        return "Таня"
    return None


def _shift_bounds(day: date) -> tuple[datetime, datetime]:
    return (datetime(day.year, day.month, day.day, 10, 0, tzinfo=TZ),
            datetime(day.year, day.month, day.day, 21, 0, tzinfo=TZ))


def shift_clock(start_ts: int, roster: dict, horizon: int = 21) -> tuple[datetime | None, str | None]:
    """Первый момент смены 10:00–21:00 не раньше входящего и менеджер этого дня.
    До 10:00 счёт начинается в 10:00 того же дня, если в графике ровно один человек.
    С 21:00 смена уже кончилась: счёт и диалог переходят на следующую смену.
    День без одной галочки рабочим не считается.
    """
    if not roster:
        return None, None
    first_day, last_day = min(roster), max(roster)
    moment = datetime.fromtimestamp(int(start_ts), TZ)
    day = moment.date()
    # Дней раньше первой строки графика не растягиваем на первый известный день.
    if day < first_day:
        return None, None
    for _ in range(horizon):
        if day > last_day:
            return None, None
        manager = _shift_manager(roster.get(day))
        open_at, close_at = _shift_bounds(day)
        if manager and moment < open_at:
            return open_at, manager
        if manager and moment < close_at:
            return moment, manager
        day += timedelta(days=1)
        moment = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return None, None


def working_span(clock: datetime, end_ts: int, roster: dict) -> float:
    end = datetime.fromtimestamp(int(end_ts), TZ)
    if end <= clock:
        return 0.0
    total = 0.0
    day = clock.date()
    while day <= end.date():
        if _shift_manager(roster.get(day)):
            open_at, close_at = _shift_bounds(day)
            left, right = max(clock, open_at), min(end, close_at)
            if right > left:
                total += (right - left).total_seconds() / 60
        day += timedelta(days=1)
    return round(total, 2)


def _amo_get(amo: lib.Amo, path: str):
    status, body = 0, {}
    for attempt in range(5):
        status, body = amo.req("GET", path)
        if status not in (429, 500, 502, 503, 504):
            return status, body
        time.sleep(min(20, 2 * (attempt + 1)))
    return status, body


def _same_phone(left: str, right: str) -> bool:
    a, b = re.sub(r"\D", "", left or ""), re.sub(r"\D", "", right or "")
    return len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]


def _lead_ids_for_phone(amo: lib.Amo, phone: str, cache: dict[str, list[int] | None]) -> list[int] | None:
    key = re.sub(r"\D", "", phone or "")[-10:]
    if len(key) < 10:
        return None
    if key in cache:
        return cache[key]
    time.sleep(0.15)
    status, body = _amo_get(amo, f"/api/v4/contacts?query={key}&limit=5")
    contacts = ((body.get("_embedded") or {}).get("contacts") or []) if status == 200 and isinstance(body, dict) else []
    found = False
    lead_ids: list[int] = []
    for contact in contacts:
        numbers = []
        for grp in contact.get("custom_fields_values") or []:
            if grp.get("field_code") != "PHONE":
                continue
            numbers.extend(str((val or {}).get("value") or "") for val in grp.get("values") or [])
        if not any(_same_phone(number, key) for number in numbers):
            continue
        found = True
        time.sleep(0.15)
        link_status, links_body = _amo_get(amo, f"/api/v4/contacts/{contact['id']}/links?limit=250")
        links = ((links_body.get("_embedded") or {}).get("links") or []) if link_status == 200 and isinstance(links_body, dict) else []
        lead_ids.extend(link["to_entity_id"] for link in links if link.get("to_entity_type") == "leads" and link.get("to_entity_id"))
    cache[key] = lead_ids if found else None
    return cache[key]


def messages_until_payment(amo: lib.Amo, orders: list[dict], msg_times: dict[int, list[int]],
                           since_day: str, since_ts: int) -> list[dict]:
    """Сколько сообщений клиента в amo было до полной оплаты. Без текста и без телефона в ответе."""
    cache: dict[str, list[int] | None] = {}
    until = []
    seen: set[str] = set()
    for order in orders:
        paid_date = str(order.get("paid_date") or "")
        if not order.get("buy") or paid_date < since_day or not order.get("phone") or not order.get("paid_at"):
            continue
        number = str(order.get("number") or "")
        if number and number in seen:
            continue
        if number:
            seen.add(number)
        paid_ts = int(datetime.fromisoformat(order["paid_at"]).timestamp())
        if paid_ts < since_ts:
            continue
        lead_ids = _lead_ids_for_phone(amo, order["phone"], cache)
        if lead_ids is None:
            continue
        count = sum(1 for lead_id in set(lead_ids) for ts in msg_times.get(lead_id, []) if ts <= paid_ts)
        until.append({"paid_date": paid_date, "count": count, "covered": True})
    return until


def get_responses(leads: list[dict], orders: list[dict] | None = None) -> dict:
    """Паузы входящее → первый исходящий в беседе, без текстов и контактов."""
    amo = lib.Amo()
    now = datetime.now(TZ)
    since = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
    lead_ids = {l["id"] for l in leads}
    response_ids = set(lead_ids)
    msg_times: dict[int, list[int]] = defaultdict(list)
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
            if row.get("entity_type") != "lead":
                continue
            msg_times[row["entity_id"]].append(row["created_at"])
            if row.get("entity_id") in lead_ids:
                events[row["id"]] = row
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("amo response events: pagination limit reached")
    names = {lib.USER_TANYA: "Таня", lib.USER_KRISTINA: "Кристина", lib.USER_OKSANA: "Оксана", lib.USER_MAXIM: "Максим"}
    pending, samples, unknown_rows = {}, [], []
    rules = response_rules()
    try:
        roster = shift_roster._rows(force=True)
        clock_status = "10-21"
    except Exception as exc:
        roster = {}
        clock_status = f"график недоступен: {type(exc).__name__}"
    current_managers = {l["id"]: names.get(l.get("manager"), "Не определён") for l in leads}
    unknown = 0
    for row in sorted(events.values(), key=lambda r: (r["created_at"], r["type"] != "incoming_chat_message", r["id"])):
        values = row.get("value_after") or []
        msg = values[0].get("message", {}) if values else {}
        if not msg.get("talk_id"):
            continue
        key = (msg.get("origin"), msg["talk_id"])
        if row["entity_id"] not in response_ids:
            continue
        if row["type"] == "incoming_chat_message":
            pending.setdefault(key, (row["created_at"], row["entity_id"]))
        elif key in pending:
            start, entity_id = pending.pop(key)
            clock, manager = shift_clock(start, roster) if clock_status == "10-21" else (None, None)
            if manager and clock is not None:
                minutes = working_span(clock, row["created_at"], roster)
                samples.append({"date": clock.date().isoformat(),
                                "manager": manager, "minutes": minutes,
                                "start_at": start, "end_at": row["created_at"],
                                "working_minutes": minutes})
            else:
                unknown += 1
                unknown_rows.append({"date": datetime.fromtimestamp(start, TZ).date().isoformat()})
    open_talks = set()
    for page in range(1, 2001):
        status, body = amo.req("GET", f"/api/v4/talks?filter[only_in_work]=1&limit=250&page={page}")
        if status == 204:
            break
        if status != 200:
            raise RuntimeError(f"amo open talks page {page}: HTTP {status}")
        batch = body.get("_embedded", {}).get("talks", [])
        open_talks.update(t["talk_id"] for t in batch if t.get("is_in_work") is True
                          and t.get("entity_type") == "lead" and t.get("entity_id") in lead_ids)
        if len(batch) < 250:
            break
    else:
        raise RuntimeError("amo open talks: pagination limit reached")
    observed_talks = {v.get("message", {}).get("talk_id") for e in events.values() for v in e.get("value_after", [])}
    pending_rows = []
    for key, (start, entity_id) in pending.items():
        clock, shift_name = shift_clock(start, roster) if clock_status == "10-21" else (None, None)
        end_ts = int(now.timestamp())
        if clock is not None:
            waited = working_span(clock, end_ts, roster)
            pending_date = clock.date().isoformat()
            pending_manager = shift_name or "Не определён"
        else:
            waited = round((now.timestamp() - start) / 60, 2)
            pending_date = datetime.fromtimestamp(start, TZ).date().isoformat()
            pending_manager = current_managers.get(entity_id, "Не определён")
        pending_rows.append({"date": pending_date, "start_at": start, "minutes": waited,
                             "working_minutes": waited, "manager": pending_manager, "talk_id": key[1],
                             "is_open": key[1] in open_talks})
    since_day = since.date().isoformat()
    since_ts = int(since.timestamp())
    until = messages_until_payment(amo, orders or [], msg_times, since_day, since_ts)
    return {"status": "ok", "source": "amoCRM events", "from": since.date().isoformat(),
            "until_revenue": until,
            "to": now.isoformat(), "samples": samples, "messages": len(events),
            "unknown_author": unknown, "unanswered": len(pending), "unknown_rows": unknown_rows,
            "pending": pending_rows, "queue_verified": True, "open_without_events": len(open_talks - observed_talks),
            "rules": rules, "rules_status": "configured" if rules else "not_configured",
            "clock": clock_status, "clock_from": min(roster).isoformat() if roster and clock_status == "10-21" else None}


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


def classify_shipments(order_lines, demands) -> list[str]:
    """Отгрузки, которые не раскладываются по позициям заказа.

    Досыл исходного заказа укладывается в остаток количества и возвратом не становится.
    Отгрузка с другим товаром или с количеством сверх заказа — обмен.
    Услуга, которой нет в заказе, сама по себе обмен не создаёт.
    """
    remaining = defaultdict(float)
    for line in order_lines or []:
        remaining[line.get("id")] += float(line.get("qty") or 0)
    extra = []
    for demand in sorted(demands, key=lambda row: row.get("moment") or ""):
        goods = [p for p in demand.get("positions") or [] if float(p.get("qty") or 0) > 0]
        if not goods:
            continue
        blocked = [p for p in goods if remaining[p.get("id")] + 1e-6 < float(p["qty"]) and p.get("type") != "service"]
        if blocked:
            extra.append(demand.get("id"))
            continue
        for pos in goods:
            if pos.get("type") == "service" and remaining[pos.get("id")] + 1e-6 < float(pos["qty"]):
                continue
            remaining[pos.get("id")] -= float(pos["qty"])
    return extra


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
    demand_rows = required_rows(ms, "/entity/demand", keep=("id", "name", "moment", "sum", "applicable", "customerOrder"))
    demands = {row["id"]: mid(row.get("customerOrder")) for row in demand_rows}
    demands_by_order = defaultdict(list)
    for row in demand_rows:
        oid = demands.get(row["id"])
        if oid and row.get("applicable") is not False and rub(row.get("sum")) > 0.009:
            demands_by_order[oid].append(row)
    shipment_cache = {}

    def shipment_positions(demand_id):
        if demand_id not in shipment_cache:
            rows = []
            for pos in required_rows(ms, f"/entity/demand/{demand_id}/positions"):
                meta = ((pos.get("assortment") or {}).get("meta") or {})
                rows.append({"id": mid(pos.get("assortment")), "qty": float(pos.get("quantity") or 0), "type": meta.get("type") or ""})
            shipment_cache[demand_id] = rows
        return shipment_cache[demand_id]

    def exchange_ids(order):
        rows = demands_by_order.get(order["id"], [])
        if len(rows) < 2:
            return []
        packed = []
        for row in rows:
            packed.append({"id": row["id"], "moment": row.get("moment") or "", "positions": shipment_positions(row["id"])})
        return classify_shipments(order.get("lines"), packed)

    returns, exchanges, unresolved = [], [], []
    built = defaultdict(list)
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
        built[original["id"]].append(event)
    for oid, events in built.items():
        extra = exchange_ids(by_id[oid])
        events.sort(key=lambda item: item.get("paid_date") or "")
        keep = len(events) if not extra else max(0, len(events) - len(extra))
        returns.extend(events[:keep])
        for event in events[keep:]:
            event["exchange"] = True
            event["return_event"] = False
            exchanges.append(event)
    snap.update(returns=returns, exchanges=exchanges, return_gaps=unresolved,
                accounting={"cost_history_started": history["started"], "returns_checked_at": observed,
                            "exchanges": len(exchanges)})


def build_order(o, positions, ms, started, order_payments, agents, channels, return_orders, products, variants, services, gaps):
    number = o.get("name", "")
    a = attrs(o)
    total = rub(o.get("sum"))
    paid = rub(o.get("payedSum"))
    is_buy = total > 0 and paid + 0.009 >= total
    if total > 0 and paid == 0:
        gaps["Оплачено пустое"].append(number)
    if bool(a.get(FIELD["paid"])) != is_buy:
        gaps["Галка Оплачен не совпадает с платежом"].append(number)
    closed = closing_payment(o["id"], total, order_payments)
    if is_buy and not closed:
        gaps["Оплачено закрыто, дата платежа не найдена"].append(number)
    elif is_buy and closed and str(a.get(FIELD["full_paid"]) or "")[:10] != closed.date().isoformat():
        if not stamp_full_paid(ms, o["id"], closed):
            gaps["Не записана дата полной оплаты"].append(number)
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
        for x in positions:
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
            size = canon_size(str(chars.get("размер") or ""))
            if not size:
                size = size_from_name(name)
            color = str(chars.get("цвет") or "").strip()
            if not color:
                color = color_from_name(name)
            qty = float(x.get("quantity") or 0)
            gross = rub(x.get("price")) * qty
            rev = round(gross * (1 - float(x.get("discount") or 0) / 100), 2)
            cost = rub((product.get("buyPrice") or {}).get("value")) * qty
            if rev > 0 and cost == 0 and not delivery_line:
                gaps["Позиция без закупочной цены"].append(number)
            lines.append({"id": aid, "name": name, "category": product.get("pathName") or "не указано",
                          "size": size, "color": color, "qty": qty, "rev": rev,
                          "list": gross, "cost": round(cost, 2), "delivery": delivery_line})
    return {
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
    }


def main() -> None:
    ms = lib.MS()
    started = datetime.now(TZ)
    print("МойСклад: заказы, платежи, номенклатура, контрагенты", flush=True)
    orders = required_rows(ms, "/entity/customerorder", {"order": "moment,desc"},
                           keep=("id", "name", "moment", "sum", "payedSum", "attributes", "agent",
                                 "salesChannel", "shipmentAddress", "shipmentAddressFull"))
    order_count = len(orders)
    paymentins = required_rows(ms, "/entity/paymentin", keep=("id", "moment", "created", "sum", "operations", "applicable"))
    payments = paymentins + required_rows(ms, "/entity/cashin", keep=("id", "moment", "operations", "applicable"))
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
    linked_ids: set[str] = set()
    for payment in payments:
        absorb_payment(payment, order_payments, invoices, linked_ids)
    del payments, paymentins
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
        total_now = rub(o.get("sum"))
        paid_now = rub(o.get("payedSum"))
        if total_now > 0 and paid_now + 0.009 >= total_now:
            positions = next(position_rows)
            paid_processed += 1
            if paid_processed % 250 == 0:
                print(f"позиций заказов: {paid_processed}/{len(paid_ids)}", flush=True)
        else:
            positions = []
        result.append(build_order(o, positions, ms, started, order_payments, agents, channels, return_orders, products, variants, services, gaps))
        if index % 500 == 0:
            print(f"обработано {index}/{len(orders)}", flush=True)
    position_rows.close()
    print("amoCRM: новая воронка и задачи", flush=True)
    leads, tasks = get_amo()
    responses = get_responses(leads, result)
    # Список заказов читается в начале сборки. Платёж, пришедший пока считались позиции,
    # в срез не попадал, хотя время среза ставится в конце. Добираем изменения.
    stamp = started.strftime("%Y-%m-%d %H:%M:%S")
    print(f"добор заказов и платежей с {stamp}", flush=True)
    try:
        fresh_orders = required_rows(ms, "/entity/customerorder",
                                     {"filter": f"updated>={stamp}", "order": "moment,desc"},
                                     keep=("id", "name", "moment", "sum", "payedSum", "attributes", "agent",
                                           "salesChannel", "shipmentAddress", "shipmentAddressFull"))
        fresh_payments = required_rows(ms, "/entity/paymentin", {"filter": f"updated>={stamp}"},
                                       keep=("id", "moment", "created", "sum", "operations", "applicable"))
        fresh_payments += required_rows(ms, "/entity/cashin", {"filter": f"updated>={stamp}"},
                                        keep=("id", "moment", "operations", "applicable"))
    except Exception as exc:
        fresh_orders, fresh_payments = [], []
        print(f"добор не удался: {type(exc).__name__}", flush=True)
    for payment in fresh_payments:
        absorb_payment(payment, order_payments, invoices, linked_ids, replace=True)
    original_money = {row["id"]: (rub(row.get("sum")), rub(row.get("payedSum"))) for row in orders}
    by_id = {row["id"]: row for row in result}
    caught = []
    for o in fresh_orders:
        money = (rub(o.get("sum")), rub(o.get("payedSum")))
        if o["id"] in original_money and original_money[o["id"]] == money:
            continue
        is_buy = money[0] > 0 and money[1] + 0.009 >= money[0]
        positions = positions_for(o["id"])[1] if is_buy else []
        row = build_order(o, positions, ms, started, order_payments, agents, channels, return_orders,
                          products, variants, services, gaps)
        if o["id"] in by_id:
            by_id[o["id"]].clear()
            by_id[o["id"]].update(row)
        else:
            result.append(row)
            by_id[o["id"]] = row
        caught.append(o.get("name") or o["id"])
    print(f"добор: {len(caught)}", flush=True)
    if caught:
        print("добор заказы: " + ", ".join(caught[:30]), flush=True)
    del orders, order_payments, products, variants, services, agents, channels, return_orders, invoices
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
