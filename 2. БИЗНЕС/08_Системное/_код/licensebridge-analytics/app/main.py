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

app = FastAPI(title="LicenseBridge USA Analytics", docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.on_event("startup")
def _startup() -> None:
    if settings.run_scheduler:
        from app.scheduler import start_background

        start_background()


def _freshness() -> dict[str, object]:
    """Возраст среза. Единственный источник правды о свежести — время
    последнего успешного сбора, а не время запроса и не дата внутри данных."""
    state = read_state()
    last = state.get("last_success_at")
    age_hours = None
    label = None
    if last:
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
            label = dt.astimezone(ZoneInfo(settings.tz)).strftime("%d.%m.%Y, %H:%M")
        except ValueError:
            pass
    stale = age_hours is None or age_hours > settings.stale_after_hours
    return {
        "stale": stale,
        "age_hours": age_hours,
        "last_success_at": last,
        "last_success_label": label,
        "last_error": state.get("last_error"),
        "schema_ok": bool(state.get("schema_ok")),
        "leads_count": state.get("leads_count"),
    }


@app.get("/health")
def health():
    """Открыт без авторизации — для мониторинга. degraded, если срез
    просрочен или последняя сверка схемы Kommo не прошла."""
    fresh = _freshness()
    has_data = snapshot_path().exists()
    status = "ok" if (has_data and not fresh["stale"] and fresh["schema_ok"]) else "degraded"
    return {"status": status, "has_data": has_data, **fresh}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(WEB_DIR / "login.html")


@app.post("/login")
def login_submit(login: str = Form(...), password: str = Form(...)):
    if verify_credentials(login, password):
        resp = RedirectResponse(url="/", status_code=302)
        set_session_cookie(resp, settings.auth_login)
        return resp
    return RedirectResponse(url="/login?e=1", status_code=302)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
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
    return {"data": data, **_freshness()}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(WEB_DIR / "index.html")
