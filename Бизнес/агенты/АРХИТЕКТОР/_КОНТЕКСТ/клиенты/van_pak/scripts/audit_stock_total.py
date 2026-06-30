#!/usr/bin/env python3
"""
Полная сверка остатков OLD ↔ NEW: все корзины, контрольные суммы, топ diff.

  PYTHONPATH=. python3 audit_stock_total.py
  PYTHONPATH=. python3 audit_stock_total.py --final
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
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

LOG_FILE = SCRIPT_DIR / "audit_stock_total.log"
REPORT_JSON = SCRIPT_DIR / "audit_stock_total_report.json"
FINAL_JSON = SCRIPT_DIR / "audit_stock_total_final.json"
CSV_FILE = SCRIPT_DIR / "audit_stock_total_diff.csv"

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
            params={"limit": 1000, "offset": offset, "filter": f"store={store_href}"},
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


def fetch_stock_all(s) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        r = api(s, "GET", f"{BASE}/report/stock/all", params={"limit": 1000, "offset": offset})
        if not r.ok:
            log.warning("stock/all: %s %s", r.status_code, r.text[:120])
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


def qty_bystore(row: dict, store_uuid: str) -> float:
    for item in row.get("stockByStore") or []:
        if uid((item.get("meta") or {}).get("href", "")) == store_uuid:
            return float(item.get("stock") or 0)
    return float(row.get("stock") or row.get("quantity") or 0)


def qty_all(row: dict) -> float:
    return float(row.get("stock") or row.get("quantity") or 0)


def build_assortment_meta(s, side: str) -> dict[tuple[str, str], dict]:
    meta: dict[tuple[str, str], dict] = {}
    for et in STOCK_TYPES:
        for row in get_all(s, et):
            au = uid(row["meta"]["href"])
            meta[(et, au)] = {
                "name": row.get("name", ""),
                "code": (row.get("code") or "").strip(),
                "archived": row.get("archived", False),
                "side": side,
            }
    return meta


def entity_exists(s, et: str, au: str) -> bool:
    r = api(s, "GET", f"{BASE}/entity/{et}/{au}")
    return r.status_code == 200


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true")
    ap.add_argument(
        "--moment",
        default=(datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
        help="Точка сверки (для отчёта)",
    )
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    if not old_api_available(old_s):
        report = {"status": "BLOCKED_OLD_API", "old_api_ok": False}
        out = FINAL_JSON if args.final else REPORT_JSON
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    store_map: dict[str, str] = umap.get("store", {})
    if not store_map:
        log.error("uuid_map store пуст")
        sys.exit(1)

    log.info("Загрузка метаданных ассортимента...")
    old_meta = build_assortment_meta(old_s, "old")
    new_meta = build_assortment_meta(new_s, "new")

    # old -> new mapping
    old_to_new: dict[tuple[str, str], str] = {}
    new_to_old: dict[tuple[str, str], str] = {}
    bad_mapping: list[dict] = []

    for et in STOCK_TYPES:
        for old_id, new_id in umap.get(et, {}).items():
            old_to_new[(et, old_id)] = new_id
            new_to_old[(et, new_id)] = old_id
            if not entity_exists(new_s, et, new_id):
                bad_mapping.append({
                    "bucket": "bad_mapping",
                    "type": et,
                    "old_uuid": old_id,
                    "new_uuid": new_id,
                    "code": old_meta.get((et, old_id), {}).get("code", ""),
                    "name": old_meta.get((et, old_id), {}).get("name", ""),
                    "reason": "new_entity_404",
                })

    store_names_old = {uid(s["meta"]["href"]): s.get("name", "") for s in get_all(old_s, "store")}
    store_names_new = {uid(s["meta"]["href"]): s.get("name", "") for s in get_all(new_s, "store")}

    # Per-store stock indices: (new_store, et, new_uuid) -> qty
    old_qty: dict[tuple[str, str, str], float] = defaultdict(float)
    new_qty: dict[tuple[str, str, str], float] = defaultdict(float)

    # Orphan buckets keyed by side uuid
    old_only_raw: dict[tuple[str, str, str], float] = defaultdict(float)  # (old_store, et, old_uuid)
    new_only_raw: dict[tuple[str, str, str], float] = defaultdict(float)  # (new_store, et, new_uuid)

    total_old_bystore = 0.0
    total_new_bystore = 0.0

    for old_store, new_store in store_map.items():
        log.info("Склад %s → %s", store_names_old.get(old_store, old_store[:8]), store_names_new.get(new_store, new_store[:8]))
        old_rows = fetch_stock_by_store(old_s, old_store)
        new_rows = fetch_stock_by_store(new_s, new_store)

        seen_old_mapped: set[tuple[str, str]] = set()
        seen_new_mapped: set[tuple[str, str]] = set()

        for row in old_rows:
            et, au = assortment_uid(row)
            if et not in STOCK_TYPES:
                continue
            q = qty_bystore(row, old_store)
            if abs(q) < 1e-9:
                continue
            total_old_bystore += q
            new_id = old_to_new.get((et, au))
            if new_id and entity_exists(new_s, et, new_id):
                key = (new_store, et, new_id)
                old_qty[key] += q
                seen_old_mapped.add((et, au))
            else:
                old_only_raw[(old_store, et, au)] += q

        for row in new_rows:
            et, au = assortment_uid(row)
            if et not in STOCK_TYPES:
                continue
            q = qty_bystore(row, new_store)
            if abs(q) < 1e-9:
                continue
            total_new_bystore += q
            old_id = new_to_old.get((et, au))
            if old_id and entity_exists(old_s, et, old_id):
                key = (new_store, et, au)
                new_qty[key] += q
                seen_new_mapped.add((et, au))
            else:
                new_only_raw[(new_store, et, au)] += q

    # Build unified diff lines
    mapped_both: list[dict] = []
    all_mapped_keys = set(old_qty.keys()) | set(new_qty.keys())

    for key in sorted(all_mapped_keys):
        new_store, et, new_id = key
        qo = old_qty.get(key, 0.0)
        qn = new_qty.get(key, 0.0)
        if abs(qo - qn) < 1e-6:
            continue
        old_id = new_to_old.get((et, new_id), "")
        nm = new_meta.get((et, new_id), {})
        mapped_both.append({
            "bucket": "mapped_both",
            "store": new_store,
            "store_name": store_names_new.get(new_store, ""),
            "assortment_type": et,
            "assortment_id": new_id,
            "old_assortment_id": old_id,
            "code": nm.get("code", ""),
            "name": nm.get("name", ""),
            "qty_old": qo,
            "qty_new": qn,
            "delta": qo - qn,
        })

    old_only: list[dict] = []
    for (old_store, et, old_id), q in sorted(old_only_raw.items(), key=lambda x: -abs(x[1])):
        om = old_meta.get((et, old_id), {})
        old_only.append({
            "bucket": "old_only",
            "old_store": old_store,
            "old_store_name": store_names_old.get(old_store, ""),
            "assortment_type": et,
            "old_assortment_id": old_id,
            "code": om.get("code", ""),
            "name": om.get("name", ""),
            "qty_old": q,
            "qty_new": 0.0,
            "delta": q,
            "reconcilable": False,
            "reason": "no_new_assortment",
        })

    new_only: list[dict] = []
    for (new_store, et, new_id), q in sorted(new_only_raw.items(), key=lambda x: -abs(x[1])):
        nm = new_meta.get((et, new_id), {})
        new_only.append({
            "bucket": "new_only",
            "store": new_store,
            "store_name": store_names_new.get(new_store, ""),
            "assortment_type": et,
            "assortment_id": new_id,
            "code": nm.get("code", ""),
            "name": nm.get("name", ""),
            "qty_old": 0.0,
            "qty_new": q,
            "delta": -q,
            "reconcilable": True,
            "reason": "no_old_mapping",
        })

    # Control sums from stock/all
    old_all_total = sum(qty_all(r) for r in fetch_stock_all(old_s))
    new_all_total = sum(qty_all(r) for r in fetch_stock_all(new_s))

    # Reconcilable lines = mapped_both + new_only
    reconcilable = [x for x in mapped_both + new_only if abs(x.get("delta", 0)) > 1e-6]
    enter_lines = [x for x in reconcilable if x["delta"] > 0]
    loss_lines = [x for x in reconcilable if x["delta"] < 0]

    sum_enter = sum(x["delta"] for x in enter_lines)
    sum_loss = sum(abs(x["delta"]) for x in loss_lines)

    all_diffs = mapped_both + old_only + new_only
    top50 = sorted(all_diffs, key=lambda x: abs(x.get("delta", 0)), reverse=True)[:50]

    buckets_summary = {
        "mapped_both": {
            "count": len(mapped_both),
            "sum_abs_delta": sum(abs(x["delta"]) for x in mapped_both),
            "sum_delta": sum(x["delta"] for x in mapped_both),
        },
        "old_only": {
            "count": len(old_only),
            "sum_qty_old": sum(x["qty_old"] for x in old_only),
            "reconcilable": False,
        },
        "new_only": {
            "count": len(new_only),
            "sum_qty_new": sum(x["qty_new"] for x in new_only),
            "sum_abs_delta": sum(abs(x["delta"]) for x in new_only),
        },
        "bad_mapping": {
            "count": len(bad_mapping),
        },
        "unmapped_store": {
            "count": 0,
            "note": "Все склады из отчёта берутся только из uuid_map.store",
        },
    }

    report = {
        "verified_at": args.moment,
        "status": "OK" if not reconcilable and not old_only else "DIFF",
        "old_api_ok": True,
        "totals": {
            "old_bystore_mapped_stores": round(total_old_bystore, 6),
            "new_bystore_mapped_stores": round(total_new_bystore, 6),
            "bystore_gap_new_minus_old": round(total_new_bystore - total_old_bystore, 6),
            "old_stock_all": round(old_all_total, 6),
            "new_stock_all": round(new_all_total, 6),
            "stock_all_gap_new_minus_old": round(new_all_total - old_all_total, 6),
        },
        "reconcile_preview": {
            "reconcilable_lines": len(reconcilable),
            "enter_lines": len(enter_lines),
            "loss_lines": len(loss_lines),
            "sum_enter_qty": round(sum_enter, 6),
            "sum_loss_qty": round(sum_loss, 6),
            "old_only_blocked_lines": len(old_only),
            "old_only_blocked_qty": round(sum(x["qty_old"] for x in old_only), 6),
        },
        "buckets_summary": buckets_summary,
        "stores_mapped": len(store_map),
        "top50_abs_delta": top50,
        "buckets": {
            "mapped_both": mapped_both,
            "old_only": old_only,
            "new_only": new_only,
            "bad_mapping": bad_mapping,
            "unmapped_store": [],
        },
        "stop_conditions": {
            "bad_mapping_with_stock": len(bad_mapping) > 0,
            "old_only_with_stock": len(old_only) > 0,
            "unmapped_store": False,
        },
    }

    out = FINAL_JSON if args.final else REPORT_JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # CSV for weak model review
    with CSV_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "bucket", "store", "store_name", "type", "assortment_id", "old_assortment_id",
            "code", "name", "qty_old", "qty_new", "delta", "reconcilable", "reason",
        ])
        for bucket_name in ("mapped_both", "old_only", "new_only"):
            for x in report["buckets"][bucket_name]:
                w.writerow([
                    x.get("bucket", bucket_name),
                    x.get("store") or x.get("old_store", ""),
                    x.get("store_name") or x.get("old_store_name", ""),
                    x.get("assortment_type", ""),
                    x.get("assortment_id", ""),
                    x.get("old_assortment_id", ""),
                    x.get("code", ""),
                    x.get("name", ""),
                    x.get("qty_old", 0),
                    x.get("qty_new", 0),
                    x.get("delta", 0),
                    x.get("reconcilable", bucket_name != "old_only"),
                    x.get("reason", ""),
                ])

    log.info(
        "ИТОГ: bystore OLD=%.2f NEW=%.2f gap=%+.2f | reconcilable=%s (enter=%s loss=%s) | old_only=%s → %s",
        total_old_bystore,
        total_new_bystore,
        total_new_bystore - total_old_bystore,
        len(reconcilable),
        len(enter_lines),
        len(loss_lines),
        len(old_only),
        out,
    )


if __name__ == "__main__":
    main()
