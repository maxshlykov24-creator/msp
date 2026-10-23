"""Заказы FBS/FBO из кабинетов → заказ покупателя в МойСклад."""

from datetime import datetime, timedelta, timezone
import time
import json

from db import (
    find_cache_group,
    get_client_by_id,
    get_order_log,
    get_setting,
    set_setting,
    get_sku,
    get_sku_by_barcode,
    init_db,
    list_cabinets,
    lock_name,
    run_lock,
    upsert_order_log,
)
from ms import ensure_projects, ms_meta, order_attrs, org_id, project_id, store_id
from net import MS_BASE, OZON_BASE, WB_BASE, WB_STATS, ms_headers, ozon_headers, req, wb_headers

MSK = timezone(timedelta(hours=3))



# Овчинников: согласованный список складов FBS, проверен в WB 2026-10-09.
# Ключ — UUID контрагента МойСклад: переименование клиента правило не снимает.
WB_FBS_WAREHOUSES = {
    "0c28fc33-b1cf-11f1-0a80-1ed500791c0a": {
        "1877978": "МСК",
        "2084561": "Смоленск (МСК)",
        "2084659": "Ярославль (МСК)",
        "2084459": "Москва 2",
        "2193868": "Внуково (МСК)",
    },
}


def filter_wb_fbs(client, orders):
    """До любых записей/списаний. Неизвестный склад ограниченного клиента запрещён."""
    owner = client["ms_counterparty_id"] if "ms_counterparty_id" in client.keys() else None
    allowed = WB_FBS_WAREHOUSES.get(owner)
    if allowed is None:
        return orders
    return [o for o in orders if str(o.get("warehouseId") or "") in allowed]


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
        raise RuntimeError('МойСклад не подтвердил поиск существующего заказа. Создание остановлено.')
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
    r = req("POST", MS_BASE + "/entity/customerorder", headers=ms_headers(), json=payload, retry_safe=False)
    if r.status_code not in (200, 201):
        return None, "%s %s" % (r.status_code, (r.text or "")[:180])
    oid = r.json().get("id")
    upsert_order_log(cab["id"], ext_id, oid, "ok", "", now_iso())
    return oid, "создан"


def since_days(days=3):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return start


def pull_wb_new(token, strict=False):
    """Актуальные новые сборочные задания одним ответом.

    Отдельная ручка WB, без страниц и без окна дат. Спрашиваем её всегда: пока
    новые заказы брали только постраничной выборкой, обрыв на середине прятал
    свежие задания от сборщика.
    """
    try:
        r = req("GET", WB_BASE + "/api/v3/orders/new", headers=wb_headers(token))
    except Exception as exc:
        if strict:
            raise RuntimeError("WB новые: ошибка соединения") from exc
        print("WB новые не ответили: %s" % exc)
        return []
    if r.status_code != 200:
        if strict:
            raise RuntimeError("WB новые: HTTP %s" % r.status_code)
        print("WB новые %s %s" % (r.status_code, (r.text or "")[:180]))
        return []
    return (r.json() or {}).get("orders") or []


def pull_wb_fbs(token, date_from, strict=False):
    """Задания за окно плюс все новые.

    Страницы идут от старых к новым, поэтому обрыв выборки терял свежие заказы.
    Начинаем с `orders/new`, дальше добираем историю по страницам, дубли по id
    отбрасываем.
    """
    out = list(pull_wb_new(token, strict=strict))
    seen = {str(o.get("id") or "") for o in out}
    nxt = 0
    ts = int(date_from.timestamp())
    while True:
        try:
            r = req(
                "GET",
                WB_BASE + "/api/v3/orders",
                headers=wb_headers(token),
                params={"limit": 1000, "next": nxt, "dateFrom": ts},
            )
        except Exception as exc:
            if strict:
                raise RuntimeError("WB FBS: неполный список, ошибка соединения") from exc
            print("WB FBS страница оборвалась, беру что успел: %s" % exc)
            break
        if r.status_code != 200:
            if strict:
                raise RuntimeError("WB FBS: неполный список, HTTP %s" % r.status_code)
            print("WB FBS %s %s" % (r.status_code, (r.text or "")[:180]))
            break
        data = r.json()
        batch = data.get("orders") or []
        for order in batch:
            ext = str(order.get("id") or "")
            if ext and ext in seen:
                continue
            seen.add(ext)
            out.append(order)
        nxt = data.get("next")
        if not batch or not nxt:
            break
    return out


def pull_wb_fbo(token, date_from, strict=False):
    r = req(
        "GET",
        WB_STATS + "/api/v1/supplier/orders",
        headers=wb_headers(token),
        params={"dateFrom": date_from.strftime("%Y-%m-%d"), "flag": 0},
    )
    if r.status_code != 200:
        if strict:
            raise RuntimeError("WB FBO: HTTP %s%s" % (r.status_code, " — нет доступа к статистике" if r.status_code == 403 else ""))
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
        if row.get("isCancel") and not strict:
            continue
        fbo.append(row)
    return fbo


def ozon_since(cabinet_id, kind, days=3, orders=False):
    """После сбоя догоняем период от последнего успеха с суточным перекрытием."""
    start = since_days(days)
    saved = get_setting("orders:%s:%s:success" % (cabinet_id, kind)) if orders else None
    if not saved:
        saved = json.loads(get_setting("sync:%s:%s" % (cabinet_id, kind)) or "{}").get("success")
    if saved:
        last = datetime.fromisoformat(saved.replace("Z", "+00:00"))
        if last.tzinfo is None:
            raise RuntimeError("Ozon: время последней загрузки без часового пояса")
        start = min(start, last - timedelta(days=1))
    if start < since_days(364):
        raise RuntimeError("Ozon: перерыв больше года, нужна загрузка истории отдельными периодами")
    return start


def ozon_list(url, headers, date_from, strict=False):
    """FBS v4 / FBO v3: полный список по cursor, без применения оборванных страниц."""
    from net import clear_fbs_list_pause, fbs_list_paused

    is_fbs = "/posting/fbs/list" in url
    if is_fbs and fbs_list_paused():
        raise RuntimeError("Ozon: список на паузе после 429")
    out, seen, cursors = [], set(), set()
    cursor = ""
    since = date_from.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    while True:
        if is_fbs:
            time.sleep(2)
        r = req(
            "POST", url, headers=headers,
            json={"dir": "ASC", "filter": {"since": since, "to": until},
                  "limit": 100, "cursor": cursor},
        )
        if r.status_code != 200:
            reason = " — у ключа нет нужных прав" if r.status_code == 403 else ""
            raise RuntimeError("Ozon: неполный список, HTTP %s%s" % (r.status_code, reason))
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get("postings"), list):
            raise RuntimeError("Ozon: список отправлений отсутствует в ответе")
        batch, more = data["postings"], data.get("has_next")
        if not isinstance(more, bool):
            raise RuntimeError("Ozon: признак продолжения списка отсутствует в ответе")
        for post in batch:
            key = post.get("posting_number") if isinstance(post, dict) else None
            if not isinstance(key, str) or not key or key in seen:
                raise RuntimeError("Ozon: неполный или повторяющийся список отправлений")
            seen.add(key)
        out.extend(batch)
        if not more:
            if is_fbs:
                clear_fbs_list_pause()
            return out
        nxt = data.get("cursor")
        if not batch or not isinstance(nxt, str) or not nxt or nxt in cursors:
            raise RuntimeError("Ozon: пустая страница или повтор курсора при наличии продолжения")
        cursors.add(nxt)
        cursor = nxt


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
    orders = filter_wb_fbs(client, orders)
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
    from shipments_pull import ozon_composition, ozon_stamp
    n = 0
    for post in postings:
        ext_id = str(post.get("posting_number") or "")
        if not ext_id or already_ok(cab["id"], ext_id):
            continue
        if post.get('status') in ('cancelled', 'canceled'):
            continue
        positions = []
        missing = []
        for prod in ozon_composition(post.get('products')):
            pid = resolve_product(
                client["id"],
                cab["id"],
                ext_key=prod['article'],
                article=prod['article'],
            )
            if not pid:
                missing.append(prod['article'])
                continue
            positions.append({"product_id": pid, "qty": prod['qty']})
        if missing or not positions:
            upsert_order_log(cab["id"], ext_id, None, "нет товара", 'Не сопоставлены: ' + ', '.join(missing), now_iso())
            continue
        oid, msg = create_order(
            client,
            cab,
            kind,
            ext_id,
            positions,
            ext_id,
            ozon_stamp((post.get("analytics_data") or {}).get("delivery_date") or post.get("in_process_at"), seconds=True),
            post.get("status") or kind.upper(),
            ozon_stamp(post.get("in_process_at"), seconds=True),
        )
        print("Ozon %s %s: %s" % (kind, ext_id, msg))
        if oid:
            n += 1
    return n


def run(client_id=None, blocking=True):
    """Заказы в МойСклад. Замок свой у каждого контрагента, как у отправлений."""
    init_db()
    with run_lock(name=lock_name(client_id), blocking=blocking):
        return _run(client_id)


def _run(client_id=None):
    ensure_projects()
    start = since_days(3)
    created = 0
    for cab in list_cabinets():
        if not cab["active"] or not cab["token"]:
            continue
        if client_id and cab["client_id"] != int(client_id):
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
                for kind, path in (("fbs", "/v4/posting/fbs/list"), ("fbo", "/v3/posting/fbo/list")):
                    try:
                        attempt = now_iso()
                        window = ozon_since(cab["id"], kind, orders=True)
                        posts = ozon_list(OZON_BASE + path, headers, window, strict=True)
                        created += handle_ozon(client, cab, kind, posts)
                        set_setting("orders:%s:%s:success" % (cab["id"], kind), attempt)
                    except Exception as exc:
                        print("кабинет %s Ozon %s сбой: %s" % (cab["id"], kind, exc))
        except Exception as exc:
            print("кабинет %s сбой: %s" % (cab["id"], exc))
    print("заказов создано: %s" % created)
    return created


if __name__ == "__main__":
    run()
