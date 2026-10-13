"""Создание и обновление товара в МойСклад и заказа поставщика."""

import re

from db import get_cabinet, get_client_by_id, get_setting, get_sku, get_sku_by_barcode, prefer_hit, upsert_sku
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
    if len(digits) in (12, 14) and gtin_ok(digits):
        return {"gtin": digits}
    return {"code128": digits or v}


def gtin14(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 13:
        return "0" + digits
    if len(digits) == 14:
        return digits
    return ""


def gtin_ok(digits):
    """Контрольная цифра GS1. МойСклад иначе отвергает штрихкод типа GTIN."""
    if not digits or not str(digits).isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


def gtin_code(raw):
    """Цифры для штрихкода типа GTIN. 13 знаков дополняются до 14, цифра та же."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 13 and gtin_ok(digits):
        digits = "0" + digits
    if len(digits) in (8, 12, 14) and gtin_ok(digits):
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
        raise ValueError("МойСклад не ответил на поиск по артикулу: HTTP %s" % r.status_code)
    rows = r.json().get("rows") or []
    if len(rows) > 1:
        raise ValueError("несколько товаров с одним артикулом в МойСклад — проверь карточки")
    return rows[0] if rows else None


def find_products_by_barcode(code):
    v = str(code or "").strip()
    if not v:
        return []
    r = req(
        "GET",
        MS_BASE + "/entity/product",
        headers=ms_headers(),
        params={"filter": "barcode=%s" % v, "limit": 100},
    )
    if r.status_code != 200:
        raise ValueError("МойСклад не ответил на поиск по штрихкоду: HTTP %s" % r.status_code)
    data = r.json() or {}
    rows = data.get("rows") or []
    if int((data.get("meta") or {}).get("size") or len(rows)) > len(rows):
        raise ValueError("слишком много товаров с одним штрихкодом в МойСклад — проверь вручную")
    return rows


def get_product(pid):
    if not pid:
        return None
    r = req("GET", MS_BASE + "/entity/product/" + pid, headers=ms_headers())
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ValueError("МойСклад не ответил на карточку товара: HTTP %s" % r.status_code)
    return r.json()


def _ref_id(value):
    if not isinstance(value, dict):
        return ""
    href = ((value.get("meta") or {}).get("href") or "").rstrip("/")
    return href.rsplit("/", 1)[-1] if href else ""


def product_owners(product):
    """Поставщик и поле клиента должны указывать на одного контрагента."""
    owners = set()
    supplier = _ref_id(product.get("supplier"))
    if supplier:
        owners.add(supplier)
    client_attr = get_setting("ATTR_CLIENT_ID")
    for attr in product.get("attributes") or []:
        meta = _ref_id(attr.get("meta"))
        if meta == client_attr or attr.get("name") == "Клиент фулфилмента":
            owner = _ref_id(attr.get("value"))
            if owner:
                owners.add(owner)
    return owners


def same_client_product(product, client, linked=False):
    owners = product_owners(product)
    want = client["ms_counterparty_id"]
    if owners and owners != {want}:
        raise ValueError("товар в МойСклад закреплён за другим контрагентом — сопоставление остановлено")
    if not owners and not linked:
        raise ValueError("у найденного товара в МойСклад не указан контрагент — сопоставление остановлено")
    return True


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
            same_client_product(found, client, linked=True)
            return found
    for code in barcodes_from_hits(hits, barcode, gtin):
        candidates = find_products_by_barcode(code)
        owned = []
        unknown = False
        for candidate in candidates:
            found = get_product(candidate.get("id")) or candidate
            owners = product_owners(found)
            if len(owners) > 1:
                raise ValueError("у товара в МойСклад расходятся поставщик и клиент фулфилмента — проверь карточку")
            if owners == {client["ms_counterparty_id"]}:
                owned.append(found)
            elif not owners:
                unknown = True
        if len(owned) > 1:
            raise ValueError("несколько товаров клиента с одним штрихкодом в МойСклад — выбери вручную")
        if owned:
            return owned[0]
        if unknown:
            raise ValueError("у товара с этим штрихкодом в МойСклад не указан контрагент — проверь карточку вручную")
        # Чужой товар с тем же EAN не обновляем. У каждого клиента своя
        # карточка с артикулом, начинающимся на код клиента.
    found = find_product(article)
    if found:
        found = get_product(found.get("id")) or found
        same_client_product(found, client)
    return found


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
    norm = gtin_code(gtin)
    for raw in list(codes or []):
        digits = re.sub(r"\D", "", str(raw or ""))
        if norm and digits == norm and len(digits) != 13:
            continue
        item = barcode_obj(raw)
        if not item:
            continue
        key = tuple(item.items())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if norm and not any(item.get("gtin") == norm for item in out):
        out.append({"gtin": norm})
    return out


def drop_gtin_code128(barcodes, norm):
    """Тот же номер не держим вторым штрихкодом Code128 рядом с типом GTIN."""
    if not norm:
        return list(barcodes or [])
    out = []
    for item in barcodes or []:
        if item.get("code128") and re.sub(r"\D", "", str(item.get("code128"))) == norm:
            continue
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
    explicit_gtin = (row.get("GTIN") or "").strip()
    gtin = explicit_gtin or gtin14(row.get("ext_barcode"))
    if explicit_gtin and not gtin_code(explicit_gtin):
        return {
            "ok": False,
            "msg": "GTIN не проходит контрольную цифру",
            "id": None,
            "article": article,
            "gtin": explicit_gtin,
            "client": client,
        }
    try:
        existing = find_linked(client, row.get("ext_barcode"), gtin, hits, article)
    except ValueError as exc:
        return {"ok": False, "msg": str(exc), "id": None, "article": article, "gtin": gtin, "client": client}
    if existing:
        article = existing.get("article") or article
    track = tracking_code(row.get("Тип продукции"))
    extra = barcodes_of_many(barcodes_from_hits(hits, row.get("ext_barcode"), gtin), gtin)
    codes = merge_barcodes(existing or {}, extra) if existing else extra
    codes = drop_gtin_code128(codes, gtin_code(gtin))
    payload = {
        "name": (existing.get("name") if existing else None) or row.get("name") or article,
        "article": article,
        "code": existing.get("code") if existing else article,
        "trackingType": track,
        "attributes": product_attrs(
            row.get("Литраж_л"),
            client["ms_counterparty_id"],
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
        r = req("POST", MS_BASE + "/entity/product", headers=ms_headers(), json=payload, retry_safe=False)
        action = "создан"
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
    r = req("POST", MS_BASE + "/entity/purchaseorder", headers=ms_headers(), json=payload, retry_safe=False)
    if r.status_code not in (200, 201):
        return None, "", "заказ поставщика %s %s" % (r.status_code, (r.text or "")[:160])
    data = r.json()
    return data.get("id"), data.get("name") or "", ""


def _attr_row(product, name):
    for row in product.get("attributes") or []:
        if row.get("name") == name:
            return row
    return None


def _attr_text(product, name):
    row = _attr_row(product, name)
    if not row or not isinstance(row.get("value"), str):
        return ""
    return row["value"].strip()


def _has_gtin_barcode(product, norm):
    return any(str(item.get("gtin") or "") == norm for item in (product.get("barcodes") or []))


def _each_product():
    """Активные и архивные: иначе GTIN архивной карточки потеряется при снятии поля."""
    for archived in ("false", "true"):
        offset = 0
        while True:
            r = req(
                "GET",
                MS_BASE + "/entity/product",
                headers=ms_headers(),
                params={"limit": 100, "offset": offset, "filter": "archived=%s" % archived},
            )
            if r.status_code != 200:
                raise ValueError("список товаров %s %s" % (r.status_code, (r.text or "")[:160]))
            rows = (r.json() or {}).get("rows") or []
            for row in rows:
                yield row
            if len(rows) < 100:
                break
            offset += 100


def _field_defs():
    r = req("GET", MS_BASE + "/entity/product/metadata/attributes", headers=ms_headers())
    if r.status_code != 200:
        raise ValueError("поля товара %s %s" % (r.status_code, (r.text or "")[:160]))
    return {row.get("name"): row for row in (r.json().get("rows") or [])}


def _drop_field(defs, name):
    row = defs.get(name)
    if not row:
        return "нет поля"
    href = (row.get("meta") or {}).get("href")
    if not href:
        return "у поля нет ссылки"
    r = req("DELETE", href, headers=ms_headers())
    if r.status_code in (200, 204, 404):
        return "снято"
    return "не снято %s %s" % (r.status_code, (r.text or "")[:120])


def rehome_card_fields(apply=False):
    """GTIN из допполя переносит в штрихкод типа GTIN и снимает поле «Маркетплейс».

    «Клиент фулфилмента» и «Организация» не меняются.
    """
    defs = _field_defs()
    planned, ready, leftovers, junk, mismatches, errors = [], [], [], [], [], []
    marketplace_n = 0
    for product in _each_product():
        article = product.get("article") or product.get("name") or product.get("id")
        supplier = _ref_id(product.get("supplier"))
        client = _attr_row(product, "Клиент фулфилмента")
        client_id = _ref_id((client or {}).get("value")) if client else ""
        if client_id and supplier and client_id != supplier:
            mismatches.append(article)
        if _attr_text(product, "Маркетплейс"):
            marketplace_n += 1
        raw = _attr_text(product, "GTIN")
        if not raw:
            continue
        norm = gtin_code(raw)
        if not norm:
            digits = re.sub(r"\D", "", raw)
            bucket = leftovers if len(digits) >= 8 else junk
            bucket.append("%s: %s" % (article, raw))
            continue
        if _has_gtin_barcode(product, norm):
            ready.append(article)
            continue
        planned.append("%s → %s" % (article, norm))
        if not apply:
            continue
        codes = drop_gtin_code128(merge_barcodes(product, [{"gtin": norm}]), norm)
        r = req(
            "PUT",
            MS_BASE + "/entity/product/" + product["id"],
            headers=ms_headers(),
            json={"barcodes": codes},
        )
        if r.status_code not in (200, 201):
            errors.append("%s %s %s" % (article, r.status_code, (r.text or "")[:160]))
            continue
        got = (r.json() or {}).get("barcodes") or []
        if not any(str(item.get("gtin") or "") == norm for item in got):
            errors.append("%s: в ответе нет штрихкода GTIN" % article)

    removed = {}
    if apply and not errors and not leftovers:
        removed["GTIN"] = _drop_field(defs, "GTIN")
    if apply:
        removed["Маркетплейс"] = _drop_field(defs, "Маркетплейс")

    def head(items, limit=20):
        shown = list(items[:limit])
        extra = len(items) - limit
        if extra > 0:
            shown.append("… ещё %s" % extra)
        return shown

    print("режим: %s" % ("запись" if apply else "просмотр"))
    print("GTIN записать в штрихкоды: %s" % len(planned))
    for line in head(planned):
        print("  ", line)
    print("GTIN уже в штрихкодах: %s" % len(ready))
    print("GTIN с битой цифрой, поле остаётся: %s" % len(leftovers))
    for line in head(leftovers):
        print("  ", line)
    print("в поле GTIN не номер, снимем вместе с полем: %s" % len(junk))
    for line in head(junk):
        print("  ", line)
    print("заполнено «Маркетплейс»: %s" % marketplace_n)
    print("поставщик и клиент фулфилмента расходятся: %s" % len(mismatches))
    for line in head(mismatches):
        print("  ", line)
    print("ошибки: %s" % len(errors))
    for line in head(errors):
        print("  ", line)
    if removed:
        print("поля: " + ", ".join("%s %s" % (name, state) for name, state in removed.items()))
    elif apply:
        print("поле GTIN оставлено: есть ошибки или битые номера")
    return {
        "planned": planned,
        "ready": ready,
        "leftovers": leftovers,
        "junk": junk,
        "mismatches": mismatches,
        "errors": errors,
        "removed": removed,
    }


def _self_check():
    body = "0460123456789"
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    code14 = body + str((10 - (total % 10)) % 10)
    assert gtin_ok(code14), code14
    assert gtin_code(code14) == code14
    code13 = code14[1:]
    assert gtin_ok(code13), code13
    assert gtin_code(code13) == code14
    assert gtin_code("123") == ""
    assert gtin_code("") == ""
    codes = barcodes_of_many(["OZN2062192717", code13], code13)
    assert {"code128": "OZN2062192717"} in codes
    assert {"ean13": code13} in codes
    assert {"gtin": code14} in codes
    only = barcodes_of_many([code14], code14)
    assert only == [{"gtin": code14}], only
    assert drop_gtin_code128([{"code128": code14}, {"ean13": code13}], code14) == [{"ean13": code13}]
    print("products_push: GTIN уходит в штрихкод, Code128 того же номера не дублируется")


if __name__ == "__main__":
    _self_check()
