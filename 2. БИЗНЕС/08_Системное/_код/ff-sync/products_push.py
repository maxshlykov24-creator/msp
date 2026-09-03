"""Создание и обновление товара в МойСклад и заказа поставщика."""

import re

from db import get_cabinet, get_client_by_id, get_sku, get_sku_by_barcode, prefer_hit, upsert_sku
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
    if not article:
        return None
    r = req(
        "GET",
        MS_BASE + "/entity/product",
        headers=ms_headers(),
        params={"filter": "article=%s" % article, "limit": 2},
    )
    if r.status_code != 200:
        return None
    rows = r.json().get("rows") or []
    return rows[0] if rows else None


def find_product_by_barcode(code):
    v = str(code or "").strip()
    if not v:
        return None
    r = req(
        "GET",
        MS_BASE + "/entity/product",
        headers=ms_headers(),
        params={"filter": "barcode=%s" % v, "limit": 2},
    )
    if r.status_code != 200:
        return None
    rows = r.json().get("rows") or []
    return rows[0] if rows else None


def get_product(pid):
    if not pid:
        return None
    r = req("GET", MS_BASE + "/entity/product/" + pid, headers=ms_headers())
    if r.status_code != 200:
        return None
    return r.json()


def find_linked(client, barcode, gtin, hits, article):
    """Уже заведённый товар: связка в базе или штрихкод / артикул в МойСклад."""
    seen = []
    for hit in prefer_hit(hits or []):
        sku = get_sku(hit["cabinet_id"], hit["ext_key"])
        if sku and sku["ms_product_id"] and sku["ms_product_id"] not in seen:
            seen.append(sku["ms_product_id"])
        sku = get_sku_by_barcode(client["id"], hit["ext_barcode"] or barcode)
        if sku and sku["ms_product_id"] and sku["ms_product_id"] not in seen:
            seen.append(sku["ms_product_id"])
    sku = get_sku_by_barcode(client["id"], barcode)
    if sku and sku["ms_product_id"] and sku["ms_product_id"] not in seen:
        seen.append(sku["ms_product_id"])
    for pid in seen:
        found = get_product(pid)
        if found:
            return found
    for code in barcodes_from_hits(hits, barcode, gtin):
        found = find_product_by_barcode(code)
        if found:
            return found
    return find_product(article)


def barcodes_from_hits(hits, barcode, gtin):
    out = []
    seen = set()
    for raw in [barcode, gtin] + [hit["ext_barcode"] for hit in (hits or [])] + [hit["gtin"] for hit in (hits or []) if "gtin" in hit.keys()]:
        v = str(raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def barcodes_of_many(codes, gtin):
    out = []
    seen = set()
    for raw in list(codes or []) + ([gtin] if gtin else []):
        item = barcode_obj(raw)
        if not item:
            continue
        key = tuple(item.items())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def merge_barcodes(existing, extra):
    have = existing.get("barcodes") or []
    seen = set()
    out = []
    for item in list(have) + list(extra or []):
        if not item:
            continue
        key = tuple(sorted((k, str(v)) for k, v in item.items() if k != "meta"))
        if key in seen:
            continue
        seen.add(key)
        clean = {k: v for k, v in item.items() if k != "meta"}
        if clean:
            out.append(clean)
    return out


def link_hits(client, hits, barcode, pid):
    seen = set()
    for hit in hits or []:
        key = (hit["cabinet_id"], hit["ext_key"])
        if key in seen:
            continue
        seen.add(key)
        upsert_sku(
            client["id"],
            hit["cabinet_id"],
            hit["marketplace"],
            hit["ext_key"] or "",
            hit["ext_article"] or "",
            hit["ext_barcode"] or barcode,
            pid,
        )
    if not seen:
        cab = get_cabinet(int((hits or [{}])[0].get("cabinet_id") or 0)) if hits else None
        if cab:
            upsert_sku(client["id"], cab["id"], cab["marketplace"], "", "", barcode, pid)


def push_one(row, hits=None, dry=False):
    cab = get_cabinet(int(row.get("cabinet_id") or 0))
    if not cab:
        return {"ok": False, "msg": "нет кабинета %s" % row.get("cabinet_id"), "id": None, "article": ""}
    client = get_client_by_id(cab["client_id"])
    hits = list(hits or [])
    if hits:
        first = prefer_hit(hits)[0]
        row = dict(row)
        row.setdefault("ext_article", first["ext_article"])
        row.setdefault("ext_key", first["ext_key"])
        row.setdefault("name", first["name"])
        row.setdefault("size", first["size"])
        row.setdefault("ext_barcode", first["ext_barcode"])
        cab = get_cabinet(first["cabinet_id"]) or cab
    article = article_of(client["code"], row)
    gtin = (row.get("GTIN") or "").strip() or gtin14(row.get("ext_barcode"))
    existing = find_linked(client, row.get("ext_barcode"), gtin, hits, article)
    if existing:
        article = existing.get("article") or article
    mps = "+".join(sorted({h["marketplace"] for h in hits})) if hits else cab["marketplace"]
    track = tracking_code(row.get("Тип продукции"))
    extra = barcodes_of_many(barcodes_from_hits(hits, row.get("ext_barcode"), gtin), gtin)
    codes = merge_barcodes(existing or {}, extra) if existing else extra
    payload = {
        "name": (existing.get("name") if existing else None) or row.get("name") or article,
        "article": article,
        "code": existing.get("code") if existing else article,
        "trackingType": track,
        "attributes": product_attrs(
            row.get("Литраж_л"),
            client["ms_counterparty_id"],
            mps,
            gtin,
        ),
    }
    if client["ms_counterparty_id"]:
        payload["supplier"] = ms_meta("counterparty", client["ms_counterparty_id"])
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
            "id": existing["id"] if existing else None,
            "article": article,
            "gtin": gtin,
            "client": client,
        }
    pid = r.json().get("id")
    link_hits(client, hits or [{"cabinet_id": cab["id"], "marketplace": cab["marketplace"],
                                 "ext_key": row.get("ext_key") or "", "ext_article": row.get("ext_article") or "",
                                 "ext_barcode": row.get("ext_barcode") or ""}], row.get("ext_barcode"), pid)
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


