from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from datetime import timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calltrack.matching import attribute_call
from app.calltrack.models import CallEvent, Visit
from app.calltrack.pool import MSK
from app.calltrack.telegram import send
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


def run_reconcile_once(db: Optional[Session] = None) -> dict[str, int]:
    """Звонок без сделки и сделка звонка без источника. Открытая сделка — примечание, не сигнал."""
    own = db is None
    if db is None:
        from app.database import get_session_factory

        db = get_session_factory()()
    assert db is not None
    counts = {"repeat": 0, "no_lead": 0, "hidden": 0, "no_source": 0}
    try:
        now = dt.datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=5)
        calls = (
            db.execute(
                select(CallEvent).where(CallEvent.status == "new", CallEvent.started_at <= cutoff)
            )
            .scalars()
            .all()
        )
        token: Optional[str] = None
        settings = get_settings()
        for call in calls:
            if not call.caller:
                if call.alerted_at is None:
                    if send(f"Звонок без номера клиента. Набор +7{call.called}, id {call.call_id}"):
                        call.alerted_at = now
                    counts["hidden"] += 1
                continue
            if call.lead_id and not _has_source(call):
                if call.alerted_at is None:
                    if send(
                        f"Сделка {call.lead_id} по звонку без источника. "
                        f"Набор +7{call.called}, id {call.call_id}"
                    ):
                        call.alerted_at = now
                    counts["no_source"] += 1
                continue
            try:
                from app import amo_v4
                from app.tokens import get_valid_access_token

                token = token or get_valid_access_token(db)
                opens = amo_v4.open_leads_for_phone(
                    phone=call.caller, access_token=token, settings=settings
                )
            except Exception:
                log.exception("ct reconcile: не прочитали сделки по звонку %s", call.call_id)
                continue
            kind = classify_call(call, opens, now)
            if kind == "repeat":
                lead = opens[0]
                attr = attribute_call(db, call)
                source = attr.tech_source or "не определён"
                from app import amo_v4

                amo_v4.add_lead_note(
                    lead_id=int(lead["id"]),
                    text=f"повторный звонок, источник {source}",
                    access_token=token or "",
                    settings=settings,
                )
                call.status = "repeat"
                call.lead_id = int(lead["id"])
                call.number_kind = attr.kind
                call.visit_id = attr.visit.id if attr.visit is not None else call.visit_id
                call.result = {"repeat": True, "tech_source": source}
                counts["repeat"] += 1
            elif kind == "no_lead":
                if call.alerted_at is None and send(
                    f"Звонок без сделки. Набор +7{call.called}, клиент +7{call.caller}, id {call.call_id}"
                ):
                    call.alerted_at = now
                call.status = "no_lead"
                counts["no_lead"] += 1
        db.commit()
        return counts
    except Exception:
        db.rollback()
        log.exception("ct reconcile failed")
        return counts
    finally:
        if own:
            db.close()


def summary_text(db: Session, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(timezone.utc)
    local = now.astimezone(MSK)
    start = dt.datetime.combine(local.date(), dt.time.min, tzinfo=MSK).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    total = db.execute(
        select(func.count()).select_from(CallEvent).where(
            CallEvent.started_at >= start, CallEvent.started_at < end
        )
    ).scalar_one()
    matched = db.execute(
        select(func.count()).select_from(CallEvent).where(
            CallEvent.started_at >= start,
            CallEvent.started_at < end,
            CallEvent.status == "matched",
        )
    ).scalar_one()
    repeat = db.execute(
        select(func.count()).select_from(CallEvent).where(
            CallEvent.started_at >= start,
            CallEvent.started_at < end,
            CallEvent.status == "repeat",
        )
    ).scalar_one()
    no_lead = db.execute(
        select(func.count()).select_from(CallEvent).where(
            CallEvent.started_at >= start,
            CallEvent.started_at < end,
            CallEvent.status == "no_lead",
        )
    ).scalar_one()
    disputed = db.execute(
        select(func.count()).select_from(Visit).where(
            Visit.disputed.is_(True), Visit.created_at >= start, Visit.created_at < end
        )
    ).scalar_one()
    key = f"ct_pool_exhausted:{local.date().isoformat()}"
    exhausted_row = db.execute(select(KvState).where(KvState.key == key)).scalar_one_or_none()
    exhausted = int(exhausted_row.value) if exhausted_row and str(exhausted_row.value).isdigit() else 0
    return (
        f"Коллтрекинг за {local.date().isoformat()}\n"
        f"Звонков: {total}\n"
        f"Сопоставлено: {matched}\n"
        f"Повторных: {repeat}\n"
        f"Без сделки: {no_lead}\n"
        f"Спорных визитов: {disputed}\n"
        f"Исчерпаний пула: {exhausted}"
    )


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
            state["last_hour"] = hour_key
        day_key = local.date().isoformat()
        if state.get("mode") == "daily" and local.hour == 9 and state.get("last_daily") != day_key:
            sent = send(summary_text(db, now)) or sent
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
            await asyncio.to_thread(maybe_summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ct reconcile loop")
        await asyncio.sleep(60)
