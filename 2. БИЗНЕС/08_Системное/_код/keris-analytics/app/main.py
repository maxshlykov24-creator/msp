from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import (
    clear_session_cookie,
    is_authenticated,
    set_session_cookie,
    verify_credentials,
)
from app.collector import read_state, snapshot_path
from app.config import settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("main")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Keris Club Pulse", docs_url=None, redoc_url=None)
if (WEB_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.on_event("startup")
def _startup() -> None:
    if settings.run_scheduler:
        from app.scheduler import start_background

        start_background()


def _freshness() -> dict[str, object]:
    state = read_state()
    last = state.get("last_success_at")
    age_hours = None
    age_minutes = None
    label = None
    if last:
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            age_hours = round(delta.total_seconds() / 3600, 1)
            age_minutes = round(delta.total_seconds() / 60)
            label = dt.astimezone(ZoneInfo(settings.tz)).strftime("%d.%m.%Y, %H:%M")
        except ValueError:
            pass
    stale = age_hours is None or age_hours > settings.stale_after_hours
    return {
        "stale": stale,
        "age_hours": age_hours,
        "age_minutes": age_minutes,
        "last_success_at": last,
        "last_success_label": label,
        "last_error": state.get("last_error"),
        "schema_ok": bool(state.get("schema_ok")),
        "leads_count": state.get("leads_count"),
    }


@app.get("/health")
def health():
    fresh = _freshness()
    has_data = snapshot_path().exists()
    status = "ok" if (has_data and not fresh["stale"] and fresh["schema_ok"]) else "degraded"
    return {"status": status, "has_data": has_data, **fresh}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="./", status_code=302)
    return FileResponse(WEB_DIR / "login.html")


@app.post("/login")
def login_submit(login: str = Form(...), password: str = Form(...)):
    if verify_credentials(login, password):
        resp = RedirectResponse(url="./", status_code=302)
        set_session_cookie(resp, settings.auth_login)
        return resp
    return RedirectResponse(url="./login?e=1", status_code=302)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="./login", status_code=302)
    clear_session_cookie(resp)
    return resp


@app.get("/api/data")
def api_data(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    path = snapshot_path()
    if not path.exists():
        return JSONResponse({"detail": "срез ещё не собран"}, status_code=503)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.exception("snapshot.json повреждён")
        return JSONResponse({"detail": "срез повреждён"}, status_code=503)
    fresh = _freshness()
    if fresh.get("age_minutes") is not None:
        data["age_minutes"] = fresh["age_minutes"]
    return {"data": data, **fresh}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="./login", status_code=302)
    return FileResponse(WEB_DIR / "index.html")


@app.get("/logo-keris.png")
def logo():
    path = WEB_DIR / "logo-keris.png"
    if not path.exists():
        path = WEB_DIR / "assets" / "logo-keris.png"
    return FileResponse(path)
