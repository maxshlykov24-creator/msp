#!/usr/bin/env python3
"""
Sync customerorder positions OLD→NEW for orders where sum mismatches.
Full-replace NEW positions with OLD (mapped assortment, qty, price, discount, vat).

  PYTHONPATH=. python3 fix_order_positions.py --month 2026-06 --dry
  PYTHONPATH=. python3 fix_order_positions.py --month 2026-06 --apply
  PYTHONPATH=. python3 fix_order_positions.py --orders ТУ-00889,ФР-00187 --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, load_umap, make_session, meta_obj, old_api_available, uid,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(SCRIPT_DIR / "fix_order_positions.log", encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)


def month_filter(month: str) -> str:
    y, m = month.split("-")
    last = {"01":31,"02":29,"03":31,"04":30,"05":31,"06":30,"07":31,"08":31,"09":30,"10":31,"11":30,"12":31}[m]
    return f"moment>={month}-01 00:00:00;moment<={month}-{last} 23:59:59"


def assortment_new(umap, a_href):
    et = a_href.split("/entity/")[-1].split("/")[0] if "/entity/" in a_href else ""
    aid = uid(a_href)
    for t in ("product", "service", "variant"):
        n = umap.get(t, {}).get(aid)
        if n:
            return t, n
    return et, None


def build_positions(umap, old_positions):
    res = []
    for p in old_positions:
        a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
        t, nid = assortment_new(umap, a_href)
        if not nid:
            return None  # unmapped assortment — abort to avoid wrong sum
        pos = {"assortment": meta_obj(t, nid), "quantity": p.get("quantity", 1),
               "price": p.get("price", 0), "discount": p.get("discount", 0), "vat": p.get("vat", 0)}
        res.append(pos)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None)
    ap.add_argument("--orders", default=None, help="comma-separated OLD order names")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        log.error("Specify --dry or --apply"); sys.exit(1)
    dry = args.dry

    umap = load_umap(); co_map = umap.get("customerorder", {})
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403"); sys.exit(1)

    if args.orders:
        olds = []
        for nm in args.orders.split(","):
            r = api(old_s, "GET", f"{BASE}/entity/customerorder", params={"filter": f"name={nm.strip()}", "limit": 1})
            if r.ok and r.json().get("rows"):
                olds.append(r.json()["rows"][0])
    else:
        olds = get_all(old_s, "customerorder", filt=month_filter(args.month))

    stats = {"checked": 0, "sum_mismatch": 0, "fixed": 0, "skipped_unmapped": 0, "errors": 0, "actions": []}

    for oo in olds:
        oid = uid(oo["meta"]["href"]); nid = co_map.get(oid)
        if not nid:
            continue
        stats["checked"] += 1
        rn = api(new_s, "GET", f"{BASE}/entity/customerorder/{nid}")
        if not rn.ok:
            continue
        nd = rn.json()
        if abs((oo.get("sum") or 0) - (nd.get("sum") or 0)) <= 1:
            continue
        stats["sum_mismatch"] += 1
        old_pos = api(old_s, "GET", f"{BASE}/entity/customerorder/{oid}/positions", params={"limit": 1000}).json().get("rows", [])
        new_positions = build_positions(umap, old_pos)
        if new_positions is None:
            stats["skipped_unmapped"] += 1
            stats["actions"].append({"order": oo.get("name"), "action": "skip_unmapped_assortment"})
            log.warning("  SKIP %s: unmapped assortment", oo.get("name"))
            continue
        rec = {"order": oo.get("name"), "old_sum": oo.get("sum"), "new_sum_before": nd.get("sum"),
               "old_pos": len(old_pos), "new_pos_before": len(api(new_s,"GET",f"{BASE}/entity/customerorder/{nid}/positions",params={"limit":1000}).json().get("rows",[]))}
        if dry:
            rec["action"] = "dry_replace"
            stats["fixed"] += 1
        else:
            r = api(new_s, "PUT", f"{BASE}/entity/customerorder/{nid}", json={"positions": new_positions})
            if r.ok:
                rec["action"] = "replaced"; rec["new_sum_after"] = r.json().get("sum")
                stats["fixed"] += 1
            else:
                rec["action"] = f"fail_{r.status_code}"; rec["error"] = r.text[:200]
                stats["errors"] += 1
        stats["actions"].append(rec)
        log.info("%s %s: old_sum=%s new_sum_before=%s pos %s→%s",
                 "DRY" if dry else "PUT", oo.get("name"), oo.get("sum"), nd.get("sum"), rec["new_pos_before"], len(new_positions))

    out = SCRIPT_DIR / "fix_order_positions_report.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: checked=%s sum_mismatch=%s fixed=%s skipped_unmapped=%s errors=%s → %s",
             stats["checked"], stats["sum_mismatch"], stats["fixed"], stats["skipped_unmapped"], stats["errors"], out)


if __name__ == "__main__":
    main()
