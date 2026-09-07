from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select, text

from app.api import router as api_router
from app.auth import (
    clear_session_cookie,
    is_authenticated,
    set_session_cookie,
    verify_credentials,
)
from app.database import SessionLocal, init_db
from app.models import SyncState

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("main")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

app = FastAPI(title="DKAcademy Analytics", docs_url=None, redoc_url=None)
app.include_router(api_router)


@app.on_event("startup")
def _startup() -> None:
    try:
        init_db()
    except Exception:  # noqa: BLE001 — БД может подниматься чуть дольше
        log.exception("init_db на старте не удался (повтор при первом запросе)")


@app.get("/health")
def health():
    """Открытый эндпоинт для мониторинга (без авторизации)."""
    db_ok = True
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        last = db.execute(
            select(SyncState).where(SyncState.key == "last_success_at")
        ).scalar_one_or_none()
        db.close()
    except Exception:  # noqa: BLE001
        db_ok = False
        last = None
    age_min = None
    if last and last.value:
        try:
            dt = datetime.fromisoformat(last.value).replace(tzinfo=timezone.utc)
            age_min = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        except ValueError:
            pass
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "data_age_min": age_min}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(WEB_DIR / "login.html")


@app.post("/login")
def login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    if verify_credentials(login, password):
        resp = RedirectResponse(url="/", status_code=302)
        set_session_cookie(resp, login)
        return resp
    return RedirectResponse(url="/login?e=1", status_code=302)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    clear_session_cookie(resp)
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(WEB_DIR / "index.html")


@app.get("/reports/{filename}", response_class=HTMLResponse)
def report(request: Request, filename: str):
    """Выдать клиентский HTML-протокол только авторизованному пользователю."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    path = REPORTS_DIR / filename
    if Path(filename).name != filename or path.suffix.lower() != ".html" or not path.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(path)
