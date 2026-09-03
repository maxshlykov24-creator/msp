#!/usr/bin/env python3
"""
Audit counterparty uuid_map integrity: detect cross-wired entries
(OLD cp → NEW cp where name/INN differ) and propose correct NEW pair by name/INN.

  PYTHONPATH=. python3 audit_counterparty_map.py
  PYTHONPATH=. python3 audit_counterparty_map.py --fix      # rewrite wrong entries
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, load_umap, make_session, old_api_available, save_umap, uid,
)

REPORT = SCRIPT_DIR / "audit_counterparty_map_report.json"


def norm(s: str) -> str:
    return (s or "").strip().lower().replace("  ", " ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        print("BLOCKER: OLD API 403"); sys.exit(1)

    cp_map = umap.get("counterparty", {})
    print(f"cp_map entries: {len(cp_map)}")

    old_cps = get_all(old_s, "counterparty")
    new_cps = get_all(new_s, "counterparty")
    print(f"OLD cp={len(old_cps)} NEW cp={len(new_cps)}")

    old_by_id = {uid(c["meta"]["href"]): c for c in old_cps}
    new_by_id = {uid(c["meta"]["href"]): c for c in new_cps}

    # NEW index by name and inn for correct-pair lookup
    new_by_name: dict[str, list[str]] = {}
    new_by_inn: dict[str, list[str]] = {}
    for c in new_cps:
        nid = uid(c["meta"]["href"])
        new_by_name.setdefault(norm(c.get("name", "")), []).append(nid)
        inn = (c.get("inn") or "").strip()
        if inn:
            new_by_inn.setdefault(inn, []).append(nid)

    mismatches = []
    ok = 0
    missing_old = 0
    missing_new = 0

    for old_id, new_id in cp_map.items():
        oc = old_by_id.get(old_id)
        nc = new_by_id.get(new_id)
        if not oc:
            missing_old += 1
            continue
        if not nc:
            missing_new += 1
            continue
        o_name = norm(oc.get("name", "")); n_name = norm(nc.get("name", ""))
        o_inn = (oc.get("inn") or "").strip(); n_inn = (nc.get("inn") or "").strip()
        name_ok = o_name == n_name
        inn_ok = (o_inn == n_inn) if (o_inn and n_inn) else None
        if name_ok or inn_ok is True:
            ok += 1
            continue
        # mismatch — propose correct
        proposed = None
        reason = None
        if o_inn and o_inn in new_by_inn and len(new_by_inn[o_inn]) == 1:
            proposed = new_by_inn[o_inn][0]; reason = "by_inn"
        elif o_name in new_by_name and len(new_by_name[o_name]) == 1:
            proposed = new_by_name[o_name][0]; reason = "by_name"
        mismatches.append({
            "old_id": old_id,
            "old_name": oc.get("name"), "old_inn": o_inn,
            "mapped_new_id": new_id,
            "mapped_new_name": nc.get("name"), "mapped_new_inn": n_inn,
            "proposed_new_id": proposed,
            "proposed_reason": reason,
        })

    fixable = [m for m in mismatches if m["proposed_new_id"]]
    unresolved = [m for m in mismatches if not m["proposed_new_id"]]

    report = {
        "cp_map_size": len(cp_map),
        "ok": ok,
        "mismatch_count": len(mismatches),
        "fixable": len(fixable),
        "unresolved": len(unresolved),
        "missing_old": missing_old,
        "missing_new": missing_new,
        "mismatches": mismatches,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK={ok} MISMATCH={len(mismatches)} (fixable={len(fixable)} unresolved={len(unresolved)}) missing_old={missing_old} missing_new={missing_new}")
    for m in mismatches[:25]:
        print(f"  OLD '{m['old_name']}' (inn={m['old_inn']}) → mapped NEW '{m['mapped_new_name']}' | proposed={m['proposed_reason']}")

    if args.fix and fixable:
        for m in fixable:
            cp_map[m["old_id"]] = m["proposed_new_id"]
        umap["counterparty"] = cp_map
        save_umap(umap)
        print(f"\nFIXED {len(fixable)} cp_map entries (saved).")

    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    main()
