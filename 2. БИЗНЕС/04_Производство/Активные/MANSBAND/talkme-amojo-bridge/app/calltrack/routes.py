from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.calltrack.phones import digits10, display, e164
from app.calltrack.pool import allocate_pool, sync_numbers, record_load
from app.calltrack.telphin import ingest
from app.config import get_settings
from app.database import get_db

router = APIRouter()

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()
_BOT = re.compile(r"bot|crawl|spider|headless|lighthouse|preview", re.I)
_JS = (Path(__file__).parent / "static" / "ct.js").read_text(encoding="utf-8")


class SessionIn(BaseModel):
    visit_id: Optional[int] = None
    url: str = ""
    referrer: str = ""
    ym_uid: Optional[str] = None
    test: bool = False


class PingIn(BaseModel):
    visit_id: int


def _allowed_origins() -> list[str]:
    return [o.strip() for o in (get_settings().ct_allowed_origins or "").split(",") if o.strip()]


def _origin(request: Request) -> Optional[str]:
    origin = (request.headers.get("origin") or "").strip()
    if origin in _allowed_origins():
        return origin
    return None


def _cors(origin: Optional[str]) -> dict[str, str]:
    if not origin:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def _client_ip(request: Request) -> str:
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    fwd = (request.headers.get("x-forwarded-for") or "").strip()
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def allow_ip(ip: str, limit: int = 30) -> bool:
    now = time.time()
    with _hits_lock:
        window = [t for t in _hits.get(ip, []) if now - t < 60]
        if len(window) >= limit:
            _hits[ip] = window
            return False
        window.append(now)
        _hits[ip] = window
        return True


def _one(qs: dict, key: str, limit: int = 255) -> Optional[str]:
    values = qs.get(key) or []
    if not values:
        return None
    text = str(values[0]).strip()
    return text[:limit] if text else None


def tracking_from_url(url: str) -> dict[str, Optional[str]]:
    try:
        qs = parse_qs(urlparse(url or "").query)
    except Exception:
        qs = {}
    return {
        "utm_source": _one(qs, "utm_source"),
        "utm_medium": _one(qs, "utm_medium"),
        "utm_campaign": _one(qs, "utm_campaign"),
        "utm_content": _one(qs, "utm_content"),
        "utm_term": _one(qs, "utm_term"),
        "yclid": _one(qs, "yclid") or _one(qs, "ysclid"),
    }


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    return text[:limit] if text else None


def _payload(mode: str, visit_id: Optional[int], number: Optional[str]) -> dict[str, Optional[object]]:
    return {
        "mode": mode,
        "visit_id": visit_id,
        "number": e164(number) if number else None,
        "display": display(number) if number else None,
    }


def _organic_number() -> Optional[str]:
    return digits10(get_settings().ct_organic_number)


def _load_visit(db: Session, visit_id: Optional[int], now: datetime):
    from app.calltrack.models import Visit

    if not visit_id:
        return None
    visit = db.get(Visit, visit_id)
    if visit is None:
        return None
    created = visit.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created < now - timedelta(hours=24):
        return None
    return visit


def _fill_visit(visit, fresh: dict[str, Optional[str]], url: str, referrer: str, ym_uid: Optional[str], ua: str, now: datetime) -> None:
    paid_now = bool(fresh.get("utm_source") or fresh.get("yclid"))
    if paid_now:
        visit.is_paid = True
    for key, val in fresh.items():
        if val and not getattr(visit, key):
            setattr(visit, key, val)
    if url and not visit.landing_url:
        visit.landing_url = _clip(url, 4000)
    if referrer and not visit.referrer:
        visit.referrer = _clip(referrer, 4000)
    if ym_uid and not visit.ym_uid:
        visit.ym_uid = _clip(ym_uid, 255)
    if ua and not visit.user_agent:
        visit.user_agent = ua[:500]
    visit.last_seen_at = now


def _new_visit(db: Session, fresh: dict[str, Optional[str]], url: str, referrer: str, ym_uid: Optional[str], ua: str, now: datetime):
    from app.calltrack.models import Visit

    visit = Visit(
        utm_source=fresh.get("utm_source"),
        utm_medium=fresh.get("utm_medium"),
        utm_campaign=fresh.get("utm_campaign"),
        utm_content=fresh.get("utm_content"),
        utm_term=fresh.get("utm_term"),
        yclid=fresh.get("yclid"),
        ym_uid=_clip(ym_uid, 255),
        referrer=_clip(referrer, 4000),
        landing_url=_clip(url, 4000),
        user_agent=(ua or "")[:500],
        is_paid=bool(fresh.get("utm_source") or fresh.get("yclid")),
        disputed=False,
        created_at=now,
        last_seen_at=now,
    )
    db.add(visit)
    db.flush()
    return visit


def _issue(db: Session, visit, now: datetime) -> dict:
    if not visit.is_paid:
        return _payload("organic", visit.id, _organic_number())
    number = allocate_pool(db, visit, now)
    if number is None:
        return _payload("fallback", visit.id, _organic_number())
    return _payload("pool", visit.id, number.number)


@router.get("/ct.js")
def ct_js() -> Response:
    settings = get_settings()
    body = (
        _JS.replace("__CT_LIVE__", "true" if settings.ct_live else "false")
        .replace("__CT_API__", (settings.bridge_public_base or "").rstrip("/"))
        .replace("__CT_ORIGINAL__", digits10(settings.ct_original_number) or "9310090251")
    )
    return Response(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "max-age=60"},
    )


@router.options("/ct/session")
@router.options("/ct/ping")
def ct_options(request: Request) -> Response:
    origin = _origin(request)
    if not origin:
        raise HTTPException(status_code=403, detail="Origin")
    return Response(status_code=204, headers=_cors(origin))


def _guard(request: Request) -> tuple[str, dict[str, str]]:
    origin = _origin(request)
    if not origin:
        raise HTTPException(status_code=403, detail="Origin")
    headers = _cors(origin)
    if not allow_ip(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too Many Requests", headers=headers)
    return origin, headers


@router.post("/ct/session")
def ct_session(
    payload: SessionIn,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    from fastapi.responses import JSONResponse

    _, headers = _guard(request)
    settings = get_settings()
    if not settings.ct_live and not payload.test:
        return JSONResponse(_payload("off", None, None), headers=headers)
    ua = request.headers.get("user-agent") or ""
    if _BOT.search(ua):
        return JSONResponse(_payload("organic", None, _organic_number()), headers=headers)
    try:
        sync_numbers(db, settings)
        now = datetime.now(timezone.utc)
        fresh = tracking_from_url(payload.url)
        visit = _load_visit(db, payload.visit_id, now)
        if visit is None:
            visit = _new_visit(db, fresh, payload.url, payload.referrer, payload.ym_uid, ua, now)
        else:
            _fill_visit(visit, fresh, payload.url, payload.referrer, payload.ym_uid, ua, now)
        body = _issue(db, visit, now)
        record_load(db, now)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(body, headers=headers)


@router.post("/ct/ping")
def ct_ping(
    payload: PingIn,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    from fastapi.responses import JSONResponse

    _, headers = _guard(request)
    try:
        sync_numbers(db, get_settings())
        now = datetime.now(timezone.utc)
        visit = _load_visit(db, payload.visit_id, now)
        if visit is None:
            raise HTTPException(status_code=404, detail="visit", headers=headers)
        visit.last_seen_at = now
        body = _issue(db, visit, now)
        record_load(db, now)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return JSONResponse(body, headers=headers)


@router.post("/webhooks/telphin/{secret}")
async def telphin_webhook(
    secret: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    import secrets as _secrets

    expected = (get_settings().telphin_webhook_secret or "").strip()
    if not expected or not secret or not _secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    raw_body = await request.body()
    content_type = (request.headers.get("content-type") or "").lower()
    data: object
    if "application/json" in content_type:
        try:
            import json

            data = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            data = {}
    else:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {"items": data}

    def _run(payload: dict) -> None:
        from app.database import get_session_factory

        session = get_session_factory()()
        try:
            ingest(session, payload)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    background_tasks.add_task(_run, data)
    return Response(status_code=200)


@router.get("/ct/health")
def ct_health(db: Session = Depends(get_db)) -> Response:
    """Проверка БД и живости сверки без клиентских данных."""
    import json
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from app.models import KvState
    try:
        db.execute(text("SELECT 1"))
        row = db.get(KvState, "ct_reconcile_state")
        state = json.loads(row.value) if row and row.value else {}
        stamp = datetime.fromisoformat(state["heartbeat"]) if state.get("heartbeat") else None
        healthy = bool(get_settings().ct_reconcile_enabled and stamp and datetime.now(timezone.utc) - stamp < timedelta(minutes=10))
        return JSONResponse({"status": "ok" if healthy else "degraded", "live": get_settings().ct_live},
                            status_code=200 if healthy else 503, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
