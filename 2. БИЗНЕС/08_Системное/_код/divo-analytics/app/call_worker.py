"""Постоянная очередь звонков. Внешние POST никогда не повторяются вслепую."""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, or_, select, text

from app.amo_client import AmoClient, AmoError
from app.call_report import render_report, report_marker
from app.call_rules import ANALYSIS_VERSION, RULE_VERSION, validate_analysis
from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import CallDelivery, CallRecord, SyncState
from app.nexara import AmbiguousSubmission, NexaraClient, RemoteFailure, download_audio

log = logging.getLogger("calls")
LOCK_KEY = 3259059807
TERMINAL = {"complete", "needs_review", "recording_unavailable", "missed", "error", "submit_ambiguous"}


def now():
    return datetime.now(timezone.utc)


def state_set(db, key: str, value: str):
    row = db.get(SyncState, key)
    if row is None:
        db.add(SyncState(key=key, value=value))
    else:
        row.value = value


def retry_later(row, error: str, minutes: int | None = None):
    row.attempts += 1
    row.last_error = error
    delay = minutes if minutes is not None else min(360, 2 ** min(row.attempts, 9))
    row.next_attempt_at = now() + timedelta(minutes=delay)


def resolve_manager(candidates: list[dict]) -> int | None:
    """Авторство и участник — разные поля; карта только после проверки."""
    author_ids = {int(x) for x in settings.calls_verified_authors.split(",") if x.strip()}
    mapping = {}
    for entry in settings.calls_manager_map.split(";"):
        pieces = entry.rsplit(":", 2)
        if len(pieces) == 3:
            mapping[(pieces[0], pieces[1])] = int(pieces[2])
    found = set()
    for p in candidates:
        if (p.get("source", ""), str(p.get("employee", ""))) in mapping:
            found.add(mapping[(p.get("source", ""), str(p.get("employee", "")))])
        if p.get("author") in author_ids:
            found.add(int(p["author"]))
    return next(iter(found)) if len(found) == 1 else None


def unique_contact_leads(amo: AmoClient, entity_id: int) -> list[int]:
    links = list(amo.paginate(f"/api/v4/contacts/{entity_id}/links", "links"))
    return sorted({int(x["to_entity_id"]) for x in links if x.get("to_entity_type") == "leads"})


def upsert_note(db, note: dict, entity_type: str, entity_id: int, lead_ids: list[int],
                *, calibration: bool = False) -> CallRecord:
    p = note.get("params") or {}
    uniq = str(p.get("uniq") or "").strip()
    note_id = int(note["id"])
    call_key = uniq or f"note:{entity_type}:{note_id}"
    row = db.execute(select(CallRecord).where(CallRecord.account_id == settings.amo_account_id,
                                               CallRecord.call_key == call_key)).scalar_one_or_none()
    if row is None and uniq:
        # Примечание могло появиться сначала без uniq, затем обновиться.
        row = db.execute(select(CallRecord).where(CallRecord.account_id == settings.amo_account_id,
                    CallRecord.call_key == f"note:{entity_type}:{note_id}")).scalar_one_or_none()
        if row:
            row.call_key = uniq
    if row is None:
        row = CallRecord(account_id=settings.amo_account_id, call_key=call_key,
                         occurred_at=int(note.get("created_at") or 0),
                         direction="in" if note.get("note_type") == "call_in" else "out",
                         state="waiting_recording", category="pending", is_calibration=calibration,
                         entity_refs=[], lead_ids=[], owner_candidates=[])
        db.add(row)
        db.flush()
    refs = list(row.entity_refs or [])
    ref = {"type": entity_type, "entity_id": entity_id, "note_id": note_id}
    if ref not in refs:
        refs.append(ref)
    row.entity_refs = refs
    row.lead_ids = sorted(set(row.lead_ids or []) | set(lead_ids))
    candidate = {"source": str(p.get("source") or ""), "employee": str(p.get("call_responsible") or ""),
                 "author": int(note.get("created_by") or 0)}
    candidates = list(row.owner_candidates or [])
    if candidate not in candidates:
        candidates.append(candidate)
    row.owner_candidates = candidates
    row.manager_id = resolve_manager(candidates)
    row.manager_verified = row.manager_id is not None
    row.source = str(p.get("source") or "")[:128]
    row.duration_sec = max(row.duration_sec or 0, int(p.get("duration") or 0))
    if p.get("link"):
        row.recording_url = str(p["link"]).strip()
    if row.state in ("waiting_recording", "recording_unavailable", "missed") and row.recording_url:
        row.state = "ready"
        row.last_error = ""
        row.next_attempt_at = None
    if len(row.lead_ids) != 1:
        # Уже отправленный отчёт не переадресуем при позднем появлении второй связи.
        row.last_error = "ambiguous_lead_binding"
    return row


def collect_calls(amo: AmoClient, *, calibration: int = 0):
    until = int(time.time())
    with SessionLocal() as db:
        start_row = db.get(SyncState, "calls_start_at")
        if calibration:
            # Калибровка не устанавливает границу нового рабочего потока.
            start = until
        elif not start_row:
            start = settings.calls_start_at or until
            state_set(db, "calls_start_at", str(start))
            db.commit()
        else:
            start = int(start_row.value)
        cursor_row = db.get(SyncState, "calls_cursor")
        cursor = int(cursor_row.value) if cursor_row else start
        since = until - 7 * 86400 if calibration else max(start, cursor - settings.calls_overlap_sec)
        events = list(amo.paginate("/api/v4/events", "events", {
            "filter[type][0]": "incoming_call", "filter[type][1]": "outgoing_call",
            "filter[created_at][from]": since,
            "filter[created_at][to]": until}, limit=100))
        entities = sorted({(e["entity_type"], int(e["entity_id"])) for e in events
                           if e.get("entity_type") in ("lead", "contact")})
        notes = []
        for typ, entity_id in entities:
            path = "leads" if typ == "lead" else "contacts"
            leads = [entity_id] if typ == "lead" else unique_contact_leads(amo, entity_id)
            for note in amo.paginate(f"/api/v4/{path}/{entity_id}/notes", "notes"):
                if note.get("note_type") in ("call_in", "call_out") and since <= int(note.get("created_at") or 0) <= until:
                    notes.append((note, typ, entity_id, leads))
        if calibration:
            # Последние входящие и исходящие; повторность/тематика определится по записи.
            groups = [sorted((n for n in notes if n[0]["note_type"] == t and (n[0].get("params") or {}).get("link")),
                             key=lambda n: int(n[0].get("created_at") or 0), reverse=True)
                      for t in ("call_in", "call_out")]
            selected, keys = [], set()
            while any(groups) and len(keys) < calibration:
                for group in groups:
                    if not group or len(keys) >= calibration:
                        continue
                    item = group.pop(0)
                    key = str((item[0].get("params") or {}).get("uniq") or item[0]["id"])
                    # Все дубли выбранного звонка нужны для обнаружения неоднозначных сделок.
                    if key not in keys:
                        selected.append(item)
                        keys.add(key)
            notes = [n for n in notes if str((n[0].get("params") or {}).get("uniq") or n[0]["id"]) in keys]
        for note, typ, entity_id, leads in notes:
            upsert_note(db, note, typ, entity_id, leads, calibration=bool(calibration))
        if not calibration:
            state_set(db, "calls_cursor", str(until))
            state_set(db, "calls_last_collection", now().isoformat())
        db.commit()
        return len(notes)


def refresh_recording(db, row: CallRecord, amo: AmoClient):
    for ref in row.entity_refs:
        path = "leads" if ref["type"] == "lead" else "contacts"
        data = amo.get(f"/api/v4/{path}/{ref['entity_id']}/notes/{ref['note_id']}") or {}
        # Получение одиночного примечания возвращает объект, без _embedded.
        note = data if data.get("id") else next(iter(data.get("_embedded", {}).get("notes", [])), {})
        if note:
            upsert_note(db, note, ref["type"], ref["entity_id"], row.lead_ids,
                        calibration=row.is_calibration)
    if row.recording_url:
        row.state = "ready"
    elif int(time.time()) - row.occurred_at > settings.calls_recording_wait_hours * 3600:
        row.state = "missed" if row.duration_sec == 0 else "recording_unavailable"
        row.category = "insufficient"
        row.category_reason = "Нет записи разговора" if row.duration_sec else "Разговор не состоялся"
        row.next_attempt_at = None
    else:
        retry_later(row, "recording_not_ready")
    db.commit()


def finish_result(row: CallRecord, result: dict):
    row.provider_result = result
    transcript = result.get("transcription") or {}
    if not isinstance(transcript, dict):
        transcript = {}
    raw = result.get("llm_output")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    row.transcript = transcript
    try:
        verdict = validate_analysis(raw if isinstance(raw, dict) else {}, transcript, row.direction)
    except (ValueError, TypeError, AttributeError):
        verdict = {"analysis": {}, "errors": ["invalid_transcript_structure"], "is_scored": False,
                   "score": None, "yes_count": 0, "applicable_count": 0}
    if not str(transcript.get("text") or "").strip() and not transcript.get("segments"):
        verdict["errors"].append("transcript_empty")
        verdict.update(is_scored=False, score=None)
    row.analysis = verdict["analysis"]
    row.validation_errors = verdict["errors"]
    row.is_scored = verdict["is_scored"]
    row.score = verdict["score"]
    row.yes_count = verdict["yes_count"]
    row.applicable_count = verdict["applicable_count"]
    row.category = row.analysis.get("category", "insufficient")
    row.category_reason = row.analysis.get("category_reason", "Анализ требует проверки")
    row.rule_version = RULE_VERSION
    row.analysis_version = ANALYSIS_VERSION
    row.state = "needs_review" if row.validation_errors or (row.category == "primary_inbound" and not row.is_scored) else "complete"
    row.last_error = "analysis_validation_failed" if row.validation_errors else ""
    row.next_attempt_at = None


def process_calls(amo: AmoClient):
    with SessionLocal() as db:
        # Перезапуск между POST и commit: неизвестно, был ли платный запрос принят.
        for row in db.scalars(select(CallRecord).where(CallRecord.state == "submitting")):
            row.state = "submit_ambiguous"
            row.last_error = "interrupted_submission_requires_review"
        db.commit()
        rows = list(db.scalars(select(CallRecord).where(
            CallRecord.state.in_(["waiting_recording", "ready", "processing"]),
            or_(CallRecord.next_attempt_at.is_(None), CallRecord.next_attempt_at <= now())
        ).order_by(CallRecord.occurred_at).limit(100)))
        nexara = NexaraClient()
        try:
            inflight = db.scalar(select(func.count()).select_from(CallRecord).where(
                CallRecord.state.in_(["processing", "submit_ambiguous"])))
            for row in rows:
                try:
                    if row.state == "waiting_recording":
                        refresh_recording(db, row, amo)
                    if row.state == "processing":
                        status = nexara.poll(row.nexara_job_id)
                        if status.get("status") == "complete":
                            finish_result(row, status.get("result") or {})
                            inflight -= 1
                        elif status.get("status") == "error":
                            row.state = "error"
                            row.last_error = "nexara_job_failed"
                            inflight -= 1
                        else:
                            row.next_attempt_at = now() + timedelta(seconds=30)
                        db.commit()
                    elif row.state == "ready" and settings.calls_process_enabled:
                        if not settings.nexara_api_key or not settings.calls_recording_hosts:
                            row.last_error = "nexara_or_recording_hosts_not_configured"
                            db.commit()
                            continue
                        if inflight >= settings.calls_max_inflight:
                            continue
                        audio, mime = download_audio(row.recording_url)
                        try:
                            row.state = "submitting"
                            row.rule_version = RULE_VERSION
                            row.analysis_version = ANALYSIS_VERSION
                            row.provider_result = {"submitted_at": now().isoformat()}
                            db.commit()
                            row.nexara_job_id = nexara.submit(audio, mime, row.direction, row.occurred_at)
                            row.state = "processing"
                            row.next_attempt_at = now() + timedelta(seconds=30)
                            row.last_error = ""
                            db.commit()
                            inflight += 1
                        finally:
                            audio.close()
                except AmbiguousSubmission as exc:
                    row.state = "submit_ambiguous"
                    row.last_error = str(exc)
                    inflight += 1
                    db.commit()
                except (RemoteFailure, httpx.TransportError) as exc:
                    code = str(exc) if isinstance(exc, RemoteFailure) else "recording_transport_failed"
                    if row.state == "processing" and code.endswith("404"):
                        row.state = "error"
                        row.last_error = "nexara_job_result_unavailable"
                        row.next_attempt_at = None
                        inflight -= 1
                        db.commit()
                        continue
                    if row.state == "submitting":
                        row.state = "ready" if code.endswith("429") else "error"
                    retry_later(row, code)
                    if row.state == "ready" and row.attempts >= 8:
                        row.state = "recording_unavailable"
                        row.category = "insufficient"
                        row.category_reason = "Не удалось получить запись"
                    db.commit()
        finally:
            nexara.close()


def find_note(amo: AmoClient, lead_id: int, marker: str) -> str | None:
    for note in amo.paginate(f"/api/v4/leads/{lead_id}/notes", "notes"):
        if note.get("note_type") == "common" and marker in str((note.get("params") or {}).get("text", "")):
            return str(note["id"])
    return None


def deliver_amo(db, delivery: CallDelivery, row: CallRecord, amo: AmoClient):
    if len(row.lead_ids) != 1 or str(row.lead_ids[0]) != delivery.target:
        delivery.state = "blocked"
        delivery.last_error = "ambiguous_lead_binding"
        db.commit()
        return
    marker = report_marker(row, delivery.target)
    existing = find_note(amo, int(delivery.target), marker)
    if existing:
        delivery.state, delivery.remote_id, delivery.last_error = "sent", existing, ""
        db.commit()
        return
    if delivery.state in ("sending", "ambiguous"):
        # Видимость amo может запаздывать: после таймаута автоматически только читаем.
        delivery.state = "ambiguous"
        retry_later(delivery, "amo_write_not_confirmed", minutes=10)
        db.commit()
        return
    delivery.state = "sending"
    delivery.attempts += 1
    db.commit()
    try:
        resp = amo.post_once(f"/api/v4/leads/{delivery.target}/notes", [
            {"note_type": "common", "params": {"text": render_report(row, target=delivery.target)}}])
    except httpx.TransportError:
        delivery.state = "ambiguous"
        retry_later(delivery, "amo_write_transport_ambiguous", minutes=10)
        db.commit()
        return
    if resp.status_code >= 500:
        delivery.state = "ambiguous"
        retry_later(delivery, "amo_write_server_ambiguous", minutes=10)
    elif resp.status_code == 429:
        delivery.state = "pending"
        retry_later(delivery, "amo_write_rate_limited")
    elif resp.status_code >= 400:
        delivery.state = "blocked"
        delivery.last_error = f"amo_write_http_{resp.status_code}"
    else:
        try:
            notes = resp.json().get("_embedded", {}).get("notes", [])
            delivery.remote_id = str(notes[0]["id"]) if notes else ""
        except (ValueError, KeyError):
            delivery.remote_id = ""
        # Подтверждение отдельным GET, даже при HTTP 200.
        delivery.state = "ambiguous"
        db.commit()
        existing = find_note(amo, int(delivery.target), marker)
        if existing:
            delivery.state, delivery.remote_id, delivery.last_error = "sent", existing, ""
        else:
            retry_later(delivery, "amo_readback_pending", minutes=2)
    db.commit()


def deliver_telegram(db, delivery: CallDelivery, row: CallRecord):
    if delivery.state in ("sending", "ambiguous"):
        delivery.state = "ambiguous"
        delivery.last_error = "telegram_delivery_requires_manual_check"
        db.commit()
        return
    delivery.state = "sending"
    delivery.attempts += 1
    db.commit()
    try:
        # Повторяется только установление соединения, до отправки HTTP-запроса.
        # IPv6 недоступен в текущей Docker-сети; используем IPv4.
        with httpx.Client(timeout=httpx.Timeout(30, connect=12),
                          transport=httpx.HTTPTransport(retries=2, local_address="0.0.0.0")) as client:
            resp = client.post(f"https://api.telegram.org/bot{settings.calls_telegram_bot_token}/sendMessage",
                json={"chat_id": delivery.target, "text": render_report(row, telegram=True),
                      "link_preview_options": {"is_disabled": True}})
        data = resp.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        delivery.state = "pending"
        retry_later(delivery, "telegram_connection_failed", minutes=2)
        db.commit()
        return
    except (httpx.TransportError, ValueError):
        delivery.state, delivery.last_error = "ambiguous", "telegram_delivery_requires_manual_check"
        db.commit()
        return
    if resp.status_code >= 500:
        delivery.state, delivery.last_error = "ambiguous", "telegram_server_ambiguous"
    elif data.get("ok") and data.get("result", {}).get("message_id"):
        delivery.state, delivery.remote_id, delivery.last_error = "sent", str(data["result"]["message_id"]), ""
    elif resp.status_code == 429:
        delivery.state = "pending"
        retry_later(delivery, "telegram_rate_limited", minutes=max(1, int(data.get("parameters", {}).get("retry_after", 60)) // 60 + 1))
    else:
        delivery.state, delivery.last_error = "blocked", f"telegram_http_{resp.status_code}"
    db.commit()


def deliver_calls(amo: AmoClient):
    with SessionLocal() as db:
        rows = list(db.scalars(select(CallRecord).where(CallRecord.is_calibration.is_(False),
                          CallRecord.state.in_(["complete", "needs_review", "missed", "recording_unavailable", "error", "submit_ambiguous"]))))
        for row in rows:
            targets = []
            if settings.calls_amo_enabled and len(row.lead_ids) == 1:
                targets.append(("amo", str(row.lead_ids[0])))
            if settings.calls_telegram_enabled and settings.calls_telegram_bot_token and settings.calls_telegram_chat_id:
                targets.append(("telegram", settings.calls_telegram_chat_id))
            for channel, target in targets:
                delivery = db.scalar(select(CallDelivery).where(CallDelivery.call_id == row.id,
                            CallDelivery.channel == channel, CallDelivery.target == target))
                if not delivery:
                    delivery = CallDelivery(call_id=row.id, channel=channel, target=target, state="pending")
                    db.add(delivery)
                    db.commit()
                if delivery.state in ("sent", "blocked"):
                    continue
                if delivery.next_attempt_at and delivery.next_attempt_at > now():
                    continue
                try:
                    if channel == "amo":
                        deliver_amo(db, delivery, row, amo)
                    else:
                        deliver_telegram(db, delivery, row)
                except (AmoError, httpx.TransportError):
                    retry_later(delivery, "amo_read_failed")
                    db.commit()


def run_once(*, calibration: int = 0):
    # Отдельное соединение держит session advisory lock на весь цикл.
    with engine.connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}):
            return
        amo = AmoClient()
        try:
            failures = []
            for phase, action in [
                ("collect", lambda: collect_calls(amo, calibration=calibration)
                 if settings.calls_enabled or calibration else None),
                ("process", lambda: process_calls(amo)),
                ("deliver", lambda: deliver_calls(amo)),
            ]:
                try:
                    action()
                except Exception as exc:
                    failures.append(f"{phase}:{type(exc).__name__}")
                    log.error("calls phase failed: %s %s", phase, type(exc).__name__)
            with SessionLocal() as db:
                if not failures:
                    state_set(db, "calls_last_success", now().isoformat())
                state_set(db, "calls_last_error", ";".join(failures))
                db.commit()
            if failures and calibration:
                raise RuntimeError("calibration_cycle_failed")
        except Exception as exc:
            with SessionLocal() as db:
                state_set(db, "calls_last_error", type(exc).__name__)
                db.commit()
            # traceback может содержать URL или входные данные: в журнал только тип.
            log.error("calls cycle failed: %s", type(exc).__name__)
            raise
        finally:
            amo.close()
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})


def retry_payment_blocked() -> int:
    """Только подтверждённый отказ до создания задания; неопределённые POST не трогаем."""
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar():
            raise RuntimeError("calls_worker_busy")
        try:
            with SessionLocal() as db:
                rows = list(db.scalars(select(CallRecord).where(
                    CallRecord.state == "error", CallRecord.last_error == "nexara_submit_http_402",
                    CallRecord.nexara_job_id.is_(None))))
                for row in rows:
                    row.state = "ready"
                    row.last_error = ""
                    row.next_attempt_at = None
                db.commit()
                return len(rows)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--calibrate", type=int, default=0)
    parser.add_argument("--retry-payment-blocked", action="store_true")
    args = parser.parse_args()
    init_db()
    if args.retry_payment_blocked:
        print(json.dumps({"requeued": retry_payment_blocked()}))
        return
    if args.once or args.calibrate:
        run_once(calibration=args.calibrate)
        return
    while True:
        started = time.monotonic()
        try:
            if settings.calls_enabled or settings.calls_process_enabled or settings.calls_amo_enabled or settings.calls_telegram_enabled:
                run_once()
        except Exception:
            pass  # ошибка записана в calls_last_error; очередь переживает перезапуск
        time.sleep(max(1, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
