#!/usr/bin/env python3
"""Город заказа сайта в допполе Город Моего Склада.

По умолчанию только сверка. --pilot пишет три старых заказа.
--apply пишет все пустые поля. Каждый PUT отключает вебхуки склада.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib

ATTR_CITY = "4e029576-c2f6-11f1-0a80-0292004dbac4"
ATTR_HREF = f"{lib.MS_BASE}/entity/customerorder/metadata/attributes/{ATTR_CITY}"
NO_HOOK = {"X-Lognex-WebHook-Disable": "true"}
REPORT = Path("/tmp/2my-city-plan.json")
ADMIN = ("россия", "russia", "область", "край", "республика", "округ", "район")
ALIAS = {
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "мск": "Москва",
    "москва": "Москва",
}


def clean_city(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    same = re.fullmatch(r"(.+?)\s*\(\1\)", text, flags=re.IGNORECASE)
    if same:
        text = same.group(1).strip()
    parts = []
    for part in text.split(","):
        part = re.sub(r"^(г\.|город)\s+", "", part.strip(), flags=re.IGNORECASE)
        low = part.lower().replace("ё", "е")
        if not part or any(word in low for word in ADMIN):
            continue
        parts.append(part)
    if not parts:
        return ""
    city = parts[0]
    mapped = ALIAS.get(city.lower().replace("ё", "е"))
    city = mapped or city[:1].upper() + city[1:]
    letters = re.findall(r"[А-Яа-яЁё]", city)
    if len(letters) < 2 or len(city) > 255:
        return ""
    return city


def source_city(billing: str, shipping: str) -> tuple[str, str]:
    ship = (shipping or "").strip()
    bill = (billing or "").strip()
    raw = ship or bill
    return raw, clean_city(raw)


def wc_orders() -> list[dict]:
    env = lib.load_env()
    site = env["WC_SITE"].rstrip("/")
    token = base64.b64encode(f"{env['WC_CK']}:{env['WC_CS']}".encode()).decode()
    out = []
    page = 1
    while True:
        url = (
            site
            + "/wp-json/wc/v3/orders?per_page=100&page="
            + str(page)
            + "&orderby=id&order=asc"
            + "&_fields=id,number,status,date_created,date_modified,billing,shipping,meta_data"
        )
        req = urllib.request.Request(url, headers={
            "Authorization": f"Basic {token}",
            "User-Agent": "MSProduct-2MY/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=90) as resp:
            chunk = json.loads(resp.read())
        if not chunk:
            break
        for order in chunk:
            wooms = ""
            for meta in order.get("meta_data") or []:
                if meta.get("key") == "wooms_id" and meta.get("value"):
                    wooms = str(meta.get("value"))
            billing = (order.get("billing") or {}).get("city") or ""
            shipping = (order.get("shipping") or {}).get("city") or ""
            raw, city = source_city(billing, shipping)
            out.append({
                "id": order.get("id"),
                "number": str(order.get("number") or ""),
                "status": order.get("status") or "",
                "created": (order.get("date_created") or "")[:10],
                "modified": order.get("date_modified") or "",
                "wooms": wooms,
                "raw": raw,
                "city": city,
                "differ": bool(billing.strip() and shipping.strip() and billing.strip() != shipping.strip()),
            })
        print(f"сайт {len(out)}", flush=True)
        if len(chunk) < 100:
            break
        page += 1
    return out


def ms_index() -> tuple[dict, dict]:
    ms = lib.MS()
    rows = ms.rows("/entity/customerorder", {"limit": 100})
    print(f"склад {len(rows)}", flush=True)
    by_id = {}
    by_post = {}
    for row in rows:
        attrs = {}
        for attr in row.get("attributes") or []:
            attrs[attr.get("id")] = attr.get("value")
        item = {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "city": str(attrs.get(ATTR_CITY) or "").strip(),
            "amo": str(attrs.get(lib.MS_ATTR_AMO_LINK) or ""),
            "attr_ids": sorted(k for k in attrs if k),
        }
        by_id[item["id"]] = item
        desc = row.get("description") or ""
        for post in re.findall(r"post=(\d+)", desc):
            by_post.setdefault(post, []).append(item["id"])
    return by_id, by_post


def pair(order: dict, by_id: dict, by_post: dict) -> tuple[str, str]:
    wooms = order["wooms"]
    if wooms and wooms in by_id:
        return wooms, "wooms"
    posts = by_post.get(str(order["id"]), [])
    if len(posts) == 1:
        return posts[0], "post"
    return "", ""


def build() -> dict:
    orders = wc_orders()
    by_id, by_post = ms_index()
    linked = [(order, *pair(order, by_id, by_post)) for order in orders]
    taken = Counter(ms_id for _order, ms_id, _how in linked if ms_id)
    rows = []
    for order, ms_id, how in linked:
        if not ms_id:
            rows.append({**order, "ms": "", "how": "", "action": "нет пары"})
            continue
        if taken[ms_id] > 1:
            rows.append({**order, "ms": ms_id, "how": how, "action": "два сайта на один склад"})
            continue
        current = by_id[ms_id]["city"]
        if not order["city"]:
            action = "пустой город"
        elif current and current != order["city"]:
            action = "уже заполнено иначе"
        elif current == order["city"]:
            action = "уже так"
        else:
            action = "записать"
        rows.append({
            **order, "ms": ms_id, "how": how, "action": action,
            "ms_name": by_id[ms_id]["name"], "amo": by_id[ms_id]["amo"],
        })
    counts = Counter(row["action"] for row in rows)
    samples = [row for row in rows if row["action"] == "записать" and row["raw"] != row["city"]][:12]
    payload = {
        "site": len(orders),
        "ms": len(by_id),
        "counts": dict(counts),
        "differ": sum(1 for row in rows if row.get("differ")),
        "samples": [{k: row.get(k) for k in ("number", "raw", "city", "ms_name")} for row in samples],
        "rows": rows,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("сверка", payload["counts"])
    print("расхождения доставки и покупателя", payload["differ"])
    print("примеры чистки", payload["samples"])
    return payload


def put_city(ms: lib.MS, order_id: str, city: str) -> int:
    status, _body = ms.send("PUT", f"/entity/customerorder/{order_id}", {"attributes": [{
        "meta": {"href": ATTR_HREF, "type": "attributemetadata", "mediaType": "application/json"},
        "value": city,
    }]}, headers=NO_HOOK)
    return status


def wc_one(order_id: int) -> dict:
    env = lib.load_env()
    token = base64.b64encode(f"{env['WC_CK']}:{env['WC_CS']}".encode()).decode()
    url = env["WC_SITE"].rstrip("/") + f"/wp-json/wc/v3/orders/{order_id}?_fields=id,status,date_modified"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "User-Agent": "MSProduct-2MY/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read())


def amo_stamp(link: str) -> str:
    match = re.search(r"(\d+)\s*$", link or "")
    if not match:
        return ""
    amo = lib.Amo()
    status, body = amo.req("GET", f"/api/v4/leads/{match.group(1)}")
    if not (200 <= status < 300):
        return f"amo {status}"
    return str(body.get("updated_at") or "")


def pilot(payload: dict) -> None:
    ready = [
        row for row in payload["rows"]
        if row["action"] == "записать" and row["status"] == "completed" and row["created"] < "2024-01-01"
    ]
    ready.sort(key=lambda row: row["created"])
    chosen = ready[:3]
    if len(chosen) < 3:
        raise SystemExit(f"для пилота мало старых заказов: {len(chosen)}")
    ms = lib.MS()
    for row in chosen:
        before_site = wc_one(row["id"])
        before_amo = amo_stamp(row.get("amo") or "")
        status = put_city(ms, row["ms"], row["city"])
        after_site = wc_one(row["id"])
        after_amo = amo_stamp(row.get("amo") or "")
        check = ms.req(f"/entity/customerorder/{row['ms']}")
        got = ""
        if check[0] == 200:
            for attr in check[1].get("attributes") or []:
                if attr.get("id") == ATTR_CITY:
                    got = attr.get("value")
        same = before_site.get("date_modified") == after_site.get("date_modified") and before_site.get("status") == after_site.get("status")
        amo_same = before_amo == after_amo
        print(
            row["number"], row["city"], "put", status, "в складе", got,
            "сайт тот же", same, "amo тот же", amo_same or "ссылки нет" if not before_amo else amo_same,
            flush=True,
        )
        if not same or (before_amo and not amo_same) or got != row["city"] or not (200 <= status < 300):
            raise SystemExit("пилот остановил запись: сайт, amo или поле не сошлись")


def apply_all(payload: dict) -> None:
    ms = lib.MS()
    done = 0
    failed = 0
    for row in payload["rows"]:
        if row["action"] != "записать":
            continue
        status = put_city(ms, row["ms"], row["city"])
        if 200 <= status < 300:
            done += 1
        else:
            failed += 1
            print("ошибка", row["number"], status, flush=True)
        if done and done % 200 == 0:
            print(f"записано {done}", flush=True)
    print(f"готово {done}, ошибок {failed}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.pilot or args.apply:
        payload = json.loads(REPORT.read_text(encoding="utf-8"))
    else:
        payload = build()
    if args.pilot:
        pilot(payload)
    if args.apply:
        apply_all(payload)


if __name__ == "__main__":
    main()
