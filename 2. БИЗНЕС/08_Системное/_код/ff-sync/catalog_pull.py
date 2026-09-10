"""Выгрузка номенклатуры кабинета в CSV. Печатает сырой JSON первой карточки."""

import argparse
import csv
import os

from db import get_cabinet, init_db, replace_cache
from ms import kind_from_subject
from net import OZON_BASE, req, ozon_headers, wb_headers
from products_push import gtin14

WB_CARDS = "https://content-api.wildberries.ru/content/v2/get/cards/list"

# Атрибуты Ozon приходят числами без названий, поэтому идентификаторы фиксируем
# здесь. Это общие для всех категорий поля справочника Ozon.
OZON_ATTR_BRAND = 85
OZON_ATTR_COLOR = 10096

FIELDS = [
    "cabinet_id",
    "marketplace",
    "ext_key",
    "ext_article",
    "ext_barcode",
    "name",
    "nmId",
    "chrtId",
    "offer_id",
    "size",
    "brand",
    "color",
    "Привезено",
    "Литраж_л",
    "gtin",
    "tracking_type",
    "subject",
    "need_kiz",
    "image",
]


def wb_photo(card):
    """Мелкая превьюшка карточки WB для списка сборки."""
    for photo in card.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        for key in ("tm", "c246x328", "square", "big"):
            if photo.get(key):
                return photo[key]
    return ""


def ozon_thumb(url):
    """Ozon отдаёт полный кадр (~200 КБ). /wc50/ — тот же файл ~1–2 КБ."""
    if not url or "/wc" in url:
        return url or ""
    head, name = url.rsplit("/", 1)
    if not name:
        return url
    return "%s/wc50/%s" % (head, name)


def ozon_photo(det):
    url = ""
    if det.get("primary_image"):
        raw = det["primary_image"]
        url = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, str) else "")
    if not url:
        for raw in det.get("images") or []:
            if isinstance(raw, str) and raw:
                url = raw
                break
            if isinstance(raw, dict) and raw.get("file_name"):
                url = raw["file_name"]
                break
    return ozon_thumb(url)


def out_dir():
    db = os.environ.get("FF_DB") or os.path.dirname(os.path.abspath(__file__))
    folder = os.path.dirname(db) if db.endswith(".db") else db
    if not folder:
        folder = os.path.dirname(os.path.abspath(__file__))
    return folder


def pull_wb(token):
    cards = []
    cursor = {"limit": 100}
    while True:
        body = {"settings": {"sort": {"ascending": True}, "cursor": cursor, "filter": {"withPhoto": -1}}}
        r = req("POST", WB_CARDS, headers=wb_headers(token), json=body)
        if r.status_code != 200:
            raise RuntimeError("WB cards %s %s" % (r.status_code, (r.text or "")[:300]))
        data = r.json()
        batch = data.get("cards") or []
        cards.extend(batch)
        cur = data.get("cursor") or {}
        total = cur.get("total") or len(batch)
        print("WB страница: %s карточек, total=%s" % (len(batch), total))
        if total < 100 or not batch:
            break
        cursor = {"limit": 100, "updatedAt": cur.get("updatedAt"), "nmID": cur.get("nmID")}
    return cards


def wb_color(card):
    """Цвет карточки WB. Отдельного поля нет — лежит характеристикой."""
    for ch in card.get("characteristics") or []:
        if not isinstance(ch, dict):
            continue
        if "цвет" not in str(ch.get("name") or "").lower():
            continue
        val = ch.get("value")
        if isinstance(val, list):
            return ", ".join(str(x) for x in val if x)
        if val:
            return str(val)
    return ""


def real_gtin(barcode):
    digits = "".join(ch for ch in str(barcode or "") if ch.isdigit())
    if len(digits) == 13 and not digits.startswith("2"):
        return gtin14(digits)
    if len(digits) == 14 and not digits.startswith("2"):
        return digits
    return ""


def rows_wb(cabinet_id, cards):
    rows = []
    for card in cards:
        title = card.get("title") or ""
        article = card.get("vendorCode") or ""
        nmid = card.get("nmID")
        subject = card.get("subjectName") or ""
        need_kiz = 1 if card.get("needKiz") else 0
        kind = kind_from_subject(subject + " " + title, need_kiz=bool(card.get("needKiz")))
        photo = wb_photo(card)
        brand = card.get("brand") or ""
        color = wb_color(card)
        sizes = card.get("sizes") or [{}]
        if not sizes:
            sizes = [{}]
        for size in sizes:
            skus = size.get("skus") or [""]
            chrt = size.get("chrtID")
            for sku in skus:
                rows.append(
                    {
                        "cabinet_id": cabinet_id,
                        "marketplace": "wb",
                        "ext_key": str(chrt or nmid or ""),
                        "ext_article": article,
                        "ext_barcode": sku,
                        "name": title,
                        "nmId": nmid or "",
                        "chrtId": chrt or "",
                        "offer_id": "",
                        "size": size.get("techSize") or "",
                        "brand": brand,
                        "color": color,
                        "Привезено": "",
                        "Литраж_л": "",
                        "gtin": real_gtin(sku),
                        "tracking_type": kind,
                        "subject": subject,
                        "need_kiz": need_kiz,
                        "image": photo,
                    }
                )
    return rows


def pull_ozon(client_id, api_key):
    items = []
    last_id = ""
    while True:
        r = req(
            "POST",
            OZON_BASE + "/v3/product/list",
            headers=ozon_headers(client_id, api_key),
            json={"filter": {}, "last_id": last_id, "limit": 100},
        )
        if r.status_code != 200:
            raise RuntimeError("Ozon list %s %s" % (r.status_code, (r.text or "")[:300]))
        block = (r.json().get("result") or {})
        batch = block.get("items") or []
        items.extend(batch)
        last_id = block.get("last_id") or ""
        print("Ozon страница: %s, last_id=%s" % (len(batch), bool(last_id)))
        if len(batch) < 100 or not last_id:
            break
    return items


def ozon_info(client_id, api_key, offer_ids):
    out = {}
    for i in range(0, len(offer_ids), 100):
        chunk = offer_ids[i : i + 100]
        r = req(
            "POST",
            OZON_BASE + "/v3/product/info/list",
            headers=ozon_headers(client_id, api_key),
            json={"offer_id": chunk, "product_id": [], "sku": []},
        )
        if r.status_code != 200:
            print("Ozon info %s %s" % (r.status_code, (r.text or "")[:200]))
            continue
        for item in r.json().get("items") or []:
            out[item.get("offer_id")] = item
    return out


def ozon_attrs(client_id, api_key, offer_ids):
    """Бренд и цвет по офферам. В /v3/product/info/list их нет вовсе.

    Метод отдаёт атрибуты числами, без названий, поэтому берём по фиксированным
    идентификаторам справочника. Пустой ответ не ошибка: у части товаров эти
    поля не заполнены, и этикетка просто напечатается без них.
    """
    out = {}
    for i in range(0, len(offer_ids), 100):
        chunk = offer_ids[i : i + 100]
        r = req(
            "POST",
            OZON_BASE + "/v4/product/info/attributes",
            headers=ozon_headers(client_id, api_key),
            json={"filter": {"offer_id": chunk, "visibility": "ALL"}, "limit": 100, "sort_dir": "ASC"},
        )
        if r.status_code != 200:
            print("Ozon attributes %s %s" % (r.status_code, (r.text or "")[:200]))
            continue
        for item in r.json().get("result") or []:
            got = {}
            for attr in item.get("attributes") or []:
                if not isinstance(attr, dict):
                    continue
                attr_id = attr.get("id", attr.get("attribute_id"))
                key = {OZON_ATTR_BRAND: "brand", OZON_ATTR_COLOR: "color"}.get(attr_id)
                if not key:
                    continue
                vals = [str(v.get("value") or "") for v in (attr.get("values") or []) if isinstance(v, dict)]
                got[key] = ", ".join(v for v in vals if v)
            if got:
                out[item.get("offer_id")] = got
    return out


def rows_ozon(cabinet_id, items, info, attrs=None):
    rows = []
    attrs = attrs or {}
    for it in items:
        offer = it.get("offer_id") or ""
        det = info.get(offer) or {}
        extra = attrs.get(offer) or {}
        name = det.get("name") or ""
        kind = kind_from_subject(name)
        photo = ozon_photo(det)
        seen_bc = set()
        barcodes = []
        for raw in det.get("barcodes") or []:
            code = str(raw or "").strip()
            if not code:
                continue
            key = code.upper() if code.upper().startswith("OZN") else "".join(ch for ch in code if ch.isdigit()) or code
            if key in seen_bc:
                continue
            seen_bc.add(key)
            barcodes.append(code)
        if not barcodes:
            barcodes = [""]
        for code in barcodes:
            rows.append(
                {
                    "cabinet_id": cabinet_id,
                    "marketplace": "ozon",
                    "ext_key": offer,
                    "ext_article": offer,
                    "ext_barcode": code,
                    "name": name,
                    "nmId": "",
                    "chrtId": "",
                    "offer_id": offer,
                    "size": "",
                    "brand": extra.get("brand") or "",
                    "color": extra.get("color") or "",
                    "Привезено": "",
                    "Литраж_л": "",
                    "gtin": real_gtin(code),
                    "tracking_type": kind,
                    "subject": name,
                    "need_kiz": 1 if kind and kind != "Не маркируется" else 0,
                    "image": photo,
                }
            )
    return rows


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def reindex_csvs():
    init_db()
    folder = out_dir()
    names = [n for n in os.listdir(folder) if n.startswith("catalog_") and n.endswith(".csv")]
    for name in names:
        parts = name.replace(".csv", "").split("_")
        if len(parts) < 3:
            continue
        cab_id = int(parts[-1])
        cab = get_cabinet(cab_id)
        if not cab:
            print("пропуск %s: нет кабинета %s" % (name, cab_id))
            continue
        with open(os.path.join(folder, name), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        replace_cache(cab["client_id"], cab["id"], rows)
        print("кэш %s: %s строк" % (name, len(rows)))


def pull_one(cab):
    raw = None
    if cab["marketplace"] == "wb":
        cards = pull_wb(cab["token"])
        if cards:
            raw = cards[0]
        rows = rows_wb(cab["id"], cards)
    elif cab["marketplace"] == "ozon":
        items = pull_ozon(cab["client_id_ext"], cab["token"])
        if items:
            raw = items[0]
        offers = [it.get("offer_id") for it in items if it.get("offer_id")]
        info = ozon_info(cab["client_id_ext"], cab["token"], offers)
        attrs = ozon_attrs(cab["client_id_ext"], cab["token"], offers)
        rows = rows_ozon(cab["id"], items, info, attrs)
    else:
        return 0, "неизвестный marketplace %s" % cab["marketplace"]
    path = os.path.join(out_dir(), "catalog_%s_%s.csv" % (cab["marketplace"], cab["id"]))
    write_csv(path, rows)
    replace_cache(cab["client_id"], cab["id"], rows)
    print("строк в CSV: %s, в кэше кабинета %s" % (len(rows), cab["id"]))
    return len(rows), ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cabinet", type=int)
    p.add_argument("--reindex", action="store_true")
    args = p.parse_args()
    if args.reindex:
        reindex_csvs()
        return
    if not args.cabinet:
        raise SystemExit("нужен --cabinet или --reindex")
    init_db()
    cab = get_cabinet(args.cabinet)
    if not cab:
        raise SystemExit("нет кабинета %s" % args.cabinet)
    n, err = pull_one(cab)
    if err:
        raise SystemExit(err)
    print("файл: catalog_%s_%s.csv, строк %s" % (cab["marketplace"], cab["id"], n))


if __name__ == "__main__":
    main()
