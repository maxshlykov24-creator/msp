import os
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ms_read import MoySklad, ReadError
from workflow import Workflow

ROOT = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None)
flow = Workflow(os.environ.get("LUKDECOR_DB", str(ROOT / "data/demo.sqlite")), MoySklad())
sessions = {}
refresh_lock = threading.Lock()


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Локальное демо. Удалённый доступ и боевой склад требуют отдельного развёртывания.
    if request.url.hostname not in ("127.0.0.1", "localhost"):
        return JSONResponse({"error": "Демо доступно только локально"}, status_code=403)
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Запрос с другого сайта запрещён"}, status_code=403)
        if request.headers.get("x-demo-action") != "1":
            return JSONResponse({"error": "Нет признака локального действия"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ReadError)
async def read_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=409)


def actor(request):
    person = sessions.get(request.cookies.get("demo_session"))
    if not person:
        raise ReadError("Войди как сотрудник демо")
    return person


class Login(BaseModel):
    employee: str = Field(min_length=1, max_length=80)
    station: str = Field(min_length=1, max_length=80)


class Wave(BaseModel):
    ids: list[str] = Field(max_length=20)
    cart: str = Field(min_length=1, max_length=100)


class Action(BaseModel):
    job: str
    action: str
    code: str = Field(default="", max_length=1000)
    target: str = Field(default="", max_length=100)


class StationScan(BaseModel):
    wave: str
    code: str = Field(min_length=1, max_length=1000)


@app.get("/")
def index():
    return FileResponse(ROOT / "web/index.html")


@app.post("/api/login")
def login(body: Login):
    token = secrets.token_urlsafe(32)
    person = {"employee": body.employee.strip(), "station": body.station.strip()}
    if not all(person.values()):
        raise ReadError("Имя и станция обязательны")
    sessions[token] = person
    result = JSONResponse(person)
    result.set_cookie("demo_session", token, httponly=True, samesite="strict")
    return result


@app.post("/api/logout")
def logout(request: Request):
    sessions.pop(request.cookies.get("demo_session"), None)
    result = JSONResponse({"ok": True})
    result.delete_cookie("demo_session", httponly=True, samesite="strict")
    return result


@app.get("/api/state")
def state(request: Request):
    person = actor(request)
    return {**flow.view(), "actor": person}


@app.post("/api/refresh")
def refresh(request: Request):
    person = actor(request)
    with refresh_lock:
        data = flow.refresh(person)
    return {"count": len(data["orders"]), "scanned": data["scanned_orders"]}


@app.post("/api/wave")
def wave(body: Wave, request: Request):
    return flow.wave(actor(request), body.ids, body.cart)


@app.post("/api/action")
def action(body: Action, request: Request):
    person = actor(request)
    try:
        return flow.action(person, body.job, body.action, body.code, body.target)
    except ReadError as exc:
        with flow.db() as db:
            flow.event(db, person, body.job, 'blocked_' + body.action, str(exc))
        raise


@app.post('/api/station-scan')
def station_scan(body: StationScan, request: Request):
    return flow.station_scan(actor(request), body.wave, body.code)


@app.get('/proposal')
def proposal():
    return FileResponse(ROOT.parents[2] / '04_Производство/Активные/ЛУКДЕКОР/presentation/Предложение_ЛУКДЕКОР.html')


@app.get("/api/label/{oid}")
def label(oid: str, request: Request):
    return Response(flow.label(actor(request), oid), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="original-label.pdf"'})
