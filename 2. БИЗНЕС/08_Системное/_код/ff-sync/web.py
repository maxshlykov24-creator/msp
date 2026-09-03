"""Панель фулфилмента: приёмка, учёт, счета. Вместо Google Таблиц."""

import base64
import hashlib
import hmac
import io
import os
import threading
import time
from urllib.parse import quote

from fastapi import Body, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from db import delete_intake_row, init_db, list_clients, list_intake, list_invoices
from net import env_opt

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(APP_DIR, "webui")
DAY = 86400

app = FastAPI(title="ff-panel", docs_url=None, redoc_url=None)
_lock = threading.Lock()

if os.path.isdir(UI_DIR):
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


def secret():
    return (env_opt("FF_SECRET") or env_opt("FF_WEB_TOKEN") or "ff-sync").encode()


def users():
    """FF_USERS=глеб:пароль,максим:пароль"""
    out = {}
    for part in env_opt("FF_USERS").split(","):
        if ":" not in part:
            continue
        login, password = part.split(":", 1)
        login = login.strip()
        if login:
            out[login.lower()] = password.strip()
    return out


def sign(login, until):
    body = "%s|%s" % (login, until)
    mac = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    raw = "%s|%s" % (body, mac)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def unsign(token):
    try:
        raw = base64.urlsafe_b64decode((token or "").encode()).decode()
        login, until, mac = raw.split("|")
    except Exception:
        return None
    if not hmac.compare_digest(mac, hmac.new(secret(), ("%s|%s" % (login, until)).encode(), hashlib.sha256).hexdigest()[:32]):
        return None
    if int(until) < int(time.time()):
        return None
    return login


def who(session):
    login = unsign(session)
    if not login:
        raise HTTPException(status_code=401, detail="нужен вход")
    return login


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    path = os.path.join(UI_DIR, "index.html")
    if not os.path.exists(path):
        return JSONResponse({"ok": False, "msg": "панель не собрана"}, status_code=500)
    return FileResponse(path)


@app.post("/api/login")
def login(resp: Response, data: dict = Body(...)):
    table = users()
    name = str(data.get("login") or "").strip().lower()
    password = str(data.get("password") or "")
    if not table:
        raise HTTPException(status_code=500, detail="не заданы пользователи")
    if name not in table or not hmac.compare_digest(table[name], password):
        raise HTTPException(status_code=401, detail="неверный логин или пароль")
    token = sign(name, int(time.time()) + 30 * DAY)
    resp.set_cookie("ff_session", token, max_age=30 * DAY, httponly=True, samesite="lax")
    return {"ok": True, "login": name}


@app.post("/api/logout")
def logout(resp: Response):
    resp.delete_cookie("ff_session")
    return {"ok": True}


@app.get("/api/me")
def me(ff_session: str = Cookie(default="")):
    return {"login": who(ff_session)}


@app.get("/api/clients")
def clients(ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    out = []
    for row in list_clients():
        if not row["active"]:
            continue
        out.append(
            {
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "tariff_storage": row["tariff_storage"],
                "tariff_intake": row["tariff_intake"],
                "tariff_ship": row["tariff_ship"],
            }
        )
    return {"clients": out}


@app.get("/api/intake")
def intake_list(ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from ms import TRACKING_LABELS

    rows = [
        {
            "id": r["id"],
            "client_id": r["client_id"],
            "client": r["client_name"],
            "barcode": r["barcode"],
            "article": r["article"],
            "name": r["name"],
            "marketplace": r["marketplace"],
            "gtin": r["gtin"],
            "tracking": r["tracking_type"],
            "liters": r["liters"],
            "qty": r["qty"],
            "state": r["state"],
            "note": r["note"],
        }
        for r in list_intake(states=("draft", "warn"))
    ]
    return {"rows": rows, "kinds": TRACKING_LABELS}


@app.post("/api/intake")
def intake_add(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from intake import add

    try:
        rid = add(
            data.get("client_id"),
            data.get("barcode"),
            data.get("qty"),
            data.get("liters"),
            data.get("kind"),
            data.get("gtin") or "",
            login,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": rid}


@app.delete("/api/intake/{row_id}")
def intake_drop(row_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    delete_intake_row(row_id)
    return {"ok": True}


@app.post("/api/intake/run")
def intake_run(ff_session: str = Cookie(default="")):
    who(ff_session)
    if not _lock.acquire(blocking=False):
        return {"ok": False, "msg": "уже идёт обработка"}
    try:
        init_db()
        from intake import run

        try:
            res = run(blocking=False)
        except BlockingIOError:
            return {"ok": False, "msg": "уже идёт обработка"}
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}
    finally:
        _lock.release()


@app.get("/api/lots")
def lots(client_id: int = 0, q: str = "", ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from account import lot_rows, totals

    rows = lot_rows(client_id=client_id or None, query=q)
    return {"rows": rows, "totals": totals(rows)}


@app.post("/api/invoice/preview")
def invoice_preview(data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from billing import preview

    try:
        return {"ok": True, **preview(data.get("lot_ids") or [])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/invoice")
def invoice_create(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from billing import create

    try:
        return {"ok": True, **create(data.get("lot_ids") or [], login)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/invoices")
def invoices(ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    rows = [
        {
            "id": r["id"],
            "client": r["client_name"],
            "number": r["ms_number"],
            "ms_id": r["ms_invoice_id"],
            "storage": r["storage"],
            "intake": r["intake"],
            "ship": r["ship"],
            "total": r["total"],
            "positions": r["lots_count"],
            "created": (r["created_at"] or "")[:16].replace("T", " "),
            "author": r["author"],
        }
        for r in list_invoices()
    ]
    return {"rows": rows}


@app.get("/api/export.xlsx")
def export(client_id: int = 0, q: str = "", ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from export_xlsx import build

    name, data = build(client_id=client_id or None, query=q)
    quoted = quote(name)

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quoted},
    )


@app.post("/api/agents/sync")
def agents_sync(ff_session: str = Cookie(default="")):
    who(ff_session)
    if not _lock.acquire(blocking=False):
        return {"ok": False, "msg": "уже идёт обработка"}
    try:
        init_db()
        from agents_sync import run

        try:
            notes = run(blocking=False)
        except BlockingIOError:
            return {"ok": False, "msg": "уже идёт обработка"}
        return {"ok": True, "notes": [list(n) for n in notes]}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}
    finally:
        _lock.release()
