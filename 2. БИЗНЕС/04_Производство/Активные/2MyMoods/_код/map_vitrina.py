#!/usr/bin/env python3
"""Сопоставить 48 названий витрины с номенклатурой МойСклад. Не угадывает."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import lib

OUT = Path(__file__).with_name("витрина_map.json")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = s.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip(" ,")
    return s


def fetch_all(ms: lib.MS) -> list[dict]:
    items = []
    for path in ("/entity/product", "/entity/variant"):
        offset = 0
        for _ in range(80):
            for attempt in range(4):
                st, b = ms.req(path, {"limit": 100, "offset": offset, "filter": "archived=false"})
                if st == 0 or st >= 500:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            if not (200 <= st < 300):
                print(f"! {path} offset={offset} [{st}] {b}")
                break
            chunk = b.get("rows") or []
            items.extend(chunk)
            size = (b.get("meta") or {}).get("size") or 0
            offset += len(chunk)
            print(f"  {path} {offset}/{size}")
            if not chunk or offset >= size:
                break
    return items


def main() -> None:
    src = json.loads(Path(__file__).with_name("витрина_48.json").read_text(encoding="utf-8"))
    ms = lib.MS()
    print("снимаю номенклатуру…")
    catalog = fetch_all(ms)
    print("позиций", len(catalog))
    catalog_n = [(norm(row.get("name") or ""), row) for row in catalog]
    mapped = []
    unmatched = []
    for i, name in enumerate(src["items"], 1):
        nsrc = norm(name)
        hits = []
        for nr, row in catalog_n:
            if nsrc and (nsrc in nr or nr.startswith(nsrc)):
                hits.append({
                    "name": row.get("name"),
                    "id": row.get("id"),
                    "type": (row.get("meta") or {}).get("type"),
                    "code": row.get("code"),
                    "archived": row.get("archived"),
                })
        rec = {"n": i, "list_name": name, "hits": hits[:20], "hits_total": len(hits)}
        mapped.append(rec)
        if not hits:
            unmatched.append(name)
        print(f"{i:02d} hits={len(hits)}  {name[:70]}")

    payload = {
        "source": src["source"],
        "store": lib.STORE_MSK,
        "catalog": len(catalog),
        "mapped": mapped,
        "unmatched": unmatched,
        "matched": len(mapped) - len(unmatched),
        "total": len(mapped),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсовпало {payload['matched']}/{payload['total']}, без пары {len(unmatched)} → {OUT}")
    for u in unmatched:
        print(" -", u)


if __name__ == "__main__":
    main()
