from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api import _auth_guard
from app.call_report import CATEGORY_NAMES, CRITERIA, STATE_NAMES, manager_name
from app.config import settings
from app.database import SessionLocal
from app.models import CallDelivery, CallRecord, SyncState
from app.nexara import download_audio

router = APIRouter(prefix="/api/calls", dependencies=[Depends(_auth_guard)])
MSK = ZoneInfo("Europe/Moscow")


def filters(start: date | None, end: date | None, manager: str | None, category: str | None,
            calibration: bool):
    if start and end and start > end:
        raise HTTPException(422, "Начало периода позже конца")
    conditions = [CallRecord.is_calibration.is_(calibration)]
    if start:
        conditions.append(CallRecord.occurred_at >= int(datetime.combine(start, time.min, MSK).timestamp()))
    if end:
        conditions.append(CallRecord.occurred_at < int(datetime.combine(end + timedelta(days=1), time.min, MSK).timestamp()))
    if manager == "unknown":
        conditions.append(CallRecord.manager_verified.is_(False))
    elif manager:
        try:
            uid = int(manager)
        except ValueError:
            raise HTTPException(422, "Некорректный менеджер") from None
        conditions += [CallRecord.manager_verified.is_(True), CallRecord.manager_id == uid]
    if category:
        if category not in CATEGORY_NAMES:
            raise HTTPException(422, "Некорректный тип разговора")
        conditions.append(CallRecord.category == category)
    return conditions


def summary_row(row):
    a = row.analysis or {}
    return {"id": row.id, "occurred_at": row.occurred_at, "direction": row.direction,
            "duration_sec": row.duration_sec, "category": row.category,
            "category_name": CATEGORY_NAMES.get(row.category, row.category),
            "category_reason": row.category_reason, "state": row.state,
            "state_name": STATE_NAMES.get(row.state, row.state), "manager_id": row.manager_id,
            "manager_verified": row.manager_verified, "manager_name": manager_name(row),
            "lead_ids": row.lead_ids, "binding_ambiguous": len(row.lead_ids) != 1,
            "is_calibration": row.is_calibration, "score": row.score,
            "yes_count": row.yes_count, "applicable_count": row.applicable_count,
            "is_scored": row.is_scored, "summary": a.get("summary", ""),
            "outcome": a.get("outcome", {}), "last_error": row.last_error}


@router.get("")
def calls(start: date | None = None, end: date | None = None, manager: str | None = None,
          category: str | None = None, calibration: bool = False,
          limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    with SessionLocal() as db:
        conditions = filters(start, end, manager, category, calibration)
        total = db.scalar(select(func.count()).select_from(CallRecord).where(*conditions))
        rows = db.scalars(select(CallRecord).where(*conditions)
                          .order_by(CallRecord.occurred_at.desc(), CallRecord.id.desc()).offset(offset).limit(limit))
        return {"total": total, "items": [summary_row(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/summary")
def summary(start: date | None = None, end: date | None = None, manager: str | None = None,
            category: str | None = None, calibration: bool = False):
    with SessionLocal() as db:
        rows = list(db.scalars(select(CallRecord).where(*filters(start, end, manager, category, calibration))))
        scored = [r for r in rows if r.is_scored]
        completed = [r for r in rows if r.state in ("complete", "needs_review")]
        meeting_count = sum((r.analysis or {}).get("outcome", {}).get("meeting_agreed") is True for r in scored)
        managers = []
        groups = {}
        for row in rows:
            uid = row.manager_id if row.manager_verified else None
            groups.setdefault(uid, []).append(row)
        for uid, group in groups.items():
            eligible = [r for r in group if r.is_scored]
            failures = Counter()
            for r in eligible:
                for c in (r.analysis or {}).get("criteria") or []:
                    if c.get("status") == "no":
                        failures[c["id"]] += 1
            managers.append({"manager_id": uid, "manager_name": manager_name(group[0]),
                             "calls": len(group), "scored": len(eligible),
                             "average_score": round(sum(r.score for r in eligible) / len(eligible), 1) if eligible else None,
                             "top_misses": [{"id": i, "name": CRITERIA[i][0], "count": count}
                                            for i, count in failures.most_common(3)]})
        states = {k: v.value for k, v in ((r.key, r) for r in db.scalars(
            select(SyncState).where(SyncState.key.in_(["calls_last_success", "calls_last_collection", "calls_last_error", "calls_start_at"]))
        ))}
        return {"total": len(rows), "processed": len(completed), "scored": len(scored),
                "average_score": round(sum(r.score for r in scored) / len(scored), 1) if scored else None,
                "meeting_agreed": meeting_count,
                "meeting_rate": round(meeting_count * 100 / len(scored), 1) if scored else None,
                "needs_review": sum(r.state in ("needs_review", "submit_ambiguous") for r in rows),
                "unavailable": sum(r.state in ("recording_unavailable", "missed", "error") for r in rows),
                "pending": sum(r.state in ("waiting_recording", "ready", "submitting", "processing") for r in rows),
                "managers": sorted(managers, key=lambda m: (m["manager_id"] is None, m["manager_name"])),
                "manager_options": [{"id": k, "name": v} for k, v in settings.call_manager_names.items()],
                "categories": CATEGORY_NAMES, "worker": states,
                "processing_enabled": settings.calls_process_enabled and (calibration or not settings.calls_calibration_only),
                "calibration_only": settings.calls_calibration_only,
                "amo_enabled": settings.calls_amo_enabled,
                "telegram_enabled": settings.calls_telegram_enabled,
                "telegram_configured": bool(settings.calls_telegram_bot_token and settings.calls_telegram_chat_id)}


@router.get("/{call_id}")
def detail(call_id: int):
    with SessionLocal() as db:
        row = db.get(CallRecord, call_id)
        if not row:
            raise HTTPException(404, "Звонок не найден")
        deliveries = list(db.scalars(select(CallDelivery).where(CallDelivery.call_id == row.id)))
        result = summary_row(row)
        result.update({"transcript": row.transcript, "analysis": row.analysis,
                       "validation_errors": row.validation_errors, "rule_version": row.rule_version,
                       "analysis_version": row.analysis_version,
                       "nexara_job_id": row.nexara_job_id,
                       "deliveries": [{"channel": d.channel, "state": d.state,
                                       "remote_id": d.remote_id, "last_error": d.last_error} for d in deliveries]})
        return result


@router.get("/{call_id}/recording")
def recording(call_id: int):
    with SessionLocal() as db:
        row = db.get(CallRecord, call_id)
        if not row or not row.recording_url:
            raise HTTPException(404, "Запись недоступна")
        url = row.recording_url
    try:
        audio, mime = download_audio(url)
    except Exception:
        # Подписанные ссылки Mango остаются на сервере и не попадают в ошибки API.
        raise HTTPException(502, "Не удалось получить запись. Попробуй позже.") from None

    def chunks():
        try:
            while chunk := audio.read(65536):
                yield chunk
        finally:
            audio.close()
    return StreamingResponse(chunks(), media_type=mime,
                             headers={"Cache-Control": "no-store", "Content-Disposition": f'inline; filename="call-{call_id}.mp3"'})
