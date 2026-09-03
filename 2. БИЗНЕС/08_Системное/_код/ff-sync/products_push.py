"""Создание и обновление товара в МойСклад и заказа поставщика."""

import re

from db import get_cabinet, get_client_by_id, upsert_sku
from ms import ms_meta, org_id, product_attrs, store_id, tracking_code
from net import MS_BASE, ms_headers, req


def barcode_obj(raw):
    v = str(raw or "").strip()
    if not v:
        return None
    if re.match(r"^OZN", v, re.I):
        return {"code128": v}
    digits = re.sub(r"\D", "", v)
    if len(digits) == 13:
        return {"ean13": digits}
    if len(digits) == 8:
        return {"ean8": digits}
    return {"code128": digits or v}


def gtin14(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 13:
        return "0" + digits
    if len(digits) == 14:
        return digits
    return ""


def article_of(code, row):
    base = (row.get("ext_article") or row.get("ext_key") or "").strip()
    size = (row.get("size") or "").strip()
    art = "%s-%s" % (code, base)
    if size:
        art = "%s-%s" % (art, size)
    return art[:255]


def find_product(article):
    r = req(
        "GET",
        MS_BASE + "/entity/product",
        headers=ms_headers(),
        params={"filter": "article=%s" % article, "limit": 2},
    )
    r.raise_for_status()
    rows = r.json().get("rows") or []
    if not rows:
        return None
    return rows[0]


def barcodes_of(ean, gtin):
    out = []
    bc = barcode_obj(ean)
    if bc:
        out.append(bc)
    if gtin and gtin != (ean or "").strip():
        out.append({"gtin": gtin})
    return out


def push_one(row, dry=False):
    cab = get_cabinet(int(row.get("cabinet_id") or 0))
    if not cab:
        return {"ok": False, "msg": "нет кабинета %s" % row.get("cabinet_id"), "id": None, "article": ""}
    client = get_client_by_id(cab["client_id"])
    article = article_of(client["code"], row)
    existing = find_product(article)
    gtin = (row.get("GTIN") or "").strip() or gtin14(row.get("ext_barcode"))
    track = tracking_code(row.get("Тип продукции"))
    payload = {
        "name": row.get("name") or article,
        "article": article,
        "code": article,
        "trackingType": track,
        "attributes": product_attrs(
            row.get("Литраж_л"),
            client["ms_counterparty_id"],
            cab["marketplace"],
            gtin,
        ),
    }
    if client["ms_counterparty_id"]:
        payload["supplier"] = ms_meta("counterparty", client["ms_counterparty_id"])
    codes = barcodes_of(row.get("ext_barcode"), gtin)
    if codes:
        payload["barcodes"] = codes
    if dry:
        return {
            "ok": True,
            "msg": "dry %s %s" % ("обновить" if existing else "создать", article),
            "id": existing["id"] if existing else None,
            "article": article,
            "gtin": gtin,
            "client": client,
        }
    if existing:
        r = req("PUT", MS_BASE + "/entity/product/" + existing["id"], headers=ms_headers(), json=payload)
        action = "обновлён"
    else:
        r = req("POST", MS_BASE + "/entity/product", headers=ms_headers(), json=payload)
        action = "создан"
    if r.status_code not in (200, 201) and any("gtin" in c for c in codes):
        payload["barcodes"] = [c for c in codes if "gtin" not in c] or None
        if not payload.get("barcodes"):
            payload.pop("barcodes", None)
        if existing:
            r = req("PUT", MS_BASE + "/entity/product/" + existing["id"], headers=ms_headers(), json=payload)
        else:
            r = req("POST", MS_BASE + "/entity/product", headers=ms_headers(), json=payload)
    if r.status_code not in (200, 201):
        return {
            "ok": False,
            "msg": "ошибка %s %s %s" % (article, r.status_code, (r.text or "")[:160]),
            "id": None,
            "article": article,
            "gtin": gtin,
            "client": client,
        }
    pid = r.json().get("id")
    upsert_sku(
        client["id"],
        cab["id"],
        cab["marketplace"],
        row.get("ext_key") or "",
        row.get("ext_article") or "",
        row.get("ext_barcode") or "",
        pid,
    )
    return {
        "ok": True,
        "msg": "%s %s id=%s" % (action, article, pid),
        "id": pid,
        "article": article,
        "gtin": gtin,
        "client": client,
    }


def create_purchase_order(client, positions):
    """Заказ поставщика. Номер не задаём — его ставит МойСклад."""
    merged = {}
    for item in positions:
        pid = item["product_id"]
        qty = float(item.get("qty") or 1)
        if pid in merged:
            merged[pid] += qty
        else:
            merged[pid] = qty
    payload = {
        "organization": ms_meta("organization", client["ms_org_id"] or org_id()),
        "agent": ms_meta("counterparty", client["ms_counterparty_id"]),
        "store": ms_meta("store", client["ms_store_id"] or store_id()),
        "vatEnabled": False,
        "positions": [
            {
                "quantity": qty,
                "price": 0,
                "assortment": ms_meta("product", pid),
            }
            for pid, qty in merged.items()
        ],
    }
    r = req("POST", MS_BASE + "/entity/purchaseorder", headers=ms_headers(), json=payload)
    if r.status_code not in (200, 201):
        return None, "", "заказ поставщика %s %s" % (r.status_code, (r.text or "")[:160])
    data = r.json()
    return data.get("id"), data.get("name") or "", ""


