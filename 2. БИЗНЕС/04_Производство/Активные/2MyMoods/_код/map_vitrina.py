#!/usr/bin/env python3
"""Сопоставить 48 названий витрины с номенклатурой МойСклад. Не угадывает."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import lib

OUT = Path(__file__).with_name("витрина_map.json")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = s.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip(" ,")
    return s


def main() -> None:
    src = json.loads(Path(__file__).with_name("витрина_48.json").read_text(encoding="utf-8"))
    ms = lib.MS()
    mapped = []
    unmatched = []
    for i, name in enumerate(src["items"], 1):
        needle = name.split(",")[0].strip()
        st, b = ms.req("/entity/assortment", {"search": needle, "limit": 25})
        rows = b.get("rows") or [] if 200 <= st < 300 else []
        nsrc = norm(name)
        hits = []
        for row in rows:
            rname = row.get("name") or ""
            nr = norm(rname)
            if nsrc in nr or nr in nsrc or norm(needle) in nr:
                hits.append({
                    "name": rname,
                    "id": row.get("id"),
                    "type": (row.get("meta") or {}).get("type"),
                    "code": row.get("code"),
                    "archived": row.get("archived"),
                })
        rec = {"n": i, "list_name": name, "hits": hits}
        mapped.append(rec)
        if not hits:
            unmatched.append(name)
        print(f"{i:02d} hits={len(hits)}  {name[:70]}")

    payload = {
        "source": src["source"],
        "store": lib.STORE_MSK,
        "mapped": mapped,
        "unmatched": unmatched,
        "matched": len(mapped) - len(unmatched),
        "total": len(mapped),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсовпало {payload['matched']}/{payload['total']}, без пары {len(unmatched)} → {OUT}")
    if unmatched:
        print("без пары:")
        for u in unmatched:
            print(" -", u)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
