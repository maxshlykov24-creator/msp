#!/usr/bin/env python3
"""
Phase 0: probe OLD/NEW tokens, collect document counts + Σsum/payedSum/shippedSum.
Saves snapshot_before.json.

  PYTHONPATH=. python3 snapshot_before.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from ms_common import (
    BASE,
    NEW_TOKEN,
    OLD_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    make_session,
    uid,
)

REPORT = SCRIPT_DIR / "snapshot_before.json"

# All types we care about in this reconcile
DOC_TYPES = [
    "customerorder",
    "demand",
    "invoiceout",
    "paymentin",
    "paymentout",
    "factureout",
]
YEAR_FILTER = "moment>=2026-01-01 00:00:00;moment<=2026-12-31 23:59:59"


def probe_token(token: str, label: str) -> bool:
    s = make_session(token)
    r = api(s, "GET", f"{BASE}/entity/organization", params={"limit": 1})
    ok = r.status_code == 200
    print(f"  {label} ({token[-8:]}): {'OK' if ok else f'FAIL {r.status_code}'}")
    return ok


def collect_stats(token: str, label: str) -> dict:
    s = make_session(token)
    stats: dict = {}
    for doc_type in DOC_TYPES:
        rows = get_all(s, doc_type, filt=YEAR_FILTER)
        count = len(rows)
        sum_total = sum(r.get("sum") or 0 for r in rows)
        payed_total = sum(r.get("payedSum") or 0 for r in rows) if doc_type == "customerorder" else None
        shipped_total = sum(r.get("shippedSum") or 0 for r in rows) if doc_type == "customerorder" else None
        stats[doc_type] = {
            "count": count,
            "sum": sum_total,
        }
        if payed_total is not None:
            stats[doc_type]["payedSum"] = payed_total
        if shipped_total is not None:
            stats[doc_type]["shippedSum"] = shipped_total
        print(f"    {doc_type}: count={count} sum={sum_total:,.0f}" +
              (f" payedSum={payed_total:,.0f} shippedSum={shipped_total:,.0f}" if payed_total is not None else ""))
    return stats


def main() -> None:
    print("=== Phase 0: Token probe ===")
    old_ok = probe_token(OLD_TOKEN, "OLD")
    new_ok = probe_token(NEW_TOKEN, "NEW")

    if not old_ok:
        print("BLOCKER: OLD API not accessible — cannot proceed.")
        sys.exit(1)
    if not new_ok:
        print("BLOCKER: NEW API not accessible — cannot proceed.")
        sys.exit(1)

    print("\n=== OLD stats (2026) ===")
    old_stats = collect_stats(OLD_TOKEN, "OLD")

    print("\n=== NEW stats (2026) ===")
    new_stats = collect_stats(NEW_TOKEN, "NEW")

    print("\n=== GAP (NEW - OLD) ===")
    for doc_type in DOC_TYPES:
        oc = old_stats[doc_type]["count"]
        nc = new_stats[doc_type]["count"]
        os_ = old_stats[doc_type]["sum"]
        ns_ = new_stats[doc_type]["sum"]
        print(f"  {doc_type}: count {oc}→{nc} (Δ{nc-oc:+d})  sum {os_:,.0f}→{ns_:,.0f} (Δ{ns_-os_:+,.0f})")
        if doc_type == "customerorder":
            op = old_stats[doc_type].get("payedSum", 0)
            np_ = new_stats[doc_type].get("payedSum", 0)
            os2 = old_stats[doc_type].get("shippedSum", 0)
            ns2 = new_stats[doc_type].get("shippedSum", 0)
            print(f"    payedSum  {op:,.0f}→{np_:,.0f} (Δ{np_-op:+,.0f})")
            print(f"    shippedSum {os2:,.0f}→{ns2:,.0f} (Δ{ns2-os2:+,.0f})")

    report = {
        "captured_at": datetime.now().isoformat(),
        "old_token_suffix": OLD_TOKEN[-8:],
        "new_token_suffix": NEW_TOKEN[-8:],
        "old_api_ok": old_ok,
        "new_api_ok": new_ok,
        "old": old_stats,
        "new": new_stats,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {REPORT}")


if __name__ == "__main__":
    main()
