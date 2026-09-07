#!/usr/bin/env python3
"""Ячейки «НОВЫЙ СКЛАД» Лукдекор (кабинет admin@katrina10).

Имя: {стеллаж}-{ряд}-{этаж}, barcode = name, зона «Склад хранения».

  export MOYSKLAD_TOKEN='...'
  python3 moysklad_lukdecor_slots.py --test     # зона + A-1-1, E-1-1, F-1-1
  python3 moysklad_lukdecor_slots.py --dry-run  # список 1140 без записи
  python3 moysklad_lukdecor_slots.py --full     # остальные до 1140 (после проверки)
  python3 moysklad_lukdecor_slots.py --verify   # сверка count и разрезов
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from typing import Any

import requests

BASE = "https://api.moysklad.ru/api/remap/1.2"
STORE_ID = "11958dc0-8cdd-11f1-0a80-1157000efde5"
ZONE_NAME = "Склад хранения"
TEST_NAMES = ("A-1-1", "E-1-1", "F-1-1")
RACK_FLOORS = {"A": 4, "B": 4, "C": 4, "D": 4, "E": 2, "F": 1}
ROWS = 60
THROTTLE_SEC = 0.25

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
    }


def request(method: str, path: str, **kw) -> Any:
    url = path if path.startswith("http") else BASE + path
    delay = 1.0
    for _ in range(10):
        r = SESS.request(method, url, headers=headers(), timeout=60, **kw)
        if r.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 1.6, 30.0)
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
    raise RuntimeError("Превышено число повторов при 429")


def planned_names() -> list[str]:
    names: list[str] = []
    for rack, floors in RACK_FLOORS.items():
        for row in range(1, ROWS + 1):
            for floor in range(1, floors + 1):
                names.append(f"{rack}-{row}-{floor}")
    return names


def list_all(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = request("GET", f"{path}?limit=1000&offset={offset}")
        batch = data.get("rows") or []
        rows.extend(batch)
        size = (data.get("meta") or {}).get("size", len(rows))
        if offset + len(batch) >= size or not batch:
            return rows
        offset += len(batch)
        time.sleep(THROTTLE_SEC)


def ensure_zone() -> dict[str, Any]:
    zones = list_all(f"/entity/store/{STORE_ID}/zones")
    for zone in zones:
        if zone.get("name") == ZONE_NAME:
            return zone
    created = request("POST", f"/entity/store/{STORE_ID}/zones", json={"name": ZONE_NAME})
    if isinstance(created, list):
        created = created[0]
    return created


def zone_meta(zone: dict[str, Any]) -> dict[str, Any]:
    meta = zone.get("meta")
    if not meta:
        raise RuntimeError(f"У зоны нет meta: {zone}")
    return {"meta": {k: meta[k] for k in ("href", "type", "mediaType") if k in meta}}


def existing_names() -> dict[str, dict[str, Any]]:
    slots = list_all(f"/entity/store/{STORE_ID}/slots")
    return {s.get("name"): s for s in slots if s.get("name")}


def create_slot(name: str, zone: dict[str, Any]) -> dict[str, Any]:
    body = {"name": name, "barcode": name, "zone": zone_meta(zone)}
    created = request("POST", f"/entity/store/{STORE_ID}/slots", json=body)
    if isinstance(created, list):
        created = created[0]
    time.sleep(THROTTLE_SEC)
    return created


def create_missing(names: list[str], zone: dict[str, Any]) -> tuple[int, int]:
    have = existing_names()
    created = skipped = 0
    todo = [n for n in names if n not in have]
    skipped = len(names) - len(todo)
    for name in todo:
        create_slot(name, zone)
        created += 1
        have[name] = {}
        if created % 50 == 0 or created == len(todo):
            print(f"  создано {created}/{len(todo)}", flush=True)
    return created, skipped


def slot_brief(slot: dict[str, Any], zone_id: str, zone_name: str) -> dict[str, Any]:
    href = ((slot.get("zone") or {}).get("meta") or {}).get("href", "")
    zid = href.rstrip("/").split("/")[-1] if href else ""
    return {
        "name": slot.get("name"),
        "barcode": slot.get("barcode"),
        "zone": zone_name if zid == zone_id else (zid or "нет"),
        "id": slot.get("id"),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'имя':<10} {'штрихкод':<10} {'зона':<20} id")
    for row in rows:
        print(f"{row['name']:<10} {row['barcode']:<10} {row['zone']:<20} {row['id']}")


def cmd_test() -> None:
    zone = ensure_zone()
    created, skipped = create_missing(list(TEST_NAMES), zone)
    have = existing_names()
    rows = [slot_brief(have[n], zone["id"], zone["name"]) for n in TEST_NAMES if n in have]
    print(f"зона: {zone.get('name')} id={zone.get('id')}")
    print(f"создано: {created}, уже были: {skipped}")
    print_table(rows)
    missing = [n for n in TEST_NAMES if n not in have]
    if missing:
        raise RuntimeError(f"После теста нет ячеек: {missing}")


def cmd_dry_run() -> None:
    names = planned_names()
    have = existing_names()
    todo = [n for n in names if n not in have]
    print(f"план: {len(names)}, уже есть: {len(have)}, к созданию: {len(todo)}")
    print("тест:", ", ".join(TEST_NAMES))
    print("первые 8:", ", ".join(names[:8]))
    print("хвост:", ", ".join(names[-4:]))


def cmd_full() -> None:
    zone = ensure_zone()
    names = planned_names()
    created, skipped = create_missing(names, zone)
    print(f"зона: {zone.get('name')} id={zone.get('id')}")
    print(f"создано: {created}, пропущено: {skipped}")
    cmd_verify()


def cmd_verify() -> None:
    zone = None
    for item in list_all(f"/entity/store/{STORE_ID}/zones"):
        if item.get("name") == ZONE_NAME:
            zone = item
            break
    slots = list_all(f"/entity/store/{STORE_ID}/slots")
    planned = set(planned_names())
    names = [s.get("name") for s in slots]
    by_rack: Counter[str] = Counter()
    extra = []
    for name in names:
        if name in planned:
            by_rack[name.split("-", 1)[0]] += 1
        else:
            extra.append(name)
    missing = sorted(planned - set(names))
    print(f"всего ячеек: {len(slots)}, план: {len(planned)}")
    print("по стеллажам:", dict(sorted(by_rack.items())))
    print("ожидалось:", {rack: ROWS * floors for rack, floors in RACK_FLOORS.items()})
    print("нет в плане:", extra[:20], f"(ещё {max(0, len(extra)-20)})" if len(extra) > 20 else "")
    print("дырки плана:", missing[:20], f"(ещё {max(0, len(missing)-20)})" if len(missing) > 20 else "")
    if zone:
        print(f"зона: {zone.get('name')} id={zone.get('id')}")


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--full", action="store_true")
    g.add_argument("--verify", action="store_true")
    args = p.parse_args()
    if args.test:
        cmd_test()
    elif args.dry_run:
        cmd_dry_run()
    elif args.full:
        cmd_full()
    else:
        cmd_verify()


if __name__ == "__main__":
    main()
