#!/usr/bin/env python3
"""Ревизия Честный знак: GTIN и поля маркировки в номенклатуре. Ничего не пишет в МС."""

from __future__ import annotations

import json
import re
from pathlib import Path

import lib

OUT = Path(__file__).with_name("marking_audit.json")


def looks_gtin(v: str) -> bool:
    s = re.sub(r"\D", "", v or "")
    return len(s) in (8, 12, 13, 14)


def main() -> None:
    ms = lib.MS()
    st, meta = ms.req("/entity/product/metadata")
    attrs = meta.get("attributes") or []
    print("атрибуты товара", len(attrs), f"[{st}]")
    interesting = []
    for a in attrs:
        if not isinstance(a, dict):
            continue
        n = (a.get("name") or "").lower()
        if any(k in n for k in ("gtin", "gtin", "маркир", "честн", "киз", "ean", "штрих")):
            interesting.append(a)
            print(" ", a.get("name"), a.get("id"), a.get("type"))
    products = ms.rows("/entity/product", {"limit": 100, "filter": "archived=false"}, pages=15)
    with_gtin = 0
    without = 0
    samples_missing = []
    gtin_ids = {a["id"] for a in interesting if "gtin" in (a.get("name") or "").lower() or "ean" in (a.get("name") or "").lower()}
    for p in products:
        vals = []
        for a in p.get("attributes") or []:
            if a.get("id") in gtin_ids or looks_gtin(str(a.get("value") or "")):
                vals.append(str(a.get("value")))
        if p.get("article") and looks_gtin(str(p.get("article"))):
            vals.append(str(p.get("article")))
        uin = p.get("uom")  # not gtin
        if vals:
            with_gtin += 1
        else:
            without += 1
            if len(samples_missing) < 15:
                samples_missing.append({"name": p.get("name"), "id": p.get("id"), "code": p.get("code")})
    payload = {
        "products_scanned": len(products),
        "with_gtin_guess": with_gtin,
        "without_gtin_guess": without,
        "marking_attrs": [{"name": a.get("name"), "id": a.get("id"), "type": a.get("type")} for a in interesting],
        "missing_samples": samples_missing,
        "crypto_pro": "На компьютеры клиента из этого контура не заходил. Обновление КриптоПро — инструкция, не удалённый патч.",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("products_scanned", "with_gtin_guess", "without_gtin_guess")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
