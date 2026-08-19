from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
# Сколько дней назад пересчитываем лиды/звонки при каждом прогоне
# (текущий + прошлый месяц: closed_at и задачи у старых сделок ещё меняются).
REFRESH_DAYS = 62
# Событийные метрики (сообщения/диалоги/ответы) тяжёлые — в регулярном прогоне
# тянем только за последние дни; остальную историю заполняет разовый бэкфилл.
EVENT_REFRESH_DAYS = 4
# Потолок задержки ответа для усреднения (мин); выбросы не должны искажать avg.
RT_CAP_MIN = 480

# Бакеты SLA первого/любого ответа (границы в минутах).
SLA_BUCKETS = [5, 15, 30, 60]


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

    def flush(self, db, only_days: set[str] | None = None) -> int:
        """Перезаписать (идемпотентно) посчитанные (day,scope,metric)."""
        rows = []
        for day, scopes in self.m.items():
            if only_days is not None and day not in only_days:
                continue
            for scope, metrics in scopes.items():
                for metric, val in metrics.items():
                    rows.append(
                        {"day": day, "scope": scope, "metric": metric,
                         "fact": float(val), "plan": None}
                    )
        if not rows:
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
    params = {"filter[pipeline_id]": pipeline_id, "limit": 250}
    return list(amo.paginate("/api/v4/leads", "leads", params))


def _fetch_call_notes(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    # фильтр заметок надёжнее по updated_at; звонки почти не редактируются,
    # поэтому updated_at ≈ created_at — бакетируем дальше по created_at.
    params = {
        "filter[note_type][0]": "call_in",
        "filter[note_type][1]": "call_out",
        "filter[updated_at][from]": from_ts,
        "filter[updated_at][to]": to_ts,
        "limit": 250,
    }
    try:
        return list(amo.paginate("/api/v4/leads/notes", "notes", params))
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


def _collect_leads_calls(
    amo: AmoClient,
    agg: DayAgg,
    from_ts: int,
    to_ts: int,
    lead_index: dict[int, dict],
) -> None:
    """Лёгкая часть окна: лиды (создание/закрытие) и звонки."""

    # — лиды-когорта (по дате создания) —
    # Поток метрик (новые/конверсия/счета) считаем ТОЛЬКО по воронке «Продажи»,
    # чтобы повторные продажи не завышали конверсию (договорённость созвона 19.06).
    # Повторная воронка учитывается отдельными метриками repeat_*.
    created = _fetch_leads_created(amo, from_ts, to_ts)
    for l in created:
        day = _msk_day(l.get("created_at"))
        rid = l.get("responsible_user_id")
        src = _source_of(l)
        st = l.get("status_id")
        pl = l.get("pipeline_id")
        if pl == settings.pipeline_sales:
            agg.add_all(day, "new_leads", 1, mgr=rid, src=src)
            if st not in (settings.status_new, settings.status_lost):
                agg.add_all(day, "converted", 1, mgr=rid, src=src)
            if st == settings.status_won:
                # когортная конверсия «лид → Успех» (по дате создания лида)
                agg.add_all(day, "cohort_won", 1, mgr=rid, src=src)
            if st in (settings.status_invoice, settings.status_won):
                price = int(l.get("price") or 0)
                agg.add_all(day, "invoice_count", 1, mgr=rid, src=src)
                agg.add_all(day, "invoice_sum", price, mgr=rid, src=src)
        if pl == settings.pipeline_repeat:
            agg.add_all(day, "repeat_created", 1, mgr=rid)
            # отдельная когортная конверсия повторных (создано → Успех)
            if st == settings.status_won:
                agg.add_all(day, "repeat_cohort_won", 1, mgr=rid)

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

    # — звонки —
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


def _collect_events(
    amo: AmoClient,
    agg: DayAgg,
    from_ts: int,
    to_ts: int,
    lead_index: dict[int, dict],
) -> None:
    """Тяжёлая часть окна: сообщения чатов, время ответа, диалоги, пропущенные."""

    # — сообщения чатов —
    chat = _fetch_events(amo, ["incoming_chat_message", "outgoing_chat_message"], from_ts, to_ts)
    events_by_lead: dict[int, list[dict]] = defaultdict(list)
    for e in chat:
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
    talks = _fetch_events(amo, ["talk_created"], from_ts, to_ts)
    for e in talks:
        day = _msk_day(e.get("created_at"))
        lead = lead_index.get(e.get("entity_id"))
        rid = lead.get("responsible_user_id") if lead else None
        src = _source_of(lead) if lead else None
        agg.add_all(day, "dialogs", 1, mgr=rid, src=src)

    missed = _fetch_events(amo, ["talk_missed_event"], from_ts, to_ts)
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


# ──────────────────────────── state snapshots ────────────────────────────

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

    # ===== LOSS REASONS (с реальными счётчиками за 60 дней) =====
    loss_meta = amo.get("/api/v4/leads/loss_reasons") or {}
    reason_names = {
        r["id"]: r["name"]
        for r in loss_meta.get("_embedded", {}).get("loss_reasons", [])
    }
    since = _ts(_now_msk() - timedelta(days=60))
    lost_params = {
        "filter[closed_at][from]": since,
        "filter[statuses][0][pipeline_id]": settings.pipeline_sales,
        "filter[statuses][0][status_id]": settings.status_lost,
        "filter[statuses][1][pipeline_id]": settings.pipeline_repeat,
        "filter[statuses][1][status_id]": settings.status_lost,
        "limit": 250,
    }
    loss_counts: dict[str, int] = defaultdict(int)
    try:
        for l in amo.paginate("/api/v4/leads", "leads", lost_params):
            rid = l.get("loss_reason_id")
            name = reason_names.get(rid, "Не указана")
            loss_counts[name] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("loss reasons fetch: %s", exc)
    _save_snapshot(db, "loss_reasons", {
        "items": [
            {"label": n, "count": c}
            for n, c in sorted(loss_counts.items(), key=lambda x: -x[1])
        ],
    })

    # ===== Постоянные клиенты (оплата за 2 мес, дедуп по контакту) =====
    repeat_customers = 0
    try:
        won_recent = _fetch_leads_closed(amo, since, now_ts)
        contacts: set[int] = set()
        for l in won_recent:
            if l.get("status_id") != settings.status_won:
                continue
            for c in (l.get("_embedded", {}) or {}).get("contacts", []) or []:
                contacts.add(c.get("id"))
        repeat_customers = len(contacts)
    except Exception as exc:  # noqa: BLE001
        log.warning("repeat customers: %s", exc)

    _save_snapshot(db, "extra", {
        "repeat_customers": repeat_customers,
        "sources": sorted({_source_of(l) for l in all_active}),
        "users": {str(k): v for k, v in users.items()},
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

        # 2) Скользящее окно: лиды/звонки — 62 дня; события — только
        #    последние дни (исторические события заполняет разовый бэкфилл).
        now = _now_msk()
        win_from = (now - timedelta(days=REFRESH_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        ev_from = (now - timedelta(days=EVENT_REFRESH_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        agg = DayAgg()
        _collect_leads_calls(amo, agg, _ts(win_from), _ts(now), base_index)
        _collect_events(amo, agg, _ts(ev_from), _ts(now), base_index)
        agg.flush(db)

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

    agg = DayAgg()
    t0 = datetime.utcnow()
    _collect_leads_calls(amo, agg, _ts(month_start), _ts(month_end), base_index)
    _collect_events(amo, agg, _ts(month_start), _ts(month_end), base_index)
    n = agg.flush(db)
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
