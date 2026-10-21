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
import shutil
import sys
import time
import urllib.parse
import urllib.request
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
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
REPORT = WORK / "2my-ms-amo-backfill-verification.local.json"
TERMINAL = {"VERIFIED", "DEFERRED", "EXISTS_BACKLINK", "EXISTS_AMO_ID", "QUARANTINED"}
_PACE = {"ms": 0.0, "amo": 0.0}
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


class Stop(RuntimeError):
    pass


class Temporary(Stop):
    """Transport is unavailable; never translate this to an empty index."""


class PacedSession(requests.Session):
    def __init__(self, system: str):
        super().__init__()
        self.system = system

    def request(self, *args, **kwargs):
        time.sleep(max(0.0, 1.0 - (time.monotonic() - _PACE[self.system])))
        try:
            return super().request(*args, **kwargs)
        finally:
            _PACE[self.system] = time.monotonic()


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + ".next")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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
                raise Stop("Обязательный GET МойСклад вернул пустой ответ")
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
                raise Temporary(f"Обязательный GET не подтверждён: {type(exc).__name__}") from exc
            time.sleep(min(60.0, 2.0 ** (attempt + 1)))
    raise Temporary("Обязательный GET исчерпал повторы")


def ms_session(ms: lib.MS) -> requests.Session:
    s = PacedSession("ms")
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


def amo_request(amo: lib.Amo, method: str, path: str) -> tuple[int, dict]:
    if method != "GET":
        raise Stop("Повторы amo_request разрешены только для GET")
    last_error = ""
    for attempt in range(6):
        try:
            response = amo_session(amo).get(amo.base + path, timeout=(10, 60))
            status = response.status_code
            body = response.json() if response.content else {}
        except Exception as exc:
            last_error = type(exc).__name__
            if attempt == 5:
                raise Temporary(f"Чтение amo не подтверждено после повторов: {last_error}") from exc
            print(f"Повтор чтения amo после временной ошибки (попытка {attempt + 2}/6)…", flush=True)
            time.sleep(min(30.0, 2.0 ** attempt))
            continue
        if status in (429, 500, 502, 503, 504):
            if attempt == 5:
                raise Temporary(f"Чтение amo не подтверждено: HTTP {status}")
            print(f"Повтор чтения amo после HTTP {status} (попытка {attempt + 2}/6)…", flush=True)
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(60.0, max(1.0, float(retry_after)))
            except ValueError:
                delay = min(30.0, 2.0 ** attempt)
            time.sleep(delay)
            continue
        return status, body if isinstance(body, dict) else {}
    raise Temporary(f"Чтение amo не подтверждено: {last_error or 'неизвестная ошибка'}")


def amo_pages(amo: lib.Amo, path: str, key: str) -> list[dict]:
    out = []
    seen = set()
    page = 1
    while True:
        status, body = amo_request(amo, "GET", f"{path}{'&' if '?' in path else '?'}limit=250&page={page}")
        if status == 204:
            return out
        if status != 200:
            raise Stop(f"Полная выгрузка amo {key}: HTTP {status}")
        chunk = (body.get("_embedded") or {}).get(key)
        if not isinstance(chunk, list):
            raise Stop(f"Неполная страница amo {key}")
        for item in chunk:
            uid = str(item.get("id") or "")
            if not uid or uid in seen:
                raise Stop(f"Пустой/повторный ID в полном индексе amo {key}")
            seen.add(uid)
        out.extend(chunk)
        if len(chunk) < 250:
            if len({str(x.get("id")) for x in out}) != len(out):
                raise Stop(f"Дубли ID в полном индексе amo {key}")
            return out
        time.sleep(0.15)
        page += 1


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
    if re.search(r"[,;\n/]", raw):
        return ""
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


def name_norm(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def amo_lead_url(amo: lib.Amo, lead_id: str | int) -> str:
    return f"{amo.base}/leads/detail/{lead_id}"


def ms_order_url(order_id: str) -> str:
    return f"https://online.moysklad.ru/app/#CustomerOrder/edit?id={order_id}"


def linked_order_id(url: str) -> str:
    try:
        u = urllib.parse.urlsplit(url.strip())
        if u.scheme != "https" or u.hostname not in {"online.moysklad.ru", "api.moysklad.ru"}:
            return ""
        if u.hostname == "api.moysklad.ru" and "/entity/customerorder/" in u.path:
            uid = u.path.rstrip("/").rsplit("/", 1)[-1].lower()
            return uid if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid) else ""
        frag = u.fragment
        if not frag.casefold().startswith("customerorder/edit?"):
            return ""
        query = frag.split("?", 1)[1] if "?" in frag else ""
        uid = (urllib.parse.parse_qs(query).get("id") or [""])[0].lower()
        return uid if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid) else ""
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


def reconcile_orders(mss: requests.Session, pace: list[float],
                     strict_snapshot: bool = True) -> tuple[list[dict], dict[str, int], int]:
    cached = read_order_cache() if ORDER_CACHE.exists() else {}
    changed = 0
    for attempt in range(1, 4):
        compact, _ = compact_order_snapshot(mss, pace)
        resolved = []
        for oid, brief in compact.items():
            prior = cached.get(oid)
            if prior and str(prior.get("updated")) == str(brief.get("updated")) and state_id(prior) == state_id(brief):
                detail = prior
            else:
                detail = fresh_order(mss, oid, pace)
                changed += 1
                cached[oid] = detail
                if changed % 25 == 0:
                    write_order_cache(list(cached.values()))
                    print(f"Подробности заказов: перечитано {changed}; промежуточный кэш сохранён.", flush=True)
            # A moving order does not invalidate unrelated completed reads.
            if state_id(detail) in STATUS_IDS.values():
                resolved.append(detail)
        counts = {name: sum(state_id(o) == sid for o in resolved) for name, sid in STATUS_IDS.items()}
        if strict_snapshot:
            after, _ = compact_order_snapshot(mss, pace)
            if compact_signature(compact) != compact_signature(after):
                write_order_cache(list(cached.values()))
                continue
        if len({str(x.get("id")) for x in resolved}) != len(resolved):
            raise Stop("Повтор UUID в подробных заказах")
        write_order_cache(list(cached.values()))
        print(f"Текущих заказов: {len(resolved)}, подробностей перечитано: {changed}", flush=True)
        return resolved, counts, changed
    raise Temporary("Состав заказов менялся во время трёх проверок; кэш сохранён")


def collect_current(ms: lib.MS, amo: lib.Amo,
                    strict_order_snapshot: bool = True) -> tuple[dict, dict]:
    """Полные исходные индексы с проверкой стабильного состава заказов."""
    started = datetime.now(TZ).isoformat(timespec="seconds")
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
    order_rows, counts, _changed = reconcile_orders(mss, pace, strict_order_snapshot)
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
    pipeline_status, pipeline = amo_request(amo, "GET", f"/api/v4/leads/pipelines/{PIPELINE}")
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
        "ledger_watermark": started,
        "state_counts": counts, "all_orders_count": total_orders, "orders": order_rows,
        "paymentin": paymentins, "cashin": cashins, "invoiceout": invoices,
        "salesreturn": returns, "demand": demands,
        "paymentout": paymentouts, "cashout": cashouts,
        "default_currency": default_currency[0],
        "leads": leads, "contacts": contacts,
        "amo_fields": {str(k): v for k, v in mf.items()},
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


def order_core_hash(order: dict) -> str:
    """Сверка базовых фактов при восстановлении уже созданной сделки."""
    projection = {k: order.get(k) for k in (
        "id", "name", "moment", "sum", "payedSum", "state", "positions",
        "organization", "store", "salesChannel",
    )}
    projection["attributes"] = [x for x in (order.get("attributes") or [])
                                if str(x.get("id") or id_of(x)) != MS_AMO_ATTR]
    agent = order.get("agent") or {}
    projection["agent_identity"] = {k: agent.get(k) for k in (
        "id", "name", "phone", "email", "legalFirstName", "legalLastName", "legalTitle",
        "actualAddress", "actualAddressFull",
    )}
    return digest(projection)


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
    first, last = str(cp.get("legalFirstName") or "").strip(), str(cp.get("legalLastName") or "").strip()
    name = " ".join(str(cp.get("name") or "").split())
    corporate = re.search(r"\b(ооо|ип|зао|пао|оао|магазин|бутик|lamoda|опт|компания|салон)\b",
                          str(cp.get("name") or ""), re.IGNORECASE)
    if corporate:
        return "", "DEFERRED_BUSINESS_CUSTOMER"
    if first and last:
        personal = " ".join((first, last)).strip()
        if name and not set(name_norm(personal).split()).issubset(set(name_norm(name).split())):
            return "", "DEFERRED_BUYER_IDENTITY"
        return personal, ""
    if not name or not re.search(r"[^\W\d_]", name, re.UNICODE):
        return "", "DEFERRED_BUYER_IDENTITY"
    tokens = name_norm(name).split()
    if len(tokens) > 1 and len(set(tokens)) == 1:
        return "", "DEFERRED_BUYER_IDENTITY"
    if re.search(r"\b(покупатель|клиент|розница|физлицо|тест|неизвестный)\b", name, re.I):
        return "", "DEFERRED_BUYER_IDENTITY"
    return name, ""


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
    notes.append("[Исторический импорт МС→amo " + datetime.now(TZ).date().isoformat() + "]")
    notes.append(f"Сумма заказа: {amount_text(total)} ₽")
    if row["return_kopecks"]:
        notes.append(f"Подтверждённый возврат: {amount_text(int(row['return_kopecks']))} ₽; вычтен один раз по документу возврата")
    notes.append(f"Сумма сделки: {amount_text(price)} ₽; округление amo до {rounded} ₽; разница округления {round_delta} коп.")
    notes.append(f"Дата создания и статус: дата заказа МойСклад {order.get('moment')}; этап установлен напрямую по плану миграции.")
    notes.append(f"Дата закрытия: {row['close_moment']} ({row['close_basis']}).")
    notes.append(f"Ответственный: {manager_reason}.")
    notes.extend(row.get("related_documents") or [])
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
    values.extend(row.get("optional_fields") or [])
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


def classify(data: dict, ms: lib.MS, roster: dict | None = None) -> dict:
    orders = data["orders"]
    payments, invoices = allocations(data)
    returns_by_order = return_map(data)
    outflow_by_return = return_outflow(data)
    roster = get_roster() if roster is None else roster
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
        selected = selected and order.get("applicable") is True
        uid = str(order.get("id") or "").casefold()
        signature = order_hash(order)
        if not selected:
            counts["EXCLUDED_NOT_FULLY_PAID"] += 1
            plan_rows.append({"order_id": uid, "source_hash": signature,
                              "number": str(order.get("name") or ""),
                              "category": "EXCLUDED_NOT_FULLY_PAID",
                              "reason": "ORDER_NOT_APPLICABLE" if order.get("applicable") is not True else "STATUS_REQUIRES_FULL_PAYMENT", "state": name,
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
            if cp.get("phone") and not phone:
                category, reason = "DEFERRED", "BUYER_PHONE_INVALID_OR_MULTIPLE"
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
            if (isinstance(rate, dict) and id_of(rate.get("currency"))
                    and id_of(rate.get("currency")) != str(data.get("default_currency", {}).get("id"))):
                category, reason = "DEFERRED", "FOREIGN_CURRENCY_OR_NONDEFAULT_RATE"

        if category == "CREATE" and not close_moment and name in ALWAYS:
            if audit_checks % 25 == 0:
                print(f"Проверяю историю статусов: {audit_checks} заказов…", flush=True)
            cache_key = uid + ":" + signature
            if cache_key in audit_cache:
                close_moment = audit_cache[cache_key]
            else:
                close_moment = audit_current_state(ms, order)
                audit_cache[cache_key] = close_moment
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
    status, body = amo_request(amo, "GET", path)
    if status not in (200, 204):
        raise Stop(f"Чтение amoCRM: HTTP {status}")
    return status, body if isinstance(body, dict) else {}


def amo_search_pages(amo: lib.Amo, entity: str, query: str, key: str) -> list[dict]:
    rows = []
    encoded = urllib.parse.quote(query)
    page, seen = 1, set()
    while True:
        status, body = amo_request(amo, "GET", f"/api/v4/{entity}?query={encoded}&limit=250&page={page}")
        if status == 204:
            return rows
        if status != 200:
            raise Stop(f"Точный поиск amo {entity}: HTTP {status}")
        chunk = (body.get("_embedded") or {}).get(key)
        if not isinstance(chunk, list):
            raise Stop(f"Неполный точный поиск amo {entity}")
        for item in chunk:
            uid = str(item.get("id") or "")
            if not uid or uid in seen:
                raise Stop(f"Повтор/пустой ID при точном поиске amo {entity}")
            seen.add(uid)
        rows.extend(chunk)
        if len(chunk) < 250:
            ids = [str(x.get("id")) for x in rows]
            if len(set(ids)) != len(ids):
                raise Stop(f"Повтор ID при точном поиске amo {entity}")
            return rows
        time.sleep(0.15)
        page += 1


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
    if getattr(amo, "_backfill_session", None) is not None:
        return amo._backfill_session
    s = PacedSession("amo")
    s.trust_env = False
    # Exactly the route selected by assign_shift.amo_client(), for reads AND writes.
    for handler in amo.opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            s.proxies.update(handler.proxies)
    s.headers.update({"Authorization": "Bearer " + amo.token,
                      "Content-Type": "application/json",
                      "Accept": "application/hal+json"})
    amo._backfill_session = s
    return s


def save_journal(journal: dict) -> None:
    atomic_json(JOURNAL, journal)


def confirm_contact(amo: lib.Amo, contact_id: str, row: dict,
                    expect_name: bool = False) -> dict:
    status, contact = amo_read(amo, f"/api/v4/contacts/{contact_id}")
    if status != 200 or str(contact.get("id")) != str(contact_id):
        raise Stop("Контакт не перечитался после записи")
    got_phone, got_email = set(), set()
    for group in contact.get("custom_fields_values") or []:
        if group.get("field_code") == "PHONE" or group.get("field_id") == AMO_PHONE_FIELD:
            got_phone.update(phone_norm(v.get("value")) for v in group.get("values") or [])
        elif group.get("field_code") == "EMAIL" or group.get("field_id") == AMO_EMAIL_FIELD:
            got_email.update(email_norm(v.get("value")) for v in group.get("values") or [])
    same_name = sorted(name_norm(contact.get("name")).split()) == sorted(name_norm(row.get("buyer")).split())
    mismatch = (not same_name or bool(row.get("phone") and row["phone"] not in got_phone)
                or bool(row.get("email") and row["email"] not in got_email)
                or contact.get("is_deleted") is True)
    if mismatch and expect_name:
        raise Stop("Созданный контакт не соответствует подтверждённому покупателю")
    if mismatch:
        contact["_migration_identity_mismatch"] = True
    return contact


def confirm_lead(amo: lib.Amo, lead_id: str, order: dict, row: dict,
                 contact_id: str, expected_payload: dict) -> dict:
    status, lead = amo_read(amo, f"/api/v4/leads/{lead_id}?with=contacts")
    if status != 200 or str(lead.get("id")) != str(lead_id):
        raise Stop("Сделка не перечиталась после записи")
    return validate_lead(lead, contact_id, expected_payload)


def validate_lead(lead: dict, contact_id: str, expected_payload: dict) -> dict:
    if int(lead.get("pipeline_id") or 0) != PIPELINE or int(lead.get("status_id") or 0) != WON:
        raise Stop("Новая сделка не оказалась в целевой воронке/этапе")
    if int(lead.get("price") or 0) != expected_payload["price"]:
        raise Stop("Сумма сделки не совпала с планом")
    if str(lead.get("name") or "") != str(expected_payload.get("name") or ""):
        raise Stop("Название сделки не совпало с планом")
    if int(lead.get("responsible_user_id") or 0) != int(expected_payload["responsible_user_id"]):
        raise Stop("Ответственный сделки не совпал с планом")
    for item in expected_payload["custom_fields_values"]:
        fid = item["field_id"]
        actual = next((x for x in lead.get("custom_fields_values") or []
                       if str(x.get("field_id")) == str(fid)), None)
        if actual is None:
            raise Stop(f"Поле amo {fid} отсутствует после записи")
        def normalized_values(field_id: int, values: list[dict]) -> list[tuple[str, str]]:
            result = []
            for value in values:
                if "enum_id" in value:
                    result.append(("enum_id", str(value.get("enum_id"))))
                elif "value" in value:
                    raw_value = value.get("value")
                    if str(field_id) == str(FIELDS["delivery"]):
                        # amo отдаёт числовые денежные поля в разных форматах
                        # (например, 395, 395.0 и 395.00). Сравниваем число,
                        # сохраняя точность Decimal, а не текстовое оформление.
                        try:
                            normalized = format(Decimal(str(raw_value)).normalize(), "f")
                        except Exception as exc:
                            raise Stop(f"Поле amo {field_id} содержит нечисловое значение") from exc
                        result.append(("number", normalized))
                    else:
                        result.append(("value", str(raw_value)))
            return result
        if normalized_values(fid, actual.get("values") or []) != normalized_values(fid, item.get("values") or []):
            raise Stop(f"Значение поля amo {fid} не совпало с планом")
    if int(lead.get("created_at") or 0) != expected_payload["created_at"]:
        raise Stop("Дата создания сделки не совпала с датой заказа")
    if int(lead.get("closed_at") or 0) != expected_payload["closed_at"]:
        raise Stop("Дата закрытия сделки не совпала с подтверждённой датой")
    linked = {str(x.get("id")) for x in (lead.get("_embedded") or {}).get("contacts", []) if x.get("id")}
    main = {str(x.get("id")) for x in (lead.get("_embedded") or {}).get("contacts", []) if x.get("is_main")}
    if linked != {contact_id} or main != {contact_id}:
        raise Stop("Сделка не связана с выбранным контактом")
    if (lead.get("_embedded") or {}).get("companies"):
        raise Stop("У импортированной сделки обнаружена компания")
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
        "responsible_user_id": manager_id,
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


class RowDeferred(Exception):
    """A known data problem: do not invent a resolution."""


class UnknownWrite(RowDeferred):
    """A possibly completed creation must never be submitted again."""


LEDGERS = ("paymentin", "cashin", "invoiceout", "salesreturn", "demand", "paymentout", "cashout")


def checkpoint_backup() -> str:
    folder = WORK / ("2my-ms-amo-backup-" + datetime.now(TZ).strftime("%Y%m%d-%H%M%S-%f"))
    folder.mkdir(mode=0o700)
    for path in (PLAN, RAW_CACHE, JOURNAL, ORDER_CACHE, STATUS_AUDIT_CACHE):
        if path.exists():
            shutil.copy2(path, folder / path.name)
            os.chmod(folder / path.name, 0o600)
    return str(folder)


def quarantine(journal: dict, oid: str, row: dict, reason: str) -> None:
    entry = journal["rows"].setdefault(oid, {})
    entry["quarantine_identity"] = {k: row.get(k) for k in ("counterparty_id", "phone", "email")}
    _set_entry(journal, oid, status="QUARANTINED", reason=reason)


def identity_blocked(journal: dict, row: dict) -> bool:
    for entry in journal["rows"].values():
        if entry.get("status") != "QUARANTINED":
            continue
        identity = entry.get("quarantine_identity") or {}
        if any(row.get(k) and row[k] == identity.get(k) for k in ("counterparty_id", "phone", "email")):
            return True
    return False


def complete_positions(session: requests.Session, entity: str, obj: dict) -> dict:
    positions = obj.get("positions") or {}
    size = (positions.get("meta") or {}).get("size")
    rows = positions.get("rows")
    if not isinstance(size, int):
        raise RowDeferred("POSITIONS_SIZE_MISSING")
    if not isinstance(rows, list) or len(rows) != size:
        rows = ms_rows(session, f"/entity/{entity}/{obj['id']}/positions",
                       {"limit": 1000, "expand": "assortment"})
        obj["positions"] = {**positions, "rows": rows}
    if len(rows) != size:
        raise RowDeferred("POSITIONS_INCOMPLETE")
    return obj


def finance_refresh(data: dict, mss: requests.Session, oid: str) -> None:
    """Capture global changes, including payments made by a different payer.

    Known dependencies are also read individually: deletes never disappear as
    a false zero return/payment. Supported updated filters were checked live.
    """
    start = datetime.now(TZ)
    since = moscow_dt(data.get("ledger_watermark") or data["captured_at"]) - timedelta(seconds=5)
    for kind in LEDGERS:
        params = {"limit": 1000, "filter": "updated>=" + since.strftime("%Y-%m-%d %H:%M:%S")}
        if kind == "salesreturn":
            params.update(limit=100, expand="positions,positions.assortment,demand")
        changed = ms_rows(mss, "/entity/" + kind, params)
        index = {str(x["id"]): x for x in data[kind]}
        index.update({str(x["id"]): x for x in changed})
        data[kind] = list(index.values())
    invoices = {str(x["id"]) for x in data["invoiceout"] if id_of(x.get("customerOrder")) == oid}
    demands = {str(x["id"]) for x in data["demand"] if id_of(x.get("customerOrder")) == oid}
    returns = {str(x["id"]) for x in data["salesreturn"] if id_of(x.get("demand")) in demands}
    targets = {oid} | invoices | demands | returns
    for kind in LEDGERS:
        index = {str(x["id"]): x for x in data[kind]}
        relevant = [x for x in data[kind] if str(x["id"]) in targets
                    or any(id_of(op.get("meta")) in targets for op in x.get("operations") or [])]
        for old in relevant:
            params = {"expand": "positions,positions.assortment,demand"} if kind == "salesreturn" else None
            try:
                obj = api_get(mss, lib.MS_BASE + f"/entity/{kind}/{old['id']}", params)
            except Stop as exc:
                if "HTTP 404" in str(exc) or "HTTP 410" in str(exc):
                    raise RowDeferred("LINKED_DOCUMENT_DELETED") from exc
                raise
            if kind == "salesreturn":
                complete_positions(mss, kind, obj)
            index[str(obj["id"])] = obj
        data[kind] = list(index.values())
    data["ledger_watermark"] = start.isoformat(timespec="seconds")


def confirm_buyer(amo: lib.Amo, cid: str, row: dict, new: bool = False) -> dict:
    contact = confirm_contact(amo, cid, row, expect_name=False)
    if contact.get("_migration_identity_mismatch"):
        raise RowDeferred("CONTACT_IDENTITY_MISMATCH")
    if new and row.get("phone"):
        numbers = [str(v.get("value") or "") for f in contact.get("custom_fields_values") or []
                   if f.get("field_code") == "PHONE" or f.get("field_id") == AMO_PHONE_FIELD
                   for v in f.get("values") or []]
        if row["phone"] not in numbers:
            raise RowDeferred("NEW_PHONE_NOT_CANONICAL")
    return contact


def set_backlink(ms: lib.MS, amo: lib.Amo, journal: dict, oid: str, lead_id: str,
                 row: dict, payload: dict, contact_id: str) -> None:
    mss = ms_session(ms)
    target = amo_lead_url(amo, lead_id)
    _set_entry(journal, oid, status="BACKLINK_WRITE_INTENT", lead_id=lead_id,
               contact_id=contact_id, backlink=target)
    for attempt in range(5):
        order = fresh_order(mss, oid, [0.0])
        link = ms_link(order)
        if link and link != target:
            raise Stop("BACKLINK_CONFLICT_AFTER_CREATION")
        confirm_lead(amo, lead_id, order, row, contact_id, payload)
        if link == target:
            _set_entry(journal, oid, status="VERIFIED", verified_at=datetime.now(TZ).isoformat(timespec="seconds"))
            return
        # Remap updates the supplied attribute; no stale copy of other attributes.
        before = attrs(order)
        attribute = {"id": MS_AMO_ATTR,
                     "meta": {"href": lib.MS_BASE + "/entity/customerorder/metadata/attributes/" + MS_AMO_ATTR,
                              "type": "attributemetadata", "mediaType": "application/json"}, "value": target}
        code, _, error = api_write(mss, "PUT", lib.MS_BASE + "/entity/customerorder/" + oid,
                                  {"attributes": [attribute]}, headers={"X-Lognex-WebHook-Disable": "true"})
        after = fresh_order(mss, oid, [0.0])
        if any(attrs(after).get(k) != v for k, v in before.items() if k != MS_AMO_ATTR):
            raise Stop("UNRELATED_ATTRIBUTE_CHANGED_DURING_BACKLINK")
        if ms_link(after) == target:
            confirm_lead(amo, lead_id, after, row, contact_id, payload)
            _set_entry(journal, oid, status="VERIFIED", verified_at=datetime.now(TZ).isoformat(timespec="seconds"))
            return
        if ms_link(after):
            raise Stop("BACKLINK_CONFLICT_AFTER_WRITE")
        if code not in (None, 429, 500, 502, 503, 504):
            raise Stop(f"BACKLINK_NOT_CONFIRMED_HTTP_{code}")
        # A PUT of this same link is safe only after a fresh read of an empty link.
        time.sleep(min(30, 2 ** (attempt + 1)))
    raise Temporary("BACKLINK_NOT_CONFIRMED_AFTER_RETRIES")


def create_entity(amo: lib.Amo, journal: dict, oid: str, row: dict, kind: str, payload: dict) -> str:
    plural = "contacts" if kind == "contact" else "leads"
    intent = "CONTACT_WRITE_INTENT" if kind == "contact" else "LEAD_WRITE_INTENT"
    if kind == "lead" and journal.get("active_run"):
        attempts = journal.setdefault("run_attempts", {}).setdefault(journal["active_run"], [])
        if oid not in attempts:
            attempts.append(oid)
            save_journal(journal)
    for attempt in range(5):
        _set_entry(journal, oid, status=intent, **{kind + "_payload": payload},
                   write_started_at=datetime.now(TZ).isoformat(timespec="seconds"))
        code, body, error = api_write(amo_session(amo), "POST", amo.base + "/api/v4/" + plural, [payload])
        _set_entry(journal, oid, last_write_http=code, last_write_error=error,
                   last_validation_errors=body.get("validation-errors") or body.get("validation_errors"))
        embedded = (body.get("_embedded") or {}).get(plural) or []
        if code in (200, 201) and len(embedded) == 1 and embedded[0].get("id"):
            result = str(embedded[0]["id"])
            _set_entry(journal, oid, **{kind + "_id": result})
            return result
        found = exact_contacts(amo, row.get("phone", ""), row.get("email", "")) if kind == "contact" else exact_leads(amo, oid)
        if len(found) == 1:
            result = str(found[0]["id"])
            _set_entry(journal, oid, **{kind + "_id": result}, recovered_after_post=True)
            return result
        if code == 429 and not found:
            time.sleep(min(30, 2 ** (attempt + 1)))
            continue
        if code is None or code >= 500 or code in (200, 201) or found:
            quarantine(journal, oid, row, "UNKNOWN_" + kind.upper() + "_WRITE")
            raise UnknownWrite("UNKNOWN_" + kind.upper() + "_WRITE")
        if code in (401, 403):
            raise Stop(f"WRITE_PERMISSION_HTTP_{code}")
        raise RowDeferred(f"{kind.upper()}_CREATE_HTTP_{code}")
    raise RowDeferred(kind.upper() + "_CREATE_RATE_LIMIT")


def current_row(data: dict, ms: lib.MS, amo: lib.Amo, oid: str, roster: dict) -> tuple[dict, dict]:
    mss = ms_session(ms)
    finance_refresh(data, mss, oid)
    order = complete_positions(mss, "customerorder", fresh_order(mss, oid, [0.0]))
    data_one = {**data, "orders": [order]}
    row = classify(data_one, ms, roster)["rows"][0]
    return order, row


def recover_pending(data: dict, ms: lib.MS, amo: lib.Amo, journal: dict) -> None:
    unknown = journal.get("unknown_write")
    if unknown:
        oid = unknown.get("order_id")
        if not oid or oid not in journal["rows"]:
            raise Stop("UNKNOWN_WRITE_WITHOUT_ORDER")
        journal["rows"][oid]["previous_unknown_write"] = unknown
        journal["unknown_write"] = None
        save_journal(journal)
    for oid, entry in list(journal["rows"].items()):
        if entry.get("status") in TERMINAL:
            continue
        row = entry.get("source_row")
        if not row:
            row = next((r for r in journal.get("legacy_plan_rows", []) if r.get("order_id") == oid), None)
        if not row:
            raise Stop("RECOVERY_SOURCE_ROW_MISSING")
        matches = exact_leads(amo, oid)
        if len(matches) > 1:
            raise Stop("MULTIPLE_LEADS_DURING_RECOVERY")
        if matches:
            lead_id = str(matches[0]["id"])
            if entry.get("lead_id") and str(entry["lead_id"]) != lead_id:
                raise Stop("RECOVERY_LEAD_ID_CONFLICT")
            payload, cid = entry.get("lead_payload"), str(entry.get("contact_id") or "")
            if not payload or not cid:
                quarantine(journal, oid, row, "RECOVERY_PAYLOAD_OR_CONTACT_MISSING")
                continue
            # The saved source/amount must still agree; do not rewrite an existing deal.
            order, recalculated = current_row(data, ms, amo, oid, {})
            checks = ("total_kopecks", "payment_kopecks", "payment_moment", "return_kopecks", "return_state", "counterparty_id", "composition", "moment")
            if any(recalculated.get(k) != row.get(k) for k in checks):
                quarantine(journal, oid, row, "SOURCE_CHANGED_AFTER_CREATION")
                continue
            set_backlink(ms, amo, journal, oid, lead_id, row, payload, cid)
            repaired = fresh_order(ms_session(ms), oid, [0.0])
            data["orders"] = [o for o in data["orders"] if str(o["id"]) != oid] + [repaired]
            continue
        if entry.get("lead_id") or entry.get("status") in {"LEAD_WRITE_INTENT", "LEAD_VERIFIED", "BACKLINK_WRITE_INTENT", "COMMENT_WRITE_INTENT"}:
            quarantine(journal, oid, row, "UNRESOLVED_LEAD_WRITE")
            continue
        contacts = exact_contacts(amo, row.get("phone", ""), row.get("email", ""))
        if len(contacts) == 1:
            cid = str(contacts[0]["id"])
            try:
                confirm_buyer(amo, cid, row)
            except RowDeferred:
                quarantine(journal, oid, row, "RECOVERY_CONTACT_IDENTITY_MISMATCH")
                continue
            journal["counterparty_contacts"][row["counterparty_id"]] = cid
            _set_entry(journal, oid, status="CONTACT_VERIFIED", contact_id=cid, source_row=row)
            # Can enter the fresh plan; the contact will be reused, never created twice.
        elif entry.get("status") in {"CONTACT_WRITE_INTENT", "CONTACT_VERIFIED", "COUNTERPARTY_WRITE_INTENT"} or entry.get("contact_id"):
            quarantine(journal, oid, row, "UNRESOLVED_CONTACT_WRITE")
        else:
            _set_entry(journal, oid, status="PLANNED", source_row=row)


def optional_fields(data: dict, order: dict, row: dict, ms: lib.MS) -> list[dict]:
    """Only existing fields and exact enum ID/code/name correspondences."""
    schema, result, omissions = data.get("amo_fields", {}), [], []
    mss = ms_session(ms)
    references = data.setdefault("references", {})

    def ref_name(ref):
        if not isinstance(ref, dict):
            return str(ref or "").strip()
        if ref.get("name"):
            return str(ref["name"])
        href = (ref.get("meta") or {}).get("href", "")
        if not href.startswith(lib.MS_BASE + "/entity/"):
            return ""
        if href not in references:
            references[href] = api_get(mss, href)
        return str(references[href].get("name") or "")

    def add(fid: int, value, ref=None):
        if value in (None, "") and not ref:
            omissions.append({"field_id": fid, "reason": "SOURCE_EMPTY"})
            return
        meta = schema.get(str(fid))
        if not meta:
            omissions.append({"field_id": fid, "reason": "FIELD_ABSENT"})
            return
        if meta["type"] == "select":
            uid = id_of(ref) if ref else ""
            enums = meta.get("enums") or []
            matches = [e for e in enums if uid and e.get("code") == uid]
            if not matches:
                value = ref_name(ref) if ref else value
                matches = [e for e in enums if name_norm(e.get("value")) == name_norm(value)]
            if len(matches) != 1:
                omissions.append({"field_id": fid, "reason": "NO_EXACT_EXISTING_ENUM"})
                return
            values = [{"enum_id": matches[0]["id"]}]
        elif meta["type"] == "date_time":
            stamp = unix_time(value)
            if not stamp:
                omissions.append({"field_id": fid, "reason": "DATE_INVALID"})
                return
            values = [{"value": stamp}]
        elif meta["type"] in {"text", "textarea", "tracking_data", "streetaddress", "url"}:
            value = ref_name(ref) if ref else str(value)
            if not value:
                omissions.append({"field_id": fid, "reason": "REFERENCE_NAME_EMPTY"})
                return
            if len(value) > (60000 if meta["type"] == "textarea" else 255):
                omissions.append({"field_id": fid, "reason": "OPTIONAL_VALUE_EXCEEDS_LIMIT"})
                return
            values = [{"value": value}]
        else:
            omissions.append({"field_id": fid, "reason": "UNSUPPORTED_FIELD_TYPE"})
            return
        result.append({"field_id": fid, "values": values})

    av = attrs(order)
    for fid, attr in ((928623, "6d95a799-f849-11f0-0a80-009c000b6c8a"),
                      (928619, "6d95a996-f849-11f0-0a80-009c000b6c8b"),
                      (928621, "6d95aa5b-f849-11f0-0a80-009c000b6c8c"),
                      (928967, "f11cca54-ed68-11ef-0a80-084e000137c7"),
                      (929039, lib.MS_ATTR_TRACK)):
        add(fid, av.get(attr))
    for fid in (928617, 928625):
        label = schema.get(str(fid), {}).get("name")
        source = [a.get("value") for a in order.get("attributes") or [] if a.get("name") == label]
        add(fid, source[0] if len(source) == 1 else None)
    for fid, key in ((929033, "organization"), (929083, "store"), (929085, "salesChannel"), (929087, "project")):
        add(fid, None, ref=order.get(key))
    add(929089, None, ref=(order.get("rate") or {}).get("currency"))
    add(928931, ref_name(order.get("salesChannel")))
    add(928941, None, ref=av.get("ed14744d-dc0d-11ef-0a80-10cd00226b06"))
    add(928959, None, ref=av.get("ed1471ec-dc0d-11ef-0a80-10cd00226b05"))
    add(929079, order.get("shipmentAddress"))
    address = order.get("shipmentAddressFull") or {}
    add(929025, address.get("comment") or address.get("addInfo"))
    add(929073, order.get("deliveryPlannedMoment"))
    add(929091, order.get("paymentPlannedMoment"))
    related = []
    for kind, ids in (("invoiceout", (929053, 929061, 929045)), ("demand", (929051, 929059, 929047))):
        docs = [d for d in data[kind] if id_of(d.get("customerOrder")) == order["id"]]
        entity = "InvoiceOut" if kind == "invoiceout" else "Demand"
        if len(docs) == 1:
            doc = docs[0]
            for fid, value in zip(ids, (doc["id"], doc.get("name"), f"https://online.moysklad.ru/app/#{entity}/edit?id={doc['id']}")):
                add(fid, value)
        else:
            for fid in ids:
                omissions.append({"field_id": fid, "reason": "MULTIPLE_DOCUMENTS" if docs else "SOURCE_EMPTY"})
        related.extend(f"{kind}: https://online.moysklad.ru/app/#{entity}/edit?id={d['id']}" for d in docs if len(docs) > 1)
    row["related_documents"] = related
    row["field_omissions"] = omissions
    return result


def apply_one(data: dict, ms: lib.MS, amo: lib.Amo, journal: dict, planned: dict, roster: dict) -> bool:
    oid = planned["order_id"]
    if journal["rows"].get(oid, {}).get("status") in TERMINAL:
        return False
    order, row = current_row(data, ms, amo, oid, roster)
    if identity_blocked(journal, row):
        raise RowDeferred("IDENTITY_HAS_UNRESOLVED_WRITE")
    if row["category"] != "CREATE":
        status = row["category"] if row["category"] in {"EXISTS_BACKLINK", "EXISTS_AMO_ID"} else "DEFERRED"
        _set_entry(journal, oid, status=status, reason=row["reason"], source_row=row, lead_id=row.get("lead_id"))
        return False
    if not moscow_dt(order.get("moment")) or state_id(order) not in STATUS_IDS.values():
        raise RowDeferred("ORDER_LEFT_ALLOWED_STATUSES")
    matches = exact_leads(amo, oid)
    if matches:
        if len(matches) != 1:
            raise RowDeferred("MULTIPLE_EXISTING_LEADS")
        _set_entry(journal, oid, status="EXISTS_AMO_ID", lead_id=str(matches[0]["id"]))
        return False
    contacts = exact_contacts(amo, row.get("phone", ""), row.get("email", ""))
    if len(contacts) > 1:
        raise RowDeferred("AMBIGUOUS_CONTACT_MATCH")
    entry = _set_entry(journal, oid, status="PLANNED", source_row=row,
                       source_hash=row["source_hash"], net_kopecks=row["net_kopecks"])
    cid = str(row.get("contact_id") or journal["counterparty_contacts"].get(row["counterparty_id"]) or "")
    if contacts:
        found_id = str(contacts[0]["id"])
        if cid and cid != found_id:
            raise RowDeferred("CONTACT_INDEX_CHANGED")
        cid = found_id
    if cid:
        contact = confirm_buyer(amo, cid, row)
    else:
        payload = {"name": row["buyer"], "responsible_user_id": MAXIM,
                   "request_id": "ms-counterparty:" + row["counterparty_id"], "custom_fields_values": []}
        for key, code in (("phone", "PHONE"), ("email", "EMAIL")):
            if row.get(key):
                payload["custom_fields_values"].append({"field_code": code, "values": [{"value": row[key]}]})
        cid = create_entity(amo, journal, oid, row, "contact", payload)
        try:
            contact = confirm_buyer(amo, cid, row, new=True)
        except RowDeferred:
            quarantine(journal, oid, row, "CREATED_CONTACT_IDENTITY_MISMATCH")
            raise UnknownWrite("CREATED_CONTACT_IDENTITY_MISMATCH")
    journal["counterparty_contacts"][row["counterparty_id"]] = cid
    _set_entry(journal, oid, status="CONTACT_VERIFIED", contact_id=cid)
    # A contact creation can take time. Recheck all financial facts and duplicate keys.
    fresh, again = current_row(data, ms, amo, oid, roster)
    keys = ("counterparty_id", "buyer", "phone", "email", "net_kopecks", "total_kopecks", "payment_kopecks", "payment_moment", "return_state", "return_kopecks", "close_moment", "composition")
    if again["category"] != "CREATE" or any(row.get(k) != again.get(k) for k in keys):
        raise RowDeferred("SOURCE_CHANGED_BEFORE_DEAL_CREATION")
    if ms_link(fresh) or exact_leads(amo, oid):
        raise RowDeferred("LINK_OR_LEAD_APPEARED_BEFORE_CREATION")
    row["optional_fields"] = optional_fields(data, fresh, row, ms)
    _set_entry(journal, oid, source_row=row)
    payload = make_lead_payload(amo, fresh, row, cid, roster)
    lid = create_entity(amo, journal, oid, row, "lead", payload)
    _set_entry(journal, oid, status="LEAD_VERIFIED", contact_id=cid, lead_id=lid, lead_payload=payload)
    lead = confirm_lead(amo, lid, fresh, row, cid, payload)
    set_backlink(ms, amo, journal, oid, lid, row, payload, cid)
    data["contacts"] = [c for c in data["contacts"] if str(c["id"]) != cid] + [contact]
    data["leads"] = [l for l in data["leads"] if str(l["id"]) != lid] + [lead]
    return True


def verify_journal(ms: lib.MS, amo: lib.Amo, journal: dict, only_ids: set[str] | None = None,
                   full_data: dict | None = None) -> dict:
    result = {"checked_at": datetime.now(TZ).isoformat(timespec="seconds"), "checked": 0,
              "ok": 0, "issues": [], "source_drift": [], "net_kopecks": 0, "budget_rubles": 0, "rounding_kopecks": 0}
    mss = ms_session(ms)
    indexes = {key: {str(x["id"]): x for x in full_data[key]} for key in ("orders", "leads", "contacts")} if full_data else {}
    seen = set()
    for oid, entry in journal["rows"].items():
        if entry.get("status") != "VERIFIED" or (only_ids is not None and oid not in only_ids):
            continue
        result["checked"] += 1
        lid, cid = str(entry.get("lead_id") or ""), str(entry.get("contact_id") or "")
        if not lid or lid in seen:
            raise Stop("JOURNAL_DUPLICATE_LEAD_ID")
        seen.add(lid)
        if full_data is not None:
            order, lead, contact = indexes["orders"].get(oid), indexes["leads"].get(lid), indexes["contacts"].get(cid)
            if not order:
                order = fresh_order(mss, oid, [0.0])
            if not order or not lead or not contact:
                result["issues"].append({"order_id": oid, "reason": "VERIFIED_ENTITY_NOT_IN_FULL_INDEX"})
                continue
            try:
                validate_lead(lead, cid, entry["lead_payload"])
            except Stop as exc:
                result["issues"].append({"order_id": oid, "reason": str(exc)})
                continue
        else:
            order = fresh_order(mss, oid, [0.0])
            lead = confirm_lead(amo, lid, order, {}, cid, entry["lead_payload"])
            st, contact = amo_read(amo, f"/api/v4/contacts/{cid}")
            if st != 200:
                raise Stop("VERIFIED_CONTACT_NOT_READABLE")
        if ms_link(order) != amo_lead_url(amo, lid):
            raise Stop("VERIFIED_BACKLINK_MISMATCH")
        matches = [l for l in full_data["leads"] if field(l, FIELDS["ms_id"]).casefold() == oid or oid in amo_links(l)] if full_data is not None else exact_leads(amo, oid)
        if len(matches) != 1 or str(matches[0]["id"]) != lid:
            raise Stop("VERIFIED_UUID_NOT_UNIQUE")
        row = entry.get("source_row") or next((r for r in journal.get("legacy_plan_rows", []) if r["order_id"] == oid), {})
        net = int(row.get("net_kopecks", entry.get("net_kopecks", 0)))
        if full_data is not None:
            ret_state, returned, ret_reason = return_state(order, return_map(full_data).get(oid, []))
            if amount(order.get("sum")) - returned != net or ret_state == "deferred":
                result["source_drift"].append({"order_id": oid, "reason": ret_reason or "SOURCE_AMOUNT_CHANGED",
                                             "imported_net_kopecks": net, "current_net_kopecks": amount(order.get("sum")) - returned})
        result["net_kopecks"] += net
        result["budget_rubles"] += lead["price"]
        result["rounding_kopecks"] += lead["price"] * 100 - net
        result["ok"] += 1
    atomic_json(REPORT, result)
    if result["issues"]:
        raise Stop(f"Полная сверка обнаружила расхождения: {len(result['issues'])}; подробности в защищённом отчёте")
    return result


def apply_plan(plan: dict, plan_hash: str, maximum: int, resume: bool = False,
               current_data: dict | None = None) -> dict:
    ms, amo = lib.MS(), assign_shift.amo_client()
    journal = journal_load()
    if journal.get("plan_sha256") and journal["plan_sha256"] != plan_hash:
        raise Stop("Журнал относится к другому плану; используйте --prepare --resume")
    if current_data is None:
        current_data, _ = collect_current(ms, amo, strict_order_snapshot=False)
    journal.setdefault("legacy_plan_rows", plan["rows"])
    recover_pending(current_data, ms, amo, journal)
    roster = get_roster()
    processed, deferred = 0, 0
    candidates = sorted((r for r in plan["rows"] if r["category"] == "CREATE"), key=lambda r: (r.get("moment", ""), r["order_id"]))
    for row in candidates:
        if processed >= maximum:
            break
        if journal["rows"].get(row["order_id"], {}).get("status") in TERMINAL:
            continue
        try:
            if apply_one(current_data, ms, amo, journal, row, roster):
                processed += 1
        except UnknownWrite:
            deferred += 1
        except RowDeferred as exc:
            _set_entry(journal, row["order_id"], status="DEFERRED", reason=str(exc))
            deferred += 1
    return {"processed": processed, "deferred": deferred}


def prepare_night(ms: lib.MS, amo: lib.Amo, data: dict, journal: dict, apply_recovery: bool) -> dict:
    if PLAN.exists() and not journal.get("legacy_plan_rows"):
        old = json.loads(PLAN.read_text(encoding="utf-8"))
        journal["legacy_plan_rows"] = old["rows"]
    journal.setdefault("schema_version", 2)
    journal.setdefault("plan_history", [])
    if apply_recovery:
        recover_pending(data, ms, amo, journal)
    plan = classify(data, ms)
    old_rows = {r["order_id"]: r for r in journal.get("legacy_plan_rows", [])}
    # Known failures are retained, but never-attempted old policy exclusions are reclassified.
    for row in plan["rows"]:
        entry = journal["rows"].get(row["order_id"], {})
        entry.setdefault("source_row", old_rows.get(row["order_id"], row))
        if entry.get("status") in {"DEFERRED", "QUARANTINED"}:
            row.update(category="DEFERRED", reason=entry.get("reason", "PRIOR_UNRESOLVED_ATTEMPT"))
        elif entry.get("status") == "VERIFIED":
            if row["category"] == "CREATE":
                raise Stop("PREVIOUSLY_VERIFIED_ORDER_NO_LONGER_LINKED")
        if identity_blocked(journal, row) and row["category"] == "CREATE":
            row.update(category="DEFERRED", reason="IDENTITY_HAS_UNRESOLVED_WRITE")
    plan["counts"] = dict(__import__('collections').Counter(r["category"] for r in plan["rows"]))
    atomic_json(PLAN, plan)
    sha = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    if journal.get("plan_sha256") != sha:
        journal["plan_history"].append({"sha256": journal.get("plan_sha256"), "changed_at": datetime.now(TZ).isoformat(timespec="seconds")})
    journal["plan_sha256"] = sha
    journal["prepared_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_journal(journal)
    return plan


def night_run(args) -> dict:
    if not args.resume:
        raise Stop("Ночной режим требует --resume, журнал нельзя начинать заново")
    backup = checkpoint_backup()
    ms, amo = lib.MS(), assign_shift.amo_client()
    journal = journal_load()
    journal["active_run"] = datetime.now(TZ).isoformat(timespec="microseconds")
    save_journal(journal)
    data, _ = collect_current(ms, amo, strict_order_snapshot=False)
    atomic_json(RAW_CACHE, data)
    plan = prepare_night(ms, amo, data, journal, apply_recovery=args.apply)
    result = {"backup": backup, "counts": plan["counts"], "processed": 0, "deferred": 0}
    if args.prepare or not args.apply:
        return result
    result["previous_verification"] = verify_journal(ms, amo, journal, full_data=data)
    candidates = sorted((r for r in plan["rows"] if r["category"] == "CREATE"), key=lambda r: (r.get("moment", ""), r["order_id"]))
    roster = get_roster()
    limit = args.max_orders if args.max_orders is not None else len(candidates)
    created_ids = set()
    def at_limit() -> bool:
        return len(journal.get("run_attempts", {}).get(journal["active_run"], [])) >= limit
    for start in range(0, len(candidates), args.batch_size):
        if at_limit():
            break
        batch = candidates[start:start + args.batch_size]
        for row in batch:
            if at_limit():
                break
            oid = row["order_id"]
            if journal["rows"].get(oid, {}).get("status") in TERMINAL:
                continue
            deadline = time.monotonic() + 1800
            while True:
                try:
                    if apply_one(data, ms, amo, journal, row, roster):
                        result["processed"] += 1
                        created_ids.add(oid)
                    break
                except Temporary:
                    if time.monotonic() >= deadline:
                        raise Stop("NETWORK_UNAVAILABLE_30_MINUTES")
                    print("Связь временно недоступна; журнал сохранён, проверю через минуту.", flush=True)
                    time.sleep(60)
                    # Recover before any new POST; a timeout may follow a completed write.
                    recover_pending(data, ms, amo, journal)
                    if journal["rows"].get(oid, {}).get("status") in TERMINAL:
                        if journal["rows"][oid]["status"] == "VERIFIED":
                            result["processed"] += 1
                            created_ids.add(oid)
                        break
                except UnknownWrite:
                    result["deferred"] += 1
                    break
                except RowDeferred as exc:
                    _set_entry(journal, oid, status="DEFERRED", reason=str(exc))
                    result["deferred"] += 1
                    break
        if created_ids:
            verify_journal(ms, amo, journal, created_ids)
        print(f"Партия проверена. Новых сделок: {result['processed']}; отложено: {result['deferred']}.", flush=True)
    # A fresh complete index proves no processed UUID becomes CREATE again.
    after, _ = collect_current(ms, amo, strict_order_snapshot=False)
    result["verification"] = verify_journal(ms, amo, journal, full_data=after)
    final_plan = prepare_night(ms, amo, after, journal, apply_recovery=True)
    atomic_json(RAW_CACHE, after)
    result["counts_after"] = final_plan["counts"]
    result["remaining"] = sum(r["category"] == "CREATE" and journal["rows"].get(r["order_id"], {}).get("status") not in TERMINAL for r in final_plan["rows"])
    journal["last_run"] = result
    save_journal(journal)
    return result


def self_test() -> None:
    """Regression checks use in-memory API doubles, never production writes."""
    import unittest
    from unittest.mock import patch
    import tempfile

    module = sys.modules[__name__]

    class Regression(unittest.TestCase):
        def test_person_type_does_not_choose_entity(self):
            for kind in ("individual", "legal", "entrepreneur", ""):
                self.assertEqual(buyer_identity({"companyType": kind, "name": "Анна"}), ("Анна", ""))
            self.assertEqual(buyer_identity({"companyType": "legal", "name": "ООО Магазин"})[1], "DEFERRED_BUSINESS_CUSTOMER")
            self.assertTrue(buyer_identity({"name": "Анна Анна"})[1])

        def test_phone_and_money(self):
            self.assertEqual(phone_norm("8 (999) 111-22-33"), "+79991112233")
            self.assertEqual(phone_norm("9991112233", "Россия"), "+79991112233")
            self.assertEqual(phone_norm("9991112233"), "")
            self.assertEqual(phone_norm("+442012345678"), "+442012345678")
            self.assertEqual(phone_norm("+79991112233, +79992223344"), "")
            self.assertEqual(rubles_round(123450), 1235)

        def test_full_payment_allocated_not_document_total(self):
            order = {"id": "o", "sum": 10000}
            self.assertEqual(payment_close(order, {"o": [("2026-10-01", 4000), ("2026-10-03", 6000)]}), (10000, "2026-10-03"))
            self.assertEqual(payment_close(order, {"o": [("2026-10-01", 4000)]}), (4000, None))

        def test_return_once_and_all_goods_without_delivery(self):
            goods = {"quantity": 1, "assortment": {"meta": {"href": "/product/g", "type": "product"}}}
            shipping = {"quantity": 1, "assortment": {"name": "Доставка", "meta": {"href": "/service/d", "type": "service"}}}
            order = {"sum": 11000, "positions": {"rows": [goods, shipping]}}
            self.assertEqual(return_state(order, [{"sum": 10000, "positions": {"rows": [goods]}}])[:2], ("full", 10000))
            self.assertEqual(return_state(order, [{"sum": 5000, "positions": {"rows": [{**goods, "quantity": .5}]}}])[:2], ("partial", 5000))
            self.assertEqual(return_state(order, [{"sum": 12000, "positions": {"rows": [goods]}}])[0], "deferred")

        def test_unknown_post_is_never_retried(self):
            j = {"rows": {}, "counterparty_contacts": {}, "active_run": "test"}
            row = {"phone": "+79991112233", "email": "", "counterparty_id": "cp"}
            with patch.object(module, "save_journal"), patch.object(module, "amo_session", return_value=object()), \
                    patch.object(module, "api_write", return_value=(None, {}, "Timeout")) as write, \
                    patch.object(module, "exact_contacts", return_value=[]):
                with self.assertRaises(UnknownWrite):
                    create_entity(type("A", (), {"base": "https://example"})(), j, "o", row, "contact", {})
                self.assertEqual(write.call_count, 1)
                self.assertEqual(j["rows"]["o"]["status"], "QUARANTINED")
                self.assertTrue(identity_blocked(j, {"counterparty_id": "other", "phone": row["phone"]}))

        def test_lead_cap_includes_uncertain_attempt(self):
            j = {"rows": {}, "counterparty_contacts": {}, "active_run": "test"}
            with patch.object(module, "save_journal"), patch.object(module, "amo_session", return_value=object()), \
                    patch.object(module, "api_write", return_value=(500, {}, "")), \
                    patch.object(module, "exact_leads", return_value=[]):
                with self.assertRaises(UnknownWrite):
                    create_entity(type("A", (), {"base": "https://example"})(), j, "o", {}, "lead", {})
            self.assertEqual(j["run_attempts"]["test"], ["o"])

        def test_existing_backlink_never_written_again(self):
            amo = type("A", (), {"base": "https://example.amocrm.ru"})()
            target = amo_lead_url(amo, "1")
            order = {"attributes": [{"id": MS_AMO_ATTR, "value": target}]}
            j = {"rows": {}}
            with patch.object(module, "save_journal"), patch.object(module, "ms_session"), \
                    patch.object(module, "fresh_order", return_value=order), \
                    patch.object(module, "confirm_lead"), patch.object(module, "api_write") as write:
                set_backlink(object(), amo, j, "o", "1", {}, {}, "2")
                write.assert_not_called()
                self.assertEqual(j["rows"]["o"]["status"], "VERIFIED")

        def test_foreign_backlink_is_system_stop(self):
            j = {"rows": {}}
            with patch.object(module, "save_journal"), patch.object(module, "ms_session"), \
                    patch.object(module, "fresh_order", return_value={"attributes": [{"id": MS_AMO_ATTR, "value": "https://other"}]}), \
                    patch.object(module, "api_write") as write:
                with self.assertRaises(Stop):
                    set_backlink(object(), type("A", (), {"base": "https://example"})(), j, "o", "1", {}, {}, "2")
                write.assert_not_called()

        def test_contact_fields_do_not_cross_match(self):
            contact = {"id": 1, "name": "Анна", "custom_fields_values": [
                {"field_code": "EMAIL", "values": [{"value": "79991112233@example.com"}]}]}
            with patch.object(module, "amo_read", return_value=(200, contact)):
                result = confirm_contact(object(), "1", {"buyer": "Анна", "phone": "+79991112233"})
            self.assertTrue(result.get("_migration_identity_mismatch"))

        def test_pagination_duplicate_before_204_stops(self):
            page = [{"id": i} for i in range(1, 251)]
            with patch.object(module, "amo_request", side_effect=[(200, {"_embedded": {"contacts": page}}), (200, {"_embedded": {"contacts": page}})]), patch.object(module.time, "sleep"):
                with self.assertRaises(Stop):
                    amo_pages(object(), "/contacts", "contacts")

        def test_payload_is_contact_only(self):
            row = {"net_kopecks": 10000, "total_kopecks": 10000, "payment_kopecks": 10000,
                   "payment_moment": "2026-10-01 12:00:00", "close_moment": "2026-10-01 12:00:00",
                   "composition": "Товар", "return_kopecks": 0, "return_state": "none",
                   "counterparty_id": "cp", "buyer": "Анна", "state": "Выполнен", "order_id": "o"}
            payload = make_lead_payload(object(), {"id": "o", "moment": "2026-10-01 11:00:00"}, row, "5", {})
            self.assertEqual(payload["_embedded"], {"contacts": [{"id": 5, "is_main": True}]})
            self.assertNotIn("source_id", payload)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Regression)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise Stop("REGRESSION_TESTS_FAILED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply an audited plan")
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--max-orders", type=int, default=None,
                        help="maximum new lead attempts, including uncertain writes (legacy default 5)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--night-run", action="store_true")
    parser.add_argument("--prepare", action="store_true", help="refresh plan and journal without CRM writes")
    parser.add_argument("--verify", action="store_true", help="read back every previously verified import")
    parser.add_argument("--self-test", action="store_true", help="run isolated regression tests; no CRM access")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--expected-code-sha256", default="", help="required for night writes; rejects an unreviewed importer")
    parser.add_argument("--reuse-raw", action="store_true",
                        help="reclassify the protected read-only source snapshot without refetching")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.night_run and args.apply:
        code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if args.expected_code_sha256 != code_hash:
            raise Stop("Ночной запуск требует SHA256 проверенного импортёра (--expected-code-sha256)")
    private_storage()
    with LOCK.open("a+") as lockfile:
        os.chmod(LOCK, 0o600)
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Stop("Уже работает другой экземпляр переноса") from exc
        if not 1 <= args.batch_size <= 25:
            raise Stop("Размер партии должен быть от 1 до 25")
        if args.max_orders is not None and not 1 <= args.max_orders <= 5000:
            raise Stop("Лимит создания должен быть от 1 до 5000")
        if args.verify:
            if args.apply or args.night_run or args.prepare:
                raise Stop("--verify является отдельной проверкой без записи в CRM")
            ms, amo = lib.MS(), assign_shift.amo_client()
            data, _ = collect_current(ms, amo, strict_order_snapshot=False)
            print(json.dumps(verify_journal(ms, amo, journal_load(), full_data=data), ensure_ascii=False))
            return
        if args.night_run or args.prepare:
            if args.prepare and args.apply:
                raise Stop("--prepare не совместим с --apply")
            print(json.dumps(night_run(args), ensure_ascii=False))
            return
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
        if args.max_orders is None:
            args.max_orders = 5
        if args.max_orders < 1 or args.max_orders > 5000:
            raise Stop("Размер запуска должен быть от 1 до 5000 заказов")
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
