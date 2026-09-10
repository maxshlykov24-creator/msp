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

from ms import order_app_url
from db import (
    assembly_counts,
    catalog_card,
    delete_intake_row,
    find_wb_supplies,
    get_cabinet,
    get_invoice,
    get_shipments_by_ids,
    init_db,
    list_assembly,
    list_clients,
    list_intake,
    list_invoice_positions,
    list_invoices,
    list_shipments,
    list_supplies,
    list_supply_shipments,
    list_wb_boxes,
    list_wb_supplies,
    overview_stats,
    set_work_state,
    status_label,
)
from db import QUEUE_STATES
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
            }
        )
    return {"clients": out}


@app.get("/api/overview")
def overview(
    client_id: int = 0,
    date_from: str = "",
    date_to: str = "",
    ff_session: str = Cookie(default=""),
):
    who(ff_session)
    init_db()
    from account import lot_rows, totals

    stock = totals(lot_rows(client_id=client_id or None))
    data = overview_stats(client_id=client_id or None, day_from=date_from, day_to=date_to)
    return {"stock": stock, **data}


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
            "dims": (r["dims"] if "dims" in r.keys() else "") or "",
            "tariff": r["tariff"] if "tariff" in r.keys() else None,
            "qty": r["qty"],
            "state": r["state"],
            "note": r["note"],
            "supply_id": r["supply_id"] if "supply_id" in r.keys() else None,
            "supply": (r["supply_number"] if "supply_number" in r.keys() else "") or "",
        }
        for r in list_intake(states=QUEUE_STATES)
    ]
    return {"rows": rows, "kinds": TRACKING_LABELS}


@app.post("/api/intake/registry")
def intake_registry(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from intake import add_registry

    raw = base64.b64decode(data.get("content") or "")
    if not raw:
        raise HTTPException(status_code=400, detail="пустой файл")
    try:
        res = add_registry(
            data.get("client_id"),
            data.get("filename") or "",
            raw,
            author=login,
            refresh=bool(data.get("refresh", True)),
            tariff=data.get("tariff"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **res}


@app.get("/api/intake/{row_id}/candidates")
def intake_candidates(row_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from intake import candidates

    try:
        return {"rows": candidates(row_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/intake/{row_id}/resolve")
def intake_resolve(row_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from intake import resolve

    try:
        row = resolve(row_id, data.get("barcode"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "row": {
            "id": row["id"],
            "barcode": row["barcode"],
            "article": row["article"],
            "name": row["name"],
            "marketplace": row["marketplace"],
            "gtin": row["gtin"],
            "tracking": row["tracking_type"],
            "state": row["state"],
            "note": row["note"],
        },
    }


@app.get("/api/supplies")
def supplies(client_id: int = 0, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    return {
        "rows": [
            {
                "id": r["id"],
                "client": r["client"],
                "number": r["number"] or "",
                "contract": r["contract"] or "",
                "planned_at": r["planned_at"] or "",
                "contact": r["contact"] or "",
                "carrier": r["carrier"] or "",
                "car_plate": r["car_plate"] or "",
                "places": r["places"] or "",
                "marking": r["marking"] or "",
                "rows_total": r["rows_total"],
                "qty_total": r["qty_total"],
                "open_rows": r["open_rows"],
                "created": (r["created_at"] or "")[:16].replace("T", " "),
                "author": r["author"] or "",
            }
            for r in list_supplies(client_id=client_id or None)
        ]
    }


@app.post("/api/intake")
def intake_add(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from intake import add_many

    text = data.get("text") or data.get("barcode") or ""
    if data.get("barcodes"):
        text = "\n".join(str(x) for x in data.get("barcodes"))
    try:
        res = add_many(
            data.get("client_id"),
            text,
            liters=data.get("liters"),
            kind=data.get("kind") or "",
            author=login,
            refresh=bool(data.get("refresh", True)),
            tariff=data.get("tariff"),
            qty=data.get("qty"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **res}


@app.post("/api/intake/file")
def intake_file(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from intake import add_many, parse_file

    raw = base64.b64decode(data.get("content") or "")
    codes = parse_file(data.get("filename") or "", raw)
    if not codes:
        raise HTTPException(status_code=400, detail="в файле нет штрихкодов")
    try:
        res = add_many(
            data.get("client_id"),
            codes,
            liters=data.get("liters"),
            kind=data.get("kind") or "",
            author=login,
            refresh=True,
            tariff=data.get("tariff"),
            qty=data.get("qty"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **res}


@app.patch("/api/intake/{row_id}")
def intake_patch(row_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from intake import patch

    try:
        row = patch(
            row_id,
            liters=data.get("liters"),
            kind=data.get("kind"),
            gtin=data.get("gtin"),
            tariff=data.get("tariff"),
            qty=data.get("qty"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "row": {
            "id": row["id"],
            "state": row["state"],
            "note": row["note"],
            "gtin": row["gtin"],
            "tracking": row["tracking_type"],
            "liters": row["liters"],
            "dims": (row["dims"] if "dims" in row.keys() else "") or "",
            "tariff": row["tariff"] if "tariff" in row.keys() else None,
            "qty": row["qty"],
        },
    }


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


@app.post("/api/lots/accept")
def lots_accept(ff_session: str = Cookie(default="")):
    """Ручная сверка с МойСклад: какие партии уже приняли на склад."""
    who(ff_session)
    init_db()
    from accept import run

    try:
        res = run(blocking=False)
    except BlockingIOError:
        return {"ok": False, "msg": "уже идёт проверка"}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}
    return {"ok": True, **res}


def marked_flag(raw):
    val = (raw or "").strip().lower()
    if val in ("yes", "1", "marked"):
        return True
    if val in ("no", "0", "unmarked"):
        return False
    return None


@app.get("/api/lots")
def lots(client_id: int = 0, q: str = "", marked: str = "", date_to: str = "", ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from account import as_day, lot_rows, totals

    rows = lot_rows(
        client_id=client_id or None,
        query=q,
        marked=marked_flag(marked),
        date_to=as_day(date_to),
    )
    return {"rows": rows, "totals": totals(rows)}


@app.get("/api/calendar")
def calendar_view(
    client_id: int = 0,
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    ff_session: str = Cookie(default=""),
):
    who(ff_session)
    init_db()
    from account import as_day, calendar

    return calendar(
        client_id=client_id or None,
        query=q,
        date_from=as_day(date_from),
        date_to=as_day(date_to),
    )


@app.get("/api/calendar.xlsx")
def calendar_export(
    client_id: int = 0,
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    ff_session: str = Cookie(default=""),
):
    who(ff_session)
    init_db()
    from account import as_day
    from export_xlsx import build_calendar

    name, raw = build_calendar(
        client_id=client_id or None,
        query=q,
        date_from=as_day(date_from),
        date_to=as_day(date_to),
    )
    return xlsx_file(name, raw)


@app.post("/api/invoice/preview")
def invoice_preview(data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from billing import preview

    try:
        return {"ok": True, **preview(data.get("lot_ids") or [], data.get("date_to") or "")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/invoice")
def invoice_create(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    from billing import create

    try:
        return {"ok": True, **create(data.get("lot_ids") or [], login, data.get("date_to") or "")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def ru_day(iso):
    text = str(iso or "")[:10]
    if len(text) != 10:
        return ""
    return "%s.%s.%s" % (text[8:10], text[5:7], text[:4])


def period_label(row):
    keys = row.keys()
    a = ru_day(row["period_from"]) if "period_from" in keys else ""
    b = ru_day(row["period_to"]) if "period_to" in keys else ""
    if a and b:
        return "%s — %s" % (a, b)
    return a or b or "—"


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
            "total": r["total"],
            "positions": r["lots_count"],
            "period": period_label(r),
            "created": (r["created_at"] or "")[:16].replace("T", " "),
            "author": r["author"],
        }
        for r in list_invoices()
    ]
    return {"rows": rows}


@app.get("/api/invoices/{invoice_id}")
def invoice_detail(invoice_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    inv = get_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="счёт не найден")
    positions = []
    liter_days = 0.0
    for r in list_invoice_positions(invoice_id):
        storage = float(r["storage"] or 0)
        liter_days += float(r["liter_days"] or 0)
        positions.append(
            {
                "article": r["article"] or "",
                "barcode": r["barcode"] or "",
                "gtin": r["gtin"] or "",
                "name": r["name"] or "",
                "qty_in": r["qty_in"],
                "liters": r["liters"],
                "tariff": r["tariff"],
                "received": ru_day(r["received_at"]),
                "period": period_label(r),
                "days": r["days"],
                "liter_days": round(float(r["liter_days"] or 0), 2),
                "storage": storage,
                "total": storage,
            }
        )
    return {
        "invoice": {
            "id": inv["id"],
            "client": inv["client_name"],
            "number": inv["ms_number"],
            "ms_id": inv["ms_invoice_id"],
            "storage": inv["storage"],
            "total": inv["total"],
            "positions": inv["lots_count"],
            "period": period_label(inv),
            "liter_days": round(liter_days, 2),
            "created": (inv["created_at"] or "")[:16].replace("T", " "),
            "author": inv["author"],
        },
        "positions": positions,
    }


@app.get("/api/export.xlsx")
def export(client_id: int = 0, q: str = "", marked: str = "", ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from export_xlsx import build

    name, data = build(client_id=client_id or None, query=q, marked=marked_flag(marked))
    return xlsx_file(name, data)


@app.post("/api/assembly/labels")
def assembly_labels(data: dict = Body(...), ff_session: str = Cookie(default="")):
    """Один PDF с этикетками на выбранные отправления."""
    who(ff_session)
    init_db()
    import labels as labels_mod

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    mode = str(data.get("mode") or labels_mod.MODE_POSTING)
    if mode not in labels_mod.MODES:
        raise HTTPException(status_code=400, detail="неизвестный режим печати %s" % mode)
    ships = get_shipments_by_ids(ids)
    if not ships:
        raise HTTPException(status_code=404, detail="отправления не найдены")
    cabs = {}
    cards = {}
    rows = []
    for s in ships:
        cab_id = s["cabinet_id"]
        if cab_id not in cabs:
            cabs[cab_id] = get_cabinet(cab_id)
        article = s["article"] or ""
        barcode = s["barcode"] or ""
        # бренд, цвет и размер живут только в каталоге кабинета, а у Ozon оттуда
        # же приходит и штрихкод: выгрузка заказов его не отдаёт вовсе
        key = (s["client_id"], article, barcode)
        if key not in cards:
            cards[key] = catalog_card(s["client_id"], barcode=barcode, article=article)
        card = cards[key]
        rows.append(
            {
                "cabinet_id": cab_id,
                "cabinet": cabs[cab_id],
                "marketplace": s["marketplace"],
                "ext_id": s["ext_id"],
                "article": article,
                "barcode": barcode or card.get("barcode") or "",
                "name": s["name"] or card.get("name") or "",
                "client": s["client_name"] or "",
                "brand": card.get("brand") or "",
                "color": card.get("color") or "",
                "size": card.get("size") or "",
            }
        )
    try:
        pdf, notes, pages = labels_mod.build(rows, mode=mode)
    except labels_mod.LabelError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    name = "Этикетки_%s_%s.pdf" % (len(rows), time.strftime("%Y-%m-%d_%H-%M"))
    headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
        # заметки отдаём заголовком: тело занято файлом, а оператору важно узнать,
        # если часть этикеток площадка не отдала
        "X-Label-Pages": str(pages),
        "X-Label-Notes": quote("; ".join(notes)),
    }
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf", headers=headers)


def xlsx_file(name, data):
    quoted = quote(name)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quoted},
    )


@app.get("/api/shipments")
def shipments_list(
    client_id: int = 0,
    q: str = "",
    mp: str = "",
    kind: str = "",
    marked: str = "",
    date_from: str = "",
    date_to: str = "",
    ff_session: str = Cookie(default=""),
):
    who(ff_session)
    init_db()
    rows = list_shipments(
        client_id=client_id or None,
        marketplace=mp,
        kind=kind,
        marked=marked_flag(marked),
        day_from=date_from,
        day_to=date_to,
        query=q,
    )
    out = []
    qty = 0
    marks = 0
    for r in rows:
        qty += float(r["qty"] or 0)
        marks += int(r["marks_count"] or 0)
        ms_id = r["ms_order_id"]
        out.append(
            {
                "id": r["id"],
                "client": r["client_name"],
                "marketplace": r["marketplace"],
                "kind": r["kind"],
                "ext_id": r["ext_id"],
                "status": status_label(r["status"]),
                "shipped": (r["shipped_at"] or "")[:10],
                "article": r["article"],
                "barcode": r["barcode"],
                "name": r["name"],
                "qty": r["qty"],
                "marks": r["marks_count"],
                "ms_order_id": ms_id or "",
                "ms_url": order_app_url(ms_id),
            }
        )
    return {
        "rows": out,
        "totals": {
            "positions": len(out),
            "qty": round(qty, 3),
            "marks": marks,
            "without": sum(1 for r in out if not r["marks"]),
        },
    }


@app.get("/api/assembly")
def assembly(
    client_id: int = 0,
    group: str = "",
    mp: str = "",
    kind: str = "",
    article: str = "",
    q: str = "",
    since: str = "",
    until: str = "",
    ff_session: str = Cookie(default=""),
):
    who(ff_session)
    init_db()
    import statuses

    filters = dict(
        client_id=client_id or None, marketplace=mp, kind=kind, article=article, query=q, since=since, until=until
    )
    counts, total = assembly_counts(**filters)
    fallback = False
    if total == 0:
        filters["since"] = ""
        filters["until"] = ""
        counts, total = assembly_counts(keep_floor=False, **filters)
        fallback = True
        counts = {key: min(100, n) for key, n in counts.items()}
    rows = list_assembly(group=group, keep_floor=not fallback, limit=100, **filters)
    out = []
    qty = 0.0
    for r in rows:
        qty += float(r["qty"] or 0)
        out.append(
            {
                "id": r["id"],
                "client": r["client_name"],
                "marketplace": r["marketplace"],
                "kind": r["kind"],
                "ext_id": r["ext_id"],
                "status": r["status"] or "",
                "group": r["eff_group"] or "",
                "work": r["work_state"] or "",
                "accepted": (r["accepted_at"] or "").replace("T", " "),
                "deadline": (r["deadline_at"] or "").replace("T", " "),
                "article": r["article"] or "",
                "name": r["name"] or "",
                "qty": r["qty"],
                "track": r["track"] or "",
                "warehouse": r["warehouse"] or "",
                "image": r["image"] or "",
                "marks": r["marks_count"],
                "supply": (r["supply_ext"] if "supply_ext" in r.keys() else "") or "",
                "box": (r["trbx_ext"] if "trbx_ext" in r.keys() else "") or "",
                "office": statuses.dropoff(
                    r["cargo_type"] if "cargo_type" in r.keys() else "",
                    r["pickup_allowed"] if "pickup_allowed" in r.keys() else "",
                ) or ((r["office"] if "office" in r.keys() else "") or ""),
                "cargo": statuses.cargo_label(
                    r["cargo_type"] if "cargo_type" in r.keys() else "",
                    r["pickup_allowed"] if "pickup_allowed" in r.keys() else "",
                ),
                "ms_order_id": r["ms_order_id"] or "",
                "ms_url": order_app_url(r["ms_order_id"]),
            }
        )
    # Поставки WB отдаём отдельным списком: во вкладках после «Новых» таблица
    # показывает строку поставки, а не пачку заданий. Клик открывает окно коробов.
    supplies = []
    for s in find_wb_supplies({r["supply"] for r in out if r["supply"]}):
        members = list_supply_shipments(s["cabinet_id"], s["ext_id"])
        supplies.append(
            {
                "id": s["id"],
                "ext_id": s["ext_id"],
                "client": s["client_name"],
                "name": s["name"] or "",
                "state": s["state"],
                "orders": s["orders"],
                "boxes": s["boxes"],
                "loose": s["loose"],
                "ship_ids": [r["id"] for r in members],
                "cargo": statuses.cargo_label(s["cargo_type"], s["pickup_allowed"] if "pickup_allowed" in s.keys() else ""),
                "pickup": statuses.to_pickup(s["cargo_type"], s["pickup_allowed"] if "pickup_allowed" in s.keys() else ""),
                "office": statuses.dropoff(s["cargo_type"], s["pickup_allowed"] if "pickup_allowed" in s.keys() else ""),
                "created": (s["created_at"] or "")[:16].replace("T", " "),
            }
        )
    return {
        "rows": out,
        "supplies": supplies,
        "groups": [
            {"code": code, "label": label, "count": counts.get(code, 0)}
            for code, label in statuses.GROUPS
        ],
        "totals": {"positions": len(out), "qty": round(qty, 3), "all": total},
        "fallback": fallback,
    }


@app.get("/api/assembly/picking.pdf")
@app.get("/api/assembly/picking.xlsx")
def assembly_picking(
    client_id: int = 0,
    group: str = "",
    mp: str = "",
    kind: str = "",
    article: str = "",
    q: str = "",
    since: str = "",
    until: str = "",
    ff_session: str = Cookie(default=""),
):
    """Лист подбора по текущей выборке «Заказов»: PDF A4 сразу на печать.

    Берём не отмеченные галочками строки, а весь фильтр: сборщик утром отбирает
    смену по контрагенту и вкладке, а не тыкает тридцать чекбоксов. Старый
    адрес .xlsx оставлен: отдаёт тот же PDF, чтобы закладки не сломались.
    """
    who(ff_session)
    init_db()
    from picking_pdf import build_picking_pdf

    rows = list_assembly(
        client_id=client_id or None, group=group, marketplace=mp, kind=kind,
        article=article, query=q, since=since, until=until,
    )
    names = {r["client_name"] for r in rows}
    name, raw, pages = build_picking_pdf(rows, who=names.pop() if len(names) == 1 else "")
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename*=UTF-8''%s" % quote(name),
            "X-Label-Pages": str(pages),
        },
    )


@app.post("/api/assembly/work")
def assembly_work(data: dict = Body(...), ff_session: str = Cookie(default="")):
    """Складская отметка: на сборке, собрано, отгружено. Площадку не трогает."""
    who(ff_session)
    init_db()
    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    state = str(data.get("state") or "")
    import statuses

    if state and state not in statuses.GROUP_CODES:
        raise HTTPException(status_code=400, detail="неизвестное состояние %s" % state)
    return {"ok": True, "changed": set_work_state(ids, state)}


@app.post("/api/assembly/take")
def assembly_take(data: dict = Body(...), ff_session: str = Cookie(default="")):
    """«Взять в сборку»: WB уходит в поставку, Ozon получает складскую отметку.

    Поставка создаётся сама — своего «взять» у WB нет, задание попадает в сборку
    вместе с добавлением в поставку.
    """
    login = who(ff_session)
    init_db()
    import supply_flow

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    try:
        return {"ok": True, **supply_flow.take(ids, author=login)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assembly/{ship_id}/kiz")
def assembly_kiz_plan(ship_id: int, ff_session: str = Cookie(default="")):
    """Сколько кодов маркировки ждёт площадка по этому отправлению."""
    who(ff_session)
    init_db()
    import kiz

    try:
        return {"ok": True, **kiz.plan(ship_id)}
    except kiz.KizError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assembly/{ship_id}/kiz")
def assembly_kiz_submit(ship_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    """Передать на площадку коды, которые склад просканировал."""
    who(ff_session)
    init_db()
    import kiz

    try:
        return {"ok": True, **kiz.submit(ship_id, data.get("codes") or [])}
    except kiz.KizError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def pdf_file(name, data, notes=(), pages=0):
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
            "X-Label-Pages": str(pages),
            "X-Label-Notes": quote("; ".join(notes)),
        },
    )


@app.post("/api/assembly/ship")
def assembly_ship(data: dict = Body(...), ff_session: str = Cookie(default="")):
    """«Собрано»: Ozon переводим на площадке, WB отмечаем складом.

    Дробление у Ozon включено: каждое грузовое место становится отдельным
    отправлением, а склад клеит по одной наклейке на единицу товара. До этого
    шага площадка этикетку отправления не отдаёт.
    """
    who(ff_session)
    init_db()
    import supply_flow

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    try:
        return {
            "ok": True,
            **supply_flow.assemble(
                ids, split=bool(data.get("split", True)), boxes=int(data.get("boxes") or 0)
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/wb/supplies")
def wb_supplies(client_id: int = 0, state: str = "", ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    return {
        "rows": [
            {
                "id": r["id"],
                "client_id": r["client_id"],
                "client": r["client_name"],
                "ext_id": r["ext_id"],
                "name": r["name"] or "",
                "state": r["state"],
                "boxes": r["boxes"],
                "orders": r["orders"],
                "loose": r["loose"],
                "created": (r["created_at"] or "")[:16].replace("T", " "),
                "delivered": (r["delivered_at"] or "")[:16].replace("T", " "),
                "author": r["author"] or "",
            }
            for r in list_wb_supplies(client_id=client_id or None, state=state)
        ]
    }


@app.post("/api/wb/supplies")
def wb_supply_create(data: dict = Body(...), ff_session: str = Cookie(default="")):
    login = who(ff_session)
    init_db()
    import supply_flow

    if not data.get("client_id"):
        raise HTTPException(status_code=400, detail="выбери контрагента")
    try:
        return {"ok": True, **supply_flow.create_supply(data["client_id"], data.get("name") or "", login)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/wb/supplies/{supply_id}")
def wb_supply_detail(supply_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import statuses
    from db import get_wb_supply

    supply = get_wb_supply(supply_id)
    if not supply:
        raise HTTPException(status_code=404, detail="поставка не найдена")
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    return {
        "supply": {
            "id": supply["id"],
            "client_id": supply["client_id"],
            "client": supply["client_name"],
            "ext_id": supply["ext_id"],
            "name": supply["name"] or "",
            "state": supply["state"],
            "created": (supply["created_at"] or "")[:16].replace("T", " "),
            "delivered": (supply["delivered_at"] or "")[:16].replace("T", " "),
            "office": statuses.dropoff(
                supply["cargo_type"] if "cargo_type" in supply.keys() else "",
                supply["pickup_allowed"] if "pickup_allowed" in supply.keys() else "",
            ),
            "pickup": statuses.to_pickup(
                supply["cargo_type"] if "cargo_type" in supply.keys() else "",
                supply["pickup_allowed"] if "pickup_allowed" in supply.keys() else "",
            ),
            "cargo": statuses.cargo_label(
                supply["cargo_type"] if "cargo_type" in supply.keys() else "",
                supply["pickup_allowed"] if "pickup_allowed" in supply.keys() else "",
            ),
        },
        "boxes": [
            {"id": b["id"], "ext_id": b["ext_id"], "orders": b["orders"]}
            for b in list_wb_boxes(supply["id"])
        ],
        "rows": [
            {
                "id": r["id"],
                "ext_id": r["ext_id"],
                "status": r["status"] or "",
                "article": r["article"] or "",
                "name": r["name"] or "",
                "qty": r["qty"],
                "image": r["image"] or "",
                "box": (r["trbx_ext"] or ""),
            }
            for r in rows
        ],
    }


@app.post("/api/wb/supplies/{supply_id}/orders")
def wb_supply_orders(supply_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    try:
        return {"ok": True, **supply_flow.add_orders(supply_id, data.get("ids") or [])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/wb/supplies/{supply_id}/boxes")
def wb_supply_boxes(supply_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    try:
        return {"ok": True, **supply_flow.make_boxes(supply_id, data.get("amount") or 1)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/wb/boxes/{box_id}/orders")
def wb_box_fill(box_id: int, data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    ids = data.get("ids") or []
    try:
        if data.get("take"):
            return {"ok": True, **supply_flow.empty_box(box_id, ids)}
        return {"ok": True, **supply_flow.fill_box(box_id, ids)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.delete("/api/wb/boxes/{box_id}")
def wb_box_drop(box_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    try:
        return supply_flow.drop_box(box_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/wb/supplies/{supply_id}/preflight")
def wb_supply_preflight(supply_id: int, ff_session: str = Cookie(default="")):
    """Что покажем в окне «точно передаём?» перед необратимым шагом."""
    who(ff_session)
    init_db()
    import supply_flow

    try:
        return supply_flow.preflight(supply_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/wb/supplies/{supply_id}/deliver")
def wb_supply_deliver(supply_id: int, data: dict = Body(None), ff_session: str = Cookie(default="")):
    """Передать поставку в доставку. Необратимо, поэтому только с confirm."""
    who(ff_session)
    init_db()
    import supply_flow

    body = data or {}
    try:
        return {
            "ok": True,
            **supply_flow.deliver(supply_id, confirm=bool(body.get("confirm")), force=bool(body.get("force"))),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/wb/supplies/{supply_id}/labels")
def wb_supply_labels(supply_id: int, data: dict = Body(None), ff_session: str = Cookie(default="")):
    """Печать из окна поставки: те же этикетки, плюс QR грузоместа."""
    who(ff_session)
    init_db()
    import labels as labels_mod
    import supply_flow

    body = data or {}
    mode = str(body.get("mode") or labels_mod.MODE_POSTING)
    try:
        pdf, notes, pages = supply_flow.print_labels(supply_id, body.get("ids") or [], mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except labels_mod.LabelError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return pdf_file("Этикетки_поставка_%s.pdf" % time.strftime("%Y-%m-%d_%H-%M"), pdf, notes, pages)


@app.post("/api/wb/supplies/{supply_id}/boxes.pdf")
def wb_supply_boxes_pdf(supply_id: int, data: dict = Body(None), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    try:
        pdf, notes, pages = supply_flow.boxes_pdf(supply_id, (data or {}).get("box_ids") or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return pdf_file("QR_грузомест_%s.pdf" % time.strftime("%Y-%m-%d_%H-%M"), pdf, notes, pages)


@app.post("/api/wb/supplies/{supply_id}/qr.pdf")
def wb_supply_qr_pdf(supply_id: int, ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    import supply_flow

    try:
        pdf, notes, pages = supply_flow.supply_pdf(supply_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return pdf_file("QR_поставки_%s.pdf" % time.strftime("%Y-%m-%d_%H-%M"), pdf, notes, pages)


@app.post("/api/shipments/sync")
def shipments_sync(data: dict = Body(None), ff_session: str = Cookie(default="")):
    who(ff_session)
    if not _lock.acquire(blocking=False):
        return {"ok": False, "msg": "уже идёт обработка"}
    try:
        from shipments_pull import run

        days = min(14, max(1, int((data or {}).get("days") or 14)))
        res = run(days=days, blocking=False)
        return {"ok": True, **res}
    except BlockingIOError:
        return {"ok": False, "msg": "уже идёт обработка"}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}
    finally:
        _lock.release()


@app.post("/api/shipments/marks.xlsx")
def shipments_marks(data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from export_xlsx import build_marks

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    name, raw = build_marks(ids)
    return xlsx_file(name, raw)


@app.post("/api/shipments/report.xlsx")
def shipments_report(data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from export_xlsx import build_ships

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны отправления")
    name, raw = build_ships(ids)
    return xlsx_file(name, raw)


@app.get("/api/shipments/weekly.xlsx")
def shipments_weekly(
    client_id: int = 0,
    date_from: str = "",
    date_to: str = "",
    ff_session: str = Cookie(default=""),
):
    """Недельный отчёт клиенту: артикулы по строкам, дни по столбцам.

    Контрагент обязателен: отчёт уходит клиенту, и мешать в нём чужие артикулы
    нельзя.
    """
    who(ff_session)
    init_db()
    from db import shipped_by_day
    from export_xlsx import build_weekly

    if not client_id:
        raise HTTPException(status_code=400, detail="выбери контрагента: отчёт собирается по одному")
    if not date_from or not date_to:
        raise HTTPException(status_code=400, detail="нужен период: с какого по какое число")
    client = next((c for c in list_clients() if c["id"] == client_id), None)
    rows = shipped_by_day(client_id, date_from[:10], date_to[:10])
    try:
        name, raw = build_weekly(client["name"] if client else "", date_from[:10], date_to[:10], rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return xlsx_file(name, raw)


@app.post("/api/lots/report.xlsx")
def lots_report(data: dict = Body(...), ff_session: str = Cookie(default="")):
    who(ff_session)
    init_db()
    from export_xlsx import build_client_stock

    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="не выбраны позиции")
    name, raw = build_client_stock(ids)
    return xlsx_file(name, raw)


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
