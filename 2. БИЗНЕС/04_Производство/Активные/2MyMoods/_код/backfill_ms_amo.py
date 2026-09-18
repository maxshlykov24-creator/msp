#!/usr/bin/env python3
"""Заказы МС без ссылки на сделку amo. Отчёт, сделки не создаёт без --apply (и всё равно не создаёт пачкой)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lib

OUT = Path(__file__).with_name("backfill_ms_amo.json")


def amo_link(order: dict) -> str:
    for a in order.get("attributes") or []:
        if a.get("id") == lib.MS_ATTR_AMO_LINK:
            return str(a.get("value") or "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ms = lib.MS()
    orders = ms.rows("/entity/customerorder", {"limit": 100, "order": "moment,desc"}, pages=30)
    missing = []
    with_link = 0
    for o in orders:
        link = amo_link(o)
        if link:
            with_link += 1
            continue
        missing.append({"name": o.get("name"), "id": o.get("id"), "moment": o.get("moment"), "sum": o.get("sum")})
    payload = {
        "scanned": len(orders),
        "with_amo_link": with_link,
        "without_amo_link": len(missing),
        "sample": missing[:40],
        "created": 0,
        "note": "Сайт пишет в МС через WooMS, виджет AMGBP — в amo. Массово плодить сделки в старую воронку нельзя. После переключения — отдельное «да» на хвост.",
    }
    if args.apply:
        payload["note"] += " --apply игнорирую: создание тысяч сделок без «да» на переключение запрещено планом."
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"скан {len(orders)}, со ссылкой {with_link}, без {len(missing)} → {OUT}")


if __name__ == "__main__":
    main()
