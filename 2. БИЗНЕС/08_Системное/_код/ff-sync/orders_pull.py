"""Заказы FBS/FBO из кабинетов → заказ покупателя в МойСклад."""

from datetime import datetime, timedelta, timezone

from account import consume_fifo
from db import (
    find_cache_group,
    get_client_by_id,
    get_order_log,
    get_sku,
    get_sku_by_barcode,
    init_db,
    list_cabinets,
    run_lock,
    upsert_order_log,
)
from ms import ensure_projects, ms_meta, order_attrs, org_id, project_id, store_id
from net import MS_BASE, OZON_BASE, WB_BASE, WB_STATS, ms_headers, ozon_headers, req, wb_headers

MSK = timezone(timedelta(hours=3))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_product(client_id, cabinet_id, ext_key=None, barcode=None, article=None):
    if ext_key:
        sku = get_sku(cabinet_id, ext_key)
        if sku and sku["ms_product_id"]:
            return sku["ms_product_id"]
    if barcode:
        sku = get_sku_by_barcode(client_id, barcode)
        if sku and sku["ms_product_id"]:
            return sku["ms_product_id"]
        for hit in find_cache_group(client_id, barcode=barcode):
            sku = get_sku(hit["cabinet_id"], hit["ext_key"])
            if sku and sku["ms_product_id"]:
                return sku["ms_product_id"]
    if article:
        for hit in find_cache_group(client_id, article=article):
            sku = get_sku(hit["cabinet_id"], hit["ext_key"])
            if sku and sku["ms_product_id"]:
                return sku["ms_product_id"]
    return None


def already_ok(cabinet_id, ext_id):
    row = get_order_log(cabinet_id, ext_id)
    return bool(row and row["result"] == "ok" and row["ms_order_id"])


def find_ms_order(ext_code):
    r = req(
        "GET",
        MS_BASE + "/entity/customerorder",
        headers=ms_headers(),
        params={"filter": "externalCode=%s" % ext_code, "limit": 1},
    )
    if r.status_code != 200:
        return None
    rows = r.json().get("rows") or []
    return rows[0]["id"] if rows else None


def create_order(client, cab, kind, ext_id, positions, posting, ship_date, status, moment):
    ext_code = "%s-%s" % (cab["id"], ext_id)
    existing = find_ms_order(ext_code)
    if existing:
        upsert_order_log(cab["id"], ext_id, existing, "ok", "", now_iso())
        return existing, "уже есть"
    pid = project_id(cab["marketplace"], kind)
    payload = {
        "organization": ms_meta("organization", client["ms_org_id"] or org_id()),
        "agent": ms_meta("counterparty", client["ms_counterparty_id"]),
        "store": ms_meta("store", client["ms_store_id"] or store_id()),
        "externalCode": ext_code,
        "vatEnabled": False,
        "positions": [
            {
                "quantity": item["qty"],
                "price": 0,
                "assortment": ms_meta("product", item["product_id"]),
            }
            for item in positions
        ],
    }
    if pid:
        payload["project"] = ms_meta("project", pid)
    if moment:
        payload["moment"] = moment
    attrs = order_attrs(posting, ship_date, cab["name"] or cab["marketplace"], status)
    if attrs:
        payload["attributes"] = attrs
    r = req("POST", MS_BASE + "/entity/customerorder", headers=ms_headers(), json=payload)
    if r.status_code not in (200, 201):
        return None, "%s %s" % (r.status_code, (r.text or "")[:180])
    oid = r.json().get("id")
    for item in positions:
        left = consume_fifo(client["id"], item["product_id"], item["qty"], oid)
        if left > 0:
            print("заказ %s: не хватило остатка %s шт товара %s" % (ext_id, left, item["product_id"]))
    upsert_order_log(cab["id"], ext_id, oid, "ok", "", now_iso())
    return oid, "создан"


def since_days(days=3):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return start


def pull_wb_fbs(token, date_from):
    out = []
    nxt = 0
    ts = int(date_from.timestamp())
    while True:
        r = req(
            "GET",
            WB_BASE + "/api/v3/orders",
            headers=wb_headers(token),
            params={"limit": 1000, "next": nxt, "dateFrom": ts},
        )
        if r.status_code != 200:
            print("WB FBS %s %s" % (r.status_code, (r.text or "")[:180]))
            break
        data = r.json()
        batch = data.get("orders") or []
        out.extend(batch)
        nxt = data.get("next")
        if not batch or not nxt:
            break
    return out


def pull_wb_fbo(token, date_from):
    r = req(
        "GET",
        WB_STATS + "/api/v1/supplier/orders",
        headers=wb_headers(token),
        params={"dateFrom": date_from.strftime("%Y-%m-%d"), "flag": 0},
    )
    if r.status_code != 200:
        print("WB FBO %s %s" % (r.status_code, (r.text or "")[:180]))
        return []
    rows = r.json() if isinstance(r.json(), list) else []
    fbo = []
    for row in rows:
        wtype = str(row.get("warehouseType") or "")
        if wtype and wtype != "Склад WB":
            continue
        if not wtype:
            continue
        if row.get("isCancel"):
            continue
        fbo.append(row)
    return fbo


def ozon_list(url, headers, date_from):
    out = []
    offset = 0
    since = date_from.strftime("%Y-%m-%dT%H:%M:%SZ")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    while True:
        r = req(
            "POST",
            url,
            headers=headers,
            json={
                "dir": "ASC",
                "filter": {"since": since, "to": until},
                "limit": 100,
                "offset": offset,
            },
        )
        if r.status_code != 200:
            print("Ozon %s %s %s" % (url, r.status_code, (r.text or "")[:180]))
            break
        data = r.json()
        block = data.get("result") or data
        if isinstance(block, list):
            batch = block
        elif isinstance(block, dict):
            batch = block.get("postings") or []
        else:
            batch = []
        out.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return out


def moment_of(raw):
    if not raw:
        return None
    text = str(raw).replace("Z", "")
    if "T" in text:
        text = text.replace("T", " ")[:19]
    else:
        text = text[:19]
    return text


def handle_wb_fbs(client, cab, orders):
    n = 0
    for order in orders:
        ext_id = str(order.get("id") or order.get("rid") or "")
        if not ext_id or already_ok(cab["id"], ext_id):
            continue
        skus = order.get("skus") or []
        barcode = skus[0] if skus else ""
        pid = resolve_product(
            client["id"],
            cab["id"],
            ext_key=str(order.get("chrtId") or "") or None,
            barcode=barcode,
            article=order.get("article"),
        )
        if not pid:
            upsert_order_log(cab["id"], ext_id, None, "нет товара", "", now_iso())
            continue
        oid, msg = create_order(
            client,
            cab,
            "fbs",
            ext_id,
            [{"product_id": pid, "qty": 1}],
            str(order.get("id") or ext_id),
            moment_of(order.get("createdAt")),
            "FBS",
            moment_of(order.get("createdAt")),
        )
        print("WB FBS %s: %s" % (ext_id, msg))
        if oid:
            n += 1
    return n


def handle_wb_fbo(client, cab, orders):
    n = 0
    for order in orders:
        ext_id = str(order.get("srid") or "")
        if not ext_id or already_ok(cab["id"], ext_id):
            continue
        pid = resolve_product(
            client["id"],
            cab["id"],
            barcode=order.get("barcode"),
            article=order.get("supplierArticle"),
        )
        if not pid:
            upsert_order_log(cab["id"], ext_id, None, "нет товара", "", now_iso())
            continue
        oid, msg = create_order(
            client,
            cab,
            "fbo",
            ext_id,
            [{"product_id": pid, "qty": 1}],
            ext_id,
            moment_of(order.get("date")),
            "FBO",
            moment_of(order.get("date")),
        )
        print("WB FBO %s: %s" % (ext_id, msg))
        if oid:
            n += 1
    return n


def handle_ozon(client, cab, kind, postings):
    n = 0
    for post in postings:
        ext_id = str(post.get("posting_number") or "")
        if not ext_id or already_ok(cab["id"], ext_id):
            continue
        positions = []
        for prod in post.get("products") or []:
            pid = resolve_product(
                client["id"],
                cab["id"],
                ext_key=prod.get("offer_id"),
                article=prod.get("offer_id"),
            )
            if not pid:
                continue
            positions.append({"product_id": pid, "qty": float(prod.get("quantity") or 1)})
        if not positions:
            upsert_order_log(cab["id"], ext_id, None, "нет товара", "", now_iso())
            continue
        oid, msg = create_order(
            client,
            cab,
            kind,
            ext_id,
            positions,
            ext_id,
            moment_of((post.get("analytics_data") or {}).get("delivery_date") or post.get("in_process_at")),
            post.get("status") or kind.upper(),
            moment_of(post.get("in_process_at")),
        )
        print("Ozon %s %s: %s" % (kind, ext_id, msg))
        if oid:
            n += 1
    return n


def run():
    init_db()
    with run_lock():
        return _run()


def _run():
    ensure_projects()
    start = since_days(3)
    created = 0
    for cab in list_cabinets():
        if not cab["active"] or not cab["token"]:
            continue
        client = get_client_by_id(cab["client_id"])
        if not client or not client["ms_counterparty_id"]:
            continue
        try:
            if cab["marketplace"] == "wb":
                created += handle_wb_fbs(client, cab, pull_wb_fbs(cab["token"], start))
                created += handle_wb_fbo(client, cab, pull_wb_fbo(cab["token"], start))
            elif cab["marketplace"] == "ozon":
                headers = ozon_headers(cab["client_id_ext"], cab["token"])
                created += handle_ozon(
                    client, cab, "fbs", ozon_list(OZON_BASE + "/v3/posting/fbs/list", headers, start)
                )
                created += handle_ozon(
                    client, cab, "fbo", ozon_list(OZON_BASE + "/v2/posting/fbo/list", headers, start)
                )
        except Exception as exc:
            print("кабинет %s сбой: %s" % (cab["id"], exc))
    print("заказов создано: %s" % created)
    return created


if __name__ == "__main__":
    run()
