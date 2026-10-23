"""Durable offline goal delivery, isolated from CRM retries and webhook latency.

The small per-call outbox lives in kv_state, independently of ct_call.result.
Ambiguous POST outcomes are recovered by a unique upload comment, never by
blindly posting the same conversion again. No customer identifiers are logged.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import logging
import re

import httpx
from sqlalchemy import JSON, select, text
from sqlalchemy.dialects.postgresql import insert

from app.calltrack.models import CallEvent, Visit
from app.config import get_settings
from app.database import get_engine, get_session_factory
from app.models import KvState

log = logging.getLogger(__name__)
PREFIX = "ct_metrika:"
API = "https://api-metrika.yandex.net/management/v1/counter/"
TERMINAL = {"processed", "skipped", "failed"}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _save(db, row, state):
    row.value = json.dumps(state, ensure_ascii=False)
    row.updated_at = _now()
    db.commit()


def enqueue_call(db, call, *, pilot=False):
    s = get_settings()
    if not s.ct_metrika_enabled or not s.ct_metrika_counter_id or not s.ct_metrika_goal:
        return False
    if not pilot and call.id <= s.ct_metrika_start_call_id:
        return False
    key = f"{PREFIX}{s.ct_metrika_counter_id}:{s.ct_metrika_goal}:{call.id}"
    if len(key) > 64:
        raise ValueError("metrika goal key too long")
    if db.get(KvState, key):
        return False
    snap = (call.result or {}).get("attribution_snapshot", {})
    visit = db.get(Visit, snap.get("visit_id") or call.visit_id) if (snap.get("visit_id") or call.visit_id) else None
    # Only a verified incoming provider event, with an unambiguous pool visit.
    raw = call.raw or {}
    reason = None
    if raw.get("EventType") != "dial-in" or raw.get("CallFlow") != "in":
        reason = "not_native_incoming"
    elif snap.get("kind", call.number_kind) != "pool" or not visit:
        reason = "no_unique_visit"
    elif snap.get("disputed") or visit.disputed:
        reason = "disputed_visit"
    uid = str(snap.get("ym_uid") or (visit.ym_uid if visit else "") or "").strip()
    yclid = str(snap.get("tracking", {}).get("yclid") or (visit.yclid if visit else "") or "").strip()
    id_type, value = ("ClientId", uid) if uid.isascii() and uid.isdigit() and int(uid) > 0 else ("Yclid", yclid)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        reason = reason or "missing_metrika_identifier"
    state = {
        "status": "skipped" if reason else "pending", "reason": reason,
        "call": call.id, "counter": s.ct_metrika_counter_id,
        "target": s.ct_metrika_goal, "id_type": id_type, "identifier": value,
        "time": int(call.started_at.timestamp()), "attempts": 0,
        "comment": f"MANSBAND{s.ct_metrika_counter_id}Goal{re.sub('[^a-zA-Z0-9]', '', s.ct_metrika_goal)}Call{call.id}",
    }
    stmt = insert(KvState).values(key=key, value=json.dumps(state), updated_at=_now()).on_conflict_do_nothing(index_elements=["key"])
    return bool(db.execute(stmt).rowcount)


def _csv(state):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow([state["id_type"], "Target", "DateTime"])
    writer.writerow([state["identifier"], state["target"], state["time"]])
    return buf.getvalue().encode("utf-8")


def _accept(db, row, state, upload):
    uid = upload.get("id")
    if not isinstance(uid, int) or uid <= 0:
        state.update(status="uncertain", error="missing_upload_id")
    else:
        state.update(status="uploaded", upload_id=uid, error=None,
                     uploaded_at=state.get("uploaded_at") or _now().isoformat())
    _save(db, row, state)


def _deliver(db, row, state, client, now):
    base = API + str(state["counter"]) + "/offline_conversions/"
    status = state["status"]
    if status in TERMINAL or float(state.get("next_try", 0)) > now.timestamp():
        return
    if now.timestamp() - state["time"] > 20 * 86400:
        state.update(status="failed", error="conversion_too_old")
        _save(db, row, state)
        return
    if status in {"sending", "uncertain"}:
        # Recover an accepted request after timeout/crash. Never re-post it.
        response = client.get(base + "uploadings", params={"type": "BASIC", "limit": 10000})
        response.raise_for_status()
        matches = [u for u in response.json().get("uploadings", []) if u.get("comment") == state["comment"]]
        if len(matches) == 1:
            _accept(db, row, state, matches[0])
        else:
            state.update(status="uncertain", error="ambiguous_upload", next_try=now.timestamp() + 300)
            _save(db, row, state)
        return
    if status == "uploaded":
        response = client.get(base + "uploading/" + str(state["upload_id"]))
        response.raise_for_status()
        upload = response.json().get("uploading", {})
        state["remote_status"] = upload.get("status")
        if upload.get("status") == "PROCESSED":
            state.update(status="processed" if upload.get("line_quantity") == 1 else "failed",
                         error=None if upload.get("line_quantity") == 1 else "row_rejected")
        elif upload.get("status") == "LINKAGE_FAILURE":
            state.update(status="failed", error="linkage_failure")
        state["next_try"] = now.timestamp() + 60
        _save(db, row, state)
        return
    if state["time"] > now.timestamp() - 2:
        return
    # Commit intent before POST: a worker crash becomes recovery, not a duplicate.
    state.update(status="sending", attempts=state.get("attempts", 0) + 1,
                 attempted_at=now.isoformat(), next_try=0)
    _save(db, row, state)
    try:
        response = client.post(base + "upload", params={"type": "BASIC", "comment": state["comment"]},
                               files={"file": ("conversion.csv", _csv(state), "text/csv")})
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        state.update(status="retry", error="connection_failed", next_try=now.timestamp() + min(900, 60 * 2 ** min(state["attempts"], 4)))
        _save(db, row, state)
        return
    except httpx.RequestError:
        state.update(status="uncertain", error="response_unknown", next_try=now.timestamp() + 60)
        _save(db, row, state)
        return
    if response.status_code in {401, 403, 429}:
        state.update(status="retry", error=f"http_{response.status_code}", next_try=now.timestamp() + 300)
        _save(db, row, state)
    elif 400 <= response.status_code < 500:
        state.update(status="failed", error=f"http_{response.status_code}")
        _save(db, row, state)
    elif response.status_code >= 500:
        state.update(status="uncertain", error=f"http_{response.status_code}", next_try=now.timestamp() + 60)
        _save(db, row, state)
    else:
        try:
            upload = response.json().get("uploading", {})
        except (ValueError, AttributeError):
            upload = {}
        _accept(db, row, state, upload)


def run_once():
    s = get_settings()
    if not s.ct_metrika_enabled:
        return {"enabled": False}
    if not s.ct_metrika_token or not s.ct_metrika_counter_id or not s.ct_metrika_goal:
        raise ValueError("metrika configuration incomplete")
    # Session lock survives queue commits and prevents concurrent senders.
    with get_engine().connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(95530802, 517568094)")):
            return {"locked": True}
        try:
            with get_session_factory()() as db:
                prefix = f"{PREFIX}{s.ct_metrika_counter_id}:{s.ct_metrika_goal}:"
                cursor_key = f"ct_metrika_scan:{s.ct_metrika_counter_id}:{s.ct_metrika_goal}"
                cursor = db.get(KvState, cursor_key)
                after = max(s.ct_metrika_start_call_id, int(cursor.value) if cursor else 0)
                calls = db.scalars(select(CallEvent).where(CallEvent.id > after).order_by(CallEvent.id).limit(200)).all()
                for call in calls:
                    enqueue_call(db, call)
                if calls:
                    if cursor is None:
                        cursor = KvState(key=cursor_key)
                        db.add(cursor)
                    cursor.value = str(calls[-1].id)
                db.commit()
                with httpx.Client(headers={"Authorization": "OAuth " + s.ct_metrika_token},
                                  timeout=20.0, follow_redirects=False) as client:
                    rows = db.scalars(select(KvState).where(
                        KvState.key.like(prefix + "%"),
                        KvState.value.cast(JSON)["status"].as_string().not_in(TERMINAL),
                    ).order_by(KvState.updated_at).limit(50)).all()
                    now = _now()
                    errors = 0
                    for row in rows:
                        state = json.loads(row.value)
                        if state["counter"] != s.ct_metrika_counter_id or state["target"] != s.ct_metrika_goal:
                            continue
                        try:
                            _deliver(db, row, state, client, now)
                        except Exception as exc:
                            db.rollback()
                            errors += 1
                            log.warning("metrika delivery call=%s error=%s", state["call"], type(exc).__name__)
                    from app.calltrack.telegram import incident

                    counts = dict(db.execute(text("SELECT value::jsonb->>'status', count(*) FROM kv_state WHERE key LIKE :prefix GROUP BY 1"),
                                             {"prefix": prefix + "%"}).all())
                    problems = errors + sum(counts.get(k, 0) for k in ("failed", "uncertain", "retry", "sending"))
                    stalled = any(json.loads(r.value).get("status") == "uploaded" and
                                  now - dt.datetime.fromisoformat(json.loads(r.value)["uploaded_at"]) > dt.timedelta(hours=3) for r in rows)
                    missing = db.scalar(text("SELECT count(*) FROM kv_state WHERE key LIKE :prefix AND value::jsonb->>'reason'='missing_metrika_identifier'"),
                                        {"prefix": prefix + "%"}) or 0
                    incident(db, "передача целей Метрики", bool(problems or stalled or missing),
                             f"Метрика: {problems} ошибок или неясных отправок, без идентификатора: {missing}, задержка обработки: {bool(stalled)}. Проверь очередь целей; повтор вслепую отключён.")
                    state = db.get(KvState, "ct_metrika_state")
                    if state is None:
                        state = KvState(key="ct_metrika_state")
                        db.add(state)
                    state.value = json.dumps({"heartbeat": now.isoformat(), "counts": counts, "errors": errors})
                    db.commit()
                    return counts
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(95530802, 517568094)"))


async def delivery_loop():
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("metrika worker error=%s", type(exc).__name__)
        await asyncio.sleep(60)
