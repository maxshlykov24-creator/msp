"""Отгрузки FBS/FBO из кабинетов + коды маркировки."""

from datetime import datetime, timedelta, timezone

import statuses as statuses_mod
from db import (
    connect,
    find_cache,
    get_client_by_id,
    get_order_log,
    init_db,
    list_cabinets,
    prefer_hit,
    prune_old_shipments,
    replace_shipment_marks,
    run_lock,
    upsert_shipment,
)
from net import OZON_BASE, WB_BASE, ozon_headers, req, wb_headers
from orders_pull import moment_of, ozon_list, pull_wb_fbo, pull_wb_fbs, since_days

MSK = timezone(timedelta(hours=3))
MARK_KEYS = {
    "sgtin",
    "cis",
    "uin",
    "kiz",
    "mark",
    "mandatory_mark",
    "datamatrix",
    "data_matrix",
}


def now_iso():
    return datetime.now(MSK).isoformat(timespec="seconds")


def day_of(raw):
    text = moment_of(raw) or ""
    return text[:10]


def stamp_of(raw):
    """Дата и время до минуты: смена делит заказы по времени поступления."""
    return (moment_of(raw) or "")[:16]


_catalog_mem = {}


def catalog_of(client_id, barcode, article):
    """Имя и фото из кэша каталога. У WB в задании названия нет — только артикул."""
    key = (client_id, barcode or "", article or "")
    if key in _catalog_mem:
        return _catalog_mem[key]
    hits = find_cache(client_id, barcode=barcode or None, article=article or None)
    name = ""
    image = ""
    want = (article or "").strip()
    for hit in prefer_hit(hits) if hits else []:
        title = (hit["name"] or "").strip()
        if title and title != want and not name:
            name = title
        elif title and not name:
            name = title
        if not image and "image" in hit.keys() and hit["image"]:
            image = hit["image"]
        if name and name != want and image:
            break
    found = {"name": name, "image": image}
    _catalog_mem[key] = found
    return found


def photo_of(client_id, barcode, article):
    return catalog_of(client_id, barcode, article)["image"]


def name_of(client_id, barcode, article, fallback=""):
    """Наименование карточки, если оно не просто повтор артикула."""
    title = catalog_of(client_id, barcode, article)["name"]
    return title or fallback or article or ""


def looks_mark(raw):
    text = str(raw or "").strip()
    if len(text) < 18:
        return False
    if text.lower().startswith("ozn"):
        return False
    return True


def add_mark(val, out):
    if isinstance(val, str):
        if looks_mark(val):
            out.append(val)
    elif isinstance(val, list):
        for item in val:
            add_mark(item, out)
    elif isinstance(val, dict):
        if "value" in val:
            add_mark(val.get("value"), out)
        else:
            collect_marks(val, out)


def collect_marks(obj, out):
    if isinstance(obj, dict):
        for key, val in obj.items():
            low = str(key).lower()
            if low in MARK_KEYS or low.endswith("sgtin") or low in ("marks", "marking_codes", "mark_codes"):
                add_mark(val, out)
            else:
                collect_marks(val, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_marks(item, out)


def uniq_marks(values):
    seen = set()
    out = []
    for raw in values:
        code = str(raw or "").strip()
        if not looks_mark(code) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def gtin_of(code):
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if digits.startswith("01") and len(digits) >= 16:
        return digits[2:16]
    if len(digits) in (13, 14):
        return digits
    return ""


def save_row(client, cab, kind, ext_id, status, shipped_at, article, barcode, name, qty, marks, extra=None):
    if not ext_id:
        return 0
    log = get_order_log(cab["id"], ext_id)
    sid = upsert_shipment(
        client["id"],
        cab["id"],
        cab["marketplace"],
        kind,
        ext_id,
        status or kind.upper(),
        shipped_at or "",
        article or "",
        barcode or "",
        name or "",
        float(qty or 1),
        (log["ms_order_id"] if log else None),
        len(marks),
        now_iso(),
        extra=extra,
    )
    replace_shipment_marks(
        sid,
        [{"code": code, "gtin": gtin_of(code), "article": article or ""} for code in marks],
    )
    return 1


def wb_statuses(token, order_ids):
    found = {}
    ids = []
    for raw in order_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    for i in range(0, len(ids), 1000):
        r = req("POST", WB_BASE + "/api/v3/orders/status", headers=wb_headers(token), json={"orders": ids[i : i + 1000]})
        if r.status_code != 200:
            continue
        for row in (r.json() or {}).get("orders") or []:
            found[str(row.get("id") or "")] = {
                "supplier": row.get("supplierStatus") or "",
                "wb": row.get("wbStatus") or "",
            }
    return found


def wb_meta(token, order_ids):
    found = {}
    ids = []
    for raw in order_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        r = req("POST", WB_BASE + "/api/marketplace/v3/orders/meta", headers=wb_headers(token), json={"orders": chunk})
        if r.status_code != 200:
            continue
        rows = (r.json() or {}).get("orders") or []
        for row in rows:
            ext = str(row.get("id") or row.get("orderId") or "")
            bag = []
            collect_marks(row, bag)
            if ext:
                found[ext] = uniq_marks(bag)
    return found


def pull_ozon_get(headers, kind, posting_number):
    if kind == "fbo":
        urls = (OZON_BASE + "/v2/posting/fbo/get", OZON_BASE + "/v3/posting/fbo/get")
        body = {"posting_number": posting_number, "with": {"analytics_data": True, "financial_data": True}}
    else:
        urls = (OZON_BASE + "/v3/posting/fbs/get",)
        body = {
            "posting_number": posting_number,
            "with": {"analytics_data": True, "financial_data": True, "product_exemplars": True},
        }
    for url in urls:
        r = req("POST", url, headers=headers, json=body)
        if r.status_code == 200:
            data = r.json()
            return data.get("result") or data
    return None


def ozon_marks(detail):
    bag = []
    collect_marks(detail, bag)
    for prod in (detail or {}).get("products") or []:
        for ex in prod.get("exemplar_info") or prod.get("exemplars") or []:
            collect_marks(ex, bag)
            if looks_mark(ex.get("mandatory_mark")):
                bag.append(ex.get("mandatory_mark"))
    return uniq_marks(bag)


def wb_office(order):
    """Куда везти задание: наш ПВЗ или СЦ, не кластер покупателя из `offices`.

    Выбрать точку через API нельзя: в методах поставки параметра нет, а
    `destinationOfficeId` только читается. `offices` у задания — это регион
    покупателя (Москва_Север), складу он не нужен. Адрес пишем сами: ПВЗ
    Домодедовская 28, если площадка запретила пункт выдачи — СЦ на Кавказском.
    """
    cargo = str(order.get("cargoType") or "")
    flag = statuses_mod.pickup_flag(order.get("isPickupPointShipmentAllowed"))
    return statuses_mod.dropoff(cargo, flag)


def handle_wb_fbs(client, cab, orders):
    n = 0
    ids = [str(o.get("id") or "") for o in orders if o.get("id")]
    statuses = wb_statuses(cab["token"], ids)
    meta = wb_meta(cab["token"], ids)
    for order in orders:
        ext_id = str(order.get("id") or "")
        info = statuses.get(ext_id) or {}
        skus = order.get("skus") or []
        article = order.get("article") or ""
        barcode = skus[0] if skus else ""
        cat = catalog_of(client["id"], barcode, article)
        n += save_row(
            client,
            cab,
            "fbs",
            ext_id,
            statuses_mod.wb_text(info.get("supplier"), info.get("wb")),
            day_of(order.get("createdAt")),
            article,
            barcode,
            cat["name"] or article,
            1,
            meta.get(ext_id) or [],
            extra={
                "status_group": statuses_mod.wb_group(info.get("supplier"), info.get("wb")),
                "accepted_at": stamp_of(order.get("createdAt")),
                # у WB в задании нет крайнего срока отгрузки и трек-номера:
                # идентификатор отправления — сам стикер
                "deadline_at": "",
                "track": "",
                "warehouse": "",
                "image": cat["image"],
                # куда везти: наш ПВЗ или СЦ. Габарит и флаг ПВЗ решают,
                # нужны ли грузоместа. Кластер из `offices` складу не показываем
                "office": wb_office(order),
                "cargo_type": str(order.get("cargoType") or ""),
                "pickup_allowed": statuses_mod.pickup_flag(order.get("isPickupPointShipmentAllowed")),
            },
        )
    return n


def handle_wb_fbo(client, cab, orders):
    n = 0
    for order in orders:
        bag = []
        collect_marks(order, bag)
        article = order.get("supplierArticle") or ""
        barcode = order.get("barcode") or ""
        cat = catalog_of(client["id"], barcode, article)
        n += save_row(
            client,
            cab,
            "fbo",
            str(order.get("srid") or ""),
            "отменён" if order.get("isCancel") else "со склада WB",
            day_of(order.get("date")),
            article,
            barcode,
            cat["name"] or article,
            float(order.get("quantity") or 1),
            uniq_marks(bag),
            extra={
                # заказ по стоку WB: товар уже на складе площадки, статуса доставки
                # этот отчёт не отдаёт. Отменённые сюда не доходят — их отсеивает pull_wb_fbo
                "status_group": statuses_mod.CANCELLED if order.get("isCancel") else statuses_mod.SHIPPED,
                "accepted_at": stamp_of(order.get("date")),
                "warehouse": order.get("warehouseName") or "",
                "image": cat["image"],
            },
        )
    return n


def as_dict(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def handle_ozon(client, cab, kind, postings, headers):
    n = 0
    for post in postings or []:
        post = as_dict(post)
        ext_id = str(post.get("posting_number") or "")
        if not ext_id:
            continue
        # FBO list уже содержит состав. GET по каждому отправлению на больших кабинетах занимает минуты.
        detail = as_dict(pull_ozon_get(headers, kind, ext_id) or post) if kind == "fbs" else post
        products = detail.get("products") or post.get("products") or []
        if not isinstance(products, list):
            products = []
        articles = [p.get("offer_id") or "" for p in products if isinstance(p, dict) and p.get("offer_id")]
        names = [p.get("name") or p.get("offer_id") or "" for p in products if isinstance(p, dict)]
        qty = sum(float(p.get("quantity") or 1) for p in products if isinstance(p, dict)) or 1
        article = articles[0] if articles else ""
        name = names[0] if names else article
        if len(articles) > 1:
            name = "%s · ещё %s" % (name, len(articles) - 1)
        analytics = detail.get("analytics_data") if isinstance(detail.get("analytics_data"), dict) else {}
        shipped = day_of(
            detail.get("shipment_date")
            or post.get("shipment_date")
            or detail.get("delivering_date")
            or post.get("delivering_date")
            or analytics.get("delivery_date")
            or post.get("in_process_at")
        )
        raw_status = detail.get("status") or post.get("status") or kind.upper()
        method = detail.get("delivery_method") or post.get("delivery_method") or {}
        if not isinstance(method, dict):
            method = {}
        n += save_row(
            client,
            cab,
            kind,
            ext_id,
            statuses_mod.ru(raw_status),
            shipped,
            article,
            "",
            name,
            qty,
            ozon_marks(detail),
            extra={
                "status_group": statuses_mod.ozon_group(raw_status),
                "accepted_at": stamp_of(detail.get("in_process_at") or post.get("in_process_at")),
                # крайний срок отгрузки: до него отправление надо собрать и сдать
                "deadline_at": stamp_of(detail.get("shipment_date") or post.get("shipment_date")),
                "track": detail.get("tracking_number") or post.get("tracking_number") or "",
                "warehouse": method.get("warehouse") or "",
                "image": photo_of(client["id"], "", article),
            },
        )
    return n


def fill_wb_names():
    """Старые WB-отправления писали артикул в имя. Подтягиваем title из каталога."""
    conn = connect()
    rows = conn.execute(
        "SELECT id, client_id, article, barcode, name FROM shipments "
        "WHERE marketplace = 'wb' AND (name IS NULL OR name = '' OR name = article)"
    ).fetchall()
    n = 0
    for row in rows:
        title = catalog_of(row["client_id"], row["barcode"] or "", row["article"] or "")["name"]
        if not title or title == (row["name"] or ""):
            continue
        conn.execute("UPDATE shipments SET name = ? WHERE id = ?", (title, row["id"]))
        n += 1
    conn.commit()
    conn.close()
    if n:
        print("наименования WB из каталога: %s" % n)
    return n


def fill_images():
    """Превью из кэша каталога: в задании площадки фото нет."""
    conn = connect()
    rows = conn.execute(
        "SELECT id, client_id, article, barcode FROM shipments "
        "WHERE image IS NULL OR image = ''"
    ).fetchall()
    n = 0
    for row in rows:
        image = catalog_of(row["client_id"], row["barcode"] or "", row["article"] or "")["image"]
        if not image:
            continue
        conn.execute("UPDATE shipments SET image = ? WHERE id = ?", (image, row["id"]))
        n += 1
    conn.commit()
    conn.close()
    if n:
        print("фото из каталога: %s" % n)
    return n


def run(days=14, blocking=True):
    init_db()
    with run_lock(blocking=blocking):
        return _run(days)


def _run(days):
    fill_wb_names()
    fill_images()
    start = since_days(int(days or 14))
    created = 0
    notes = []
    for cab in list_cabinets():
        if not cab["active"] or not cab["token"]:
            continue
        client = get_client_by_id(cab["client_id"])
        if not client:
            continue
        try:
            n = 0
            if cab["marketplace"] == "wb":
                n += handle_wb_fbs(client, cab, pull_wb_fbs(cab["token"], start))
                n += handle_wb_fbo(client, cab, pull_wb_fbo(cab["token"], start))
            elif cab["marketplace"] == "ozon":
                headers = ozon_headers(cab["client_id_ext"], cab["token"])
                n += handle_ozon(
                    client, cab, "fbs", ozon_list(OZON_BASE + "/v3/posting/fbs/list", headers, start), headers
                )
                n += handle_ozon(
                    client, cab, "fbo", ozon_list(OZON_BASE + "/v2/posting/fbo/list", headers, start), headers
                )
            created += n
            notes.append("%s %s: %s отправлений" % (client["name"], cab["marketplace"], n))
        except Exception as exc:
            notes.append("%s %s: %s" % (client["name"], cab["marketplace"], exc))
    gone = prune_old_shipments(int(days or 14))
    if gone:
        print("отправлений старше окна: снял %s" % gone)
        notes.append("снял старше %s дней: %s" % (int(days or 14), gone))
    print("отгрузок обновлено: %s" % created)
    return {"count": created, "notes": notes}


if __name__ == "__main__":
    print(run())
