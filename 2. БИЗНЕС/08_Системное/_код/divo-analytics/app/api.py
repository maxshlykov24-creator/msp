from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth import is_authenticated
from app.config import settings
from app.database import SessionLocal
from app.models import Snapshot, SyncState

router = APIRouter(prefix="/api")


def _auth_guard(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/daily")
def daily(_: None = Depends(_auth_guard)):
    """Плоский объект DAILY[date] = {...} — контракт, который ждёт фронт
    (замена зашитого const DAILY в прежнем статическом прототипе)."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(Snapshot).where(Snapshot.section == "daily")
        ).scalar_one_or_none()
        if not row:
            return {}
        return row.payload.get("days", {})
    finally:
        db.close()


@router.get("/meta")
def meta(_: None = Depends(_auth_guard)):
    """Служебные метаданные: свежесть данных, надёжность исторической воронки,
    имена менеджеров — чтобы фронт мог честно показать оговорки, а не молчать."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(Snapshot).where(Snapshot.section == "daily")
        ).scalar_one_or_none()
        schema_ok = db.execute(
            select(SyncState).where(SyncState.key == "schema_ok")
        ).scalar_one_or_none()
        last_error = db.execute(
            select(SyncState).where(SyncState.key == "last_error")
        ).scalar_one_or_none()
        payload = row.payload if row else {}
        days = payload.get("days", {})
        day_keys = sorted(days.keys())
        return {
            "generated_at": payload.get("generated_at"),
            "stage_reliable_from": payload.get("stage_reliable_from"),
            "min_date": day_keys[0] if day_keys else None,
            "max_date": day_keys[-1] if day_keys else None,
            "schema_ok": bool(schema_ok and schema_ok.value == "true"),
            "last_error": (last_error.value or None) if last_error else None,
            "managers": settings.manager_key_map_dict,
        }
    finally:
        db.close()
