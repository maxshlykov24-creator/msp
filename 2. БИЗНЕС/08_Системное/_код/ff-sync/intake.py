"""Приёмка: очередь строк в базе → товары и заказ поставщика в МойСклад."""

from datetime import datetime, timedelta, timezone

from db import (
    add_intake_row,
    find_cache,
    get_client_by_id,
    init_db,
    insert_lot,
    list_intake,
    run_lock,
    update_intake_row,
    upsert_sku,
)
from ms import tracking_code
from products_push import create_purchase_order, gtin14, push_one

MSK = timezone(timedelta(hours=3))


def now_iso():
    return datetime.now(MSK).isoformat()


def parse_num(raw, default=None):
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def lookup(client_id, barcode, article=""):
    """Что сервер уже знает о товаре по кабинетам клиента."""
    hits = find_cache(client_id, barcode=barcode or None, article=article or None)
    if not hits:
        return None
    first = hits[0]
    return {
        "hits": hits,
        "article": first["ext_article"] or "",
        "name": first["name"] or "",
        "marketplace": "+".join(sorted({h["marketplace"] for h in hits})),
        "gtin": gtin14(barcode) or gtin14(first["ext_barcode"]),
    }


def add(client_id, barcode, qty, liters, kind, gtin="", author=""):
    client = get_client_by_id(int(client_id))
    if not client:
        raise ValueError("клиент не найден")
    barcode = str(barcode or "").strip()
    if not barcode:
        raise ValueError("нужен штрихкод")
    kind = (kind or "").strip() or "Не маркируется"
    liters = parse_num(liters)
    qty = parse_num(qty, 1) or 1
    found = lookup(client["id"], barcode)
    note = ""
    state = "draft"
    if not found:
        note = "нет в кабинетах этого клиента"
        state = "warn"
        found = {"article": "", "name": "", "marketplace": "", "gtin": gtin14(barcode)}
    gtin = str(gtin or "").strip() or found["gtin"]
    if liters is None:
        note = "нужен литраж"
        state = "warn"
    elif not gtin and tracking_code(kind) != "NOT_TRACKED":
        note = "нужен GTIN, товар маркируется"
        state = "warn"
    fields = {
        "barcode": barcode,
        "article": found["article"],
        "name": found["name"],
        "marketplace": found["marketplace"],
        "gtin": gtin,
        "tracking_type": kind,
        "liters": liters,
        "qty": qty,
        "state": state,
        "note": note,
    }
    rid = add_intake_row(client["id"], fields, now_iso(), author)
    return rid


def recheck(row):
    """Перед отправкой в МойСклад пересобираем строку по свежему кэшу."""
    client = get_client_by_id(row["client_id"])
    liters = row["liters"]
    kind = row["tracking_type"] or "Не маркируется"
    if liters is None:
        return None, "нужен литраж"
    found = lookup(client["id"], row["barcode"], row["article"] or "")
    if not found:
        return None, "нет в кабинетах этого клиента"
    gtin = (row["gtin"] or "").strip() or found["gtin"]
    if not gtin and tracking_code(kind) != "NOT_TRACKED":
        return None, "нужен GTIN, товар маркируется"
    first = found["hits"][0]
    payload = {
        "cabinet_id": first["cabinet_id"],
        "ext_key": first["ext_key"],
        "ext_article": first["ext_article"],
        "ext_barcode": row["barcode"] or first["ext_barcode"],
        "name": first["name"],
        "size": first["size"],
        "Литраж_л": liters,
        "Тип продукции": kind,
        "GTIN": gtin,
    }
    return {"payload": payload, "hits": found["hits"], "gtin": gtin, "marketplace": found["marketplace"]}, ""


def process_row(row):
    prepared, err = recheck(row)
    if err:
        update_intake_row(row["id"], state="warn", note=err)
        return None, err
    res = push_one(prepared["payload"], dry=False)
    if not res["ok"]:
        update_intake_row(row["id"], state="warn", note=res["msg"])
        return None, res["msg"]
    for hit in prepared["hits"][1:]:
        upsert_sku(
            row["client_id"],
            hit["cabinet_id"],
            hit["marketplace"],
            hit["ext_key"],
            hit["ext_article"],
            hit["ext_barcode"],
            res["id"],
        )
    update_intake_row(
        row["id"],
        article=res["article"],
        name=prepared["payload"]["name"],
        marketplace=prepared["marketplace"],
        gtin=prepared["gtin"],
        ms_product_id=res["id"],
        note=res["msg"],
    )
    return {"client": res["client"], "product_id": res["id"], "article": res["article"]}, ""


def run(blocking=True):
    init_db()
    with run_lock(blocking=blocking):
        return _run()


def _run():
    rows = list_intake(states=("draft", "warn"))
    done = []
    skipped = []
    pending = {}
    for row in rows:
        extra, err = process_row(row)
        if err:
            skipped.append({"id": row["id"], "barcode": row["barcode"], "note": err})
            print("приёмка %s: %s" % (row["barcode"], err))
            continue
        bucket = pending.setdefault(
            extra["client"]["id"], {"client": extra["client"], "items": [], "rows": []}
        )
        bucket["items"].append({"product_id": extra["product_id"], "qty": float(row["qty"] or 1)})
        bucket["rows"].append((row, extra))
    orders = []
    for bucket in pending.values():
        oid, number, err = create_purchase_order(bucket["client"], bucket["items"])
        if err:
            for row, _extra in bucket["rows"]:
                update_intake_row(row["id"], state="warn", note=err)
                skipped.append({"id": row["id"], "barcode": row["barcode"], "note": err})
            print("заказ поставщика: %s" % err)
            continue
        label = number or oid
        orders.append({"client": bucket["client"]["name"], "number": label, "ms_id": oid})
        for row, extra in bucket["rows"]:
            insert_lot(
                row["client_id"],
                extra["product_id"],
                extra["article"],
                row["barcode"],
                row["gtin"],
                row["name"],
                row["tracking_type"],
                row["liters"],
                float(row["qty"] or 1),
                now_iso(),
                oid,
            )
            update_intake_row(row["id"], state="done", ms_order_name=label, note="заказ поставщика %s" % label)
            done.append({"id": row["id"], "barcode": row["barcode"], "order": label})
            print("приёмка %s: заказ поставщика %s" % (row["barcode"], label))
    return {"done": done, "skipped": skipped, "orders": orders}


if __name__ == "__main__":
    print(run())
