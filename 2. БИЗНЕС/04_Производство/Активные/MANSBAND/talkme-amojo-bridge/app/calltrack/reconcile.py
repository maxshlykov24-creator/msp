from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from datetime import timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calltrack.matching import attribute_call, apply_call
from app.calltrack.models import CallEvent, Visit
from app.calltrack.pool import MSK, pool_snapshot
from app.calltrack.telegram import send, incident
from app.config import get_settings
from app.models import KvState

log = logging.getLogger(__name__)

_STATE_KEY = "ct_reconcile_state"


def classify_call(call: CallEvent, open_leads: list[dict[str, Any]], now: dt.datetime) -> str:
    """repeat, wait или no_lead. Скрытый номер и уже привязанная сделка сюда не входят."""
    if open_leads:
        return "repeat"
    started = call.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if now - started >= timedelta(minutes=30):
        return "no_lead"
    return "wait"


def _has_source(call: CallEvent) -> bool:
    result = call.result or {}
    return bool(result.get("tech_written") or result.get("written"))


def _load_state(db: Session) -> dict[str, Any]:
    row = db.execute(select(KvState).where(KvState.key == _STATE_KEY)).scalar_one_or_none()
    if row is None or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(db: Session, state: dict[str, Any]) -> None:
    raw = json.dumps(state, ensure_ascii=False)
    row = db.execute(select(KvState).where(KvState.key == _STATE_KEY)).scalar_one_or_none()
    if row is None:
        db.add(KvState(key=_STATE_KEY, value=raw))
    else:
        row.value = raw
        row.updated_at = dt.datetime.now(timezone.utc)


def save_repeat_call(db: Session, call: CallEvent, lead_id: int, token: str) -> None:
    """Compact attribution snapshot committed before CRM; retries reuse it."""
    from app import amo_v4
    result = dict(call.result or {})
    snapshot = result.get("repeat_snapshot")
    if snapshot is None:
        attr = attribute_call(db, call)
        snapshot = {
            "tracking": {k: str(v)[:2048 if k == "utm_referrer" else 255]
                         for k, v in attr.tracking.items() if v},
            "tech_source": attr.tech_source,
            "disputed": attr.disputed,
        }
        call.visit_id = attr.visit.id if attr.visit else None
        call.number_kind = attr.kind
        call.lead_id = lead_id
        result.update(repeat_snapshot=snapshot, repeat_note_pending=True)
        call.result = result
        db.commit()
    marker = f"[ct-repeat:{call.id}]"
    lines = ["Повторный звонок", f"Время: {call.started_at.astimezone(MSK):%d.%m.%Y %H:%M:%S} МСК",
             f"Номер набора: +7{call.called}"]
    if call.visit_id:
        lines.append(f"Визит: {call.visit_id}")
    if snapshot.get("tech_source"):
        lines.append(f"Источник: {snapshot['tech_source']}")
    if snapshot.get("disputed"):
        lines.append("Привязка к визиту спорная")
    tracking = snapshot["tracking"]
    lines.extend(f"{k}: {v}" for k, v in tracking.items())
    if not tracking:
        lines.append("Метки визита не найдены")
    lines.append(marker)
    if not amo_v4.lead_has_note_text(lead_id=call.lead_id, needle=marker, access_token=token):
        amo_v4.add_lead_note(lead_id=call.lead_id, text="\n".join(lines),
                             access_token=token, raise_on_error=True)
    call.status = "repeat"
    call.result = {**result, "repeat": True, "repeat_note_pending": False,
                   "tech_source": snapshot.get("tech_source")}
    db.commit()


def run_reconcile_once(db: Optional[Session] = None) -> dict[str, int]:
    own = db is None
    if db is None:
        from app.database import get_session_factory
        db = get_session_factory()()
    counts = {"repeat": 0, "no_lead": 0, "retry": 0, "errors": 0}
    try:
        from app import amo_v4
        from app.tokens import get_valid_access_token
        now = dt.datetime.now(timezone.utc)
        settings = get_settings()
        calls = db.execute(select(CallEvent).where(
            CallEvent.status.in_(["new", "retry", "unattributed"]),
            CallEvent.started_at <= now - timedelta(minutes=5)
        ).order_by(CallEvent.started_at).limit(100)).scalars().all()
        token = None
        for call in calls:
            if not call.called or not call.caller:
                continue  # Отдельные сгруппированные сигналы monitor_once.
            try:
                token = token or get_valid_access_token(db)
                if (call.result or {}).get("repeat_note_pending") and call.lead_id:
                    save_repeat_call(db, call, call.lead_id, token)
                    counts["repeat"] += 1
                    continue
                if call.status in {"retry", "unattributed"} and call.lead_id:
                    lead = amo_v4.get_lead(lead_id=call.lead_id, access_token=token, settings=settings)
                    apply_call(db, call, call.lead_id, lead, token)
                    counts["retry"] += 1
                    continue
                opens = amo_v4.open_leads_for_phone(
                    phone=call.caller, access_token=token, settings=settings)
                if not opens:
                    closed = amo_v4.recent_closed_leads_for_call(
                        phone=call.caller, started_at=call.started_at,
                        access_token=token, settings=settings)
                    if len(closed) == 1:
                        save_repeat_call(db, call, int(closed[0]["id"]), token)
                        counts["repeat"] += 1
                        continue
                    if len(closed) > 1:
                        call.status = "error"
                        call.result = {**(call.result or {}), "error": "multiple_recent_closed_leads"}
                        db.commit()
                        continue
                kind = classify_call(call, opens, now)
                if kind == "repeat":
                    # Новая сделка рядом со звонком: возможно, add_lead был потерян.
                    # Не помечаем такой звонок повторным без попытки дописать метки.
                    if len(opens) != 1:
                        call.status = "error"
                        call.result = {**(call.result or {}), "error": "multiple_open_leads"}
                    else:
                        lead = amo_v4.get_lead(lead_id=int(opens[0]["id"]), access_token=token, settings=settings)
                        created = dt.datetime.fromtimestamp(int(lead.get("created_at") or 0), timezone.utc)
                        started = call.started_at
                        if started - timedelta(minutes=2) <= created <= started + timedelta(minutes=settings.ct_call_window_minutes):
                            apply_call(db, call, int(lead["id"]), lead, token)
                        else:
                            save_repeat_call(db, call, int(lead["id"]), token)
                            counts["repeat"] += 1
                elif kind == "no_lead":
                    call.status = "no_lead"
                    counts["no_lead"] += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                counts["errors"] += 1
                previous = dict(call.result or {})
                attempts = int(previous.get("reconcile_attempts", 0)) + 1
                call.result = {**previous, "reconcile_attempts": attempts, "error": type(exc).__name__}
                if attempts >= 6:
                    call.status = "error"
                db.commit()
        return counts
    finally:
        if own:
            db.close()


def monitor_once(db: Session, now: Optional[dt.datetime] = None) -> None:
    now = now or dt.datetime.now(timezone.utc)
    load = pool_snapshot(db, now)
    day = now.astimezone(MSK).date().isoformat()
    state = _load_state(db)
    exhausted_row = db.get(KvState, f"ct_pool_exhausted:{day}")
    exhausted = int(exhausted_row.value) if exhausted_row else 0
    if state.get("exhausted_day") != day:
        state["exhausted_seen"] = 0
    new_exhaustion = exhausted > int(state.get("exhausted_seen", 0))
    # Недавний отказ удерживает инцидент, чтобы не объявлять восстановление немедленно.
    if new_exhaustion:
        state["last_exhaustion"] = now.timestamp()
    overloaded = now.timestamp() - float(state.get("last_exhaustion", 0)) < 300
    incident(db, "нехватка номеров", overloaded,
             f"Не хватает номеров. Занято {load['busy']} из {load['total']}, активных рекламных визитов {load['demand']}. "
             "Показываем основной номер. Проверь пик нагрузки перед докупкой.", now)
    incident(db, "загрузка пула", bool(load["total"] and load["busy"] / load["total"] >= .8),
             f"Пул заполнен минимум на 80%: {load['busy']} из {load['total']}. Следи за нехваткой.", now)
    incident(db, "пустой пул", bool(get_settings().ct_live and not load["total"]),
             "Боевой режим включён, пул пуст. Проверь настройки номеров.", now)
    calls = db.execute(select(CallEvent).where(
        CallEvent.status.in_(["new", "retry", "error", "no_lead", "unattributed"])
    )).scalars().all()
    groups = {
        "формат Телфин": [c for c in calls if not c.called],
        "звонки без номера клиента": [c for c in calls if c.called and not c.caller],
        "ошибка CRM": [c for c in calls if c.status in {"retry", "error"}],
        "звонки без сделки": [c for c in calls if c.status == "no_lead"],
        "звонки без источника": [c for c in calls if c.status == "unattributed" and c.number_kind != "shared"],
    }
    for key, rows in groups.items():
        links = [f"https://{get_settings().amo_subdomain}.{get_settings().amo_base_domain}/leads/detail/{c.lead_id}"
                 for c in rows if c.lead_id][:3]
        incident(db, key, bool(rows), f"{key}: {len(rows)} не закрыто. " + " ".join(links), now)
    api_errors = db.get(KvState, "ct_api_errors")
    recent_error = bool(api_errors and now - api_errors.updated_at < timedelta(minutes=5))
    incident(db, "ошибки выдачи номера", recent_error,
             "Ошибка сервера при выдаче или продлении номера. Проверь журнал сервиса; посетителю остаётся исходный номер.", now)
    state.update(exhausted_day=day, exhausted_seen=exhausted, heartbeat=now.isoformat())
    _save_state(db, state)
    db.commit()


def summary_text(db: Session, now: Optional[dt.datetime] = None, *, previous_day: bool = False) -> str:
    now = now or dt.datetime.now(timezone.utc)
    local = now.astimezone(MSK)
    report_date = local.date() - timedelta(days=1) if previous_day else local.date()
    start = dt.datetime.combine(report_date, dt.time.min, tzinfo=MSK).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    calls = db.scalars(select(CallEvent).where(
        CallEvent.started_at >= start, CallEvent.started_at < end,
        CallEvent.status != "archived_test")).all()
    total = len(calls)
    repeat = sum(c.status == "repeat" for c in calls)
    linked = sum(bool(c.lead_id) for c in calls)
    by_visit = sum(c.number_kind == "pool" and bool(c.visit_id) for c in calls)
    by_channel = sum(c.number_kind == "channel" for c in calls)
    shared = sum(c.number_kind == "shared" for c in calls)
    other = total - by_visit - by_channel - shared
    key = f"ct_pool_exhausted:{report_date.isoformat()}"
    exhausted_row = db.execute(select(KvState).where(KvState.key == key)).scalar_one_or_none()
    exhausted = int(exhausted_row.value) if exhausted_row and str(exhausted_row.value).isdigit() else 0
    fallback_rows = db.execute(select(KvState).where(
        KvState.key.like(f"ct_fallback:{report_date}:%"))).scalars().all()
    fallback = [json.loads(row.value) for row in fallback_rows]
    recovered = sum(bool(item.get("recovered_at")) for item in fallback)
    visits = db.execute(select(Visit).where(Visit.created_at >= start, Visit.created_at < end)).scalars().all()
    sources: dict[str, int] = {}
    from app.calltrack.pool import utm_source_map

    aliases = utm_source_map(get_settings())
    for visit in visits:
        raw = (visit.utm_source or "").strip().lower()
        source = aliases.get(raw, raw) if raw else ("Яндекс Директ" if visit.yclid else "Без меток")
        source = " ".join(source.split())[:48]
        sources[source] = sources.get(source, 0) + 1
    peaks = {}
    for metric in ("busy", "demand"):
        row = db.get(KvState, f"ct_peak_{metric}:{report_date}")
        peaks[metric] = int(row.value) if row else "нет данных"
    problems = []
    for label, count in (
        ("ошибки CRM", sum(c.status in {"retry", "error"} for c in calls)),
        ("не найдена связь со сделкой", sum(c.status == "no_lead" for c in calls)),
        ("источник требует проверки", sum(c.status == "unattributed" and c.number_kind != "shared" for c in calls)),
        ("ожидают обработки", sum(c.status == "new" for c in calls)),
    ):
        if count:
            problems.append(f"{label}: {count}")
    attribution = f"Привязка: визит — {by_visit} · канал — {by_channel} · общий номер — {shared}"
    if other:
        attribution += f" · не определена — {other}"
    lines = [f"Коллтрекинг · {report_date:%d.%m.%Y}", "",
             f"Звонки: {total} · повторные: {repeat}",
             f"Со сделкой: {linked} из {total}", attribution,
             "Обработка: " + ("; ".join(problems) if problems else "ошибок нет"), "",
             "Номера",
             f"Пик занятости: {peaks['busy']} · визитов с метками: {peaks['demand']}",
             f"Резерв: {exhausted} показов" if exhausted else "Резерв: не понадобился"]
    if fallback:
        lines.append(f"Визитов с резервом: {len(fallback)} · позже получили подмену: {recovered}")
    elif exhausted:
        lines.append("Число визитов с резервом за этот день не записывалось.")
    lines.extend(["", f"Трафик: {len(visits)} визитов скрипта"])
    ranked = sorted(sources.items(), key=lambda item: (-item[1], item[0]))
    top = [f"{name}: {count}" for name, count in ranked[:3]]
    remaining = sum(count for _, count in ranked[3:])
    if remaining:
        top.append(f"Остальные: {remaining}")
    for index in range(0, len(top), 2):
        lines.append(" · ".join(top[index:index + 2]))
    if exhausted:
        lines.extend(["", "Резерв — общий номер: звонок проходит, точные метки могут отсутствовать."])
    return "\n".join(lines)



def maybe_summary(db: Optional[Session] = None, now: Optional[dt.datetime] = None) -> bool:
    """В первый день каждый час до 22:00 МСК, дальше раз в сутки в 09:00."""
    own = db is None
    if db is None:
        from app.database import get_session_factory

        db = get_session_factory()()
    assert db is not None
    try:
        now = now or dt.datetime.now(timezone.utc)
        local = now.astimezone(MSK)
        state = _load_state(db)
        if "first_date" not in state:
            state["first_date"] = local.date().isoformat()
            state["mode"] = "hourly"
        sent = False
        if state.get("mode") == "hourly" and (
            local.date().isoformat() != state.get("first_date") or local.hour >= 22
        ):
            state["mode"] = "daily"
        hour_key = local.strftime("%Y-%m-%d %H")
        if state.get("mode") == "hourly" and state.get("last_hour") != hour_key:
            sent = send(summary_text(db, now))
            if sent:
                state["last_hour"] = hour_key
        day_key = local.date().isoformat()
        if state.get("mode") == "daily" and local.hour == 9 and state.get("last_daily") != day_key:
            sent = send(summary_text(db, now, previous_day=True)) or sent
            if sent:
                state["last_daily"] = day_key
        _save_state(db, state)
        db.commit()
        return sent
    except Exception:
        db.rollback()
        log.exception("ct summary failed")
        return False
    finally:
        if own:
            db.close()


async def reconcile_loop() -> None:
    await asyncio.sleep(30)
    last_check = 0.0
    while True:
        try:
            loop = asyncio.get_running_loop()
            now_m = loop.time()
            if now_m - last_check >= 300:
                await asyncio.to_thread(run_reconcile_once)
                last_check = now_m
            from app.database import get_session_factory
            def monitor():
                with get_session_factory()() as db:
                    monitor_once(db)
            await asyncio.to_thread(monitor)
            await asyncio.to_thread(maybe_summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ct reconcile loop")
        await asyncio.sleep(60)
