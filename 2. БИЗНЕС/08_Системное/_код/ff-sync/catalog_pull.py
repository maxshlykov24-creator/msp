"""Выгрузка номенклатуры кабинета в CSV. Печатает сырой JSON первой карточки."""

import argparse
import csv
import os

from db import get_cabinet, init_db, replace_cache
from net import OZON_BASE, req, ozon_headers, wb_headers

WB_CARDS = "https://content-api.wildberries.ru/content/v2/get/cards/list"
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
    "Привезено",
    "Литраж_л",
]


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


def rows_wb(cabinet_id, cards):
    rows = []
    for card in cards:
        title = card.get("title") or ""
        article = card.get("vendorCode") or ""
        nmid = card.get("nmID")
        sizes = card.get("sizes") or [{}]
        if not sizes:
            sizes = [{}]
        for size in sizes:
            skus = size.get("skus") or []
            chrt = size.get("chrtID")
            rows.append(
                {
                    "cabinet_id": cabinet_id,
                    "marketplace": "wb",
                    "ext_key": str(chrt or nmid or ""),
                    "ext_article": article,
                    "ext_barcode": skus[0] if skus else "",
                    "name": title,
                    "nmId": nmid or "",
                    "chrtId": chrt or "",
                    "offer_id": "",
                    "size": size.get("techSize") or "",
                    "Привезено": "",
                    "Литраж_л": "",
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


def rows_ozon(cabinet_id, items, info):
    rows = []
    for it in items:
        offer = it.get("offer_id") or ""
        det = info.get(offer) or {}
        barcodes = [b for b in (det.get("barcodes") or []) if b and not str(b).startswith("OZN")]
        if not barcodes:
            barcodes = det.get("barcodes") or []
        rows.append(
            {
                "cabinet_id": cabinet_id,
                "marketplace": "ozon",
                "ext_key": offer,
                "ext_article": offer,
                "ext_barcode": barcodes[0] if barcodes else "",
                "name": det.get("name") or "",
                "nmId": "",
                "chrtId": "",
                "offer_id": offer,
                "size": "",
                "Привезено": "",
                "Литраж_л": "",
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
        rows = rows_ozon(cab["id"], items, info)
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
