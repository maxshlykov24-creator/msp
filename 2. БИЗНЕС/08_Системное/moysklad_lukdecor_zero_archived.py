#!/usr/bin/env python3
"""Обнулить остаток и резерв на архивных складах Лукдекор (admin@katrina10).

  export MOYSKLAD_TOKEN='...'
  python3 moysklad_lukdecor_zero_archived.py --probe
  python3 moysklad_lukdecor_zero_archived.py --store-name 'ЛОБНЯ курьерская доставка'
  python3 moysklad_lukdecor_zero_archived.py --all
  python3 moysklad_lukdecor_zero_archived.py --verify
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

BASE = "https://api.moysklad.ru/api/remap/1.2"
ORG_ID = "0c598780-b8c4-11ed-0a80-117f001e3184"
OZON_FBO_ID = "0c5b3847-b8c4-11ed-0a80-117f001e3186"
THROTTLE_SEC = 0.25
CHUNK = 800

SESS = requests.Session()


def token() -> str:
    value = os.environ.get("MOYSKLAD_TOKEN", "").strip()
    if not value:
        sys.exit("Нет MOYSKLAD_TOKEN в окружении.")
    return value


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Lognex-WebHook-Disable": "true",
    }


def request(method: str, path: str, **kw) -> Any:
    url = path if path.startswith("http") else BASE + path
    timeout = kw.pop("timeout", 120)
    retries = kw.pop("retries", 25)
    delay = 2.0
    for _ in range(retries):
        try:
            r = SESS.request(method, url, headers=headers(), timeout=timeout, **kw)
        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ):
            time.sleep(delay)
            delay = min(delay * 1.8, 90.0)
            continue
        if r.status_code in (429, 502, 503, 504):
            time.sleep(max(delay, 15.0 if r.status_code == 429 else 5.0))
            delay = min(delay * 1.8, 90.0)
            continue
        if r.status_code >= 400:
            try:
                err = r.json()
            except Exception:
                err = r.text
            raise RuntimeError(f"MoySklad {r.status_code} {method} {path}: {err}")
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()
    raise RuntimeError("Превышено число повторов при 429/timeout/обрыве связи")


def meta(entity: str, entity_id: str) -> dict[str, Any]:
    return {
        "meta": {
            "href": f"{BASE}/entity/{entity}/{entity_id}",
            "type": entity,
            "mediaType": "application/json",
        }
    }


def assortment_meta(href: str, typ: str) -> dict[str, Any]:
    clean = href.split("?")[0]
    return {"meta": {"href": clean, "type": typ, "mediaType": "application/json"}}


def entity_id(href: str) -> str:
    return urlparse(href.split("?")[0]).path.rstrip("/").split("/")[-1]


def paged(
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 120,
    retries: int = 10,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        q = dict(params or {})
        q["limit"] = 1000
        q["offset"] = offset
        data = request("GET", path, params=q, timeout=timeout, retries=retries)
        batch = data.get("rows") or []
        rows.extend(batch)
        size = (data.get("meta") or {}).get("size", len(rows))
        if offset + len(batch) >= size or not batch:
            return rows
        offset += len(batch)
        time.sleep(THROTTLE_SEC)


def archived_stores() -> list[dict[str, Any]]:
    return paged("/entity/store", {"filter": "archived=true"})


def store_stock(store_id: str, mode: str = "nonEmpty") -> list[dict[str, Any]]:
    href = f"{BASE}/entity/store/{store_id}"
    rows = paged("/report/stock/all", {"filter": f"store={href};stockMode={mode}"})
    if mode == "all":
        return [
            r
            for r in rows
            if float(r.get("stock") or 0) != 0 or float(r.get("reserve") or 0) != 0
        ]
    return rows


def moment() -> str:
    return (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")


def unpost_order(order_id: str) -> None:
    """Снять проведение: иначе МойСклад снова ставит резерв на архивном складе."""
    request(
        "PUT",
        f"/entity/customerorder/{order_id}",
        json={"applicable": False},
    )


def iter_hot_orders(store_id: str, assortment_href: str):
    store_href = f"{BASE}/entity/store/{store_id}"
    combined = store_id != OZON_FBO_ID
    offset = 0
    while True:
        params = {
            "filter": (
                f"assortment={assortment_href};store={store_href}"
                if combined
                else f"assortment={assortment_href}"
            ),
            "limit": 1000,
            "offset": offset,
        }
        try:
            data = request("GET", "/entity/customerorder", params=params, timeout=90, retries=2)
        except Exception:
            if combined:
                combined = False
                offset = 0
                continue
            raise
        batch = data.get("rows") or []
        size = (data.get("meta") or {}).get("size", offset + len(batch))
        for order in batch:
            ohref = ((order.get("store") or {}).get("meta") or {}).get("href", "")
            if entity_id(ohref) != store_id:
                continue
            if float(order.get("reservedSum") or 0) <= 0:
                continue
            yield order
        offset += len(batch)
        if offset >= size or not batch:
            return
        time.sleep(THROTTLE_SEC)


def unreserve_store(store: dict[str, Any], items: list[dict[str, Any]]) -> int:
    store_id = store["id"]
    cleared = 0
    seen_orders: set[str] = set()
    failed: set[str] = set()
    pass_n = 0
    while True:
        reserved_items = [
            i
            for i in items
            if float(i.get("reserve") or 0) != 0
            and ((i.get("meta") or {}).get("href") or "").split("?")[0] not in failed
        ]
        if not reserved_items:
            return cleared
        pass_n += 1
        print(f"    SKU с резервом: {len(reserved_items)} (проход {pass_n})", flush=True)
        item = reserved_items[0]
        href = ((item.get("meta") or {}).get("href") or "").split("?")[0]
        need = float(item.get("reserve") or 0)
        got = 0.0
        print(f"    {item.get('name')} резерв {need}", flush=True)
        for order in iter_hot_orders(store_id, href):
            oid = order["id"]
            if oid in seen_orders:
                continue
            unpost_order(oid)
            seen_orders.add(oid)
            cleared += 1
            print(f"      снят с проведения заказ {order.get('name')}", flush=True)
            time.sleep(THROTTLE_SEC)
            # после пачки перечитаем остаток этого SKU
            if cleared % 15 == 0:
                now = store_stock(store_id, "all")
                still = next(
                    (
                        float(i.get("reserve") or 0)
                        for i in now
                        if ((i.get("meta") or {}).get("href") or "").split("?")[0] == href
                    ),
                    0.0,
                )
                print(f"      сверка: резерв {item.get('name')} сейчас {still}", flush=True)
                if still < 1e-9:
                    got = need
                    break
        if got + 1e-9 < need:
            now = store_stock(store_id, "all")
            still = next(
                (
                    float(i.get("reserve") or 0)
                    for i in now
                    if ((i.get("meta") or {}).get("href") or "").split("?")[0] == href
                ),
                0.0,
            )
            if still < 1e-9:
                got = need
            else:
                print(f"    не нашёл весь резерв {item.get('name')}: осталось {still}", flush=True)
                failed.add(href)
        time.sleep(0.8)
        items = store_stock(store_id, "all")


def chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def post_adjust(kind: str, store: dict[str, Any], positions: list[dict[str, Any]]) -> list[str]:
    ids = []
    for part in chunks(positions, CHUNK):
        body = {
            "moment": moment(),
            "applicable": True,
            "organization": meta("organization", ORG_ID),
            "store": meta("store", store["id"]),
            "description": f"Обнуление архивного склада: {store.get('name')}",
            "positions": part,
        }
        created = request("POST", f"/entity/{kind}", json=body)
        ids.append(created.get("id") or created.get("name") or "?")
        time.sleep(THROTTLE_SEC)
    return ids


def adjust_stock(store: dict[str, Any], items: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    loss_pos = []
    enter_pos = []
    for item in items:
        stock = float(item.get("stock") or 0)
        reserve = float(item.get("reserve") or 0)
        free = stock - reserve if stock > 0 else stock
        if abs(free) < 1e-9:
            continue
        href = (item.get("meta") or {}).get("href") or ""
        typ = (item.get("meta") or {}).get("type") or "product"
        pos = {"assortment": assortment_meta(href, typ), "quantity": abs(free)}
        if free > 0:
            loss_pos.append(pos)
        else:
            enter_pos.append(pos)
    docs: list[str] = []
    if loss_pos:
        print(f"    списываю {len(loss_pos)} поз", flush=True)
        docs.extend(post_adjust("loss", store, loss_pos))
    if enter_pos:
        print(f"    оприходую {len(enter_pos)} поз", flush=True)
        docs.extend(post_adjust("enter", store, enter_pos))
    return len(loss_pos), len(enter_pos), docs


def zero_store(store: dict[str, Any], mode: str = "nonEmpty") -> dict[str, Any]:
    items = store_stock(store["id"], mode)
    result = {
        "name": store.get("name"),
        "id": store["id"],
        "before": len(items),
        "unreserved": 0,
        "loss": 0,
        "enter": 0,
        "docs": [],
        "after": None,
    }
    if not items:
        result["after"] = 0
        return result
    result["unreserved"] = unreserve_store(store, items)
    time.sleep(0.8)
    items = store_stock(store["id"], "all")
    loss_n, enter_n, docs = adjust_stock(store, items)
    result["loss"] += loss_n
    result["enter"] += enter_n
    result["docs"].extend(docs)
    time.sleep(0.8)
    result["after"] = len(store_stock(store["id"], "all"))
    return result


def print_result(res: dict[str, Any]) -> None:
    print(
        f"{res['name']}: было {res['before']} поз, "
        f"резерв снят {res['unreserved']}, "
        f"списание {res['loss']}, оприходование {res['enter']}, "
        f"осталось {res['after']}",
        flush=True,
    )


def cmd_probe() -> None:
    stores = archived_stores()
    print(f"архивных складов: {len(stores)}")
    for store in stores:
        items = store_stock(store["id"])
        if not items:
            continue
        stock = sum(float(i.get("stock") or 0) for i in items)
        reserve = sum(float(i.get("reserve") or 0) for i in items)
        print(f"  {store.get('name')}: поз={len(items)} stock={stock} reserve={reserve}")
        time.sleep(THROTTLE_SEC)


def cmd_verify() -> None:
    stores = archived_stores()
    leftover = []
    for store in stores:
        items = store_stock(store["id"], "all")
        if items:
            leftover.append((store.get("name"), len(items)))
            stock = sum(float(i.get("stock") or 0) for i in items)
            reserve = sum(float(i.get("reserve") or 0) for i in items)
            print(f"ОСТАТОК {store.get('name')}: поз={len(items)} stock={stock} reserve={reserve}")
        time.sleep(THROTTLE_SEC)
    if not leftover:
        print(f"Все {len(stores)} архивных складов на нуле: остаток и резерв.")
    else:
        print(f"с остатком или резервом: {len(leftover)}")


def pick_stores(name: str | None) -> list[dict[str, Any]]:
    stores = archived_stores()
    if not name:
        return stores
    found = [s for s in stores if s.get("name") == name]
    if not found:
        sys.exit(f"Склад не найден среди архивных: {name}")
    return found


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--probe", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--all", action="store_true")
    g.add_argument("--reserves", action="store_true")
    g.add_argument("--store-name")
    args = p.parse_args()
    if args.probe:
        cmd_probe()
        return
    if args.verify:
        cmd_verify()
        return
    print("Старт обнуления архивных складов", flush=True)
    mode = "all" if (args.reserves or args.store_name) else "nonEmpty"
    targets = pick_stores(args.store_name)
    for store in targets:
        items = store_stock(store["id"], mode)
        if not items and args.store_name is None:
            continue
        print(f"\n→ {store.get('name')} позиций {len(items)}", flush=True)
        print_result(zero_store(store, mode))


if __name__ == "__main__":
    main()
