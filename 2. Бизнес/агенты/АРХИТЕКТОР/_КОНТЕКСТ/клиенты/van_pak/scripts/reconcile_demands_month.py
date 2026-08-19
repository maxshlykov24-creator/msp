#!/usr/bin/env python3
"""
Per-order demand reconciler for a month: link NEW demands to NEW order as in OLD;
report extra (duplicate) demands in NEW. Drives shippedSum to match OLD.

  PYTHONPATH=. python3 reconcile_demands_month.py --month 2026-06 --dry
  PYTHONPATH=. python3 reconcile_demands_month.py --month 2026-06 --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, is_uuid, load_umap, make_session, meta_obj, old_api_available, save_umap, uid,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(SCRIPT_DIR / "reconcile_demands_month.log", encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)


def span_filter(month: str) -> str:
    y, m = month.split("-")
    last = {"01":31,"02":29,"03":31,"04":30,"05":31,"06":30,"07":31,"08":31,"09":30,"10":31,"11":30,"12":31}[m]
    start_m = max(1, int(m) - 1)
    return f"moment>={y}-{start_m:02d}-01 00:00:00;moment<={month}-{last} 23:59:59"


def co_link(d: dict) -> str:
    return uid((d.get("customerOrder") or {}).get("meta", {}).get("href", ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        log.error("Specify --dry or --apply"); sys.exit(1)
    dry = args.dry

    umap = load_umap()
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403"); sys.exit(1)

    co_map = umap.get("customerorder", {})
    dem_map = umap.get("demand", {})
    inv_dem = {v: k for k, v in dem_map.items()}

    filt = span_filter(args.month)
    log.info("=== Demands %s | %s | %s ===", args.month, "DRY" if dry else "APPLY", filt)

    old_dems = get_all(old_s, "demand", filt=filt, expand="customerOrder")
    new_dems = get_all(new_s, "demand", filt=filt, expand="customerOrder")
    log.info("OLD demands=%s NEW demands=%s", len(old_dems), len(new_dems))

    new_by_id = {uid(d["meta"]["href"]): d for d in new_dems}

    old_dem_by_order: dict[str, list[dict]] = defaultdict(list)
    for d in old_dems:
        co = co_link(d)
        if co:
            old_dem_by_order[co].append(d)

    new_dem_by_order: dict[str, list[dict]] = defaultdict(list)
    for d in new_dems:
        co = co_link(d)
        if co:
            new_dem_by_order[co].append(d)

    stats = {"month": args.month, "dry": dry, "linked": 0, "extra_demands": [], "skipped_unmapped": 0, "errors": 0}

    for old_co, dems in old_dem_by_order.items():
        new_co = co_map.get(old_co)
        if not new_co:
            continue
        expected_new_dem_ids = set()
        for od in dems:
            od_id = uid(od["meta"]["href"])
            nd_id = dem_map.get(od_id)
            if not nd_id:
                stats["skipped_unmapped"] += 1
                continue
            expected_new_dem_ids.add(nd_id)
            nd = new_by_id.get(nd_id)
            if nd is None:
                continue
            cur_co = co_link(nd)
            if cur_co != new_co:
                if dry:
                    stats["linked"] += 1
                else:
                    r = api(new_s, "PUT", f"{BASE}/entity/demand/{nd_id}", json={"customerOrder": meta_obj("customerorder", new_co)})
                    if r.ok:
                        stats["linked"] += 1
                    else:
                        stats["errors"] += 1
                        log.warning("  PUT demand %s: %s %s", nd.get("name"), r.status_code, r.text[:120])

        for nd in new_dem_by_order.get(new_co, []):
            nd_id = uid(nd["meta"]["href"])
            if nd_id in expected_new_dem_ids:
                continue
            mapped_old = inv_dem.get(nd_id)
            if mapped_old and any(uid(od["meta"]["href"]) == mapped_old for od in dems):
                continue
            stats["extra_demands"].append({
                "old_order": old_co, "new_order": new_co,
                "extra_demand_id": nd_id, "demand_name": nd.get("name"),
                "mapped_old": mapped_old,
            })

    if not dry:
        save_umap(umap)

    out = SCRIPT_DIR / f"reconcile_demands_{args.month}.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ %s: linked=%s extra_demands=%s skipped_unmapped=%s errors=%s → %s",
             args.month, stats["linked"], len(stats["extra_demands"]), stats["skipped_unmapped"], stats["errors"], out)


if __name__ == "__main__":
    main()
