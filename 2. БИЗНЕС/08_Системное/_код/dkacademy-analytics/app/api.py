from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth import is_authenticated
from app.config import settings
from app.database import SessionLocal
from app.models import MetricDaily, Snapshot, SyncState

router = APIRouter(prefix="/api")
MSK = ZoneInfo("Europe/Moscow")

# Плановые метрики и дефолтные значения (помесячно, по отделу).
# Совпадают с ключами в таблице (XLS_ROWS) на фронте.
DEFAULT_PLANS: dict[str, float] = {
    "calls_out": 3000,
    "call_minutes": 3000,
    "new_leads": 1000,
    "converted": 750,
    "repeat_created": 50,
    "invoice_count": 1000,
    "invoice_sum": 12000000,
    "paid": 1000,
    "revenue": 12900000,
    "avg_check": 15000,
    "conv_lead_deal": 75,
    "conv_repeat": 40,
    "dialogs": 50,
    "messages": 3000,
}
# Метрики-доли/средние: не суммируются по периоду, а усредняются.
PCT_METRICS = {"conv_lead_deal", "conv_repeat", "avg_check"}


def _is_manager(user_id: str) -> bool:
    return settings.is_manager(user_id)

# Метрики для динамики/таблицы по дням (хранимые «как есть»).
DAILY_METRICS = [
    "new_leads", "converted", "invoice_count", "invoice_sum",
    "paid", "revenue", "repeat_created", "repeat_deals",
    "calls_out", "call_minutes", "msg_in", "msg_out",
    "dialogs", "missed",
]


def _auth_guard(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="unauthorized")


# ──────────────────────────── period helpers ────────────────────────────

def _today_msk() -> datetime:
    return datetime.now(MSK)


def _parse_range(frm: str | None, to: str | None) -> tuple[str, str]:
    """Вернуть (from, to) YYYY-MM-DD. Дефолт — текущий месяц по МСК."""
    today = _today_msk().date()
    try:
        to_d = datetime.strptime(to, "%Y-%m-%d").date() if to else today
    except ValueError:
        to_d = today
    try:
        from_d = (
            datetime.strptime(frm, "%Y-%m-%d").date()
            if frm else today.replace(day=1)
        )
    except ValueError:
        from_d = today.replace(day=1)
    if from_d > to_d:
        from_d, to_d = to_d, from_d
    return from_d.isoformat(), to_d.isoformat()


def _prev_range(frm: str, to: str) -> tuple[str, str]:
    """Предыдущий период той же длины (для трендов)."""
    f = datetime.strptime(frm, "%Y-%m-%d").date()
    t = datetime.strptime(to, "%Y-%m-%d").date()
    span = (t - f).days + 1
    pt = f - timedelta(days=1)
    pf = pt - timedelta(days=span - 1)
    return pf.isoformat(), pt.isoformat()


# ──────────────────────────── data access ────────────────────────────

def _totals(db, frm: str, to: str) -> dict[str, dict[str, float]]:
    """{scope: {metric: sum_fact}} за период."""
    rows = db.execute(
        select(MetricDaily.scope, MetricDaily.metric, func.sum(MetricDaily.fact))
        .where(MetricDaily.day >= frm, MetricDaily.day <= to)
        .group_by(MetricDaily.scope, MetricDaily.metric)
    ).all()
    agg: dict[str, dict[str, float]] = defaultdict(dict)
    for scope, metric, s in rows:
        agg[scope][metric] = float(s or 0)
    return agg


def _series(db, frm: str, to: str, scope: str = "all") -> dict[str, dict[str, float]]:
    """{metric: {day: fact}} для одного scope за период."""
    rows = db.execute(
        select(MetricDaily.day, MetricDaily.metric, func.sum(MetricDaily.fact))
        .where(MetricDaily.day >= frm, MetricDaily.day <= to, MetricDaily.scope == scope)
        .group_by(MetricDaily.day, MetricDaily.metric)
    ).all()
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for day, metric, s in rows:
        out[metric][day] = float(s or 0)
    return out


def _snapshot(db, section: str) -> dict:
    row = db.execute(
        select(Snapshot).where(Snapshot.section == section)
    ).scalar_one_or_none()
    return row.payload if row else {}


def _day_list(frm: str, to: str) -> list[str]:
    f = datetime.strptime(frm, "%Y-%m-%d").date()
    t = datetime.strptime(to, "%Y-%m-%d").date()
    out = []
    d = f
    while d <= t:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _avg(d: dict[str, float], sum_key: str, cnt_key: str) -> float:
    return _div(d.get(sum_key, 0.0), d.get(cnt_key, 0.0))


# ──────────────────────────── flow endpoints (period) ────────────────────────────

@router.get("/overview")
def overview(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        t = _totals(db, frm, to)
        a = t.get("all", {})
        extra = _snapshot(db, "extra")
        stuck = _snapshot(db, "stuck")
        new_leads = a.get("new_leads", 0)
        paid = a.get("paid", 0)
        revenue = a.get("revenue", 0)
        data = {
            "new_leads": int(new_leads),
            "converted": int(a.get("converted", 0)),
            "paid_count": int(paid),
            "revenue": int(revenue),
            "avg_check": round(_div(revenue, paid)),
            "invoice_count": int(a.get("invoice_count", 0)),
            "invoice_sum": int(a.get("invoice_sum", 0)),
            "calls_out": int(a.get("calls_out", 0)),
            "call_minutes": round(a.get("call_minutes", 0)),
            "messages_in": int(a.get("msg_in", 0)),
            "messages_out": int(a.get("msg_out", 0)),
            "messages_total": int(a.get("msg_in", 0) + a.get("msg_out", 0)),
            "conv_lead_deal": round(_div(a.get("cohort_won", 0), new_leads) * 100, 2),
            "conv_repeat": round(_div(a.get("repeat_cohort_won", 0), a.get("repeat_created", 0)) * 100, 2),
            "repeat_deals": int(a.get("repeat_deals", 0)),
            "repeat_customers": int(extra.get("repeat_customers", 0)),
            "missed_dialogs": int(a.get("missed", 0)),
            "stuck_count": int(stuck.get("total", 0)),
            "rt_avg": round(_avg(a, "rt_sum", "rt_cnt")),
            "first_rt_avg": round(_avg(a, "first_rt_sum", "first_rt_cnt")),
        }
        return {"data": data, "range": {"from": frm, "to": to}}
    finally:
        db.close()


@router.get("/series")
def series(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        s = _series(db, frm, to)
        days = _day_list(frm, to)
        out = {m: [round(s.get(m, {}).get(d, 0)) for d in days] for m in DAILY_METRICS}
        return {"days": days, "series": out, "range": {"from": frm, "to": to}}
    finally:
        db.close()


@router.get("/managers")
def managers(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        t = _totals(db, frm, to)
        extra = _snapshot(db, "extra")
        users = extra.get("users", {})
        state = _snapshot(db, "mgr_state").get("rows", {})
        store = _plans_payload(db)
        months = _months_overlap(frm, to)
        plan_month = months[0][0] if len(months) == 1 else None
        ids = {s.split(":", 1)[1] for s in t if s.startswith("mgr:")}
        ids |= set(state.keys())
        ids = {uid for uid in ids if _is_manager(uid)}
        rows = []
        for uid in ids:
            m = t.get(f"mgr:{uid}", {})
            st = state.get(uid, {})
            leads = m.get("new_leads", 0)
            paid = m.get("paid", 0)
            revenue = m.get("revenue", 0)
            plan_revenue = _period_mgr_plan(store, frm, to, uid, "revenue")
            rows.append({
                "user_id": uid,
                "name": st.get("name") or users.get(uid, f"#{uid}"),
                "calls": int(m.get("calls_out", 0)),
                "leads": int(leads),
                "conv": round(_div(m.get("cohort_won", 0), leads) * 100, 2),
                "invoices": int(m.get("invoice_count", 0)),
                "paid": int(paid),
                "revenue": int(revenue),
                "plan_revenue": int(plan_revenue),
                "plan_pct": round(_div(revenue, plan_revenue) * 100) if plan_revenue else 0,
                "avg_check": round(_div(revenue, paid)),
                "rt": round(_avg(m, "rt_sum", "rt_cnt")),
                "cycle": round(_avg(m, "cycle_sum", "cycle_cnt")),
                "open": int(st.get("open", 0)),
                "no_task": int(st.get("no_task", 0)),
                "overdue": int(st.get("overdue", 0)),
            })
        rows.sort(key=lambda x: x["revenue"], reverse=True)
        return {
            "data": {"rows": rows},
            "plan_month": plan_month,
            "plan_editable": plan_month is not None,
            "range": {"from": frm, "to": to},
        }
    finally:
        db.close()


@router.get("/speed")
def speed(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        t = _totals(db, frm, to)
        a = t.get("all", {})
        extra = _snapshot(db, "extra")
        users = extra.get("users", {})

        buckets = {
            "le5": int(a.get("rt_le5", 0)),
            "le15": int(a.get("rt_le15", 0)),
            "le30": int(a.get("rt_le30", 0)),
            "le60": int(a.get("rt_le60", 0)),
            "gt60": int(a.get("rt_gt60", 0)),
        }
        total_rt = sum(buckets.values()) or 1
        sla_ok = buckets["le5"] + buckets["le15"]
        # приближённая медиана по бакетам
        order = [("le5", 5), ("le15", 15), ("le30", 30), ("le60", 60), ("gt60", 90)]
        half, acc, median = total_rt / 2, 0, 0
        for key, val in order:
            acc += buckets[key]
            if acc >= half:
                median = val
                break

        # время ответа по дням
        s_sum = _series(db, frm, to)  # all-scope series
        days = _day_list(frm, to)
        rt_daily = []
        for d in days:
            rs = s_sum.get("rt_sum", {}).get(d, 0)
            rc = s_sum.get("rt_cnt", {}).get(d, 0)
            rt_daily.append(round(_div(rs, rc)))

        # по менеджерам
        by_mgr = []
        for scope, m in t.items():
            if not scope.startswith("mgr:"):
                continue
            uid = scope.split(":", 1)[1]
            if not _is_manager(uid):
                continue
            cnt = m.get("rt_cnt", 0)
            if not cnt:
                continue
            avg = round(_avg(m, "rt_sum", "rt_cnt"))
            by_mgr.append({"name": users.get(uid, f"#{uid}"), "min": avg, "sla": avg <= 15})
        by_mgr.sort(key=lambda x: x["min"])

        # первый ответ по источникам
        fr_src = []
        for scope, m in t.items():
            if not scope.startswith("src:"):
                continue
            name = scope.split(":", 1)[1]
            cnt = m.get("first_rt_cnt", 0)
            if not cnt:
                continue
            fr_src.append({"source": name, "min": round(_avg(m, "first_rt_sum", "first_rt_cnt"))})
        fr_src.sort(key=lambda x: x["min"], reverse=True)

        data = {
            "rt_avg": round(_avg(a, "rt_sum", "rt_cnt")),
            "rt_median": median,
            "missed": int(a.get("missed", 0)),
            "sla_pct": round(_div(sla_ok, total_rt) * 100),
            "first_rt_avg": round(_avg(a, "first_rt_sum", "first_rt_cnt")),
            "messages_in": int(a.get("msg_in", 0)),
            "buckets": buckets,
            "rt_daily": {"days": days, "values": rt_daily},
            "by_mgr": by_mgr,
            "fr_source": fr_src,
        }
        return {"data": data, "range": {"from": frm, "to": to}}
    finally:
        db.close()


@router.get("/sources")
def sources(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        t = _totals(db, frm, to)
        rows = []
        for scope, m in t.items():
            if not scope.startswith("src:"):
                continue
            name = scope.split(":", 1)[1]
            leads = m.get("new_leads", 0)
            closed_won = m.get("paid", 0)
            revenue = m.get("revenue", 0)
            rows.append({
                "source": name,
                "leads": int(leads),
                "cohort_won": int(m.get("cohort_won", 0)),
                "closed_won": int(closed_won),
                "revenue": int(revenue),
                "conv": round(_div(m.get("cohort_won", 0), leads) * 100, 2),
                "missed": int(m.get("missed", 0)),
                "avg_check": round(_div(revenue, closed_won)),
            })
        rows.sort(key=lambda x: x["leads"], reverse=True)
        return {"data": {"rows": rows}, "range": {"from": frm, "to": to}}
    finally:
        db.close()


@router.get("/activity")
def activity(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        t = _totals(db, frm, to)
        a = t.get("all", {})
        # подённый стек
        s = _series(db, frm, to)
        days = _day_list(frm, to)

        # почасовые гистограммы — СРЕДНЕЕ за период (а не сумма): делим на число
        # активных дней (где была хоть какая-то активность), чтобы при выборе
        # квартала/месяца показывать типичную нагрузку часа, а не накопленную.
        active_days = sum(
            1 for d in days
            if (s.get("msg_in", {}).get(d, 0)
                + s.get("msg_out", {}).get(d, 0)
                + s.get("calls_out", {}).get(d, 0)) > 0
        ) or 1
        hours = list(range(8, 21))
        msg_hour = [round(a.get(f"hour_msg_{h:02d}", 0) / active_days) for h in hours]
        miss_hour = [round(a.get(f"hour_miss_{h:02d}", 0) / active_days) for h in hours]
        daily = {
            "days": days,
            "msg_in": [round(s.get("msg_in", {}).get(d, 0)) for d in days],
            "msg_out": [round(s.get("msg_out", {}).get(d, 0)) for d in days],
            "calls": [round(s.get("calls_out", {}).get(d, 0)) for d in days],
        }

        # сообщения по менеджерам
        extra = _snapshot(db, "extra")
        users = extra.get("users", {})
        msg_mgr = []
        for scope, m in t.items():
            if not scope.startswith("mgr:"):
                continue
            uid = scope.split(":", 1)[1]
            if not _is_manager(uid):
                continue
            mi, mo = m.get("msg_in", 0), m.get("msg_out", 0)
            if not (mi or mo):
                continue
            msg_mgr.append({"name": users.get(uid, f"#{uid}"),
                            "msg_in": int(mi), "msg_out": int(mo)})
        msg_mgr.sort(key=lambda x: x["msg_in"] + x["msg_out"], reverse=True)

        # пропущенные по источникам
        miss_src = []
        for scope, m in t.items():
            if not scope.startswith("src:"):
                continue
            mv = m.get("missed", 0)
            if not mv:
                continue
            miss_src.append({"source": scope.split(":", 1)[1], "count": int(mv)})
        miss_src.sort(key=lambda x: x["count"], reverse=True)

        # тренды vs предыдущий период
        pf, pt = _prev_range(frm, to)
        tp = _totals(db, pf, pt).get("all", {})

        def trend(metric, *, derived=None):
            cur = a.get(metric, 0)
            prev = tp.get(metric, 0)
            if derived == "avg_check":
                cur = _div(a.get("revenue", 0), a.get("paid", 0))
                prev = _div(tp.get("revenue", 0), tp.get("paid", 0))
            if derived == "messages":
                cur = a.get("msg_in", 0) + a.get("msg_out", 0)
                prev = tp.get("msg_in", 0) + tp.get("msg_out", 0)
            if not prev:
                return 0
            return round((cur - prev) / prev * 100)

        trends = {
            "labels": ["Звонки", "Лиды", "Счета", "Приход", "Ср. чек", "Сообщения"],
            "values": [
                trend("calls_out"), trend("new_leads"), trend("invoice_count"),
                trend("revenue"), trend(None, derived="avg_check"),
                trend(None, derived="messages"),
            ],
        }

        return {"data": {
            "hours": [f"{h}:00" for h in hours],
            "msg_hour": msg_hour,
            "miss_hour": miss_hour,
            "daily": daily,
            "msg_mgr": msg_mgr,
            "miss_src": miss_src,
            "trends": trends,
        }, "range": {"from": frm, "to": to}}
    finally:
        db.close()


@router.get("/table")
def table(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    scope: str = Query("all"),
):
    frm, to = _parse_range(frm, to)
    if scope.startswith("mgr:") and not _is_manager(scope.split(":", 1)[1]):
        scope = "all"
    db = SessionLocal()
    try:
        s = _series(db, frm, to, scope=scope)
        t = _totals(db, frm, to).get(scope, {})
        days = _day_list(frm, to)
        series_out = {}
        for m in DAILY_METRICS:
            series_out[m] = {d: round(s.get(m, {}).get(d, 0)) for d in days}
        totals = {m: round(t.get(m, 0)) for m in DAILY_METRICS}
        # производные тоталы
        totals["avg_check"] = round(_div(t.get("revenue", 0), t.get("paid", 0)))
        totals["conv_lead_deal"] = round(_div(t.get("cohort_won", 0), t.get("new_leads", 0)) * 100, 2)
        totals["conv_repeat"] = round(_div(t.get("repeat_cohort_won", 0), t.get("repeat_created", 0)) * 100, 2)
        return {"data": {"days": days, "series": series_out, "totals": totals},
                "scope": scope, "range": {"from": frm, "to": to}}
    finally:
        db.close()


# ──────────────────────────── state endpoints (no period) ────────────────────────────

@router.get("/funnels")
def funnels(_: None = Depends(_auth_guard)):
    db = SessionLocal()
    try:
        data = _snapshot(db, "funnels")
        bm = data.get("by_mgr")
        if isinstance(bm, dict):
            data["by_mgr"] = {k: v for k, v in bm.items() if _is_manager(k)}
        return {"data": data}
    finally:
        db.close()


@router.get("/stuck")
def stuck(_: None = Depends(_auth_guard)):
    db = SessionLocal()
    try:
        data = _snapshot(db, "stuck")
        bm = data.get("by_mgr")
        if isinstance(bm, list):
            data["by_mgr"] = [
                r for r in bm
                if _is_manager(str(r.get("user_id", "")))
            ]
        return {"data": data}
    finally:
        db.close()


@router.get("/loss_reasons")
def loss_reasons(_: None = Depends(_auth_guard)):
    db = SessionLocal()
    try:
        return {"data": _snapshot(db, "loss_reasons")}
    finally:
        db.close()


@router.get("/meta")
def meta(_: None = Depends(_auth_guard)):
    db = SessionLocal()
    try:
        data = dict(_snapshot(db, "extra"))
        users = data.get("users")
        if isinstance(users, dict):
            data["users"] = {k: v for k, v in users.items() if _is_manager(k)}
        return {"data": data}
    finally:
        db.close()


@router.get("/data_quality")
def data_quality(_: None = Depends(_auth_guard)):
    db = SessionLocal()
    try:
        return {"data": _snapshot(db, "data_quality")}
    finally:
        db.close()


# ──────────────────────────── plans (editable targets) ────────────────────────────

# Метрики, которые планируются по каждому менеджеру. План отдела по ним =
# сумма планов менеджеров (договорённость созвона: «сумму оплаченную выставляем
# только по менеджерам, а в отделе — автоматически суммой»).
MGR_PLAN_METRICS = {"revenue"}


def _plans_payload(db) -> dict:
    raw = _snapshot(db, "plans")
    return {
        "default": {**DEFAULT_PLANS, **(raw.get("default") or {})},
        "by_month": raw.get("by_month") or {},
        # {YYYY-MM: {user_id: {metric: value}}}
        "by_mgr": raw.get("by_mgr") or {},
    }


def _mgr_plan_sum(store: dict, month: str, metric: str) -> float | None:
    """Сумма планов менеджеров по метрике за месяц (None, если ничего не задано)."""
    month_map = (store.get("by_mgr") or {}).get(month) or {}
    vals = [
        float(m[metric])
        for m in month_map.values()
        if isinstance(m, dict) and m.get(metric) is not None
    ]
    return sum(vals) if vals else None


def _resolve_mgr_plan(store: dict, month: str, uid: str, metric: str) -> float:
    m = ((store.get("by_mgr") or {}).get(month) or {}).get(str(uid)) or {}
    if metric in m and m[metric] is not None:
        return float(m[metric])
    return 0.0


def _resolve_plan(store: dict, month: str, metric: str) -> float:
    # Для метрик-по-менеджерам план отдела = сумма планов менеджеров (если заданы).
    if metric in MGR_PLAN_METRICS:
        s = _mgr_plan_sum(store, month, metric)
        if s is not None:
            return s
    bm = store["by_month"].get(month, {})
    if metric in bm and bm[metric] is not None:
        return float(bm[metric])
    return float(store["default"].get(metric, 0))


def _months_overlap(frm: str, to: str) -> list[tuple[str, int, int]]:
    """[(YYYY-MM, overlap_days, days_in_month), ...] для диапазона."""
    f = datetime.strptime(frm, "%Y-%m-%d").date()
    t = datetime.strptime(to, "%Y-%m-%d").date()
    out: dict[str, int] = defaultdict(int)
    d = f
    while d <= t:
        out[f"{d.year:04d}-{d.month:02d}"] += 1
        d += timedelta(days=1)
    res = []
    for ym, days in out.items():
        y, m = int(ym[:4]), int(ym[5:7])
        res.append((ym, days, calendar.monthrange(y, m)[1]))
    return sorted(res)


def _period_mgr_plan(store: dict, frm: str, to: str, uid: str, metric: str) -> float:
    """План менеджера по метрике за период (пропорционально дням месяцев)."""
    months = _months_overlap(frm, to)
    acc = sum(
        _resolve_mgr_plan(store, ym, uid, metric) * d / dim
        for ym, d, dim in months
    )
    return round(acc)


def _period_plans(store: dict, frm: str, to: str) -> dict[str, float]:
    months = _months_overlap(frm, to)
    out: dict[str, float] = {}
    total_days = sum(d for _, d, _ in months) or 1
    for metric in DEFAULT_PLANS:
        if metric in PCT_METRICS:
            # взвешенное по дням среднее месячных целей
            acc = sum(_resolve_plan(store, ym, metric) * d for ym, d, _ in months)
            out[metric] = round(acc / total_days, 1)
        else:
            acc = sum(
                _resolve_plan(store, ym, metric) * d / dim
                for ym, d, dim in months
            )
            out[metric] = round(acc)
    return out


@router.get("/plans")
def get_plans(
    _: None = Depends(_auth_guard),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    frm, to = _parse_range(frm, to)
    db = SessionLocal()
    try:
        store = _plans_payload(db)
        months = _months_overlap(frm, to)
        month = months[0][0] if len(months) == 1 else None
        monthly = (
            {m: _resolve_plan(store, month, m) for m in DEFAULT_PLANS}
            if month else {}
        )
        mgr_monthly = (
            {uid: vals for uid, vals in (store.get("by_mgr") or {}).get(month, {}).items()}
            if month else {}
        )
        return {
            "month": month,
            "editable": month is not None,
            "monthly": monthly,
            "mgr_monthly": mgr_monthly,
            "period": _period_plans(store, frm, to),
            "range": {"from": frm, "to": to},
        }
    finally:
        db.close()


@router.put("/plans")
def put_plans(
    _: None = Depends(_auth_guard),
    body: dict = Body(...),
):
    month = body.get("month")
    plans = body.get("plans") or {}
    if not isinstance(month, str) or len(month) != 7:
        raise HTTPException(status_code=400, detail="month must be 'YYYY-MM'")
    clean: dict[str, float] = {}
    for k, v in plans.items():
        if k not in DEFAULT_PLANS:
            continue
        try:
            clean[k] = float(v)
        except (TypeError, ValueError):
            continue
    db = SessionLocal()
    try:
        store = _plans_payload(db)
        by_month = store["by_month"]
        by_month.setdefault(month, {}).update(clean)
        payload = {
            "default": store["default"],
            "by_month": by_month,
            "by_mgr": store.get("by_mgr") or {},
        }
        stmt = pg_insert(Snapshot).values(section="plans", payload=payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Snapshot.section],
            set_={"payload": payload, "updated_at": func.now()},
        )
        db.execute(stmt)
        db.commit()
        return {"ok": True, "month": month, "saved": clean}
    finally:
        db.close()


@router.put("/manager_plans")
def put_manager_plans(
    _: None = Depends(_auth_guard),
    body: dict = Body(...),
):
    """План «оплачено (сумма)» по одному менеджеру за месяц.

    body: {month: 'YYYY-MM', user_id: '123', plans: {revenue: 1000000}}
    План отдела по этой метрике пересчитывается автоматически как сумма менеджеров.
    """
    month = body.get("month")
    uid = str(body.get("user_id") or "").strip()
    plans = body.get("plans") or {}
    if not isinstance(month, str) or len(month) != 7:
        raise HTTPException(status_code=400, detail="month must be 'YYYY-MM'")
    if not uid:
        raise HTTPException(status_code=400, detail="user_id required")
    clean: dict[str, float] = {}
    for k, v in plans.items():
        if k not in MGR_PLAN_METRICS:
            continue
        try:
            clean[k] = float(v)
        except (TypeError, ValueError):
            continue
    if not clean:
        raise HTTPException(status_code=400, detail="no valid metrics (revenue)")
    db = SessionLocal()
    try:
        store = _plans_payload(db)
        by_mgr = store.get("by_mgr") or {}
        by_mgr.setdefault(month, {}).setdefault(uid, {}).update(clean)
        payload = {
            "default": store["default"],
            "by_month": store["by_month"],
            "by_mgr": by_mgr,
        }
        stmt = pg_insert(Snapshot).values(section="plans", payload=payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Snapshot.section],
            set_={"payload": payload, "updated_at": func.now()},
        )
        db.execute(stmt)
        db.commit()
        # вернём пересчитанный план отдела по метрике
        dept = {m: _mgr_plan_sum(store, month, m) for m in MGR_PLAN_METRICS}
        return {"ok": True, "month": month, "user_id": uid, "saved": clean, "dept": dept}
    finally:
        db.close()


@router.get("/status")
def status(_: None = Depends(_auth_guard)):
    """Свежесть данных для бейджа «обновлено N мин назад»."""
    db = SessionLocal()
    try:
        rows = {r.key: r.value for r in db.execute(select(SyncState)).scalars().all()}
    finally:
        db.close()
    last = rows.get("last_success_at")
    age_min = None
    if last:
        try:
            dt = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            age_min = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        except ValueError:
            pass
    return {
        "last_success_at": last,
        "age_min": age_min,
        "last_error": rows.get("last_error"),
        "backfill_done_from": rows.get("backfill_done_from"),
    }
