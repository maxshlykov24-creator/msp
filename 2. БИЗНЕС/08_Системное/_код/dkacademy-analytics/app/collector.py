from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.amo_client import AmoClient
from app.config import settings
from app.database import SessionLocal, init_db
from app.models import MetricDaily, Snapshot, SyncState

log = logging.getLogger("collector")
MSK = ZoneInfo("Europe/Moscow")

ACTIVE_PIPELINES = [settings.pipeline_sales, settings.pipeline_repeat]
SOURCE_FIELD_ID = 2816115  # поле «Источник»

# Глубина истории для бэкфилла (мес.)
BACKFILL_MONTHS = 12
# Регулярное окно потоков. Поздние успехи старых когорт обновляются отдельно
# через lead_status_changed, поэтому полный 62-дневный перечит больше не нужен.
REFRESH_DAYS = 14
# Событийные метрики (сообщения/диалоги/ответы) тяжёлые — в регулярном прогоне
# тянем только за последние дни; остальную историю заполняет разовый бэкфилл.
EVENT_REFRESH_DAYS = 4
# Потолок задержки ответа для усреднения (мин); выбросы не должны искажать avg.
RT_CAP_MIN = 480

# Бакеты SLA первого/любого ответа (границы в минутах).
SLA_BUCKETS = [5, 15, 30, 60]

CREATED_METRICS = {
    "new_leads", "converted", "cohort_won",
    "repeat_created", "repeat_cohort_won",
}
CLOSED_METRICS = {
    "paid", "revenue", "repeat_deals", "repeat_revenue", "cycle_sum", "cycle_cnt",
}
CALL_METRICS = {"calls_out", "call_minutes"}
EVENT_METRICS = {
    "invoice_count", "invoice_sum", "msg_in", "msg_out", "dialogs", "missed",
    "rt_sum", "rt_cnt", "first_rt_sum", "first_rt_cnt",
    "rt_le5", "rt_le15", "rt_le30", "rt_le60", "rt_gt60",
    *{f"hour_msg_{h:02d}" for h in range(24)},
    *{f"hour_miss_{h:02d}" for h in range(24)},
}


# ──────────────────────────── helpers ────────────────────────────

def _now_msk() -> datetime:
    return datetime.now(MSK)


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _prev_month_start(dt: datetime) -> datetime:
    first = _month_start(dt)
    return _month_start(first - timedelta(days=1))


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _days_for_window(start: datetime, end: datetime) -> set[str]:
    days: set[str] = set()
    cursor = start.date()
    while cursor <= end.date():
        days.add(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _msk_dt(unix_ts: int | None) -> datetime | None:
    if not unix_ts:
        return None
    return datetime.fromtimestamp(unix_ts, MSK)


def _msk_day(unix_ts: int | None) -> str | None:
    dt = _msk_dt(unix_ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def _cf_value(entity: dict, field_id: int) -> str | None:
    for cf in entity.get("custom_fields_values") or []:
        if cf.get("field_id") == field_id:
            vals = cf.get("values") or []
            if vals:
                return vals[0].get("value")
    return None


def _source_of(lead: dict) -> str:
    val = (_cf_value(lead, SOURCE_FIELD_ID) or "Не определён").strip() or "Не определён"
    return val[:56]  # scope 'src:<name>' должен помещаться в varchar(64)


def _save_snapshot(db, section: str, payload: dict) -> None:
    stmt = pg_insert(Snapshot).values(section=section, payload=payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Snapshot.section],
        set_={"payload": payload, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)
    db.commit()


def _snapshot_payload(db, section: str) -> dict:
    row = db.get(Snapshot, section)
    return row.payload if row else {}


def _set_state(db, key: str, value: str) -> None:
    stmt = pg_insert(SyncState).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SyncState.key],
        set_={"value": value, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)
    db.commit()


def _get_state(db, key: str) -> str | None:
    row = db.get(SyncState, key)
    return row.value if row else None


class DayAgg:
    """Аккумулятор подённых метрик: M[day][scope][metric] += value."""

    def __init__(self) -> None:
        self.m: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )

    def add(self, day: str | None, scope: str, metric: str, val: float = 1.0) -> None:
        if not day:
            return
        self.m[day][scope][metric] += val

    def add_all(self, day, metric, val, *, mgr=None, src=None) -> None:
        """Добавить в scope all и (опц.) в mgr:<id> и src:<name>."""
        self.add(day, "all", metric, val)
        if mgr:
            self.add(day, f"mgr:{mgr}", metric, val)
        if src:
            self.add(day, f"src:{src}", metric, val)

    def flush(
        self,
        db,
        *,
        replace_days: set[str] | None = None,
        replace_metrics: set[str] | None = None,
    ) -> int:
        """Полностью заменить рассчитанные метрики в заданных днях.

        Удаление перед upsert важно: если значение стало нулём, старый агрегат
        не должен оставаться в БД.
        """
        if replace_days and replace_metrics:
            db.execute(
                delete(MetricDaily).where(
                    MetricDaily.day.in_(replace_days),
                    MetricDaily.metric.in_(replace_metrics),
                )
            )
        rows = []
        for day, scopes in self.m.items():
            if replace_days is not None and day not in replace_days:
                continue
            for scope, metrics in scopes.items():
                for metric, val in metrics.items():
                    rows.append(
                        {"day": day, "scope": scope, "metric": metric,
                         "fact": float(val), "plan": None}
                    )
        if not rows:
            db.commit()
            return 0
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            stmt = pg_insert(MetricDaily).values(chunk)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_metric_daily",
                set_={"fact": stmt.excluded.fact, "updated_at": datetime.utcnow()},
            )
            db.execute(stmt)
        db.commit()
        return len(rows)


# ──────────────────────────── fetchers ────────────────────────────

def _fetch_users(amo: AmoClient) -> dict[int, str]:
    out: dict[int, str] = {}
    data = amo.get("/api/v4/users", {"limit": 250}) or {}
    for u in data.get("_embedded", {}).get("users", []):
        out[u["id"]] = u.get("name") or f"#{u['id']}"
    return out


def _fetch_leads_created(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    params = {
        "filter[created_at][from]": from_ts,
        "filter[created_at][to]": to_ts,
        "with": "contacts",
        "limit": 250,
    }
    try:
        return list(amo.paginate("/api/v4/leads", "leads", params))
    except Exception as exc:  # noqa: BLE001
        log.warning("leads created fetch: %s", exc)
        return []


def _fetch_leads_closed(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    params = {
        "filter[closed_at][from]": from_ts,
        "filter[closed_at][to]": to_ts,
        "with": "contacts",
        "limit": 250,
    }
    try:
        return list(amo.paginate("/api/v4/leads", "leads", params))
    except Exception as exc:  # noqa: BLE001
        log.warning("leads closed fetch: %s", exc)
        return []


def _fetch_leads_active(amo: AmoClient, pipeline_id: int) -> list[dict]:
    meta = amo.get(f"/api/v4/leads/pipelines/{pipeline_id}") or {}
    statuses = [
        status.get("id")
        for status in (meta.get("_embedded") or {}).get("statuses", [])
        if status.get("id") not in (settings.status_won, settings.status_lost)
    ]
    params: dict[str, int] = {"limit": 250}
    for index, status_id in enumerate(statuses):
        params[f"filter[statuses][{index}][pipeline_id]"] = pipeline_id
        params[f"filter[statuses][{index}][status_id]"] = status_id
    if not statuses:
        params["filter[pipeline_id]"] = pipeline_id
    return list(amo.paginate("/api/v4/leads", "leads", params))


def _fetch_leads_by_ids(amo: AmoClient, ids: set[int]) -> list[dict]:
    """Загрузить карточки любых лидов, включая закрытые."""
    out: list[dict] = []
    clean = sorted(i for i in ids if isinstance(i, int) and i > 0)
    for start in range(0, len(clean), 100):
        chunk = clean[start:start + 100]
        params: dict[str, int | str] = {"with": "contacts", "limit": 250}
        for pos, lead_id in enumerate(chunk):
            params[f"filter[id][{pos}]"] = lead_id
        try:
            out.extend(amo.paginate("/api/v4/leads", "leads", params))
        except Exception as exc:  # noqa: BLE001
            log.warning("leads by ids fetch (%d): %s", len(chunk), exc)
    return out


def _ensure_leads_index(amo: AmoClient, lead_index: dict[int, dict], ids: set[int]) -> None:
    missing = ids - set(lead_index)
    for lead in _fetch_leads_by_ids(amo, missing):
        if lead.get("id"):
            lead_index[lead["id"]] = lead


def _fetch_call_notes(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    # API notes фильтрует по updated_at. Бизнес-дата метрики — created_at,
    # поэтому читаем изменения до текущего момента, а затем применяем строгую
    # границу created_at и дедупликацию. Так не теряются старые звонки,
    # отредактированные уже после исходного периода.
    params = {
        "filter[note_type][0]": "call_in",
        "filter[note_type][1]": "call_out",
        "filter[updated_at][from]": from_ts,
        "filter[updated_at][to]": int(_now_msk().timestamp()),
        "limit": 250,
    }
    try:
        notes = list(amo.paginate("/api/v4/leads/notes", "notes", params))
        seen: set[int] = set()
        return [
            note for note in notes
            if from_ts <= int(note.get("created_at") or 0) <= to_ts
            and (not note.get("id") or note["id"] not in seen)
            and (not note.get("id") or not seen.add(note["id"]))
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("call notes fetch: %s", exc)
        return []


def _fetch_events(amo: AmoClient, types: list[str], from_ts: int, to_ts: int) -> list[dict]:
    params = {
        "filter[created_at][from]": from_ts,
        "filter[created_at][to]": to_ts,
        "limit": 100,
    }
    for i, t in enumerate(types):
        params[f"filter[type][{i}]"] = t
    try:
        return list(amo.paginate("/api/v4/events", "events", params))
    except Exception as exc:  # noqa: BLE001 — типы событий могут быть недоступны
        log.warning("events %s не получены: %s", types, exc)
        return []


def _status_after(event: dict) -> tuple[int | None, int | None]:
    """Извлечь (status_id, pipeline_id) из разных форматов события amoCRM."""
    raw = event.get("value_after")
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        if not isinstance(value, dict):
            continue
        candidate = value.get("lead_status") or value.get("status") or value
        if not isinstance(candidate, dict):
            continue
        status_id = candidate.get("id") or candidate.get("status_id")
        pipeline_id = candidate.get("pipeline_id")
        try:
            status_id = int(status_id) if status_id is not None else None
            pipeline_id = int(pipeline_id) if pipeline_id is not None else None
        except (TypeError, ValueError):
            continue
        if status_id is not None:
            return status_id, pipeline_id
    return None, None


def _fetch_tasks_open(amo: AmoClient) -> list[dict]:
    params = {"filter[is_completed]": 0, "limit": 250}
    return list(amo.paginate("/api/v4/tasks", "tasks", params))


# ──────────────────────────── window aggregation ────────────────────────────

def _sla_bucket(minutes: float) -> str:
    for b in SLA_BUCKETS:
        if minutes <= b:
            return f"rt_le{b}"
    return "rt_gt60"


def _response_deltas(events_by_lead: dict[int, list[dict]]):
    """Для каждого лида: пары входящее→следующее исходящее = время ответа.

    Возвращает список (day, lead_id, delta_min, is_first).
    """
    out = []
    for lead_id, evs in events_by_lead.items():
        evs.sort(key=lambda e: e.get("created_at") or 0)
        pending_in: int | None = None
        first_done = False
        for e in evs:
            t = e.get("created_at") or 0
            if e.get("type") == "incoming_chat_message":
                if pending_in is None:
                    pending_in = t
            elif e.get("type") == "outgoing_chat_message":
                if pending_in is not None:
                    delta_min = max(0.0, (t - pending_in) / 60.0)
                    day = _msk_day(pending_in)
                    out.append((day, lead_id, delta_min, not first_done))
                    first_done = True
                    pending_in = None
    return out


def _collect_created_leads(
    amo: AmoClient,
    agg: DayAgg,
    from_ts: int,
    to_ts: int,
) -> None:
    """Когорты по created_at; успех определяется текущим статусом карточки."""
    for lead in _fetch_leads_created(amo, from_ts, to_ts):
        day = _msk_day(lead.get("created_at"))
        rid = lead.get("responsible_user_id")
        src = _source_of(lead)
        status_id = lead.get("status_id")
        pipeline_id = lead.get("pipeline_id")
        if pipeline_id == settings.pipeline_sales:
            agg.add_all(day, "new_leads", 1, mgr=rid, src=src)
            if status_id == settings.status_won:
                agg.add_all(day, "converted", 1, mgr=rid, src=src)
                agg.add_all(day, "cohort_won", 1, mgr=rid, src=src)
        elif pipeline_id == settings.pipeline_repeat:
            agg.add_all(day, "repeat_created", 1, mgr=rid)
            if status_id == settings.status_won:
                agg.add_all(day, "repeat_cohort_won", 1, mgr=rid)


def _collect_leads_calls(
    amo: AmoClient,
    agg: DayAgg,
    from_ts: int,
    to_ts: int,
    lead_index: dict[int, dict],
) -> None:
    """Лёгкая часть окна: лиды (создание/закрытие) и звонки."""

    _collect_created_leads(amo, agg, from_ts, to_ts)

    # — выигранные (по дате закрытия) = деньги —
    # Приход/оплачено основного дашборда — ТОЛЬКО воронка «Продажи».
    # Повторные продажи учитываются отдельно (repeat_deals / repeat_revenue).
    closed = _fetch_leads_closed(amo, from_ts, to_ts)
    for l in closed:
        if l.get("status_id") != settings.status_won:
            continue
        day = _msk_day(l.get("closed_at"))
        rid = l.get("responsible_user_id")
        src = _source_of(l)
        price = int(l.get("price") or 0)
        pl = l.get("pipeline_id")
        if pl == settings.pipeline_repeat:
            agg.add_all(day, "repeat_deals", 1, mgr=rid)
            agg.add_all(day, "repeat_revenue", price, mgr=rid)
            continue
        if pl != settings.pipeline_sales:
            continue
        agg.add_all(day, "paid", 1, mgr=rid, src=src)
        agg.add_all(day, "revenue", price, mgr=rid, src=src)
        created_at = l.get("created_at")
        closed_at = l.get("closed_at")
        if created_at and closed_at and closed_at >= created_at:
            cycle_days = (closed_at - created_at) / 86400.0
            agg.add(day, "all", "cycle_sum", cycle_days)
            agg.add(day, "all", "cycle_cnt", 1)
            if rid:
                agg.add(day, f"mgr:{rid}", "cycle_sum", cycle_days)
                agg.add(day, f"mgr:{rid}", "cycle_cnt", 1)

    _collect_calls(amo, agg, from_ts, to_ts)


def _collect_calls(amo: AmoClient, agg: DayAgg, from_ts: int, to_ts: int) -> None:
    """Звонки по created_at при серверной выборке notes по updated_at."""
    for n in _fetch_call_notes(amo, from_ts, to_ts):
        day = _msk_day(n.get("created_at"))
        rid = n.get("responsible_user_id")
        ntype = n.get("note_type")
        dur = (n.get("params", {}) or {}).get("duration", 0) or 0
        if ntype == "call_out":
            agg.add(day, "all", "calls_out", 1)
            if rid:
                agg.add(day, f"mgr:{rid}", "calls_out", 1)
        mins = dur / 60.0
        if mins:
            agg.add(day, "all", "call_minutes", mins)
            if rid:
                agg.add(day, f"mgr:{rid}", "call_minutes", mins)


def _collect_invoice_events(
    amo: AmoClient,
    db,
    status_events: list[dict],
    lead_index: dict[int, dict],
) -> set[str]:
    """Счета по дате первого доступного перехода на этап, а не по created_at."""
    invoice_events = []
    for event in status_events:
        status_id, pipeline_id = _status_after(event)
        if status_id == settings.status_invoice and pipeline_id in (None, settings.pipeline_sales):
            invoice_events.append(event)

    ids = {
        event.get("entity_id") for event in invoice_events
        if isinstance(event.get("entity_id"), int)
    }
    _ensure_leads_index(amo, lead_index, ids)

    stored = _snapshot_payload(db, "invoice_facts").get("by_lead", {})
    stored_days = {
        fact.get("day") for fact in stored.values()
        if isinstance(fact, dict) and fact.get("day")
    } if isinstance(stored, dict) else set()
    facts: dict[str, dict] = dict(stored) if isinstance(stored, dict) else {}
    for event in sorted(invoice_events, key=lambda item: item.get("created_at") or 0):
        lead_id = event.get("entity_id")
        if not isinstance(lead_id, int):
            continue
        lead = lead_index.get(lead_id)
        if not lead or lead.get("pipeline_id") != settings.pipeline_sales:
            continue
        event_at = int(event.get("created_at") or 0)
        previous = facts.get(str(lead_id))
        if previous and int(previous.get("event_at") or 0) <= event_at:
            continue
        facts[str(lead_id)] = {
            "event_id": event.get("id"),
            "event_at": event_at,
            "day": _msk_day(event_at),
            "manager_id": lead.get("responsible_user_id"),
            "source": _source_of(lead),
            "amount": int(lead.get("price") or 0),
        }

    _save_snapshot(db, "invoice_facts", {"by_lead": facts})
    agg = DayAgg()
    days: set[str] = set()
    for fact in facts.values():
        day = fact.get("day")
        if not day:
            continue
        days.add(day)
        agg.add_all(
            day,
            "invoice_count",
            1,
            mgr=fact.get("manager_id"),
            src=fact.get("source"),
        )
        agg.add_all(
            day,
            "invoice_sum",
            int(fact.get("amount") or 0),
            mgr=fact.get("manager_id"),
            src=fact.get("source"),
        )
    replace_days = days | stored_days
    agg.flush(db, replace_days=replace_days, replace_metrics={"invoice_count", "invoice_sum"})
    return replace_days


def _collect_events(
    amo: AmoClient,
    agg: DayAgg,
    from_ts: int,
    to_ts: int,
    lead_index: dict[int, dict],
) -> None:
    """Тяжёлая часть окна: сообщения чатов, время ответа, диалоги, пропущенные."""

    chat = _fetch_events(amo, ["incoming_chat_message", "outgoing_chat_message"], from_ts, to_ts)
    talks = _fetch_events(amo, ["talk_created"], from_ts, to_ts)
    missed = _fetch_events(amo, ["talk_missed_event"], from_ts, to_ts)
    all_events = chat + talks + missed
    event_lead_ids = {
        event.get("entity_id") for event in all_events
        if event.get("entity_type") in ("leads", "lead")
        and isinstance(event.get("entity_id"), int)
    }
    _ensure_leads_index(amo, lead_index, event_lead_ids)

    # — сообщения чатов —
    events_by_lead: dict[int, list[dict]] = defaultdict(list)
    seen_event_ids: set[int] = set()
    for e in chat:
        event_id = e.get("id")
        if isinstance(event_id, int) and event_id in seen_event_ids:
            continue
        if isinstance(event_id, int):
            seen_event_ids.add(event_id)
        day = _msk_day(e.get("created_at"))
        ent = e.get("entity_id")
        lead = lead_index.get(ent)
        rid = lead.get("responsible_user_id") if lead else None
        src = _source_of(lead) if lead else None
        metric = "msg_in" if e.get("type") == "incoming_chat_message" else "msg_out"
        agg.add_all(day, metric, 1, mgr=rid, src=src)
        dt = _msk_dt(e.get("created_at"))
        if dt:
            agg.add(day, "all", f"hour_msg_{dt.hour:02d}", 1)
        if e.get("entity_type") in ("leads", "lead") and ent:
            events_by_lead[ent].append(e)

    # — время ответа (из чатов) —
    for day, lead_id, delta_min, is_first in _response_deltas(events_by_lead):
        lead = lead_index.get(lead_id)
        rid = lead.get("responsible_user_id") if lead else None
        src = _source_of(lead) if lead else None
        capped = min(delta_min, RT_CAP_MIN)
        agg.add_all(day, "rt_sum", capped, mgr=rid, src=src)
        agg.add_all(day, "rt_cnt", 1, mgr=rid, src=src)
        bucket = _sla_bucket(delta_min)
        agg.add_all(day, bucket, 1, mgr=rid, src=src)
        if is_first:
            agg.add_all(day, "first_rt_sum", capped, mgr=rid, src=src)
            agg.add_all(day, "first_rt_cnt", 1, mgr=rid, src=src)

    # — диалоги (talk_created) и пропущенные (talk_missed_event) —
    for e in talks:
        day = _msk_day(e.get("created_at"))
        lead = lead_index.get(e.get("entity_id"))
        rid = lead.get("responsible_user_id") if lead else None
        src = _source_of(lead) if lead else None
        agg.add_all(day, "dialogs", 1, mgr=rid, src=src)

    for e in missed:
        day = _msk_day(e.get("created_at"))
        lead = lead_index.get(e.get("entity_id"))
        rid = lead.get("responsible_user_id") if lead else None
        src = _source_of(lead) if lead else None
        agg.add_all(day, "missed", 1, mgr=rid, src=src)
        dt = _msk_dt(e.get("created_at"))
        if dt:
            agg.add(day, "all", f"hour_miss_{dt.hour:02d}", 1)

    # fallback: если talk_created недоступен — считаем диалоги как уникальные
    # пары (лид, день) с входящими сообщениями.
    if not talks and events_by_lead:
        seen: set[tuple[str, int]] = set()
        for lead_id, evs in events_by_lead.items():
            for e in evs:
                if e.get("type") != "incoming_chat_message":
                    continue
                day = _msk_day(e.get("created_at"))
                if not day or (day, lead_id) in seen:
                    continue
                seen.add((day, lead_id))
                lead = lead_index.get(lead_id)
                rid = lead.get("responsible_user_id") if lead else None
                src = _source_of(lead) if lead else None
                agg.add_all(day, "dialogs", 1, mgr=rid, src=src)


def _refresh_changed_cohorts(
    amo: AmoClient,
    db,
    status_events: list[dict],
    lead_index: dict[int, dict],
    regular_from: datetime,
) -> int:
    """Адресно пересчитать старые когорты, затронутые сменой статуса.

    Так успех через 90+ дней обновляет день создания сделки без тяжёлого
    повторного чтения всей истории каждые 10 минут.
    """
    changed_ids = {
        event.get("entity_id") for event in status_events
        if isinstance(event.get("entity_id"), int)
    }
    _ensure_leads_index(amo, lead_index, changed_ids)
    old_days = {
        day
        for lead_id in changed_ids
        for lead in [lead_index.get(lead_id)]
        for day in [_msk_day(lead.get("created_at")) if lead else None]
        if day and day < regular_from.strftime("%Y-%m-%d")
        and lead.get("pipeline_id") in ACTIVE_PIPELINES
    }
    refreshed = 0
    for day in sorted(old_days):
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=MSK)
        end = start + timedelta(days=1) - timedelta(seconds=1)
        agg = DayAgg()
        _collect_created_leads(amo, agg, _ts(start), _ts(end))
        refreshed += agg.flush(
            db,
            replace_days={day},
            replace_metrics=CREATED_METRICS,
        )
    return refreshed


# ──────────────────────────── state snapshots ────────────────────────────


def _state_refresh_due(db, key: str, hours: int) -> bool:
    raw = _get_state(db, key)
    if not raw:
        return True
    try:
        previous = datetime.fromisoformat(raw)
        now = datetime.now(previous.tzinfo) if previous.tzinfo else datetime.utcnow()
        return now - previous >= timedelta(hours=hours)
    except ValueError:
        return True


def _collect_loss_reasons(amo: AmoClient, db) -> None:
    """Тяжёлый фиксированный срез; обновлять не чаще раза в 6 часов."""
    if not _state_refresh_due(db, "last_loss_reasons_at", 6):
        return
    loss_meta = amo.get("/api/v4/leads/loss_reasons") or {}
    reason_names = {
        reason["id"]: reason["name"]
        for reason in loss_meta.get("_embedded", {}).get("loss_reasons", [])
    }
    since = _ts(_now_msk() - timedelta(days=60))
    params = {
        "filter[closed_at][from]": since,
        "filter[statuses][0][pipeline_id]": settings.pipeline_sales,
        "filter[statuses][0][status_id]": settings.status_lost,
        "filter[statuses][1][pipeline_id]": settings.pipeline_repeat,
        "filter[statuses][1][status_id]": settings.status_lost,
        "limit": 250,
    }
    counts: dict[str, int] = defaultdict(int)
    try:
        for lead in amo.paginate("/api/v4/leads", "leads", params):
            name = reason_names.get(lead.get("loss_reason_id"), "Не указана")
            counts[name] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("loss reasons fetch: %s", exc)
        return
    _save_snapshot(db, "loss_reasons", {
        "items": [
            {"label": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda item: -item[1])
        ],
        "window_days": 60,
        "checked_at": _now_msk().isoformat(),
    })
    _set_state(db, "last_loss_reasons_at", datetime.utcnow().isoformat())


def _collect_state(
    amo: AmoClient,
    db,
    users: dict[int, str],
    sales_leads: list[dict],
    repeat_leads: list[dict],
) -> None:
    open_tasks = _fetch_tasks_open(amo)
    all_active = sales_leads + repeat_leads

    # ===== FUNNELS (всего + по менеджерам) =====
    pipelines_meta = amo.get("/api/v4/leads/pipelines") or {}
    stage_order: dict[int, list[dict]] = {}
    for pl in pipelines_meta.get("_embedded", {}).get("pipelines", []):
        if pl["id"] not in ACTIVE_PIPELINES:
            continue
        stages = [
            s for s in pl["_embedded"]["statuses"]
            if s["id"] != settings.status_lost
        ]
        stage_order[pl["id"]] = stages

    def _funnel_for(leads: list[dict], pipeline_id: int) -> list[dict]:
        counts: dict[int, int] = defaultdict(int)
        for l in leads:
            if l.get("pipeline_id") == pipeline_id:
                counts[l.get("status_id")] += 1
        return [
            {"label": s["name"], "count": counts.get(s["id"], 0)}
            for s in stage_order.get(pipeline_id, [])
        ]

    funnels = {
        "sales": _funnel_for(all_active, settings.pipeline_sales),
        "repeat": _funnel_for(all_active, settings.pipeline_repeat),
        "by_mgr": {},
    }
    leads_by_mgr: dict[int, list[dict]] = defaultdict(list)
    for l in all_active:
        rid = l.get("responsible_user_id")
        if rid:
            leads_by_mgr[rid].append(l)
    for rid, leads in leads_by_mgr.items():
        funnels["by_mgr"][str(rid)] = {
            "name": users.get(rid, f"#{rid}"),
            "sales": _funnel_for(leads, settings.pipeline_sales),
            "repeat": _funnel_for(leads, settings.pipeline_repeat),
        }
    _save_snapshot(db, "funnels", funnels)

    # ===== STUCK (застрявшие) =====
    stuck = [
        l for l in all_active
        if l.get("status_id") in (settings.status_noanswer, settings.status_waitlist)
    ]
    stuck_by_stage: dict[int, int] = defaultdict(int)
    stuck_by_mgr: dict[int, int] = defaultdict(int)
    for l in stuck:
        stuck_by_stage[l.get("status_id")] += 1
        rid = l.get("responsible_user_id")
        if rid:
            stuck_by_mgr[rid] += 1
    stage_names = {
        settings.status_noanswer: "Без ответа (3 дня)",
        settings.status_waitlist: "Лист ожидания",
    }
    _save_snapshot(db, "stuck", {
        "total": len(stuck),
        "by_stage": [
            {"label": stage_names.get(sid, str(sid)), "count": c}
            for sid, c in sorted(stuck_by_stage.items(), key=lambda x: -x[1])
        ],
        "by_mgr": [
            {"user_id": str(rid), "name": users.get(rid, f"#{rid}"), "count": c}
            for rid, c in sorted(stuck_by_mgr.items(), key=lambda x: -x[1])
        ],
    })

    # ===== TASKS state: open / no_task / overdue по менеджерам =====
    now_ts = _ts(_now_msk())
    leads_with_task: set[int] = set()
    overdue_by_mgr: dict[int, int] = defaultdict(int)
    for t in open_tasks:
        if t.get("entity_type") != "leads":
            continue
        leads_with_task.add(t.get("entity_id"))
        if (t.get("complete_till") or 0) < now_ts:
            rid = t.get("responsible_user_id")
            if rid:
                overdue_by_mgr[rid] += 1

    active_open = [
        l for l in all_active
        if l.get("status_id") not in (settings.status_won, settings.status_lost)
    ]
    mgr_state: dict[str, dict] = {}
    for l in active_open:
        rid = l.get("responsible_user_id")
        if not rid:
            continue
        key = str(rid)
        st = mgr_state.setdefault(key, {"name": users.get(rid, f"#{rid}"),
                                        "open": 0, "no_task": 0, "overdue": 0})
        st["open"] += 1
        if l.get("id") not in leads_with_task:
            st["no_task"] += 1
    for rid, cnt in overdue_by_mgr.items():
        key = str(rid)
        st = mgr_state.setdefault(key, {"name": users.get(rid, f"#{rid}"),
                                        "open": 0, "no_task": 0, "overdue": 0})
        st["overdue"] = cnt
    _save_snapshot(db, "mgr_state", {"rows": mgr_state})

    # ===== LOSS REASONS (фиксированное окно 60 дней) =====
    _collect_loss_reasons(amo, db)

    # ===== Постоянные клиенты и качество успешных сделок =====
    previous_extra = _snapshot_payload(db, "extra")
    repeat_customers = int(previous_extra.get("repeat_customers", 0))
    won_leads: list[dict] = []
    quality_due = _state_refresh_due(db, "last_quality_at", 6)
    try:
        won_params = {
            "filter[statuses][0][pipeline_id]": settings.pipeline_sales,
            "filter[statuses][0][status_id]": settings.status_won,
            "filter[statuses][1][pipeline_id]": settings.pipeline_repeat,
            "filter[statuses][1][status_id]": settings.status_won,
            "with": "contacts",
            "limit": 250,
        }
        if quality_due:
            contacts: set[int] = set()
            won_leads = list(amo.paginate("/api/v4/leads", "leads", won_params))
            for l in won_leads:
                for c in (l.get("_embedded", {}) or {}).get("contacts", []) or []:
                    cid = c.get("id")
                    if isinstance(cid, int):
                        contacts.add(cid)
            repeat_customers = len(contacts)
    except Exception as exc:  # noqa: BLE001
        log.warning("repeat customers: %s", exc)

    def _ids(predicate) -> list[int]:
        return [lead["id"] for lead in won_leads if lead.get("id") and predicate(lead)]

    quality_ids = {
        "zero_price": _ids(lambda lead: int(lead.get("price") or 0) == 0),
        "no_contact": _ids(
            lambda lead: not ((lead.get("_embedded") or {}).get("contacts") or [])
        ),
        "no_source": _ids(lambda lead: _source_of(lead) == "Не определён"),
        "no_responsible": _ids(lambda lead: not lead.get("responsible_user_id")),
        "invalid_closed_at": _ids(
            lambda lead: not lead.get("closed_at")
            or (
                lead.get("created_at")
                and int(lead.get("closed_at") or 0) < int(lead.get("created_at") or 0)
            )
        ),
    }
    if quality_due and won_leads:
        _save_snapshot(db, "data_quality", {
            "checked_won": len(won_leads),
            "counts": {key: len(ids) for key, ids in quality_ids.items()},
            "lead_ids": quality_ids,
            "scope": "all_won_sales_and_repeat",
            "checked_at": _now_msk().isoformat(),
        })
        _set_state(db, "last_quality_at", datetime.utcnow().isoformat())

    canonical_users = {
        str(user_id): name
        for user_id, name in users.items()
        if settings.is_manager(user_id)
    }
    _save_snapshot(db, "extra", {
        "repeat_customers": repeat_customers,
        "sources": sorted({_source_of(l) for l in all_active}),
        "users": {str(k): v for k, v in users.items()},
        "manager_users": canonical_users,
    })


# ──────────────────────────── orchestration ────────────────────────────

def _build_index(*lead_lists: list[dict]) -> dict[int, dict]:
    idx: dict[int, dict] = {}
    for lst in lead_lists:
        for l in lst:
            if l.get("id"):
                idx[l["id"]] = l
    return idx


def run_collection() -> None:
    """Один полный прогон: состояние + скользящее окно + один шаг бэкфилла."""
    init_db()
    if not settings.amo_access_token:
        log.error("AMO_ACCESS_TOKEN пуст — сбор пропущен")
        return

    amo = AmoClient()
    db = SessionLocal()
    started = datetime.utcnow()
    try:
        users = _fetch_users(amo)
        sales_leads = _fetch_leads_active(amo, settings.pipeline_sales)
        repeat_leads = _fetch_leads_active(amo, settings.pipeline_repeat)
        base_index = _build_index(sales_leads, repeat_leads)

        # 1) Состояние «на текущий момент»
        _collect_state(amo, db, users, sales_leads, repeat_leads)

        # 2) Скользящее окно: лиды/звонки — 14 дней; события — только
        #    последние дни (исторические события заполняет разовый бэкфилл).
        now = _now_msk()
        win_from = (now - timedelta(days=REFRESH_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        ev_from = (now - timedelta(days=EVENT_REFRESH_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        lead_agg = DayAgg()
        _collect_leads_calls(amo, lead_agg, _ts(win_from), _ts(now), base_index)
        lead_agg.flush(
            db,
            replace_days=_days_for_window(win_from, now),
            replace_metrics=CREATED_METRICS | CLOSED_METRICS | CALL_METRICS,
        )

        event_agg = DayAgg()
        _collect_events(amo, event_agg, _ts(ev_from), _ts(now), base_index)
        event_agg.flush(
            db,
            replace_days=_days_for_window(ev_from, now),
            replace_metrics=EVENT_METRICS,
        )
        status_events = _fetch_events(
            amo, ["lead_status_changed"], _ts(ev_from), _ts(now)
        )
        _collect_invoice_events(amo, db, status_events, base_index)
        refreshed = _refresh_changed_cohorts(
            amo, db, status_events, base_index, win_from
        )
        if refreshed:
            log.info("refreshed %d old cohort rows after status changes", refreshed)

        # отметка свежести окна
        _set_state(db, "last_success_at", datetime.utcnow().isoformat())
        log.info("window collect ok in %.1fs", (datetime.utcnow() - started).total_seconds())

        # 3) Один шаг бэкфилла (один месяц за прогон)
        _backfill_step(amo, db, base_index, now)

    except Exception as exc:  # noqa: BLE001
        log.exception("collection failed: %s", exc)
        _set_state(db, "last_error", f"{datetime.utcnow().isoformat()} {exc}")
        raise
    finally:
        db.close()
        amo.close()


def _backfill_cursor(db, now: datetime) -> datetime:
    """Текущая граница бэкфилла. По умолчанию — начало сегодняшнего дня,
    чтобы первый шаг покрыл и текущий месяц (события за прошлые дня месяца)."""
    raw = _get_state(db, "backfill_done_from")
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=MSK)
        except ValueError:
            pass
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _backfill_step(amo: AmoClient, db, base_index: dict, now: datetime) -> None:
    """Считает один более ранний месяц за прогон, двигая курсор назад."""
    target_start = _month_start(now) - timedelta(days=BACKFILL_MONTHS * 31)
    target_start = _month_start(target_start)

    done_from = _backfill_cursor(db, now)
    if done_from <= target_start:
        return  # бэкфилл завершён

    month_start = _month_start(done_from - timedelta(days=1))
    month_end = done_from - timedelta(seconds=1)

    lead_agg = DayAgg()
    event_agg = DayAgg()
    # Гидратация событий может добавить десятки тысяч закрытых лидов за месяц.
    # Не накапливаем их в общем индексе на протяжении всего годового backfill.
    month_index = dict(base_index)
    t0 = datetime.utcnow()
    _collect_leads_calls(amo, lead_agg, _ts(month_start), _ts(month_end), month_index)
    _collect_events(amo, event_agg, _ts(month_start), _ts(month_end), month_index)
    month_days = _days_for_window(month_start, month_end)
    n = lead_agg.flush(
        db,
        replace_days=month_days,
        replace_metrics=CREATED_METRICS | CLOSED_METRICS | CALL_METRICS,
    )
    n += event_agg.flush(
        db,
        replace_days=month_days,
        replace_metrics=EVENT_METRICS,
    )
    status_events = _fetch_events(
        amo, ["lead_status_changed"], _ts(month_start), _ts(month_end)
    )
    _collect_invoice_events(amo, db, status_events, month_index)
    _set_state(db, "backfill_done_from", month_start.isoformat())
    log.info("backfill %s: %d строк за %.1fs",
             month_start.strftime("%Y-%m"), n, (datetime.utcnow() - t0).total_seconds())


def run_backfill_all() -> None:
    """Разовый полный бэкфилл 12 мес за один процесс (без повтора окна).

    Запуск в фоне на сервере:
        docker compose exec -d worker python -m app.collector backfill
    """
    init_db()
    if not settings.amo_access_token:
        log.error("AMO_ACCESS_TOKEN пуст — бэкфилл пропущен")
        return
    amo = AmoClient()
    db = SessionLocal()
    try:
        sales_leads = _fetch_leads_active(amo, settings.pipeline_sales)
        repeat_leads = _fetch_leads_active(amo, settings.pipeline_repeat)
        base_index = _build_index(sales_leads, repeat_leads)
        now = _now_msk()
        target_start = _month_start(_month_start(now) - timedelta(days=BACKFILL_MONTHS * 31))
        for _ in range(BACKFILL_MONTHS + 3):
            done_from = _backfill_cursor(db, now)
            if done_from <= target_start:
                log.info("бэкфилл завершён до %s", done_from.date())
                break
            _backfill_step(amo, db, base_index, now)
    finally:
        db.close()
        amo.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        run_backfill_all()
    else:
        run_collection()
