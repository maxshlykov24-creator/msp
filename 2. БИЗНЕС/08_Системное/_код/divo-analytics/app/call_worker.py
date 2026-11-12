"""Постоянная очередь звонков. Внешние POST никогда не повторяются вслепую."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import defer

from app.amo_client import AmoClient, AmoError
from app.call_report import render_report, report_marker
from app.call_rules import ANALYSIS_VERSION, MIN_CALL_DURATION_SEC, RULE_VERSION, LEGACY_RULE_VERSION, validate_analysis
from app.call_rules import analysis_version_for, analysis_schema, analysis_prompt, SUPPORTED_RULE_VERSIONS, V3_RULE_VERSION
from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import CallDelivery, CallRecord, SyncState, CallAnalysisRevision, CallDeliverySnapshot
from app.nexara import AmbiguousSubmission, NexaraClient, RemoteFailure, SonnetCallAgent, download_audio

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
    participants = {int(x) for x in settings.calls_verified_participants.split(",") if x.strip()}
    found = set()
    for p in candidates:
        if p.get("source") == "MangoOfficeWidget" and p.get("participant") in participants:
            found.add(int(p["participant"]))
        if (p.get("source", ""), str(p.get("employee", ""))) in mapping:
            found.add(mapping[(p.get("source", ""), str(p.get("employee", "")))])
        if p.get("author") in author_ids:
            found.add(int(p["author"]))
    return next(iter(found)) if len(found) == 1 else None


def spoken_manager_resolution(transcript: dict) -> dict | None:
    """Only direct employee self-introductions, never a client's address/name."""
    found = {}
    for segment in (transcript or {}).get("segments") or []:
        speaker = str(segment.get("speaker") or "").lower()
        if not any(role in speaker for role in ("менеджер", "сотрудник", "оператор")):
            continue
        text_value = str(segment.get("text") or "")
        for uid, name in settings.call_manager_names.items():
            name_pattern = re.escape(name)
            patterns = [rf"меня\s+зовут\s+{name_pattern}\b",
                        rf"меня\s+{name_pattern}\s+зовут\b",
                        rf"\b{name_pattern}[,.]?\s+слушаю\s+вас\b"]
            # A name-only opening greeting is also a self-introduction.
            if float(segment.get("start") or 0) <= 10:
                patterns.append(rf"^\s*{name_pattern}[,.!]+\s*Добрый\s+(?:день|вечер)\b")
            for pattern in patterns:
                match = re.search(pattern, text_value, re.IGNORECASE)
                if match:
                    found.setdefault(uid, {"quote": match.group(0), "start": segment.get("start"),
                                           "end": segment.get("end"), "speaker": segment.get("speaker")})
                    break
    if len(found) == 1:
        uid = next(iter(found))
        return {"source": "spoken_self_intro", "manager_id": uid,
                "evidence": [found[uid]], "version": "self-intro.1"}
    if found:
        return {"source": "multiple_self_introductions", "manager_id": None,
                "evidence": list(found.values()), "version": "self-intro.1"}
    return None


def assign_call_manager(row: CallRecord) -> None:
    history = dict(row.provider_result or {})
    previous = history.get("manager_resolution") or {}
    # Retain a contextual human review only while its exact source fragments exist.
    reviewed = previous.get("source") == "reviewed_self_intro" and previous.get("manager_id") in settings.call_manager_names
    texts = [str(s.get("text") or "") for s in (row.transcript or {}).get("segments", [])]
    reviewed = reviewed and bool(previous.get("evidence")) and all(
        e.get("quote") and any(e["quote"] in text_value for text_value in texts)
        for e in previous.get("evidence", []))
    resolution = previous if reviewed else spoken_manager_resolution(row.transcript or {})
    if resolution is None:
        resolution = {"source": "call_participant", "manager_id": resolve_manager(row.owner_candidates or []),
                      "evidence": [], "version": "self-intro.1"}
    uid = resolution["manager_id"]
    if previous != resolution:
        if row.manager_id != uid or row.manager_verified != (uid is not None):
            changes = list(history.get("manager_resolution_history") or [])
            changes.append({"at": now().isoformat(), "previous_manager_id": row.manager_id,
                            "previous_verified": row.manager_verified, "resolution": resolution})
            history["manager_resolution_history"] = changes
        history["manager_resolution"] = resolution
        row.provider_result = history
    row.manager_id = uid
    row.manager_verified = uid is not None


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
                 "author": int(note.get("created_by") or 0),
                 "participant": int(note.get("responsible_user_id") or 0)}
    candidates = list(row.owner_candidates or [])
    if candidate not in candidates:
        candidates.append(candidate)
    row.owner_candidates = candidates
    assign_call_manager(row)
    row.source = str(p.get("source") or "")[:128]
    row.duration_sec = max(row.duration_sec or 0, int(p.get("duration") or 0))
    if p.get("link"):
        row.recording_url = str(p["link"]).strip()
    if row.state in ("waiting_recording", "recording_unavailable", "missed") and row.recording_url:
        row.state = "ready"
        row.last_error = ""
        row.next_attempt_at = None
    if row.state == "ignored_short" and row.duration_sec >= MIN_CALL_DURATION_SEC and row.recording_url:
        row.state = "analysis_ready" if SonnetCallAgent.catalog(row.transcript or {}) else "ready"
        row.last_error = ""
        row.next_attempt_at = None
    if len(row.lead_ids) != 1:
        # Уже отправленный отчёт не переадресуем при позднем появлении второй связи.
        row.last_error = "ambiguous_lead_binding"
    return row


def collect_calls(amo: AmoClient, *, calibration: int = 0, backfill_days: int = 0, since_at: int = 0):
    until = int(time.time())
    with SessionLocal() as db:
        start_row = db.get(SyncState, "calls_start_at")
        if calibration:
            # Калибровка не устанавливает границу нового рабочего потока.
            start = until
        elif since_at:
            start = since_at
        elif backfill_days:
            start = until - backfill_days * 86400
        elif not start_row:
            start = settings.calls_start_at or until
            state_set(db, "calls_start_at", str(start))
            db.commit()
        else:
            start = int(start_row.value)
        cursor_row = db.get(SyncState, "calls_cursor")
        cursor = int(cursor_row.value) if cursor_row else start
        since = (until - 7 * 86400 if calibration else start if backfill_days or since_at
                 else max(start, cursor - settings.calls_overlap_sec))
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
        if not calibration and not backfill_days and not since_at:
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


def processing_context(previous: dict | None) -> dict:
    """Keep local review/delivery controls across external transcription responses."""
    keys = ("reanalysis_delivery_suppressed", "manual_review", "reviews",
            "week_review_probe", "week_review_batch", "manager_resolution")
    return {key: previous[key] for key in keys if key in (previous or {})}


def finish_result(row: CallRecord, result: dict):
    row.provider_result = {**result, **processing_context(row.provider_result)}
    # Pure diarization returns the transcript itself; legacy combined jobs wrap it.
    transcript = result.get("transcription") or (result if "text" in result or "segments" in result else {})
    if not isinstance(transcript, dict):
        transcript = {}
    row.transcript = transcript
    assign_call_manager(row)
    if row.duration_sec < MIN_CALL_DURATION_SEC:
        row.transcript = transcript
        row.state, row.category = "ignored_short", "insufficient"
        row.category_reason = "Разговор короче 30 секунд"
        row.analysis, row.validation_errors = {}, []
        row.is_scored, row.score = False, None
        row.yes_count = row.applicable_count = 0
        row.next_attempt_at = None
        return
    if settings.calls_analysis_enabled:
        row.transcript = transcript
        row.analysis = {}
        row.is_scored = False
        row.score = None
        row.yes_count = row.applicable_count = 0
        row.validation_errors = []
        row.state = "analysis_ready" if SonnetCallAgent.catalog(transcript) else "needs_review"
        row.last_error = "" if row.state == "analysis_ready" else "transcript_empty"
        row.next_attempt_at = None
        row.analysis_version = analysis_version_for(row.rule_version or active_rules_for_default())
        return
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
    row.rule_version = LEGACY_RULE_VERSION
    row.analysis_version = ANALYSIS_VERSION
    row.state = "needs_review" if row.validation_errors or (row.category == "primary_inbound" and not row.is_scored) else "complete"
    row.last_error = "analysis_validation_failed" if row.validation_errors else ""

    row.next_attempt_at = None


def analyze_calls():
    if not settings.calls_analysis_enabled or not settings.calls_process_enabled:
        return
    with SessionLocal() as db:
        cooldown = db.get(SyncState, "calls_analysis_retry_after")
        if cooldown and cooldown.value and now().timestamp() < float(cooldown.value):
            return
        for row in db.scalars(select(CallRecord).where(CallRecord.state == "analyzing")):
            row.state = "analysis_ambiguous"
            row.last_error = "agent_interrupted_requires_review"
        db.commit()
        if not settings.calls_sonnet_api_key:
            raise RuntimeError("agent_not_configured")
        query = select(CallRecord).where(CallRecord.state == "analysis_ready",
            CallRecord.duration_sec >= MIN_CALL_DURATION_SEC,
            or_(CallRecord.next_attempt_at.is_(None), CallRecord.next_attempt_at <= now()))
        if settings.calls_process_from:
            # The original accepted sample may predate the live/history boundary.
            # Explicitly queued text-only calibration must still finish.
            query = query.where(or_(CallRecord.occurred_at >= settings.calls_process_from,
                                   CallRecord.is_calibration.is_(True)))
        if settings.calls_calibration_only:
            query = query.where(CallRecord.is_calibration.is_(True))
        agent = SonnetCallAgent()
        try:
            for row in db.scalars(query.order_by(CallRecord.occurred_at.desc()).limit(5)):
                version = row.rule_version if (row.provider_result or {}).get("agent_result") and row.validation_errors else active_rules(db)
                row.rule_version = version
                row.state = "analyzing"
                row.analysis_version = analysis_version_for(version)
                db.commit()
                try:
                    history = dict(row.provider_result or {})
                    previous = history.get("agent_result")
                    correction = {"references": previous["references"], "errors": row.validation_errors} if previous and row.validation_errors else None
                    result = agent.analyze(row.transcript, row.direction, row.occurred_at, rule_version=version, **({"correction": correction} if correction else {}))
                    state_set(db, "calls_analysis_retry_after", "")
                    state_set(db, "calls_analysis_blocked", "")
                    attempts = list(history.get("agent_attempts") or [])
                    if previous and not attempts:
                        attempts.append(previous)
                    attempts.append(result)
                    history.update(agent_result=result, agent_attempts=attempts)
                    row.provider_result = history
                    verdict = validate_analysis(result["analysis"], row.transcript, row.direction, version)
                    row.validation_errors = verdict["errors"]
                    db.commit()  # Сохраняем оплаченный ответ до единственной коррекции.
                    if verdict["errors"] and not correction:
                        result = agent.analyze(row.transcript, row.direction, row.occurred_at, rule_version=version,
                            correction={"references": result["references"], "errors": verdict["errors"]})
                        history = {**history, "agent_result": result, "agent_attempts": attempts + [result]}
                        row.provider_result = history
                        verdict = validate_analysis(result["analysis"], row.transcript, row.direction, version)
                    row.analysis = verdict["analysis"]
                    row.validation_errors = verdict["errors"]
                    row.is_scored, row.score = verdict["is_scored"], verdict["score"]
                    row.yes_count, row.applicable_count = verdict["yes_count"], verdict["applicable_count"]
                    row.category = row.analysis.get("category", "insufficient")
                    row.category_reason = row.analysis.get("category_reason", "Анализ требует проверки")
                    row.rule_version = version
                    row.state = "needs_review" if row.validation_errors or (version == V3_RULE_VERSION and candidate_state(verdict, version)=="needs_review") else (
                        "ignored_no_conversation" if row.analysis.get("conversation_occurred") is False else
                        "insufficient" if row.category == "primary_inbound" and not row.is_scored else "complete")
                    row.last_error = "analysis_validation_failed" if row.validation_errors else ""
                    if version != LEGACY_RULE_VERSION and not row.validation_errors:
                        archive_current(db, row, "automatic", result)
                except AmbiguousSubmission as exc:
                    row.state, row.last_error = "analysis_ambiguous", str(exc)
                except RemoteFailure as exc:
                    if hasattr(exc, 'provider_payload'):
                        row.provider_result = {**history, 'failed_provider_response': exc.provider_payload}
                    if str(exc) == "agent_connection_not_established":
                        # No HTTP request was sent: a delayed retry cannot duplicate a paid analysis.
                        row.state = "analysis_ready"
                        retry_later(row, str(exc), minutes=5)
                        db.commit()
                        break
                    if str(exc) == "agent_http_402":
                        row.state = "analysis_ready"
                        retry_later(row, str(exc), minutes=30)
                        state_set(db, "calls_analysis_retry_after", str((now() + timedelta(minutes=30)).timestamp()))
                        state_set(db, "calls_analysis_blocked", str(exc))
                        db.commit()
                        break
                    if str(exc) == "agent_http_429":
                        # Explicit rejection is safe to retry. Preserve any paid first
                        # answer and continue its correction after the cooldown.
                        row.state = "analysis_ready"
                        retry_later(row, str(exc))
                        db.commit()
                        break
                    row.state, row.last_error = "needs_review", str(exc)
                except Exception:
                    row.state, row.last_error = "needs_review", "agent_result_invalid"
                row.next_attempt_at = None
                db.commit()
        finally:
            agent.close()


def reanalyze_calibration(*, repair: bool = False) -> int:
    """Явная команда: сохранённые транскрипты, без повторной отправки аудио."""
    if not settings.calls_analysis_enabled or not settings.calls_sonnet_api_key:
        raise RuntimeError("agent_not_configured")
    with engine.connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}):
            raise RuntimeError("calls_worker_busy")
        try:
            with SessionLocal() as db:
                rows = list(db.scalars(select(CallRecord).where(CallRecord.is_calibration.is_(True),
                    CallRecord.state.in_(["complete", "needs_review"]))))
                count = 0
                for row in rows:
                    if not SonnetCallAgent.catalog(row.transcript):
                        continue
                    prior = (row.provider_result or {}).get("agent_result")
                    if repair:
                        if not prior or not row.validation_errors or len((row.provider_result or {}).get("agent_attempts") or []) >= 2:
                            continue
                    elif row.analysis_version.startswith("sonnet.qa.") and prior:
                        continue
                    row.state, row.last_error = "analysis_ready", ""
                    row.analysis = {}
                    if not repair:
                        row.validation_errors = []
                    row.category, row.category_reason = "pending", "Ожидает анализа агента"
                    row.yes_count = row.applicable_count = 0
                    row.is_scored, row.score = False, None
                    row.next_attempt_at = None
                    count += 1
                db.commit()
                return count
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})


def process_calls(amo: AmoClient):
    with SessionLocal() as db:
        for row in db.scalars(select(CallRecord).where(
                CallRecord.state.in_(["ready", "analysis_ready"]),
                CallRecord.duration_sec < MIN_CALL_DURATION_SEC)):
            row.state, row.category = "ignored_short", "insufficient"
            row.category_reason = "Разговор короче 30 секунд"
            row.next_attempt_at = None
        db.commit()
        # Перезапуск между POST и commit: неизвестно, был ли платный запрос принят.
        for row in db.scalars(select(CallRecord).where(CallRecord.state == "submitting")):
            row.state = "submit_ambiguous"
            row.last_error = "interrupted_submission_requires_review"
        db.commit()
        processing_query = select(CallRecord).where(
            CallRecord.state.in_(["waiting_recording", "ready", "processing"]),
            or_(CallRecord.next_attempt_at.is_(None), CallRecord.next_attempt_at <= now())
        )
        if settings.calls_process_from:
            processing_query = processing_query.where(or_(
                CallRecord.occurred_at >= settings.calls_process_from,
                CallRecord.state == "processing"))
        if settings.calls_calibration_only:
            processing_query = processing_query.where(or_(CallRecord.is_calibration.is_(True),
                CallRecord.state.in_(["processing", "waiting_recording"])))
        rows = list(db.scalars(processing_query.order_by(
            case((CallRecord.state == "processing", 0), else_=1),
            CallRecord.occurred_at.desc()).limit(100)))
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
                        if row.duration_sec < MIN_CALL_DURATION_SEC:
                            row.state, row.category = "ignored_short", "insufficient"
                            row.category_reason = "Разговор короче 30 секунд"
                            row.next_attempt_at = None
                            db.commit()
                            continue
                        if settings.calls_calibration_only and not row.is_calibration:
                            continue
                        if not settings.nexara_api_key or not settings.calls_recording_hosts:
                            row.last_error = "nexara_or_recording_hosts_not_configured"
                            db.commit()
                            continue
                        if inflight >= settings.calls_max_inflight:
                            continue
                        audio, mime = download_audio(row.recording_url)
                        try:
                            row.state = "submitting"
                            row.rule_version = active_rules(db)
                            row.analysis_version = analysis_version_for(row.rule_version)
                            row.provider_result = {**processing_context(row.provider_result), "submitted_at": now().isoformat()}
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
    body = freeze_delivery(db, delivery, row)
    delivery.state = "sending"
    delivery.attempts += 1
    db.commit()
    try:
        resp = amo.post_once(f"/api/v4/leads/{delivery.target}/notes", [
            {"note_type": "common", "params": {"text": body}}])
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
    body = freeze_delivery(db, delivery, row)
    delivery.state = "sending"
    delivery.attempts += 1
    db.commit()
    try:
        # Повторяется только установление соединения, до отправки HTTP-запроса.
        with httpx.Client(timeout=httpx.Timeout(30, connect=12),
                          transport=httpx.HTTPTransport(retries=2)) as client:
            resp = client.post(f"https://api.telegram.org/bot{settings.calls_telegram_bot_token}/sendMessage",
                json={"chat_id": delivery.target, "text": body,
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


def delivery_skip_reason(row: CallRecord) -> str:
    """Диагностика не является отчётом о разговоре и остаётся в дашборде."""
    if row.duration_sec < MIN_CALL_DURATION_SEC:
        return "short_call"
    if row.is_calibration:
        return "calibration_sample"
    if not row.recording_url:
        return "no_recording"
    if (row.analysis or {}).get("conversation_occurred") is False:
        return "no_conversation"
    if settings.calls_delivery_from and row.occurred_at < settings.calls_delivery_from:
        return "historical_call"
    if not SonnetCallAgent.catalog(row.transcript or {}):
        return "no_transcript"
    if row.category == "insufficient" or row.state == "insufficient":
        return "insufficient_data"
    if row.state not in ("complete", "needs_review") or row.validation_errors:
        return "analysis_not_validated"
    if not (row.analysis or {}).get("summary", "").strip():
        return "no_analysis"
    return ""


def deliver_calls(amo: AmoClient):
    with SessionLocal() as db:
        # Filter historical/non-reportable calls before loading their paid payloads.
        conditions = [CallRecord.is_calibration.is_(False),
                      CallRecord.duration_sec >= MIN_CALL_DURATION_SEC,
                      CallRecord.recording_url != "",
                      CallRecord.state.in_(["complete", "needs_review"])]
        if settings.calls_delivery_from:
            conditions.append(CallRecord.occurred_at >= settings.calls_delivery_from)
        ids = list(db.scalars(select(CallRecord.id).where(*conditions)))
        for call_id in ids:
            row = db.scalar(select(CallRecord).options(
                defer(CallRecord.provider_result, raiseload=True),
                defer(CallRecord.entity_refs, raiseload=True),
                defer(CallRecord.owner_candidates, raiseload=True))
                .where(CallRecord.id == call_id).with_for_update())
            if row is None:
                continue
            if delivery_skip_reason(row):
                continue
            targets = []
            if settings.calls_amo_enabled and len(row.lead_ids) == 1:
                targets.append(("amo", str(row.lead_ids[0])))
            if settings.calls_telegram_enabled and settings.calls_telegram_bot_token and settings.calls_telegram_chat_id:
                targets.append(("telegram", settings.calls_telegram_chat_id))
            for channel, target in targets:
                delivery = db.scalar(select(CallDelivery).where(CallDelivery.call_id == row.id,
                            CallDelivery.channel == channel, CallDelivery.target == target))
                if not delivery and db.scalar(select(CallRecord.provider_result["reanalysis_delivery_suppressed"].as_boolean()).where(CallRecord.id == row.id)):
                    continue
                if not delivery:
                    delivery = CallDelivery(call_id=row.id, channel=channel, target=target, state="pending")
                    db.add(delivery)
                    db.flush()
                    freeze_delivery(db, delivery, row)
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


def run_once(*, calibration: int = 0, backfill_days: int = 0):
    # Отдельное соединение держит session advisory lock на весь цикл.
    with engine.connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}):
            return
        amo = AmoClient()
        try:
            failures = []
            for phase, action in [
                ("collect", lambda: collect_calls(amo, calibration=calibration, backfill_days=backfill_days)
                 if settings.calls_enabled or calibration or backfill_days else None),
                ("process", lambda: process_calls(amo)),
                ("analyze", analyze_calls),
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
            if failures and (calibration or backfill_days):
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


def close_insufficient(call_id: int, reviewer: str, reason: str, *, apply: bool = False) -> dict:
    """Закрыть нехватку данных без присвоения оценки, сохранив решение и исходник."""
    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer_and_reason_required")
    with SessionLocal() as db:
        row = db.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if not row:
            raise ValueError("call_not_found")
        if row.state == "insufficient":
            return {"id": call_id, "state": row.state, "changed": False}
        a = row.analysis or {}
        quality_issue = any(a.get(k) is False for k in ("recording_complete", "speech_clear", "roles_reliable"))
        unknown = any(c.get("status") == "unknown" or any(v.get("status") == "unknown"
                      for v in c.get("conditions", [])) for c in a.get("criteria", []))
        if row.state != "needs_review" or row.validation_errors or row.is_scored or not (quality_issue or unknown):
            raise ValueError("not_a_quality_insufficiency")
        result = {"id": call_id, "state": "insufficient", "changed": apply}
        if apply:
            history = dict(row.provider_result or {})
            reviews = list(history.get("reviews") or [])
            reviews.append({"at": now().isoformat(), "reviewer": reviewer, "reason": reason,
                            "previous_state": row.state, "decision": "insufficient", "analysis": row.analysis})
            history.update(reviews=reviews, manual_review=reviews[-1])
            row.provider_result = history
            row.state = "insufficient"
            row.category_reason = reason
            row.score, row.is_scored = None, False
            row.yes_count = row.applicable_count = 0
            row.last_error = ""
            db.commit()
        return result



SNAPSHOT_FIELDS = ("analysis", "category", "category_reason", "state", "score", "yes_count",
                   "applicable_count", "is_scored", "rule_version", "analysis_version", "validation_errors")


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def active_rules(db) -> str:
    state = db.get(SyncState, "calls_active_rule_version")
    version = state.value if state else LEGACY_RULE_VERSION
    if version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError("unsupported_active_rule_version")
    return version


def active_rules_for_default() -> str:
    with SessionLocal() as db:
        return active_rules(db)


def snapshot(row) -> dict:
    return {k: getattr(row, k) for k in SNAPSHOT_FIELDS}


def manual_history_hash(row) -> str:
    provider = row.provider_result or {}
    return stable_hash({"manual_review": provider.get("manual_review"), "reviews": provider.get("reviews")})


def source_hash(row) -> str:
    return stable_hash({"snapshot": snapshot(row), "transcript": row.transcript,
                        "manual_review": (row.provider_result or {}).get("manual_review")})


def freeze_delivery(db, delivery, row) -> str:
    saved = db.get(CallDeliverySnapshot, delivery.id)
    if saved:
        return saved.body
    db.flush()
    db.refresh(row, attribute_names=list(SNAPSHOT_FIELDS), with_for_update=True)
    saved = CallDeliverySnapshot(delivery_id=delivery.id, rule_version=row.rule_version,
        analysis_hash=stable_hash(row.analysis),
        body=render_report(row, telegram=delivery.channel == "telegram",
                           target=delivery.target if delivery.channel == "amo" else ""))
    db.add(saved)
    db.flush()
    return saved.body


def archive_current(db, row, origin: str, provider: dict | None = None):
    content = snapshot(row)
    key = f"archive:{row.id}:{stable_hash(content)}"
    existing = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.job_key == key))
    if existing:
        return existing
    rev = CallAnalysisRevision(call_id=row.id, job_key=key, rule_version=row.rule_version,
        analysis_version=row.analysis_version, transcript_hash=stable_hash(row.transcript),
        base_hash=stable_hash(content), state="archived" if origin == "before_reanalysis" else "published",
        result={"snapshot": content, "origin": origin, "provider": provider or {}},
        review={"previous_manual_review": (row.provider_result or {}).get("manual_review")},
        published_at=now() if origin != "before_reanalysis" else None)
    db.add(rev)
    db.flush()
    return rev


def candidate_state(verdict, version):
    analysis = verdict.get('analysis') or {}
    unresolved = version == V3_RULE_VERSION and (
        any(u.get('impact') in ('category','applicability') or
            (analysis.get('category')=='primary_inbound' and u.get('criterion_id')==0 and u.get('impact')!='display')
            for u in analysis.get('uncertainties', [])) or
        analysis.get('category') == 'insufficient' or
        (analysis.get('category') == 'primary_inbound' and not verdict.get('is_scored')))
    return 'needs_review' if verdict['errors'] or unresolved else 'ready'


def candidate_validation_metadata(version):
    from app.call_rules import evaluation_fingerprint
    return {'validation_engine_hash': evaluation_fingerprint(version)}


def stage_revision(call_id: int, run: int = 1, *, rule_version: str = RULE_VERSION,
                   correction_revision: int | None = None, findings: list[str] | None = None) -> dict:
    """One durable paid attempt; never clears a live analysis or touches deliveries."""
    with SessionLocal() as db:
        row = db.get(CallRecord, call_id)
        if not row or not row.analysis or not SonnetCallAgent.catalog(row.transcript):
            raise ValueError("published_transcript_required")
        thash = stable_hash(row.transcript)
        av = analysis_version_for(rule_version)
        correction=None
        previous=None
        if correction_revision is not None:
            previous=db.get(CallAnalysisRevision,correction_revision)
            if (not previous or previous.call_id!=call_id or previous.state not in ('ready','needs_review')
                    or previous.base_hash!=source_hash(row) or previous.rule_version!=rule_version):
                raise ValueError('matching_draft_revision_required')
            if not findings or len(findings)>10:raise ValueError('specific_semantic_findings_required')
            if previous.review.get('audio_review_required'):raise ValueError('audio_review_required')
            correction={'references':previous.result['provider']['references'],'errors':findings}
        from app.nexara import SonnetCallAgent as RequestBuilder
        body, _ = RequestBuilder.request_body(row.transcript, row.direction, row.occurred_at, rule_version=rule_version, correction=correction)
        configuration = stable_hash({"request": body, "rules": rule_version, "analysis": av})
        key = f"candidate:{call_id}:" + stable_hash({"transcript": thash, "configuration": configuration, "run": run})
        prior = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.job_key == key))
        if prior:
            return {"id": prior.id, "call_id": call_id, "state": prior.state, "existing": True}
        if previous and previous.review.get('semantic_child_revision'):
            raise ValueError('semantic_correction_already_attempted')
        rev = CallAnalysisRevision(call_id=call_id, job_key=key, rule_version=rule_version,
            analysis_version=av, transcript_hash=thash, base_hash=source_hash(row),
            state="running", result={"baseline": snapshot(row), "configuration_hash": configuration},
            review={"manual_hold": bool((row.provider_result or {}).get("manual_review") or (row.provider_result or {}).get("reviews"))})
        db.add(rev)
        db.flush()
        if previous:
            previous.review={**previous.review,'semantic_child_revision':rev.id,'semantic_findings':findings}
            previous.state='needs_review'
            rev.result={**rev.result,'correction_of':previous.id,'semantic_findings':findings}
        db.commit()
        rid, transcript, direction, occurred = rev.id, row.transcript, row.direction, row.occurred_at
    agent = SonnetCallAgent()
    try:
        result = agent.analyze(transcript, direction, occurred, rule_version=rule_version,
                               **({'correction':correction} if correction else {}))
        verdict = validate_analysis(result["analysis"], transcript, direction, rule_version)
        # Persist the paid result before any optional correction request.
        with SessionLocal() as db:
            rev = db.get(CallAnalysisRevision, rid)
            rev.result = {**rev.result, "provider": result, "verdict": verdict, **candidate_validation_metadata(rule_version)}
            rev.state = candidate_state(verdict, rule_version)
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            rev = db.get(CallAnalysisRevision, rid)
            if hasattr(exc, 'provider_payload'):
                rev.result = {**rev.result, 'failed_provider_response': exc.provider_payload}
            rev.state = "ambiguous" if isinstance(exc, AmbiguousSubmission) else "error"
            rev.error = str(exc) if isinstance(exc, (RemoteFailure, AmbiguousSubmission)) else type(exc).__name__
            db.commit()
    finally:
        agent.close()
    with SessionLocal() as db:
        rev = db.get(CallAnalysisRevision, rid)
        return {"id": rid, "call_id": call_id, "state": rev.state,
                "errors": rev.result.get("verdict", {}).get("errors", []), "error": rev.error}


def repair_revision(revision_id: int) -> dict:
    """One correction of a saved, rejected answer. Never repeat an ambiguous request."""
    with SessionLocal() as db:
        rev = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.id == revision_id).with_for_update())
        if not rev or rev.state not in ("needs_review", "ready") or rev.result.get("correction_started_at"):
            raise ValueError("revision_not_repairable")
        row = db.get(CallRecord, rev.call_id)
        if db.scalar(select(CallAnalysisRevision.id).where(CallAnalysisRevision.call_id == rev.call_id,
                CallAnalysisRevision.review["audio_review_required"].as_boolean().is_(True)).limit(1)):
            raise ValueError("audio_review_required")
        if source_hash(row) != rev.base_hash:
            raise ValueError("revision_source_changed")
        previous = rev.result["provider"]
        errors = rev.result["verdict"]["errors"] or ["semantic_review: Перечитай весь разговор и проверь каждое условие, особенно конкретные требования клиента, принадлежность реплик и подтверждение сроков. Общий вопрос менеджера не доказывает выявленную потребность. Исправь фактические пропуски и неверные цитаты; верни полный разбор."]
        rev.result = {**rev.result, "correction_started_at": now().isoformat(), "first_provider": previous,
                      "first_verdict": rev.result["verdict"], "first_analysis_version": rev.analysis_version}
        rev.analysis_version = analysis_version_for(rev.rule_version)
        rule_version = rev.rule_version
        rev.state = "correcting"
        transcript, direction, occurred = row.transcript, row.direction, row.occurred_at
        db.commit()
    agent = SonnetCallAgent()
    try:
        result = agent.analyze(transcript, direction, occurred, rule_version=rule_version,
            correction={"references": previous["references"], "errors": errors})
        verdict = validate_analysis(result["analysis"], transcript, direction, rule_version)
        with SessionLocal() as db:
            rev = db.get(CallAnalysisRevision, revision_id)
            rev.result = {**rev.result, "provider": result, "verdict": verdict, **candidate_validation_metadata(rule_version)}
            rev.state = candidate_state(verdict, rule_version)
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            rev = db.get(CallAnalysisRevision, revision_id)
            if hasattr(exc, 'provider_payload'):
                rev.result = {**rev.result, 'failed_provider_response': exc.provider_payload}
            rev.state = "ambiguous" if isinstance(exc, AmbiguousSubmission) else "error"
            rev.error = str(exc) if isinstance(exc, (RemoteFailure, AmbiguousSubmission)) else type(exc).__name__
            db.commit()
    finally:
        agent.close()
    with SessionLocal() as db:
        rev = db.get(CallAnalysisRevision, revision_id)
        return {"id": rev.id, "call_id": rev.call_id, "state": rev.state,
                "errors": rev.result["verdict"]["errors"], "error": rev.error}


def review_revision(revision_id: int, reviewer: str, reason: str) -> dict:
    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer_and_reason_required")
    with SessionLocal() as db:
        rev = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.id == revision_id).with_for_update())
        if not rev or rev.state != "ready" or rev.result.get("verdict", {}).get("errors"):
            raise ValueError("revision_not_ready")
        if rev.rule_version==V3_RULE_VERSION and rev.result.get('validation_engine_hash')!=candidate_validation_metadata(rev.rule_version)['validation_engine_hash']:
            raise ValueError('revision_requires_local_revalidation')
        row = db.get(CallRecord, rev.call_id)
        if db.scalar(select(CallAnalysisRevision.id).where(CallAnalysisRevision.call_id == rev.call_id,
                CallAnalysisRevision.review["audio_review_required"].as_boolean().is_(True)).limit(1)):
            raise ValueError("audio_review_required")
        if source_hash(row) != rev.base_hash:
            raise ValueError("revision_source_changed")
        checked = validate_analysis(rev.result["provider"]["analysis"], row.transcript, row.direction, rev.rule_version)
        if checked["errors"]:
            raise ValueError("revision_no_longer_valid")
        if stable_hash(checked) != stable_hash(rev.result['verdict']):
            raise ValueError('revision_verdict_changed_before_review')
        rev.result = {**rev.result, "initial_verdict": rev.result["verdict"], "verdict": checked}
        rev.review = {**rev.review, "reviewer": reviewer, "reason": reason, "at": now().isoformat(),
                      "verdict_hash": stable_hash(checked),
                      "manual_history_hash": manual_history_hash(row)}
        rev.state = "verified"
        db.commit()
        return {"id": rev.id, "state": rev.state}


def publish_revision(revision_id: int) -> dict:
    with SessionLocal() as db:
        rev = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.id == revision_id).with_for_update())
        if not rev or rev.state != "verified":
            raise ValueError("verified_revision_required")
        row = db.scalar(select(CallRecord).where(CallRecord.id == rev.call_id).with_for_update())
        if db.scalar(select(CallAnalysisRevision.id).where(CallAnalysisRevision.call_id == rev.call_id,
                CallAnalysisRevision.review["audio_review_required"].as_boolean().is_(True)).limit(1)):
            raise ValueError("audio_review_required")
        if row.state not in ("complete", "insufficient", "needs_review") or source_hash(row) != rev.base_hash:
            raise ValueError("revision_source_changed")
        if stable_hash(row.transcript) != rev.transcript_hash:
            raise ValueError("revision_transcript_changed")
        if rev.review.get("manual_history_hash") != manual_history_hash(row):
            raise ValueError("revision_manual_history_changed")
        # Pin unsent reports BEFORE changing the published analysis. Sent history is not fabricated.
        for delivery in db.scalars(select(CallDelivery).where(CallDelivery.call_id == row.id,
                                                              CallDelivery.state == "pending")):
            freeze_delivery(db, delivery, row)
        archive_current(db, row, "before_reanalysis")
        verdict = validate_analysis(rev.result["provider"]["analysis"], row.transcript, row.direction, rev.rule_version)
        if verdict["errors"]:
            raise ValueError("revision_no_longer_valid")
        if stable_hash(verdict) != rev.review.get('verdict_hash', stable_hash(rev.result['verdict'])):
            raise ValueError('revision_verdict_changed_since_review')
        for key in ("analysis", "score", "yes_count", "applicable_count", "is_scored"):
            setattr(row, key, verdict[key])
        row.validation_errors = []
        row.rule_version, row.analysis_version = rev.rule_version, rev.analysis_version
        row.category = row.analysis["category"]
        row.category_reason = row.analysis["category_reason"]
        row.state = "insufficient" if row.category == "primary_inbound" and not row.is_scored else "complete"
        row.last_error = ""
        rev.result = {**rev.result, "snapshot": snapshot(row),
                      "published_manual_history_hash": manual_history_hash(row)}
        rev.state, rev.published_at = "published", now()
        # Prevent regraded history from creating any previously absent outbound deliveries.
        history = dict(row.provider_result or {})
        history["reanalysis_delivery_suppressed"] = True
        history["published_revision_id"] = rev.id
        row.provider_result = history
        db.commit()
        return {"id": rev.id, "call_id": row.id, "state": rev.state, "score": row.score}



def publish_review_hold(revision_id: int, reviewer: str, reason: str) -> dict:
    """Publish an explicitly unresolved result without a score; never approve its facts."""
    if not reviewer.strip() or not reason.strip():raise ValueError('reviewer_and_reason_required')
    with SessionLocal() as db:
        rev=db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.id==revision_id).with_for_update())
        if not rev or rev.state!='needs_review' or rev.rule_version!=V3_RULE_VERSION:
            raise ValueError('unresolved_v3_revision_required')
        row=db.scalar(select(CallRecord).where(CallRecord.id==rev.call_id).with_for_update())
        if source_hash(row)!=rev.base_hash or stable_hash(row.transcript)!=rev.transcript_hash:
            raise ValueError('revision_source_changed')
        verdict=validate_analysis(rev.result['provider']['analysis'],row.transcript,row.direction,rev.rule_version)
        if (rev.result.get('validation_engine_hash')!=candidate_validation_metadata(rev.rule_version)['validation_engine_hash']
                or stable_hash(verdict)!=stable_hash(rev.result['verdict'])):
            raise ValueError('revision_requires_local_revalidation')
        if not verdict.get('analysis'):raise ValueError('readable_analysis_required')
        for delivery in db.scalars(select(CallDelivery).where(CallDelivery.call_id==row.id,CallDelivery.state=='pending')):
            freeze_delivery(db,delivery,row)
        archive_current(db,row,'before_review_hold')
        row.analysis=verdict['analysis'];row.validation_errors=verdict['errors']
        row.category=row.analysis.get('category','insufficient');row.category_reason=reason
        row.rule_version=rev.rule_version;row.analysis_version=rev.analysis_version
        row.state='needs_review';row.score=None;row.is_scored=False
        row.yes_count=verdict['yes_count'];row.applicable_count=verdict['applicable_count']
        row.last_error='analysis_requires_review'
        rev.review={**rev.review,'reviewer':reviewer,'reason':reason,'decision':'unresolved_no_score',
                    'at':now().isoformat(),'published_without_approval_of_facts':True}
        rev.result={**rev.result,'snapshot':snapshot(row),'published_manual_history_hash':manual_history_hash(row)}
        rev.state='published';rev.published_at=now()
        row.provider_result={**(row.provider_result or {}),'reanalysis_delivery_suppressed':True,'published_revision_id':rev.id}
        db.commit()
        return {'id':rev.id,'call_id':row.id,'state':'needs_review','score':None}


def rollback_revision(revision_id: int, reviewer: str, reason: str) -> dict:
    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer_and_reason_required")
    with SessionLocal() as db:
        rev = db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.id == revision_id).with_for_update())
        if not rev or rev.state != "published" or "baseline" not in rev.result:
            raise ValueError("published_candidate_required")
        row = db.scalar(select(CallRecord).where(CallRecord.id == rev.call_id).with_for_update())
        if snapshot(row) != rev.result.get("snapshot") or stable_hash(row.transcript) != rev.transcript_hash:
            raise ValueError("published_result_changed")
        if rev.result.get("published_manual_history_hash") != manual_history_hash(row):
            raise ValueError("published_manual_history_changed")
        for k, v in rev.result["baseline"].items():
            setattr(row, k, v)
        history = dict(row.provider_result or {})
        history.pop("published_revision_id", None)
        row.provider_result = history
        rev.state = "rolled_back"
        rev.review = {**rev.review, "rollback": {"reviewer": reviewer, "reason": reason, "at": now().isoformat()}}
        db.commit()
        return {"id": rev.id, "state": rev.state}


def stage_history(run: int) -> dict:
    """Frozen historical manifest; sequential, resumable candidates, no auto-publication."""
    from app.calls_api import published_conditions
    key = f"calls_regrade_{RULE_VERSION}_{run}"
    with SessionLocal() as db:
        saved = db.get(SyncState, key)
        if saved:
            manifest = json.loads(saved.value)
        else:
            ids = list(db.scalars(select(CallRecord.id).where(*published_conditions(),
                CallRecord.rule_version != RULE_VERSION).order_by(CallRecord.id)))
            manifest = {"ids": ids, "total": len(ids), "processed": 0, "counts": {}, "status": "running"}
            state_set(db, key, json.dumps(manifest));db.commit()
    for call_id in manifest["ids"][manifest["processed"]:]:
        with SessionLocal() as db:
            held = db.scalar(select(CallAnalysisRevision.id).where(CallAnalysisRevision.call_id == call_id,
                CallAnalysisRevision.review["audio_review_required"].as_boolean().is_(True)).limit(1))
        if held:
            result = {"call_id": call_id, "state": "audio_review"}
        else:
            result = stage_revision(call_id, run)
            if result["state"] == "needs_review":
                with SessionLocal() as db:
                    revision = db.get(CallAnalysisRevision, result["id"])
                    repairable = not revision.result.get("correction_started_at")
                if repairable:
                    result = repair_revision(result["id"])
        state = result["state"]
        if state in ("error", "ambiguous", "running", "correcting"):
            manifest["status"] = "stopped_for_check"
        else:
            manifest["processed"] += 1
            manifest["counts"][state] = manifest["counts"].get(state, 0) + 1
            manifest["status"] = "running"
        manifest["last_result"] = result
        manifest["updated_at"] = now().isoformat()
        with SessionLocal() as db:
            state_set(db, key, json.dumps(manifest));db.commit()
        print(json.dumps({k: v for k, v in manifest.items() if k != "ids"}), flush=True)
        if manifest["status"] == "stopped_for_check":
            return manifest
    manifest["status"] = "candidates_ready"
    with SessionLocal() as db:
        state_set(db, key, json.dumps(manifest));db.commit()
    return manifest


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reanalyze-calibration", action="store_true")
    parser.add_argument("--repair-calibration", action="store_true")
    parser.add_argument("--calibrate", type=int, default=0)
    parser.add_argument("--backfill-days", type=int, choices=range(1, 32))
    parser.add_argument("--retry-payment-blocked", action="store_true")
    parser.add_argument("--close-insufficient", type=int)
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage-history", action="store_true", help="Stage frozen history sequentially; never publish")
    parser.add_argument("--stage-v2", help="Comma-separated call IDs; no publication or delivery")
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--repair-revision", type=int)
    parser.add_argument("--review-revision", type=int)
    parser.add_argument("--publish-revision", type=int)
    parser.add_argument("--rollback-revision", type=int)
    parser.add_argument("--activate-rules", choices=SUPPORTED_RULE_VERSIONS)
    parser.add_argument("--rules", choices=SUPPORTED_RULE_VERSIONS, default=RULE_VERSION)
    args = parser.parse_args()
    init_db()
    if args.stage_history:
        stage_history(args.run)
        return
    if args.stage_v2:
        for call_id in [int(x) for x in args.stage_v2.split(",")]:
            result = stage_revision(call_id, args.run, rule_version=args.rules)
            print(json.dumps(result), flush=True)
            if result["state"] in ("error", "ambiguous", "running"):
                break
        return
    if args.repair_revision:
        print(json.dumps(repair_revision(args.repair_revision)))
        return
    if args.review_revision:
        print(json.dumps(review_revision(args.review_revision, args.reviewer, args.reason)))
        return
    if args.publish_revision:
        print(json.dumps(publish_revision(args.publish_revision)))
        return
    if args.rollback_revision:
        print(json.dumps(rollback_revision(args.rollback_revision, args.reviewer, args.reason)))
        return
    if args.activate_rules:
        with SessionLocal() as db:
            state_set(db, "calls_active_rule_version", args.activate_rules)
            db.commit()
        print(json.dumps({"active_rules": args.activate_rules}))
        return
    if args.close_insufficient:
        print(json.dumps(close_insufficient(args.close_insufficient, args.reviewer, args.reason, apply=args.apply)))
        return
    if args.reanalyze_calibration or args.repair_calibration:
        print(json.dumps({"requeued_analysis": reanalyze_calibration(repair=args.repair_calibration)}))
        return
    if args.retry_payment_blocked:
        print(json.dumps({"requeued": retry_payment_blocked()}))
        return
    if args.once or args.calibrate or args.backfill_days:
        run_once(calibration=args.calibrate, backfill_days=args.backfill_days or 0)
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
