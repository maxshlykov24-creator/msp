#!/usr/bin/env python3
"""Безопасный перенос заказов МойСклад в amoCRM.

По умолчанию строит закрытый аудит и план без записей. Запись требует
явных --apply и SHA256 файла плана. Любая неоднозначность остаётся исключением.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import assign_shift
import lib
import shift_roster

TZ = ZoneInfo("Europe/Moscow")
WORK = Path.home() / "Library" / "Caches"
PLAN = WORK / "2my-ms-amo-backfill-plan.local.json"
RAW_CACHE = WORK / "2my-ms-amo-backfill-source.local.json"
ORDER_CACHE = WORK / "2my-ms-amo-expanded-orders.local.jsonl"
STATUS_AUDIT_CACHE = WORK / "2my-ms-amo-status-audit.local.json"
JOURNAL = WORK / "2my-ms-amo-backfill-journal.local.json"
LOCK = WORK / "2my-ms-amo-backfill.lock"
PIPELINE = 11318374
WON = 142
MAXIM = 9490530
MS_AMO_ATTR = lib.MS_ATTR_AMO_LINK
STATUS_IDS = {
    "Выполнен": lib.MS_STATE_DONE,
    "Покупка в шоуруме": lib.MS_STATE_SHOWROOM,
    "Самовывоз": "655b1f7f-c447-11eb-0a80-08be002efc0d",
    "Подтвержден": lib.MS_STATE_CONFIRMED,
    "Обработка": "655b1eed-c447-11eb-0a80-08be002efc0c",
}
ALWAYS = {"Выполнен", "Покупка в шоуруме"}
PAID_REQUIRED = {"Самовывоз", "Подтвержден", "Обработка"}
FIELDS = {
    "ms_id": 929049, "number": 929057, "ms_url": 929081,
    "counterparty_id": 929055, "counterparty_name": 929031,
    "source": 929043, "state": 929075, "payment": 929029,
    "items": 929063, "comment": 929071, "delivery": 929041,
    "returned": 1151122, "partial_return": 1151124,
}
ENUM = {"source_ms": 1255285, "unpaid": 1255257,
        "partial": 1255259, "paid": 1255261}
AMO_ORDER_FIELD = 929081
AMO_PHONE_FIELD = 928609
AMO_EMAIL_FIELD = 928611
SOURCE_ID = 23495049


class Stop(RuntimeError):
    pass


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + ".next")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def private_storage() -> None:
    if not WORK.is_dir() or WORK.stat().st_mode & 0o077:
        raise Stop("Защищённое постоянное хранилище недоступно")


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def id_of(ref: object) -> str:
    if not isinstance(ref, dict):
        return ""
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else ref
    return str(meta.get("href") or "").rstrip("/").rsplit("/", 1)[-1]


def state_id(order: dict) -> str:
    return id_of(order.get("state"))


def attrs(row: dict) -> dict:
    return {str(x.get("id") or id_of(x)): x.get("value") for x in row.get("attributes") or []}


def ms_link(order: dict) -> str:
    return str(attrs(order).get(MS_AMO_ATTR) or "").strip()


def field_values(entity: dict, field_id: int) -> list[str]:
    for group in entity.get("custom_fields_values") or []:
        if str(group.get("field_id")) == str(field_id):
            return [str(v.get("value") or "").strip() for v in group.get("values") or []
                    if v.get("value") not in (None, "")]
    return []


def field(entity: dict, field_id: int) -> str:
    values = field_values(entity, field_id)
    return values[0] if values else ""


def api_get(session: requests.Session, url: str, params: dict | None = None,
            last_call: list[float] | None = None) -> dict:
    for attempt in range(6):
        if last_call is not None:
            pause = max(0.0, 1.0 - (time.monotonic() - last_call[0]))
            if pause:
                time.sleep(pause)
        try:
            response = session.get(url, params=params, timeout=(10, 60))
            if last_call is not None:
                last_call[0] = time.monotonic()
            if response.status_code == 200:
                body = response.json()
                if not isinstance(body, dict):
                    raise Stop("Неверный формат обязательного GET")
                return body
            if response.status_code == 204:
                return {}
            if response.status_code not in (429, 500, 502, 503, 504):
                raise Stop(f"Обязательный GET: HTTP {response.status_code}")
            retry = response.headers.get("Retry-After", "")
            try:
                delay = min(60.0, max(1.0, float(retry)))
            except ValueError:
                delay = min(60.0, 2.0 ** (attempt + 1))
            time.sleep(delay)
        except (requests.RequestException, ValueError) as exc:
            if last_call is not None:
                last_call[0] = time.monotonic()
            if attempt == 5:
                raise Stop(f"Обязательный GET не подтверждён: {type(exc).__name__}") from exc
            time.sleep(min(60.0, 2.0 ** (attempt + 1)))
    raise Stop("Обязательный GET исчерпал повторы")


def ms_session(ms: lib.MS) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": "Bearer " + ms.token,
                      "Accept": "application/json;charset=utf-8",
                      "User-Agent": "MSProduct-2MY/1.0"})
    return s


def ms_rows(session: requests.Session, path: str, params: dict | None = None,
            pace: list[float] | None = None) -> list[dict]:
    p = dict(params or {})
    p.setdefault("limit", 1000)
    offset, expected, out = 0, None, []
    while True:
        p["offset"] = offset
        body = api_get(session, lib.MS_BASE + path, p, pace)
        meta = body.get("meta") or {}
        size = meta.get("size")
        rows = body.get("rows")
        if not isinstance(size, int) or not isinstance(rows, list):
            raise Stop(f"Страница МойСклад {path} не содержит полный meta.size/rows")
        if expected is None:
            expected = size
        elif size != expected:
            raise Stop(f"Размер набора МойСклад {path} изменился во время чтения")
        if not rows and offset < expected:
            raise Stop(f"Пагинация МойСклад {path} оборвалась")
        out.extend(rows)
        offset += len(rows)
        if offset >= expected:
            row_keys = [str(r.get("id") or digest(r)) for r in out]
            if len(out) != expected or len(set(row_keys)) != len(out):
                raise Stop(f"Полнота/уникальность набора МойСклад {path} не подтверждена")
            return out


def amo_pages(amo: lib.Amo, path: str, key: str) -> list[dict]:
    out = []
    for page in range(1, 501):
        status, body = amo.req("GET", f"{path}{'&' if '?' in path else '?'}limit=250&page={page}")
        if status == 204:
            return out
        if status != 200:
            raise Stop(f"Полная выгрузка amo {key}: HTTP {status}")
        chunk = (body.get("_embedded") or {}).get(key)
        if not isinstance(chunk, list):
            raise Stop(f"Неполная страница amo {key}")
        out.extend(chunk)
        if len(chunk) < 250:
            if len({str(x.get("id")) for x in out}) != len(out):
                raise Stop(f"Дубли ID в полном индексе amo {key}")
            return out
        time.sleep(0.15)
    raise Stop(f"Предел страниц amo {key}")


def amount(value: object) -> int:
    try:
        return int(Decimal(str(value or "0")))
    except Exception as exc:
        raise Stop("Невозможно точно разобрать сумму") from exc


def moscow_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def amount_text(kopecks: int) -> str:
    return f"{Decimal(kopecks) / Decimal(100):.2f}"


def rubles_round(kopecks: int) -> int:
    return int((Decimal(kopecks) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def phone_norm(value: object, address: object = "") -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if digits.startswith("00") and 10 <= len(digits) <= 17:
        return "+" + digits[2:]
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[-10:]
    addr = str(address or "").casefold()
    if len(digits) == 10 and any(x in addr for x in ("россия", "российская федерация", " russia", "countrycode=ru")):
        return "+7" + digits
    return ""


def email_norm(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text) else ""


def amo_lead_url(amo: lib.Amo, lead_id: str | int) -> str:
    return f"{amo.base}/leads/detail/{lead_id}"


def ms_order_url(order_id: str) -> str:
    return f"https://online.moysklad.ru/app/#CustomerOrder/edit?id={order_id}"


def linked_order_id(url: str) -> str:
    try:
        u = urllib.parse.urlsplit(url.strip())
        frag = u.fragment
        query = frag.split("?", 1)[1] if "?" in frag else ""
        return (urllib.parse.parse_qs(query).get("id") or [""])[0].lower()
    except Exception:
        return ""


def compact_order_snapshot(mss: requests.Session, pace: list[float]) -> tuple[dict[str, dict], dict[str, int]]:
    snapshot: dict[str, dict] = {}
    counts = {}
    for name, sid in STATUS_IDS.items():
        href = f"{lib.MS_BASE}/entity/customerorder/metadata/states/{sid}"
        rows = ms_rows(mss, "/entity/customerorder", {
            "limit": 1000, "filter": "state=" + href,
            "order": "moment,desc",
        }, pace)
        if any(state_id(row) != sid for row in rows):
            raise Stop(f"Фильтр статуса МойСклад не совпал: {name}")
        counts[name] = len(rows)
        for row in rows:
            oid = str(row.get("id") or "").casefold()
            if not oid or oid in snapshot:
                raise Stop("Пустой или повторный UUID в текущем списке заказов")
            snapshot[oid] = row
    return snapshot, counts


def compact_signature(snapshot: dict[str, dict]) -> str:
    return digest({oid: (row.get("updated"), state_id(row)) for oid, row in snapshot.items()})


def read_order_cache() -> dict[str, dict]:
    if not ORDER_CACHE.exists():
        raise Stop("Защищённый локальный кэш подробных заказов отсутствует")
    result = {}
    try:
        with ORDER_CACHE.open(encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                oid = str(row.get("id") or "").casefold()
                if not oid or oid in result:
                    raise Stop("Локальный кэш заказов содержит пустой или повторный ID")
                result[oid] = row
    except Stop:
        raise
    except Exception as exc:
        raise Stop("Локальный кэш подробных заказов повреждён") from exc
    return result


def write_order_cache(rows: list[dict]) -> None:
    tmp = ORDER_CACHE.with_suffix(ORDER_CACHE.suffix + ".next")
    with tmp.open("w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, ORDER_CACHE)


def status_audit_cache_load() -> dict[str, str | None]:
    if not STATUS_AUDIT_CACHE.exists():
        return {}
    try:
        value = json.loads(STATUS_AUDIT_CACHE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise Stop("Кэш истории статусов повреждён") from exc
    if not isinstance(value, dict):
        raise Stop("Формат кэша истории статусов не распознан")
    return value


def status_audit_cache_save(value: dict[str, str | None]) -> None:
    atomic_json(STATUS_AUDIT_CACHE, value)


def reconcile_orders(mss: requests.Session, pace: list[float]) -> tuple[list[dict], dict[str, int], int]:
    cached = read_order_cache()
    for attempt in range(1, 4):
        print(f"Сверяю полный список заказов с защищённым подробным кэшем (попытка {attempt}/3)…", flush=True)
        compact, counts = compact_order_snapshot(mss, pace)
        resolved = []
        changed = 0
        stable = True
        for oid, brief in compact.items():
            prior = cached.get(oid)
            if prior and str(prior.get("updated") or "") == str(brief.get("updated") or "") and state_id(prior) == state_id(brief):
                resolved.append(prior)
                continue
            changed += 1
            detail = api_get(mss, lib.MS_BASE + f"/entity/customerorder/{oid}", {
                "expand": "positions,positions.assortment,agent,state",
            }, pace)
            if (str(detail.get("updated") or "") != str(brief.get("updated") or "")
                    or state_id(detail) != state_id(brief)):
                stable = False
                break
            resolved.append(detail)
        if stable:
            confirm, confirm_counts = compact_order_snapshot(mss, pace)
            stable = (compact_signature(compact) == compact_signature(confirm)
                      and counts == confirm_counts)
        if stable:
            if len(resolved) != len(compact) or len({str(x.get("id")).casefold() for x in resolved}) != len(resolved):
                raise Stop("Полнота подробных заказов не подтверждена")
            write_order_cache(resolved)
            print(f"  текущих заказов: {len(resolved)}, подробностей перечитано: {changed}", flush=True)
            return resolved, counts, changed
        print("  набор менялся при сверке; повторяю компактный срез.", flush=True)
    raise Stop("Не удалось получить стабильный состав заказов за три попытки")


def collect_current(ms: lib.MS, amo: lib.Amo) -> tuple[dict, dict]:
    """Полные исходные индексы с проверкой стабильного состава заказов."""
    mss = ms_session(ms)
    pace = [0.0]
    ms_meta = api_get(mss, lib.MS_BASE + "/entity/customerorder/metadata", last_call=pace)
    state_names = {str(x.get("id")): str(x.get("name") or "") for x in ms_meta.get("states") or []}
    if any(state_names.get(sid) != name for name, sid in STATUS_IDS.items()):
        raise Stop("Статусы МойСклад не совпали с утверждёнными ID")
    attrs_meta = api_get(mss, lib.MS_BASE + "/entity/customerorder/metadata/attributes", last_call=pace)
    attr_rows = attrs_meta.get("rows")
    if not isinstance(attr_rows, list):
        raise Stop("Метаданные дополнительных полей МойСклад не прочитаны полностью")
    amo_attr_ids = {str(x.get("id")) for x in attr_rows}
    if MS_AMO_ATTR not in amo_attr_ids:
        raise Stop("Поле ссылки на amo отсутствует в метаданных заказа")
    order_rows, counts, _changed = reconcile_orders(mss, pace)
    total_page = api_get(mss, lib.MS_BASE + "/entity/customerorder", {"limit": 1}, pace)
    total_orders = (total_page.get("meta") or {}).get("size")
    if not isinstance(total_orders, int) or sum(counts.values()) > total_orders:
        raise Stop("Не удалось сверить общий размер реестра заказов")
    if len({x.get("id") for x in order_rows}) != len(order_rows):
        raise Stop("Пересекающиеся фильтры статусов МойСклад")

    print("Читаю оплаты и связанные документы МойСклад…", flush=True)
    paymentins = ms_rows(mss, "/entity/paymentin", {"limit": 1000}, pace)
    cashins = ms_rows(mss, "/entity/cashin", {"limit": 1000}, pace)
    invoices = ms_rows(mss, "/entity/invoiceout", {"limit": 1000}, pace)
    returns = ms_rows(mss, "/entity/salesreturn", {
        "limit": 100, "expand": "positions,positions.assortment,demand",
    }, pace)
    demands = ms_rows(mss, "/entity/demand", {"limit": 1000}, pace)
    paymentouts = ms_rows(mss, "/entity/paymentout", {"limit": 1000}, pace)
    cashouts = ms_rows(mss, "/entity/cashout", {"limit": 1000}, pace)
    currencies = ms_rows(mss, "/entity/currency", {"limit": 1000}, pace)
    default_currency = [x for x in currencies if x.get("default") is True]
    if len(default_currency) != 1 or str(default_currency[0].get("isoCode") or "").upper() != "RUB":
        raise Stop("Валюта по умолчанию МойСклад не подтверждена как RUB")

    print("Читаю полный индекс amoCRM…", flush=True)
    leads = amo_pages(amo, "/api/v4/leads?with=contacts,source", "leads")
    contacts = amo_pages(amo, "/api/v4/contacts", "contacts")
    mfields = amo_pages(amo, "/api/v4/leads/custom_fields", "custom_fields")
    cfields = amo_pages(amo, "/api/v4/contacts/custom_fields", "custom_fields")
    pipeline_status, pipeline = amo.req("GET", f"/api/v4/leads/pipelines/{PIPELINE}")
    if pipeline_status != 200 or pipeline.get("name") != "Продажи 2MY":
        raise Stop("Целевая воронка не совпала с утверждённой схемой")
    won = [x for x in pipeline.get("_embedded", {}).get("statuses", []) if x.get("id") == WON]
    if len(won) != 1 or won[0].get("name") != "Успешно реализовано":
        raise Stop("Этап успешной реализации не совпал")
    mf = {x.get("id"): x for x in mfields}
    cf = {x.get("id"): x for x in cfields}
    for fid, typ in ((929049, "text"), (929057, "text"), (929081, "url"),
                     (929055, "text"), (929031, "text"), (929043, "select"),
                     (929075, "text"), (929029, "select"), (929063, "textarea"),
                     (929071, "textarea")):
        if fid not in mf or mf[fid].get("type") != typ:
            raise Stop(f"Обязательное поле сделки {fid} отсутствует или изменило тип")
    source_enum = {int(x.get("id")): x.get("value") for x in mf[929043].get("enums") or []}
    payment_enum = {int(x.get("id")): x.get("value") for x in mf[929029].get("enums") or []}
    if source_enum.get(ENUM["source_ms"]) != "Из МойСклад" or any(
        payment_enum.get(ENUM[key]) != label for key, label in (
            ("unpaid", "Не оплачен"), ("partial", "Частично оплачен"), ("paid", "Оплачен"))):
        raise Stop("Справочник источника/оплаты amoCRM изменился")
    if cf.get(AMO_PHONE_FIELD, {}).get("type") != "multitext" or cf.get(AMO_EMAIL_FIELD, {}).get("type") != "multitext":
        raise Stop("Поля телефона/email контакта не совпали со схемой")
    active_users = {str(x.get("id")): x for x in amo_pages(amo, "/api/v4/users", "users")}
    maxim = active_users.get(str(MAXIM))
    if not maxim or not maxim.get("rights", {}).get("is_active", True):
        raise Stop("Технический ответственный неактивен")
    print(f"Индекс собран: {len(order_rows)} заказов, {len(leads)} сделок, {len(contacts)} контактов", flush=True)
    payload = {
        "captured_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "state_counts": counts, "all_orders_count": total_orders, "orders": order_rows,
        "paymentin": paymentins, "cashin": cashins, "invoiceout": invoices,
        "salesreturn": returns, "demand": demands,
        "paymentout": paymentouts, "cashout": cashouts,
        "default_currency": default_currency[0],
        "leads": leads, "contacts": contacts,
    }
    schema = {"pipeline": pipeline.get("name"), "won": won[0].get("name"),
              "lead_fields": {str(k): v.get("type") for k, v in mf.items() if k in FIELDS.values()},
              "contact_fields": {str(k): v.get("type") for k, v in cf.items() if k in (AMO_PHONE_FIELD, AMO_EMAIL_FIELD)}}
    return payload, schema


def allocations(data: dict) -> tuple[dict[str, list[tuple[str, int]]], dict[str, str]]:
    invoice_order = {str(x.get("id")): id_of(x.get("customerOrder")) for x in data["invoiceout"]}
    by_order: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for kind in ("paymentin", "cashin"):
        for payment in data[kind]:
            if payment.get("applicable") is not True:
                continue
            for op in payment.get("operations") or []:
                meta = op.get("meta") or {}
                ref_type = meta.get("type") or ""
                ref_id = id_of(meta)
                oid = ref_id if ref_type == "customerorder" else invoice_order.get(ref_id) if ref_type == "invoiceout" else ""
                linked = amount(op.get("linkedSum"))
                if oid and linked > 0:
                    by_order[oid].append((str(payment.get("moment") or ""), linked))
    for values in by_order.values():
        values.sort(key=lambda x: x[0])
    return by_order, invoice_order


def payment_close(order: dict, by_order: dict[str, list[tuple[str, int]]]) -> tuple[int, str | None]:
    total = amount(order.get("sum"))
    running = 0
    close = None
    for moment, value in by_order.get(str(order.get("id")), []):
        running += value
        if close is None and total > 0 and running >= total:
            close = moment
    return running, close


def order_hash(order: dict) -> str:
    agent = order.get("agent") or {}
    stable_agent = {k: agent.get(k) for k in (
        "id", "name", "phone", "email", "legalFirstName", "legalLastName", "legalTitle",
        "actualAddress", "actualAddressFull",
    )}
    return digest({k: order.get(k) for k in (
        "id", "name", "moment", "updated", "sum", "payedSum", "state",
        "attributes", "positions", "organization", "store", "salesChannel", "description",
    )} | {"agent_identity": stable_agent})


def get_roster() -> dict:
    try:
        return shift_roster._rows(force=True)
    except Exception:
        return {}


def manager_for(moment: str | None, roster: dict) -> tuple[int, str]:
    dt = moscow_dt(moment)
    flags = roster.get(dt.date()) if dt else None
    if flags == (True, False):
        return lib.USER_KRISTINA, "Кристина по графику смены закрывающего платежа"
    if flags == (False, True):
        return lib.USER_TANYA, "Таня по графику смены закрывающего платежа"
    return MAXIM, "Максим назначен технически: подтверждённой смены нет"


def buyer_identity(cp: dict) -> tuple[str, str]:
    company_type = str(cp.get("companyType") or "")
    legal_title = str(cp.get("legalTitle") or "").strip()
    first, last = str(cp.get("legalFirstName") or "").strip(), str(cp.get("legalLastName") or "").strip()
    if company_type == "individual":
        name = str(cp.get("name") or "").strip()
        if len(name.split()) < 2:
            return "", "DEFERRED_BUYER_IDENTITY"
        return name, ""
    corporate = re.search(r"\b(ооо|ип|зао|пао|оао|магазин|бутик|lamoda|опт|компания|салон)\b",
                          str(cp.get("name") or ""), re.IGNORECASE)
    if company_type == "legal" and first and last and not legal_title and not corporate:
        return " ".join((first, last)), "FIX_CONFIRMED_PERSON_TYPE"
    if company_type in ("legal", "entrepreneur"):
        return "", "DEFERRED_BUSINESS_CUSTOMER"
    return "", "DEFERRED_BUYER_IDENTITY"


def unix_time(value: object) -> int | None:
    stamp = moscow_dt(value)
    return int(stamp.timestamp()) if stamp else None


def lead_day(value: object) -> str:
    try:
        return datetime.fromtimestamp(int(value), TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def looks_like_duplicate(lead: dict, order: dict, net_kopecks: int,
                         close_moment: str | None) -> bool:
    number = str(order.get("name") or "").strip().casefold()
    cp_id = id_of(order.get("agent")).casefold()
    if not number or field(lead, FIELDS["number"]).casefold() != number:
        return False
    if field(lead, FIELDS["counterparty_id"]).casefold() != cp_id:
        return False
    expected = rubles_round(net_kopecks)
    if abs(int(lead.get("price") or 0) - expected) > 1:
        return False
    order_day = (moscow_dt(order.get("moment")) or moscow_dt(order.get("created")))
    order_day = order_day.date().isoformat() if order_day else ""
    close_day = moscow_dt(close_moment).date().isoformat() if moscow_dt(close_moment) else ""
    return bool((order_day and lead_day(lead.get("created_at")) == order_day)
                or (close_day and lead_day(lead.get("closed_at")) == close_day))


def compose_order(order: dict) -> tuple[str, int, int, str]:
    rows = ((order.get("positions") or {}).get("rows") or [])
    expected_size = (order.get("positions") or {}).get("meta", {}).get("size")
    if not rows or (expected_size is not None and len(rows) != expected_size):
        return "", 0, 0, "ORDER_POSITIONS_INCOMPLETE"
    lines, total_lines, delivery = [], 0, 0
    for line in rows:
        item = line.get("assortment") or {}
        name = str(item.get("name") or "").strip()
        if not name:
            return "", 0, 0, "ORDER_ITEM_NAME_MISSING"
        qty = Decimal(str(line.get("quantity") or "0"))
        unit = amount(line.get("price"))
        discount = Decimal(str(line.get("discount") or "0"))
        line_sum = int((Decimal(unit) * qty * (Decimal(100) - discount) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        total_lines += line_sum
        if is_delivery(line):
            delivery += line_sum
        quantity = format(qty.normalize(), "f")
        lines.append(f"{name} × {quantity}: {amount_text(line_sum)} ₽" + (f" (скидка {discount}%)" if discount else ""))
    if abs(total_lines - amount(order.get("sum"))) > 1:
        return "", delivery, total_lines, "ORDER_LINES_SUM_MISMATCH"
    composition = "\n".join(lines)
    if len(composition) > 20000:
        return "", delivery, total_lines, "ORDER_COMPOSITION_FIELD_LIMIT"
    return composition, delivery, total_lines, ""


def deal_custom_fields(order: dict, row: dict, composition: str, delivery: int,
                       manager_reason: str) -> list[dict]:
    total = int(row["total_kopecks"])
    paid = int(row["payment_kopecks"])
    payment_enum = ENUM["paid"] if paid >= total else (ENUM["partial"] if paid > 0 else ENUM["unpaid"])
    original_comment = str(order.get("description") or "").strip()
    price = int(row["net_kopecks"])
    rounded = rubles_round(price)
    round_delta = rounded * 100 - price
    notes = [original_comment] if original_comment else []
    notes.append("[Исторический импорт МС→amo 2026-10-15]")
    notes.append(f"Сумма заказа: {amount_text(total)} ₽")
    if row["return_kopecks"]:
        notes.append(f"Подтверждённый возврат: {amount_text(int(row['return_kopecks']))} ₽; вычтен один раз по документу возврата")
    notes.append(f"Сумма сделки: {amount_text(price)} ₽; округление amo до {rounded} ₽; разница округления {round_delta} коп.")
    notes.append(f"Дата создания и статус: дата заказа МойСклад {order.get('moment')}; этап установлен напрямую по плану миграции.")
    notes.append(f"Дата закрытия: {row['close_moment']} ({row['close_basis']}).")
    notes.append(f"Ответственный: {manager_reason}.")
    note = "\n".join(notes)
    if len(note) > 60000:
        raise Stop("Комментарий заказа превышает лимит поля, заказ отложен")
    values = [
        {"field_id": FIELDS["ms_id"], "values": [{"value": str(order["id"])}]},
        {"field_id": FIELDS["number"], "values": [{"value": str(order.get("name") or "")}]},
        {"field_id": FIELDS["ms_url"], "values": [{"value": ms_order_url(str(order["id"]))}]},
        {"field_id": FIELDS["counterparty_id"], "values": [{"value": str(row["counterparty_id"])}]},
        {"field_id": FIELDS["counterparty_name"], "values": [{"value": row["buyer"]}]},
        {"field_id": FIELDS["source"], "values": [{"enum_id": ENUM["source_ms"]}]},
        {"field_id": FIELDS["state"], "values": [{"value": row["state"]}]},
        {"field_id": FIELDS["payment"], "values": [{"enum_id": payment_enum}]},
        {"field_id": FIELDS["items"], "values": [{"value": composition}]},
        {"field_id": FIELDS["comment"], "values": [{"value": note}]},
    ]
    if delivery:
        values.append({"field_id": FIELDS["delivery"], "values": [{"value": float(Decimal(delivery) / 100)}]})
    if row["return_state"] == "partial":
        values.append({"field_id": FIELDS["partial_return"], "values": [{"value": True}]})
    return values


def return_map(data: dict) -> dict[str, list[dict]]:
    demand_order = {str(x.get("id")): id_of(x.get("customerOrder")) for x in data["demand"]}
    result: dict[str, list[dict]] = defaultdict(list)
    for ret in data["salesreturn"]:
        demand_id = id_of(ret.get("demand"))
        oid = demand_order.get(demand_id)
        if oid and ret.get("applicable") is True:
            result[oid].append(ret)
    return result


def return_outflow(data: dict) -> dict[str, int]:
    known_returns = {str(x.get("id")) for x in data["salesreturn"]}
    totals: dict[str, int] = defaultdict(int)
    for kind in ("paymentout", "cashout"):
        for payment in data[kind]:
            if payment.get("applicable") is not True:
                continue
            for op in payment.get("operations") or []:
                rid = id_of(op.get("meta"))
                if rid in known_returns:
                    totals[rid] += amount(op.get("linkedSum"))
    return totals


def is_delivery(line: dict) -> bool:
    assortment = line.get("assortment") or {}
    name = str(assortment.get("name") or "").strip().casefold()
    return ((assortment.get("meta") or {}).get("type") == "service"
            and any(word in name for word in ("достав", "сдэк", "курьер", "почта россии")))


def return_state(order: dict, returns: list[dict]) -> tuple[str, int, str]:
    if not returns:
        return "none", 0, ""
    ordered = defaultdict(Decimal)
    for line in ((order.get("positions") or {}).get("rows") or []):
        if is_delivery(line):
            continue
        aid = id_of(line.get("assortment"))
        if not aid:
            return "deferred", 0, "RETURN_POSITION_ID_MISSING"
        ordered[aid] += Decimal(str(line.get("quantity") or 0))
    returned = defaultdict(Decimal)
    refund = 0
    for ret in returns:
        refund += amount(ret.get("sum"))
        lines = ((ret.get("positions") or {}).get("rows") or [])
        if not lines and (ret.get("positions") or {}).get("meta", {}).get("size", 0):
            return "deferred", 0, "RETURN_POSITIONS_INCOMPLETE"
        for line in lines:
            if is_delivery(line):
                continue
            aid = id_of(line.get("assortment"))
            if not aid or aid not in ordered:
                return "deferred", 0, "RETURN_POSITION_NOT_IN_ORDER"
            returned[aid] += Decimal(str(line.get("quantity") or 0))
    if refund < 0 or refund > amount(order.get("sum")):
        return "deferred", 0, "RETURN_AMOUNT_CONFLICT"
    if amount(order.get("sum")) > 0 and refund >= amount(order.get("sum")):
        return "full", refund, "FULL_AMOUNT_RETURNED"
    if ordered and all(returned[k] >= qty for k, qty in ordered.items()):
        return "full", refund, "ALL_GOODS_RETURNED"
    if not ordered and refund >= amount(order.get("sum")):
        return "full", refund, "FULL_AMOUNT_RETURNED"
    if any(returned.values()) or refund:
        return "partial", refund, "PARTIAL_RETURN_DOCUMENTED"
    return "deferred", 0, "RETURN_SCOPE_UNKNOWN"


def audit_current_state(ms: lib.MS, order: dict) -> str | None:
    """Полная история нужна только для заказа без полной оплаты."""
    session = ms_session(ms)
    pace = [0.0]
    events = ms_rows(session, f"/entity/customerorder/{order['id']}/audit", {"limit": 100}, pace)
    created = [moscow_dt(e.get("moment")) for e in events if e.get("eventType") == "create"]
    if len(created) != 1 or not created[0]:
        return None
    actual_created = moscow_dt(order.get("created"))
    if not actual_created or abs((created[0] - actual_created).total_seconds()) > 2:
        return None
    changes = []
    for event in reversed(events):
        diff = (event.get("diff") or {}).get("state") if isinstance(event.get("diff"), dict) else None
        if diff is None:
            continue
        if not isinstance(diff, dict):
            return None
        stamp = moscow_dt(event.get("moment"))
        if not stamp:
            return None
        changes.append((stamp, id_of(diff.get("oldValue")) or None,
                        id_of(diff.get("newValue")) or None))
    current = state_id(order)
    last_after = None
    last_stamp = created[0]
    for stamp, before, after in changes:
        if stamp < last_stamp:
            return None
        if last_after is not None and before != last_after:
            return None
        last_after, last_stamp = after, stamp
    if last_after is not None and last_after != current:
        return None
    if not changes:
        # A create event without explicit initial state is not enough evidence.
        return None
    if changes[-1][2] != current:
        return None
    return changes[-1][0].isoformat(timespec="seconds")


def all_contacts_index(contacts: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    phones: dict[str, set[str]] = defaultdict(set)
    emails: dict[str, set[str]] = defaultdict(set)
    for contact in contacts:
        if contact.get("is_deleted"):
            continue
        cid = str(contact.get("id"))
        for group in contact.get("custom_fields_values") or []:
            if group.get("field_code") == "PHONE":
                for v in group.get("values") or []:
                    raw = str(v.get("value") or "")
                    digits = re.sub(r"\D", "", raw)
                    normalized = phone_norm(raw)
                    if normalized:
                        phones[normalized].add(cid)
            elif group.get("field_code") == "EMAIL":
                for v in group.get("values") or []:
                    val = email_norm(v.get("value"))
                    if val:
                        emails[val].add(cid)
    return phones, emails


def amo_links(lead: dict) -> set[str]:
    return {linked_order_id(v) for v in field_values(lead, AMO_ORDER_FIELD) if linked_order_id(v)}


def classify(data: dict, ms: lib.MS) -> dict:
    orders = data["orders"]
    payments, invoices = allocations(data)
    returns_by_order = return_map(data)
    outflow_by_return = return_outflow(data)
    roster = get_roster()
    leads = data["leads"]
    uuid_index: dict[str, list[dict]] = defaultdict(list)
    url_index: dict[str, list[dict]] = defaultdict(list)
    by_number_buyer: dict[tuple[str, str], list[dict]] = defaultdict(list)
    contact_ids_by_lead: dict[str, set[str]] = {}
    for lead in leads:
        uid = field(lead, FIELDS["ms_id"]).casefold()
        if uid:
            uuid_index[uid].append(lead)
        for oid in amo_links(lead):
            url_index[oid].append(lead)
        pair = (field(lead, FIELDS["number"]).casefold(), field(lead, FIELDS["counterparty_id"]).casefold())
        if all(pair):
            by_number_buyer[pair].append(lead)
        contact_ids_by_lead[str(lead.get("id"))] = {
            str(x.get("id")) for x in (lead.get("_embedded") or {}).get("contacts", []) if x.get("id")
        }
    phone_index, email_index = all_contacts_index(data["contacts"])
    cp_contact = {str(c.get("id")): c for c in data["contacts"]}
    plan_rows, counts = [], defaultdict(int)
    audit_checks = 0
    audit_cache = status_audit_cache_load()
    for order in orders:
        name = str(order.get("_verified_state_name") or "")
        if not name:
            name = next((n for n, sid in STATUS_IDS.items() if sid == state_id(order)), "")
        total = amount(order.get("sum"))
        paid_recorded = amount(order.get("payedSum"))
        allocated, paid_moment = payment_close(order, payments)
        selected = name in ALWAYS or (name in PAID_REQUIRED and total > 0 and
                                       (paid_recorded >= total or allocated >= total))
        uid = str(order.get("id") or "").casefold()
        signature = order_hash(order)
        if not selected:
            counts["EXCLUDED_NOT_FULLY_PAID"] += 1
            plan_rows.append({"order_id": uid, "source_hash": signature,
                              "number": str(order.get("name") or ""),
                              "category": "EXCLUDED_NOT_FULLY_PAID",
                              "reason": "STATUS_REQUIRES_FULL_PAYMENT", "state": name,
                              "moment": order.get("moment"), "updated": order.get("updated"),
                              "total_kopecks": total, "payment_kopecks": allocated,
                              "payed_sum_kopecks": paid_recorded})
            continue
        link = ms_link(order)
        uuid_matches = uuid_index.get(uid, [])
        url_matches = url_index.get(uid, [])
        if link:
            category, reason = "EXISTS_BACKLINK", "MS_AMO_LINK_NONEMPTY_OWNER_RULE"
            lead_id = ""
            m = re.search(r"/leads/detail/(\d+)(?:[/?#]|$)", link)
            if m:
                lead_id = m.group(1)
            if uuid_matches and (len(uuid_matches) != 1 or (lead_id and str(uuid_matches[0].get("id")) != lead_id)):
                category, reason = "CONFLICT", "BACKLINK_AND_AMO_UUID_DISAGREE"
        elif len(uuid_matches) > 1 or len(url_matches) > 1:
            category, reason, lead_id = "CONFLICT", "MULTIPLE_AMO_MATCHES", ""
        elif uuid_matches or url_matches:
            match = (uuid_matches or url_matches)[0]
            category, reason, lead_id = "EXISTS_AMO_ID", "AMO_HAS_MS_UUID_OR_URL", str(match.get("id"))
        else:
            category, reason, lead_id = "CREATE", "", ""

        order_returns = returns_by_order.get(uid, [])
        ret_state, ret_sum, ret_reason = return_state(order, order_returns)
        for ret in order_returns:
            linked_out = outflow_by_return.get(str(ret.get("id")), 0)
            if linked_out and linked_out != amount(ret.get("sum")):
                ret_state, ret_reason = "deferred", "RETURN_OUTFLOW_AMOUNT_CONFLICT"
        if ret_state == "full":
            category, reason, lead_id = "EXCLUDED_FULL_RETURN", ret_reason, ""
        elif ret_state == "deferred":
            category, reason, lead_id = "DEFERRED", ret_reason, ""

        if category == "CREATE":
            if total <= 0:
                category, reason = "DEFERRED", "ZERO_OR_NEGATIVE_ORDER_SUM"
            elif abs(paid_recorded - allocated) > 1:
                category, reason = "DEFERRED", "PAYMENT_SUM_CONFLICT"
            elif name in PAID_REQUIRED and (allocated < total or not paid_moment):
                category, reason = "DEFERRED", "FULL_PAYMENT_NOT_DOCUMENTED"
            elif allocated >= total and not paid_moment:
                category, reason = "DEFERRED", "FULL_PAYMENT_DATE_MISSING"

        cp = order.get("agent") if isinstance(order.get("agent"), dict) else {}
        cp_id = str(cp.get("id") or id_of(cp))
        buyer, buyer_reason = buyer_identity(cp)
        fix_company_type = buyer_reason == "FIX_CONFIRMED_PERSON_TYPE"
        if category == "CREATE" and buyer_reason and not fix_company_type:
            category, reason = "DEFERRED", buyer_reason
        if category == "CREATE" and not cp_id:
            category, reason = "DEFERRED", "COUNTERPARTY_ID_MISSING"

        close_moment = paid_moment if allocated >= total and total > 0 else None
        close_basis = "дата полного связанного платежа" if close_moment else ""

        if category == "CREATE":
            contact_id = ""
            phone = phone_norm(cp.get("phone"), cp.get("actualAddressFull") or cp.get("actualAddress"))
            email = email_norm(cp.get("email"))
            pids, eids = phone_index.get(phone, set()) if phone else set(), email_index.get(email, set()) if email else set()
            if len(pids) > 1 or len(eids) > 1:
                category, reason = "DEFERRED", "AMBIGUOUS_CONTACT_MATCH"
            elif pids and eids and pids != eids:
                category, reason = "DEFERRED", "PHONE_EMAIL_POINT_TO_DIFFERENT_CONTACTS"
            elif pids or eids:
                contact_id = next(iter(pids or eids))
                if cp_contact.get(contact_id, {}).get("is_deleted"):
                    category, reason = "DEFERRED", "MATCHED_CONTACT_DELETED"
            elif not phone and not email:
                category, reason = "DEFERRED", "BUYER_HAS_NO_UNIQUE_PHONE_OR_EMAIL"
            else:
                contact_id = ""
            if category == "CREATE" and not phone and not email:
                category, reason = "DEFERRED", "BUYER_CONTACT_DATA_INVALID"
        else:
            phone, email, contact_id = "", "", ""

        if category == "CREATE":
            net = total - ret_sum
            if net < 0:
                category, reason = "DEFERRED", "RETURN_EXCEEDS_ORDER_SUM"
        else:
            net = total - ret_sum

        pair = (str(order.get("name") or "").casefold(), cp_id.casefold())
        possible = by_number_buyer.get(pair, [])
        if category == "CREATE" and any(looks_like_duplicate(x, order, net, close_moment) for x in possible):
            category, reason = "CONFLICT", "POSSIBLE_DEAL_WITH_SAME_ORDER_NUMBER_BUYER_AMOUNT_AND_DATE"
        if category == "CREATE" and contact_id:
            linked_leads = [lead for lead in leads
                            if contact_id in contact_ids_by_lead.get(str(lead.get("id")), set())]
            if any(abs(int(x.get("price") or 0) - rubles_round(net)) <= 1
                   and ((lead_day(x.get("created_at")) == (moscow_dt(order.get("moment")).date().isoformat() if moscow_dt(order.get("moment")) else ""))
                        or (lead_day(x.get("closed_at")) == (moscow_dt(close_moment).date().isoformat() if moscow_dt(close_moment) else "")))
                   for x in linked_leads):
                category, reason = "CONFLICT", "POSSIBLE_DEAL_WITH_SAME_CONTACT_AMOUNT_AND_DATE"

        composition, delivery_kopecks, _line_sum, item_issue = compose_order(order)
        if category == "CREATE" and item_issue:
            category, reason = "DEFERRED", item_issue
        if category == "CREATE":
            rate = order.get("rate")
            if isinstance(rate, dict) and rate.get("value") not in (None, 1, 1.0, "1", "1.0"):
                category, reason = "DEFERRED", "FOREIGN_CURRENCY_OR_NONDEFAULT_RATE"

        if category == "CREATE" and not close_moment and name in ALWAYS:
            if audit_checks % 25 == 0:
                print(f"Проверяю историю статусов: {audit_checks} заказов…", flush=True)
            if uid in audit_cache:
                close_moment = audit_cache[uid]
            else:
                close_moment = audit_current_state(ms, order)
                audit_cache[uid] = close_moment
                status_audit_cache_save(audit_cache)
            audit_checks += 1
            if close_moment:
                close_basis = "последний подтверждённый переход в текущий статус МойСклад"
        if category == "CREATE" and not close_moment:
            category, reason = "DEFERRED", "NO_VERIFIED_CLOSE_DATE"
        if category == "CREATE":
            created_dt, close_dt = moscow_dt(order.get("moment")), moscow_dt(close_moment)
            now_dt = datetime.now(TZ)
            if not created_dt or created_dt > now_dt:
                category, reason = "DEFERRED", "INVALID_ORDER_DATE"
            elif not close_dt or close_dt < created_dt or close_dt > now_dt:
                category, reason = "DEFERRED", "INVALID_CLOSE_DATE"

        counts[category] += 1
        plan_rows.append({
                "order_id": uid, "source_hash": signature, "number": str(order.get("name") or ""),
                "category": category, "reason": reason, "state": name,
                "moment": order.get("moment"), "updated": order.get("updated"),
                "total_kopecks": total, "payment_kopecks": allocated,
                "payed_sum_kopecks": paid_recorded, "payment_moment": paid_moment,
                "return_state": ret_state, "return_kopecks": ret_sum,
                "net_kopecks": net, "counterparty_id": cp_id,
                "counterparty_type": cp.get("companyType"), "buyer": buyer,
                "fix_company_type": fix_company_type,
                "composition": composition, "delivery_kopecks": delivery_kopecks,
                "phone": phone, "email": email, "contact_id": contact_id,
                "lead_id": lead_id, "link": link, "close_moment": close_moment,
                "close_basis": close_basis,
            })
    return {"captured_at": data["captured_at"], "counts": dict(counts),
            "state_counts": data["state_counts"], "all_orders_count": data["all_orders_count"], "rows": plan_rows,
            "schema_checked": True}


def journal_load() -> dict:
    if JOURNAL.exists():
        try:
            value = json.loads(JOURNAL.read_text(encoding="utf-8"))
        except Exception as exc:
            raise Stop("Журнал повреждён, автоматическое продолжение запрещено") from exc
        if not isinstance(value, dict) or not isinstance(value.get("rows"), dict):
            raise Stop("Формат журнала не распознан")
        return value
    return {"started_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "plan_sha256": "", "rows": {}, "counterparty_contacts": {}, "unknown_write": None}


def amo_read(amo: lib.Amo, path: str) -> tuple[int, dict]:
    try:
        status, body = amo.req("GET", path)
    except Exception as exc:
        raise Stop(f"Чтение amoCRM не подтверждено: {type(exc).__name__}") from exc
    if status not in (200, 204):
        raise Stop(f"Чтение amoCRM: HTTP {status}")
    return status, body if isinstance(body, dict) else {}


def amo_search_pages(amo: lib.Amo, entity: str, query: str, key: str) -> list[dict]:
    rows = []
    encoded = urllib.parse.quote(query)
    for page in range(1, 501):
        status, body = amo.req("GET", f"/api/v4/{entity}?query={encoded}&limit=250&page={page}")
        if status == 204:
            return rows
        if status != 200:
            raise Stop(f"Точный поиск amo {entity}: HTTP {status}")
        chunk = (body.get("_embedded") or {}).get(key)
        if not isinstance(chunk, list):
            raise Stop(f"Неполный точный поиск amo {entity}")
        rows.extend(chunk)
        if len(chunk) < 250:
            ids = [str(x.get("id")) for x in rows]
            if len(set(ids)) != len(ids):
                raise Stop(f"Повтор ID при точном поиске amo {entity}")
            return rows
        time.sleep(0.15)
    raise Stop(f"Предел страниц точного поиска amo {entity}")


def exact_leads(amo: lib.Amo, order_id: str) -> list[dict]:
    rows = amo_search_pages(amo, "leads", order_id, "leads")
    return [x for x in rows if field(x, FIELDS["ms_id"]).casefold() == order_id.casefold()
            or order_id.casefold() in amo_links(x)]


def exact_contacts(amo: lib.Amo, phone: str, email: str) -> list[dict]:
    found = {}
    queries = []
    if phone:
        digits = re.sub(r"\D", "", phone)
        if digits:
            queries.append(digits[-10:])
    if email:
        queries.append(email)
    for query in queries:
        for contact in amo_search_pages(amo, "contacts", query, "contacts"):
            if contact.get("is_deleted"):
                continue
            numbers, emails = set(), set()
            for group in contact.get("custom_fields_values") or []:
                if group.get("field_code") == "PHONE":
                    numbers |= {phone_norm(v.get("value")) for v in group.get("values") or []}
                elif group.get("field_code") == "EMAIL":
                    emails |= {email_norm(v.get("value")) for v in group.get("values") or []}
            if (phone and phone in numbers) or (email and email in emails):
                found[str(contact.get("id"))] = contact
    return list(found.values())


def api_write(session: requests.Session, method: str, url: str, body: object,
              headers: dict | None = None, pace: list[float] | None = None) -> tuple[int | None, dict, str]:
    if pace is not None:
        pause = max(0.0, 1.0 - (time.monotonic() - pace[0]))
        if pause:
            time.sleep(pause)
    try:
        response = session.request(method, url, json=body, headers=headers or {}, timeout=(10, 60))
        if pace is not None:
            pace[0] = time.monotonic()
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        return response.status_code, data if isinstance(data, dict) else {}, ""
    except requests.RequestException as exc:
        if pace is not None:
            pace[0] = time.monotonic()
        return None, {}, type(exc).__name__


def amo_session(amo: lib.Amo) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": "Bearer " + amo.token,
                      "Content-Type": "application/json",
                      "Accept": "application/hal+json"})
    return s


def save_journal(journal: dict) -> None:
    atomic_json(JOURNAL, journal)


def confirm_contact(amo: lib.Amo, contact_id: str, row: dict) -> dict:
    status, contact = amo_read(amo, f"/api/v4/contacts/{contact_id}")
    if status != 200 or str(contact.get("id")) != str(contact_id):
        raise Stop("Контакт не перечитался после записи")
    if str(contact.get("name") or "").strip() != str(row.get("buyer") or "").strip():
        raise Stop("Имя контакта после записи не совпало с покупателем заказа")
    values = []
    for group in contact.get("custom_fields_values") or []:
        values.extend(v.get("value") for v in group.get("values") or []
                      if group.get("field_code") in ("PHONE", "EMAIL"))
    got_phone = {phone_norm(x) for x in values if x and (str(x).startswith("+") or len(re.sub(r"\D", "", str(x))) >= 10)}
    got_email = {email_norm(x) for x in values if x and "@" in str(x)}
    if row.get("phone") and row["phone"] not in got_phone:
        raise Stop("Созданный контакт не содержит ожидаемый точный телефон")
    if row.get("email") and row["email"] not in got_email:
        raise Stop("Созданный контакт не содержит ожидаемый email")
    return contact


def confirm_lead(amo: lib.Amo, lead_id: str, order: dict, row: dict,
                 contact_id: str, expected_payload: dict) -> dict:
    status, lead = amo_read(amo, f"/api/v4/leads/{lead_id}?with=contacts")
    if status != 200 or str(lead.get("id")) != str(lead_id):
        raise Stop("Сделка не перечиталась после записи")
    if int(lead.get("pipeline_id") or 0) != PIPELINE or int(lead.get("status_id") or 0) != WON:
        raise Stop("Новая сделка не оказалась в целевой воронке/этапе")
    if int(lead.get("price") or 0) != expected_payload["price"]:
        raise Stop("Сумма сделки не совпала с планом")
    if str(lead.get("name") or "") != str(expected_payload.get("name") or ""):
        raise Stop("Название сделки не совпало с планом")
    if int(lead.get("source_id") or 0) != SOURCE_ID:
        raise Stop("Источник сделки не совпал с МойСклад")
    if int(lead.get("responsible_user_id") or 0) != int(expected_payload["responsible_user_id"]):
        raise Stop("Ответственный сделки не совпал с планом")
    for item in expected_payload["custom_fields_values"]:
        fid = item["field_id"]
        actual = next((x for x in lead.get("custom_fields_values") or []
                       if str(x.get("field_id")) == str(fid)), None)
        if actual is None:
            raise Stop(f"Поле amo {fid} отсутствует после записи")
        def normalized_values(values: list[dict]) -> list[tuple[str, str]]:
            result = []
            for value in values:
                if "enum_id" in value:
                    result.append(("enum_id", str(value.get("enum_id"))))
                elif "value" in value:
                    result.append(("value", str(value.get("value"))))
            return result
        if normalized_values(actual.get("values") or []) != normalized_values(item.get("values") or []):
            raise Stop(f"Значение поля amo {fid} не совпало с планом")
    if int(lead.get("created_at") or 0) != expected_payload["created_at"]:
        raise Stop("Дата создания сделки не совпала с датой заказа")
    if int(lead.get("closed_at") or 0) != expected_payload["closed_at"]:
        raise Stop("Дата закрытия сделки не совпала с подтверждённой датой")
    linked = {str(x.get("id")) for x in (lead.get("_embedded") or {}).get("contacts", []) if x.get("id")}
    if contact_id not in linked:
        raise Stop("Сделка не связана с выбранным контактом")
    return lead


def make_lead_payload(amo: lib.Amo, order: dict, row: dict,
                      contact_id: str, roster: dict) -> dict:
    created_at = unix_time(order.get("moment"))
    closed_at = unix_time(row.get("close_moment"))
    if not created_at or not closed_at:
        raise Stop("Нет точной даты создания/закрытия")
    manager_id, manager_reason = manager_for(
        row.get("payment_moment") if row.get("payment_kopecks", 0) >= row.get("total_kopecks", 0)
        else None, roster)
    row["close_basis"] = row.get("close_basis") or "подтверждённый переход статуса МойСклад"
    fields = deal_custom_fields(order, row, row["composition"],
                                row.get("delivery_kopecks", 0), manager_reason)
    body = {
        "name": f"Заказ МойСклад №{order.get('name')}",
        "price": rubles_round(int(row["net_kopecks"])),
        "pipeline_id": PIPELINE, "status_id": WON,
        "source_id": SOURCE_ID, "responsible_user_id": manager_id,
        "created_at": created_at, "closed_at": closed_at,
        "request_id": "ms-order:" + row["order_id"],
        "custom_fields_values": fields,
        "_embedded": {"contacts": [{"id": int(contact_id), "is_main": True}]},
    }
    return body


def _set_entry(journal: dict, order_id: str, **kwargs) -> dict:
    item = journal["rows"].setdefault(order_id, {"status": "PLANNED"})
    item.update(kwargs)
    item["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_journal(journal)
    return item


def fresh_order(ms_session_: requests.Session, order_id: str, pace: list[float]) -> dict:
    return api_get(ms_session_, lib.MS_BASE + f"/entity/customerorder/{order_id}",
                   {"expand": "positions,positions.assortment,agent,state"}, pace)


def apply_plan(plan: dict, plan_hash: str, maximum: int, resume: bool = False) -> dict:
    ms, amo = lib.MS(), assign_shift.amo_client()
    mss, amos = ms_session(ms), amo_session(amo)
    # API reads use the established client; writes use a no-retry transport.
    roster = get_roster()
    journal = journal_load()
    if journal.get("unknown_write"):
        raise Stop("Предыдущая неопределённая запись требует ручного сверочного разбора")
    if journal.get("plan_sha256") and journal["plan_sha256"] != plan_hash:
        raise Stop("Журнал относится к другому плану")
    journal["plan_sha256"] = plan_hash
    save_journal(journal)
    create_rows = sorted((r for r in plan["rows"] if r["category"] == "CREATE"),
                         key=lambda x: (x.get("moment") or "", x["order_id"]))
    done = sum(1 for rid, x in journal["rows"].items() if x.get("status") == "VERIFIED")
    candidates = [r for r in create_rows if journal["rows"].get(r["order_id"], {}).get("status") != "VERIFIED"]
    if not candidates:
        return {"verified_before": done, "processed": 0, "remaining": 0}
    candidates = candidates[:maximum]
    print("Повторно сверяю текущие оплаты, возвраты, сделки и контакты перед этой партией…", flush=True)
    current_data, _ = collect_current(ms, amo)
    current_orders = {str(x.get("id")).casefold(): x for x in current_data["orders"]}
    current_payments, _ = allocations(current_data)
    current_returns = return_map(current_data)
    current_outflow = return_outflow(current_data)
    current_by_order: dict[str, dict] = {}
    for row in candidates:
        oid = row["order_id"]
        order = current_orders.get(oid.casefold())
        if not order or order_hash(order) != row["source_hash"]:
            raise Stop("План устарел: заказ изменился или вышел из разрешённых статусов")
        paid_total, paid_moment = payment_close(order, current_payments)
        if paid_total != row["payment_kopecks"] or paid_moment != row["payment_moment"]:
            raise Stop("План устарел: связанная полная оплата изменилась")
        ret_state, ret_sum, _ = return_state(order, current_returns.get(oid, []))
        for ret in current_returns.get(oid, []):
            linked_out = current_outflow.get(str(ret.get("id")), 0)
            if linked_out and linked_out != amount(ret.get("sum")):
                ret_state = "deferred"
        if ret_state != row["return_state"] or ret_sum != row["return_kopecks"]:
            raise Stop("План устарел: возврат заказа изменился")
        current_by_order[oid] = order
    writer_pace = [0.0]
    ms_pace = [0.0]
    processed = 0
    for row in candidates:
        oid = row["order_id"]
        entry = journal["rows"].setdefault(oid, {"status": "PLANNED"})
        recovered_lead_id = ""
        if entry.get("status") in ("CONTACT_WRITE_INTENT", "LEAD_WRITE_INTENT", "BACKLINK_WRITE_INTENT",
                                    "COUNTERPARTY_WRITE_INTENT"):
            # Recovery checks every possible target before any retry.
            found = exact_leads(amo, oid)
            if len(found) > 1:
                raise Stop("После прошлой записи найдено несколько сделок с UUID заказа")
            if found:
                recovered_lead_id = str(found[0]["id"])
                entry["lead_id"] = recovered_lead_id
                if entry.get("status") != "CONTACT_WRITE_INTENT":
                    entry["status"] = "LEAD_RECOVERED"
                    save_journal(journal)
            elif entry.get("status") == "CONTACT_WRITE_INTENT":
                found_contacts = exact_contacts(amo, row.get("phone", ""), row.get("email", ""))
                if len(found_contacts) != 1:
                    journal["unknown_write"] = {"order_id": oid, "kind": "contact_recovery",
                                                "matches": len(found_contacts)}
                    save_journal(journal)
                    raise Stop("После незавершённой записи нельзя однозначно восстановить контакт")
                contact_id = str(found_contacts[0]["id"])
                journal["counterparty_contacts"][row["counterparty_id"]] = contact_id
                _set_entry(journal, oid, status="CONTACT_VERIFIED", contact_id=contact_id)
            elif entry.get("status") == "COUNTERPARTY_WRITE_INTENT":
                cp_detail = api_get(mss, lib.MS_BASE + f"/entity/counterparty/{row['counterparty_id']}",
                                    last_call=ms_pace)
                if cp_detail.get("companyType") != "individual":
                    journal["unknown_write"] = {"order_id": oid, "kind": "companyType_recovery"}
                    save_journal(journal)
                    raise Stop("После прерванного исправления типа контрагента состояние не подтверждено")
                _set_entry(journal, oid, status="COUNTERPARTY_VERIFIED")
            else:
                journal["unknown_write"] = {"order_id": oid, "status": entry["status"]}
                save_journal(journal)
                raise Stop("Результат прошлой записи сделки/ссылки не удалось однозначно восстановить")

        fresh = fresh_order(mss, oid, ms_pace)
        if order_hash(fresh) != row["source_hash"]:
            raise Stop("Источник изменился после плана; остановлен до следующей записи")
        if order_hash(fresh) != order_hash(current_by_order[oid]):
            raise Stop("Заказ изменился уже после повторной полной сверки")
        if ms_link(fresh):
            if recovered_lead_id and ms_link(fresh) == amo_lead_url(amo, recovered_lead_id):
                contact_id = str(entry.get("contact_id") or journal["counterparty_contacts"].get(row["counterparty_id"]) or "")
                lead_payload = entry.get("lead_payload") or {}
                if not contact_id or not lead_payload:
                    raise Stop("Восстановленной сделке не хватает зафиксированного payload для проверки")
                confirmed = confirm_lead(amo, recovered_lead_id, fresh, row, contact_id, lead_payload)
                if field(confirmed, FIELDS["ms_url"]) != ms_order_url(oid):
                    raise Stop("Восстановленная сделка содержит другую ссылку на заказ")
                _set_entry(journal, oid, status="VERIFIED", lead_id=recovered_lead_id,
                           contact_id=contact_id, backlink=ms_link(fresh),
                           verified_at=datetime.now(TZ).isoformat(timespec="seconds"))
                processed += 1
                continue
            if recovered_lead_id:
                raise Stop("Ссылка МойСклад после прошлой записи указывает на другую сделку")
            _set_entry(journal, oid, status="EXISTS_BACKLINK", backlink=ms_link(fresh))
            continue
        matches = exact_leads(amo, oid)
        if matches:
            if recovered_lead_id and len(matches) == 1 and str(matches[0].get("id")) == recovered_lead_id:
                pass
            elif len(matches) == 1:
                _set_entry(journal, oid, status="EXISTS_AMO_ID", lead_id=str(matches[0]["id"]))
                continue
            else:
                raise Stop("Свежая проверка нашла несколько сделок с UUID заказа")
        cp = fresh.get("agent") or {}
        cp_id = row["counterparty_id"]
        if str(cp.get("id") or id_of(cp)) != cp_id:
            raise Stop("Контрагент заказа изменился после плана")
        if row.get("fix_company_type"):
            cp_detail = api_get(mss, lib.MS_BASE + f"/entity/counterparty/{cp_id}", last_call=ms_pace)
            if cp_detail.get("companyType") == "individual":
                pass
            elif cp_detail.get("companyType") == "legal" and cp_detail.get("legalFirstName") and cp_detail.get("legalLastName") and not cp_detail.get("legalTitle"):
                _set_entry(journal, oid, status="COUNTERPARTY_WRITE_INTENT", counterparty_snapshot=cp_detail)
                code, body, error = api_write(mss, "PUT", lib.MS_BASE + f"/entity/counterparty/{cp_id}",
                                              {"companyType": "individual"}, pace=writer_pace)
                check = api_get(mss, lib.MS_BASE + f"/entity/counterparty/{cp_id}", last_call=ms_pace)
                if check.get("companyType") != "individual":
                    if code is None:
                        journal["unknown_write"] = {"order_id": oid, "kind": "companyType", "error": error}
                        save_journal(journal)
                        raise Stop("Тип контрагента после записи не подтверждён")
                    raise Stop(f"Не удалось исправить тип физлица: HTTP {code}")
                _set_entry(journal, oid, status="COUNTERPARTY_VERIFIED")
            else:
                _set_entry(journal, oid, status="DEFERRED", reason="COUNTERPARTY_CHANGED_OR_NOT_PERSON")
                continue
        else:
            if cp.get("companyType") != "individual":
                _set_entry(journal, oid, status="DEFERRED", reason="COUNTERPARTY_TYPE_CHANGED")
                continue

        phone, email = row.get("phone", ""), row.get("email", "")
        contacts_now = exact_contacts(amo, phone, email)
        if len(contacts_now) > 1:
            _set_entry(journal, oid, status="DEFERRED", reason="CONTACT_BECAME_AMBIGUOUS")
            continue
        if row.get("contact_id") and contacts_now and str(contacts_now[0]["id"]) != str(row["contact_id"]):
            raise Stop("Контакт изменился после плана; требуется повторный полный аудит")
        if row.get("contact_id"):
            if not contacts_now:
                raise Stop("Контакт из полного индекса не находится свежим поиском")
            contact_id = str(contacts_now[0]["id"])
            confirm_contact(amo, contact_id, row)
        elif cp_id in journal["counterparty_contacts"]:
            contact_id = str(journal["counterparty_contacts"][cp_id])
            confirm_contact(amo, contact_id, row)
        elif contacts_now:
            contact_id = str(contacts_now[0]["id"])
            confirm_contact(amo, contact_id, row)
            journal["counterparty_contacts"][cp_id] = contact_id
            save_journal(journal)
        else:
            contact_payload = {"name": row["buyer"], "responsible_user_id": MAXIM,
                               "request_id": "ms-counterparty:" + cp_id,
                               "custom_fields_values": []}
            if phone:
                contact_payload["custom_fields_values"].append({"field_code": "PHONE", "values": [{"value": phone}]})
            if email:
                contact_payload["custom_fields_values"].append({"field_code": "EMAIL", "values": [{"value": email}]})
            _set_entry(journal, oid, status="CONTACT_WRITE_INTENT", contact_payload=contact_payload)
            code, body, error = api_write(amos, "POST", amo.base + "/api/v4/contacts", [contact_payload], pace=writer_pace)
            embedded = ((body.get("_embedded") or {}).get("contacts") or []) if isinstance(body, dict) else []
            if code in (200, 201) and len(embedded) == 1 and embedded[0].get("id"):
                contact_id = str(embedded[0]["id"])
            else:
                recovered = exact_contacts(amo, phone, email)
                if len(recovered) == 1:
                    contact_id = str(recovered[0]["id"])
                elif code is None or code >= 500:
                    journal["unknown_write"] = {"order_id": oid, "kind": "contact", "error": error or code}
                    save_journal(journal)
                    raise Stop("Результат создания контакта не удалось однозначно восстановить")
                else:
                    _set_entry(journal, oid, status="DEFERRED", reason=f"CONTACT_CREATE_HTTP_{code}")
                    continue
            confirm_contact(amo, contact_id, row)
            journal["counterparty_contacts"][cp_id] = contact_id
            _set_entry(journal, oid, status="CONTACT_VERIFIED", contact_id=contact_id)

        _set_entry(journal, oid, status="CONTACT_VERIFIED", contact_id=contact_id)
        if recovered_lead_id:
            lead_id = recovered_lead_id
            lead_payload = entry.get("lead_payload") or {}
            if not lead_payload or str(entry.get("contact_id") or "") != contact_id:
                raise Stop("Восстановленной сделке не хватает зафиксированного payload/контакта")
            confirm_lead(amo, lead_id, fresh, row, contact_id, lead_payload)
        else:
            lead_payload = make_lead_payload(amo, fresh, row, contact_id, roster)
            _set_entry(journal, oid, status="LEAD_WRITE_INTENT", contact_id=contact_id,
                       lead_payload=lead_payload)
            code, body, error = api_write(amos, "POST", amo.base + "/api/v4/leads", [lead_payload], pace=writer_pace)
            embedded = ((body.get("_embedded") or {}).get("leads") or []) if isinstance(body, dict) else []
            if code in (200, 201) and len(embedded) == 1 and embedded[0].get("id"):
                lead_id = str(embedded[0]["id"])
            else:
                recovered = exact_leads(amo, oid)
                if len(recovered) == 1:
                    lead_id = str(recovered[0]["id"])
                elif code is None or code >= 500:
                    journal["unknown_write"] = {"order_id": oid, "kind": "lead", "error": error or code}
                    save_journal(journal)
                    raise Stop("Результат создания сделки не удалось однозначно восстановить")
                else:
                    _set_entry(journal, oid, status="DEFERRED", reason=f"LEAD_CREATE_HTTP_{code}")
                    continue
        _set_entry(journal, oid, status="LEAD_VERIFIED", contact_id=contact_id, lead_id=lead_id)
        confirmed_lead = confirm_lead(amo, lead_id, fresh, row, contact_id, lead_payload)

        fresh_after = fresh_order(mss, oid, ms_pace)
        if ms_link(fresh_after):
            raise Stop("После создания сделки в МойСклад уже появилась посторонняя ссылка")
        attrs_now = fresh_after.get("attributes") or []
        found_attr = False
        for item in attrs_now:
            if str(item.get("id") or id_of(item)) == MS_AMO_ATTR:
                item["value"] = amo_lead_url(amo, lead_id)
                found_attr = True
                break
        if not found_attr:
            metadata = api_get(mss, lib.MS_BASE + "/entity/customerorder/metadata/attributes", last_call=ms_pace)
            attr_rows = metadata.get("rows")
            if not isinstance(attr_rows, list) or not any(str(x.get("id")) == MS_AMO_ATTR for x in attr_rows):
                raise Stop("Атрибут обратной ссылки отсутствует в метаданных заказа")
            attrs_now.append({"id": MS_AMO_ATTR,
                              "meta": {"href": f"{lib.MS_BASE}/entity/customerorder/metadata/attributes/{MS_AMO_ATTR}",
                                       "type": "attributemetadata", "mediaType": "application/json"},
                              "value": amo_lead_url(amo, lead_id)})
        _set_entry(journal, oid, status="BACKLINK_WRITE_INTENT", lead_id=lead_id,
                   contact_id=contact_id, backlink=amo_lead_url(amo, lead_id))
        code, body, error = api_write(mss, "PUT", lib.MS_BASE + f"/entity/customerorder/{oid}",
                                      {"attributes": attrs_now},
                                      headers={"X-Lognex-WebHook-Disable": "true"}, pace=writer_pace)
        after = fresh_order(mss, oid, ms_pace)
        got_link = ms_link(after)
        if got_link != amo_lead_url(amo, lead_id):
            if code is None:
                journal["unknown_write"] = {"order_id": oid, "kind": "backlink", "error": error}
                save_journal(journal)
            raise Stop(f"Обратная ссылка после записи не подтверждена (HTTP {code})")
        read_lead = confirm_lead(amo, lead_id, after, row, contact_id, lead_payload)
        if field(read_lead, FIELDS["ms_url"]) != ms_order_url(oid):
            raise Stop("Ссылка из сделки ведёт не на текущий заказ МойСклад")
        _set_entry(journal, oid, status="VERIFIED", lead_id=lead_id, contact_id=contact_id,
                   backlink=got_link, verified_at=datetime.now(TZ).isoformat(timespec="seconds"))
        processed += 1
    done = sum(1 for item in journal["rows"].values() if item.get("status") == "VERIFIED")
    return {"verified_before": done - processed, "processed": processed,
            "verified_total": done, "remaining_create_rows": max(0, len(create_rows) - done)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply an audited plan")
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--max-orders", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reuse-raw", action="store_true",
                        help="reclassify the protected read-only source snapshot without refetching")
    args = parser.parse_args()
    private_storage()
    with LOCK.open("a+") as lockfile:
        os.chmod(LOCK, 0o600)
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Stop("Уже работает другой экземпляр переноса") from exc
        if not args.apply:
            if args.reuse_raw:
                if not RAW_CACHE.exists():
                    raise Stop("Защищённый исходный снимок для повторной классификации не найден")
                ms = lib.MS()
                data = json.loads(RAW_CACHE.read_text(encoding="utf-8"))
            else:
                ms, amo = lib.MS(), assign_shift.amo_client()
                print("Снимаю полный исходный индекс МойСклад и amoCRM…", flush=True)
                data, _schema = collect_current(ms, amo)
                atomic_json(RAW_CACHE, data)
                print("Защищённый исходный срез сохранён локально для сверки.", flush=True)
            result = classify(data, ms)
            atomic_json(args.plan, result)
            file_hash = hashlib.sha256(args.plan.read_bytes()).hexdigest()
            print(json.dumps({"plan": str(args.plan), "sha256": file_hash,
                              "counts": result["counts"], "state_counts": result["state_counts"],
                              "captured_at": result["captured_at"]}, ensure_ascii=False))
            return
        if not args.sha256:
            raise Stop("Для apply обязателен SHA256 плана")
        if args.max_orders < 1 or args.max_orders > 25:
            raise Stop("Размер одного запуска должен быть от 1 до 25 заказов")
        raw = args.plan.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.sha256:
            raise Stop("SHA256 плана не совпал")
        plan = json.loads(raw)
        if not isinstance(plan, dict) or not isinstance(plan.get("rows"), list) or not plan.get("schema_checked"):
            raise Stop("Формат плана/проверка схемы не подтверждены")
        journal = journal_load()
        if journal.get("rows") and not args.resume:
            raise Stop("Для продолжения существующего журнала требуется --resume")
        if args.resume and journal.get("plan_sha256") and journal["plan_sha256"] != args.sha256:
            raise Stop("SHA256 нового плана отличается от журнала переноса")
        result = apply_plan(plan, args.sha256, args.max_orders, resume=args.resume)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Stop as exc:
        print(f"ОСТАНОВКА: {exc}", file=sys.stderr)
        raise SystemExit(2)
