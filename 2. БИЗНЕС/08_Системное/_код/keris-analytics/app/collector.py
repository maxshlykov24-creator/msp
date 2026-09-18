"""Сбор среза amoCRM + bookings → snapshot.json.

Правила — `04_Производство/Активные/Keris_Club/03_Проекты/Дашборд/ПАСПОРТ_МЕТРИК.md`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.amocrm import AmoClient
from app.bookings import fetch_bookings, is_visit
from app.config import TEST_NAME_MARKERS, settings

log = logging.getLogger("collector")


class SchemaError(Exception):
    """Структура amoCRM не совпадает с ожидаемой — публиковать срез нельзя."""


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def _now() -> datetime:
    return datetime.now(_tz())


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(_tz()).replace(tzinfo=None)


def _day_start(now: datetime, days_back: int = 0) -> datetime:
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=days_back)


def is_test_lead(lead: dict[str, Any]) -> bool:
    name = str(lead.get("name") or "")
    return any(m in name for m in TEST_NAME_MARKERS)


def _when(unix: int | None) -> datetime | None:
    if not unix:
        return None
    return datetime.fromtimestamp(int(unix), _tz())


def _in_range(unix: int | None, start: datetime, end: datetime) -> bool:
    dt = _when(unix)
    if dt is None:
        return False
    return start <= dt < end


def _cf(lead: dict[str, Any], field_id: int) -> Any:
    for field in lead.get("custom_fields_values") or []:
        if field.get("field_id") == field_id:
            values = field.get("values") or []
            if values:
                return values[0].get("value")
    return None


def validate_schema(client: AmoClient) -> dict[int, dict[str, Any]]:
    account = client.account()
    if int(account.get("id") or 0) != settings.amocrm_account_id:
        raise SchemaError(
            f"account_id не совпадает: ожидали {settings.amocrm_account_id}, "
            f"получили {account.get('id')}"
        )
    pipelines = {p["id"]: p for p in client.pipelines()}
    for pid, label in (
        (settings.pipeline_sales, "Продажи"),
        (settings.pipeline_puppies, "Щенки"),
        (settings.pipeline_installment, "Рассрочка"),
    ):
        if pid not in pipelines:
            raise SchemaError(f"воронка {label} (id={pid}) не найдена")

    sales_ids = {
        s["id"] for s in pipelines[settings.pipeline_sales].get("_embedded", {}).get("statuses", [])
    }
    needed = {
        settings.status_new,
        settings.status_in_work,
        settings.status_wait_litter,
        settings.status_booked,
        settings.status_sold,
        settings.status_lost,
    }
    missing = needed - sales_ids
    if missing:
        raise SchemaError(f"в воронке Продажи нет этапов {sorted(missing)}")
    return pipelines


def _plural(num: int, one: str, few: str, many: str) -> str:
    n10, n100 = num % 10, num % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def _pretty_h(mins: int) -> int | float:
    if mins <= 0:
        return 0
    rounded = round(mins / 60, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def _parse_hhmm(raw: str) -> int | None:
    parts = str(raw or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if hours < 0 or minutes < 0 or minutes >= 60:
        return None
    return hours * 60 + minutes


def _overlap_min(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> int:
    lo = max(a0, b0)
    hi = min(a1, b1)
    if hi <= lo:
        return 0
    return int((hi - lo).total_seconds() // 60)


def _shift_window(shift: dict[str, Any]) -> tuple[datetime, datetime] | None:
    try:
        day = datetime.fromisoformat(str(shift.get("date_iso") or "")[:10])
    except ValueError:
        return None
    start_m = _parse_hhmm(str(shift.get("starts_at") or ""))
    end_m = _parse_hhmm(str(shift.get("ends_at") or ""))
    if start_m is None or end_m is None or end_m <= start_m:
        return None
    start = day + timedelta(minutes=start_m)
    end = day + timedelta(minutes=end_m)
    return start, end


def _load_block(
    bookings: list[dict[str, Any]],
    shifts: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    masters_map: dict[str, str],
) -> dict[str, Any]:
    by_master: dict[str, dict[str, int]] = defaultdict(lambda: {"occupied_min": 0, "available_min": 0})
    available = 0
    for shift in shifts:
        window = _shift_window(shift)
        if window is None:
            continue
        mins = _overlap_min(window[0], window[1], start, end)
        if not mins:
            continue
        available += mins
        mid = str(shift.get("master_id") or "")
        by_master[mid]["available_min"] += mins
    occupied = 0
    for row in bookings:
        if str(row.get("status") or "") == "cancelled":
            continue
        starts = row.get("starts_at")
        ends = row.get("ends_at")
        if starts is None or ends is None:
            continue
        mins = _overlap_min(_naive(starts), _naive(ends), start, end)
        if not mins:
            continue
        occupied += mins
        mid = str(row.get("master_id") or "")
        by_master[mid]["occupied_min"] += mins
    masters = []
    for mid, data in sorted(by_master.items(), key=lambda x: -x[1]["occupied_min"]):
        if not data["available_min"] and not data["occupied_min"]:
            continue
        masters.append(
            {
                "id": mid,
                "name": masters_map.get(mid, mid or "без мастера"),
                "occupied_min": data["occupied_min"],
                "available_min": data["available_min"],
                "occupied_h": _pretty_h(data["occupied_min"]),
                "available_h": _pretty_h(data["available_min"]),
            }
        )
    return {
        "occupied_min": occupied,
        "available_min": available,
        "occupied_h": _pretty_h(occupied),
        "available_h": _pretty_h(available),
        "show": available > 0,
        "masters": masters,
    }


def _sleeping_count(bookings: list[dict[str, Any]], now: datetime) -> int:
    by_phone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bookings:
        phone = str(row.get("owner_phone") or "").strip()
        if phone:
            by_phone[phone].append(row)
    count = 0
    closed = {"cancelled", "no_show"}
    for rows in by_phone.values():
        usable = [b for b in rows if b.get("starts_at") is not None]
        future = [
            b for b in usable
            if str(b.get("status") or "") not in closed and _naive(b["starts_at"]) > now
        ]
        if future:
            continue
        visits = [
            b for b in usable
            if is_visit(str(b.get("status") or ""), _naive(b["starts_at"]), now)
        ]
        if not visits:
            continue
        last = max(_naive(b["starts_at"]) for b in visits)
        if (now - last).days >= settings.sleeping_days:
            count += 1
    return count


def _sleeping_task(count: int) -> dict[str, Any] | None:
    if count <= 0:
        return None
    noun = _plural(count, "клиент", "клиента", "клиентов")
    verb = "не был" if noun == "клиент" else "не были"
    return {
        "kind": "sleeping",
        "badge": "салон",
        "title": f"{count} {noun} {verb} {settings.sleeping_days}+ дней",
        "sub": "Можно написать и пригласить на стрижку.",
    }


def _merge_master_load(masters: list[dict[str, Any]], load: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {str(m["id"]): dict(m) for m in masters}
    for row in load.get("masters") or []:
        mid = str(row.get("id") or "")
        extra = {"occupied_h": row.get("occupied_h"), "available_h": row.get("available_h")}
        if mid in by_id:
            by_id[mid].update(extra)
        elif row.get("available_min") or row.get("occupied_min"):
            by_id[mid] = {
                "id": mid,
                "name": row.get("name") or mid,
                "visits": 0,
                "revenue": 0,
                **extra,
            }
    return sorted(by_id.values(), key=lambda m: (-int(m.get("visits") or 0), str(m.get("name") or "")))


def _kennel_period(leads: list[dict[str, Any]], start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
    cohort = [l for l in leads if _in_range(l.get("created_at"), start, end)]
    booked_statuses = {settings.status_booked, settings.status_docs, settings.status_sold}
    booked = [l for l in cohort if l.get("status_id") in booked_statuses]
    cohort_sold = [l for l in cohort if l.get("status_id") == settings.status_sold]
    closed = [
        l for l in leads
        if l.get("status_id") == settings.status_sold and _in_range(l.get("closed_at"), start, end)
    ]
    waiting = sum(1 for l in leads if l.get("status_id") == settings.status_wait_litter)
    sources = []
    for lead in cohort:
        val = _cf(lead, settings.field_source)
        if val not in (None, "", 0, "0"):
            sources.append(str(val))
    top = None
    if sources:
        name, count = Counter(sources).most_common(1)[0]
        top = {"name": name, "count": count, "of": len(cohort)}
    sold_sum = sum(int(l.get("price") or 0) for l in closed)
    sold_n = len(closed)
    return {
        "leads": len(cohort),
        "booked": len(booked),
        "sold": sold_n,
        "sold_sum": sold_sum,
        "avg_check": round(sold_sum / sold_n) if sold_n else 0,
        "show_avg_check": sold_n > 0,
        "waiting_litter": waiting,
        "show_waiting": waiting > 0,
        "show_source": bool(top),
        "funnel": [
            {"name": "Заявка", "count": len(cohort)},
            {"name": "Бронь", "count": len(booked)},
            {"name": "Продано", "count": len(cohort_sold)},
        ],
        "top_source": top,
        "prev_leads": None,
    }


def _stale_task(leads: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    early = {settings.status_new, settings.status_in_work}
    stale = []
    newest = None
    for lead in leads:
        if lead.get("status_id") not in early:
            continue
        updated = _when(lead.get("updated_at"))
        if updated is None or (now - updated) <= timedelta(hours=24):
            continue
        stale.append(lead)
        created = _when(lead.get("created_at"))
        if created and (newest is None or created > newest):
            newest = created
    if not stale:
        return None
    last = newest.strftime("%d.%m") if newest else "—"
    return {
        "kind": "stale_leads",
        "badge": "сейчас",
        "title": f"{len(stale)} заявок без ответа больше суток",
        "sub": f"Почти все висят в новых. Последняя заявка {last}.",
    }


def _installment_tasks(leads: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    end = now + timedelta(days=7)
    out = []
    for lead in leads:
        raw = _cf(lead, settings.field_next_pay)
        if raw in (None, "", 0, "0"):
            continue
        try:
            pay = datetime.fromtimestamp(int(raw), _tz())
        except (TypeError, ValueError, OSError):
            continue
        if not (now <= pay < end):
            continue
        out.append({
            "kind": "installment",
            "badge": pay.strftime("%d.%m"),
            "title": "Платёж рассрочки в ближайшие 7 дней",
            "sub": lead.get("name") or "Рассрочка",
        })
    return out


def _grooming_period(
    visits: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    masters_map: dict[str, str],
    show_no_show: bool,
    show_repeat: bool,
) -> dict[str, Any]:
    rows = [b for b in visits if start <= _naive(b["starts_at"]) < end]
    empty = {
        "revenue": 0,
        "visits": 0,
        "avg_check": 0,
        "show_avg_check": False,
        "no_show": 0,
        "repeat": 0,
        "show_no_show": False,
        "show_repeat": False,
        "masters": [],
        "prev_visits": None,
    }
    if not rows:
        return empty
    by_master: dict[str, dict[str, Any]] = defaultdict(lambda: {"visits": 0, "revenue": 0})
    phones: dict[str, int] = defaultdict(int)
    for row in rows:
        mid = str(row.get("master_id") or "")
        by_master[mid]["visits"] += 1
        by_master[mid]["revenue"] += int(row.get("price") or 0)
        phone = str(row.get("owner_phone") or "")
        if phone:
            phones[phone] += 1
    masters = [
        {
            "id": mid,
            "name": masters_map.get(mid, mid or "без мастера"),
            "visits": data["visits"],
            "revenue": data["revenue"],
        }
        for mid, data in sorted(by_master.items(), key=lambda x: -x[1]["visits"])
    ]
    revenue = sum(int(r.get("price") or 0) for r in rows)
    visits_n = len(rows)
    return {
        "revenue": revenue,
        "visits": visits_n,
        "avg_check": round(revenue / visits_n) if visits_n else 0,
        "show_avg_check": visits_n > 0,
        "no_show": 0,
        "repeat": sum(1 for c in phones.values() if c >= 2),
        "show_no_show": False,
        "show_repeat": show_repeat and sum(1 for c in phones.values() if c >= 2) > 0,
        "masters": masters,
        "prev_visits": None,
    }


def collect(client: AmoClient) -> dict[str, Any]:
    validate_schema(client)
    now = _now()
    today0 = _day_start(now)
    week0 = today0 - timedelta(days=7)
    month0 = today0 - timedelta(days=30)
    prev_week0 = week0 - timedelta(days=7)
    prev_month0 = month0 - timedelta(days=30)
    end = now + timedelta(seconds=1)

    sales = [l for l in client.leads(settings.pipeline_sales) if not is_test_lead(l)]
    installment = [l for l in client.leads(settings.pipeline_installment) if not is_test_lead(l)]

    week_k = _kennel_period(sales, week0, end, now)
    month_k = _kennel_period(sales, month0, end, now)
    week_k["prev_leads"] = _kennel_period(sales, prev_week0, week0, now)["leads"]
    month_k["prev_leads"] = _kennel_period(sales, prev_month0, month0, now)["leads"]

    bookings, masters_rows, shifts = fetch_bookings()
    now_naive = _naive(now)
    usable = [b for b in bookings if b.get("starts_at") is not None]
    has_no_show = any(str(b.get("status")) == "no_show" for b in usable)
    visits = [
        b for b in usable
        if is_visit(str(b.get("status")), _naive(b["starts_at"]), now_naive)
    ]
    no_shows = [b for b in usable if str(b.get("status")) == "no_show"]
    masters_map = {str(m["id"]): str(m.get("name") or m["id"]) for m in masters_rows}
    ahead = _load_block(
        usable, shifts, now_naive, _naive(now + timedelta(days=7)), masters_map
    )
    sleeping = _sleeping_count(usable, now_naive)

    def groom(start: datetime, end: datetime, repeat: bool) -> dict[str, Any]:
        block = _grooming_period(visits, _naive(start), _naive(end), masters_map, has_no_show, repeat)
        if has_no_show:
            ns = [b for b in no_shows if _naive(start) <= _naive(b["starts_at"]) < _naive(end)]
            block["no_show"] = len(ns)
            block["show_no_show"] = True
        load = _load_block(usable, shifts, _naive(start), _naive(end), masters_map)
        block["load"] = load
        block["ahead"] = ahead
        block["sleeping"] = sleeping
        block["show_sleeping"] = sleeping > 0
        block["masters"] = _merge_master_load(block["masters"], load)
        return block

    week_g = groom(week0, end, False)
    month_g = groom(month0, end, True)
    week_g["prev_visits"] = groom(prev_week0, week0, False)["visits"]
    month_g["prev_visits"] = groom(prev_month0, month0, True)["visits"]

    tomorrow0 = today0 + timedelta(days=1)
    day_after = today0 + timedelta(days=2)
    pending_tomorrow = [
        b for b in usable
        if str(b.get("status")) == "pending"
        and _naive(tomorrow0) <= _naive(b["starts_at"]) < _naive(day_after)
    ]
    today_visits = [
        b for b in usable
        if str(b.get("status")) not in {"cancelled"}
        and _naive(today0) <= _naive(b["starts_at"]) < _naive(tomorrow0)
    ]
    upcoming = sorted(
        (b for b in today_visits if _naive(b["starts_at"]) >= now_naive),
        key=lambda b: _naive(b["starts_at"]),
    )
    next_at = upcoming[0]["starts_at"] if upcoming else None
    next_label = _naive(next_at).strftime("%H:%M") if next_at is not None else None

    tasks: list[dict[str, Any]] = []
    stale = _stale_task(sales, now)
    if stale:
        tasks.append(stale)
    tasks.extend(_installment_tasks(installment, now))
    if pending_tomorrow:
        tasks.append({
            "kind": "unconfirmed",
            "badge": "завтра",
            "title": f"{len(pending_tomorrow)} записей без подтверждения",
            "sub": "Администратор ещё не подтвердил визит.",
        })
    sleeping_card = _sleeping_task(sleeping)
    if sleeping_card:
        tasks.append(sleeping_card)

    today_leads = sum(1 for l in sales if _in_range(l.get("created_at"), today0, tomorrow0))

    snapshot = {
        "collected_at": now.isoformat(timespec="seconds"),
        "age_minutes": 0,
        "today": {
            "date": today0.date().isoformat(),
            "tasks": tasks,
            "kennel_new_leads": today_leads,
            "grooming_visits": len(today_visits),
            "grooming_next": next_label,
        },
        "periods": {
            "week": {
                "label": "за 7 дней",
                "prev_label": "на прошлой неделе",
                "kennel": week_k,
                "grooming": week_g,
            },
            "month": {
                "label": "за 30 дней",
                "prev_label": "за предыдущие 30 дней",
                "kennel": month_k,
                "grooming": month_g,
            },
        },
        "meta": {
            "sales_leads": len(sales),
            "bookings": len(bookings),
            "has_no_show": has_no_show,
        },
    }
    log.info(
        "срез: заявок продаж %s, записей %s, сегодня задач %s",
        len(sales),
        len(bookings),
        len(tasks),
    )
    return snapshot


def snapshot_path() -> Path:
    return Path(settings.data_dir) / "snapshot.json"


def state_path() -> Path:
    return Path(settings.data_dir) / "state.json"


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_collection() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    state = read_state()
    try:
        with AmoClient() as client:
            snapshot = collect(client)
        _write_atomic(snapshot_path(), snapshot)
        state.update(
            {
                "last_success_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_error": None,
                "schema_ok": True,
                "leads_count": snapshot["meta"]["sales_leads"],
                "bookings_count": snapshot["meta"]["bookings"],
                "duration_sec": round(
                    (datetime.now(timezone.utc) - started).total_seconds(), 1
                ),
            }
        )
        _write_atomic(state_path(), state)
        return state
    except Exception as e:  # noqa: BLE001
        log.exception("сбор не удался")
        state["last_error"] = str(e)
        state["schema_ok"] = not isinstance(e, SchemaError)
        _write_atomic(state_path(), state)
        return state
