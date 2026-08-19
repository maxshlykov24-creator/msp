#!/usr/bin/env python3
"""
Сверка остатков: только ассортимент NEW → ищем пару в OLD → сравниваем qty по складам.

  python3 audit_stock.py
  python3 audit_stock.py --final
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    load_umap,
    make_session,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "audit_stock.log"
REPORT_JSON = SCRIPT_DIR / "audit_stock_report.json"
FINAL_JSON = SCRIPT_DIR / "audit_stock_final.json"

STOCK_TYPES = ("product", "variant")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def fetch_stock_by_store(s, store_uuid: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    store_href = f"{BASE}/entity/store/{store_uuid}"
    while True:
        r = api(
            s,
            "GET",
            f"{BASE}/report/stock/bystore",
            params={
                "limit": 1000,
                "offset": offset,
                "filter": f"store={store_href}",
            },
        )
        if not r.ok:
            log.warning("stock/bystore %s: %s %s", store_uuid[:8], r.status_code, r.text[:120])
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def assortment_uid(row: dict) -> tuple[str, str]:
    href = (row.get("meta") or {}).get("href", "")
    if "/entity/" in href:
        parts = href.split("/entity/")[-1].split("/")
        return parts[0], parts[1].split("?")[0]
    ass = row.get("assortment") or {}
    href2 = (ass.get("meta") or {}).get("href", "")
    if "/entity/" in href2:
        parts = href2.split("/entity/")[-1].split("/")
        return parts[0], parts[1].split("?")[0]
    return "", ""


def qty(row: dict, store_uuid: str) -> float:
    """MoySklad report/stock/bystore: qty в stockByStore[], не в корне row."""
    for item in row.get("stockByStore") or []:
        if uid((item.get("meta") or {}).get("href", "")) == store_uuid:
            return float(item.get("stock") or 0)
    return float(row.get("stock") or row.get("quantity") or 0)


def build_new_catalog(umap: dict, new_s) -> dict[tuple[str, str], dict]:
    """(type, new_uuid) -> {name, code, old_uuid} — только товары/модификации NEW с парой в OLD."""
    rev: dict[tuple[str, str], str] = {}
    for etype in STOCK_TYPES:
        for old_id, new_id in umap.get(etype, {}).items():
            rev[(etype, new_id)] = old_id

    catalog: dict[tuple[str, str], dict] = {}
    for etype in STOCK_TYPES:
        for row in get_all(new_s, etype):
            new_id = uid(row["meta"]["href"])
            old_id = rev.get((etype, new_id))
            if not old_id:
                continue
            catalog[(etype, new_id)] = {
                "name": row.get("name", ""),
                "code": row.get("code", ""),
                "old_uuid": old_id,
            }
    return catalog


def build_index(
    rows: list[dict],
    umap: dict,
    side: str,
    catalog: dict[tuple[str, str], dict],
    new_store_uuid: str,
    old_store_uuid: str,
) -> dict[tuple[str, str, str], float]:
    """(new_store, type, new_uuid) -> qty; только позиции из catalog (NEW-only)."""
    idx: dict[tuple[str, str, str], float] = {}
    allowed = set(catalog)
    qty_store = old_store_uuid if side == "old" else new_store_uuid

    for row in rows:
        et, au = assortment_uid(row)
        if side == "old":
            for t in STOCK_TYPES:
                mapped = umap.get(t, {}).get(au)
                if mapped and (t, mapped) in allowed:
                    et, au = t, mapped
                    break
            else:
                continue
        if (et, au) not in allowed:
            continue
        key = (new_store_uuid, et, au)
        idx[key] = idx.get(key, 0.0) + qty(row, qty_store)
    return idx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        report = {"mismatch_count": None, "status": "BLOCKED_OLD_API", "old_api_ok": False}
        out = FINAL_JSON if args.final else REPORT_JSON
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API 403 — audit stock невозможен")
        return

    store_map = umap.get("store", {})
    if not store_map:
        log.error("uuid_map store пуст")
        sys.exit(1)

    catalog = build_new_catalog(umap, new_s)
    log.info("NEW catalog (mapped to OLD): %s позиций", len(catalog))

    old_idx: dict[tuple[str, str, str], float] = {}
    new_idx: dict[tuple[str, str, str], float] = {}

    for old_store, new_store in store_map.items():
        log.info("Склад OLD %s → NEW %s", old_store[:8], new_store[:8])
        old_rows = fetch_stock_by_store(old_s, old_store)
        new_rows = fetch_stock_by_store(new_s, new_store)
        log.info("  rows old=%s new=%s", len(old_rows), len(new_rows))
        for k, v in build_index(old_rows, umap, "old", catalog, new_store, old_store).items():
            old_idx[k] = old_idx.get(k, 0.0) + v
        for k, v in build_index(new_rows, umap, "new", catalog, new_store, old_store).items():
            new_idx[k] = new_idx.get(k, 0.0) + v

    # Ключи: все NEW catalog × stores (даже если qty=0 с обеих сторон)
    all_keys: set[tuple[str, str, str]] = set()
    for new_store in store_map.values():
        for etype, new_uid in catalog:
            all_keys.add((new_store, etype, new_uid))

    mismatches = []
    for key in sorted(all_keys):
        qo = old_idx.get(key, 0.0)
        qn = new_idx.get(key, 0.0)
        if abs(qo - qn) > 1e-6:
            store_id, et, au = key
            meta = catalog[(et, au)]
            mismatches.append({
                "store": store_id,
                "assortment_type": et,
                "assortment_id": au,
                "name": meta["name"],
                "code": meta.get("code", ""),
                "old_assortment_id": meta["old_uuid"],
                "qty_old": qo,
                "qty_new": qn,
                "delta": qo - qn,
            })

    enter_lines = sum(1 for m in mismatches if m["delta"] > 0)
    loss_lines = sum(1 for m in mismatches if m["delta"] < 0)
    report = {
        "mode": "new_catalog_only",
        "catalog_size": len(catalog),
        "mismatch_count": len(mismatches),
        "enter_lines": enter_lines,
        "loss_lines": loss_lines,
        "stores": len(store_map),
        "mismatches": mismatches,
        "status": "OK" if not mismatches else "DIFF",
        "old_api_ok": True,
    }
    out = FINAL_JSON if args.final else REPORT_JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: mismatches=%s (enter_lines=%s loss_lines=%s) → %s",
        len(mismatches), enter_lines, loss_lines, out,
    )


if __name__ == "__main__":
    main()
