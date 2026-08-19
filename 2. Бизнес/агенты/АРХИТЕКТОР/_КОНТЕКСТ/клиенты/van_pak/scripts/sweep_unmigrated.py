#!/usr/bin/env python3
"""
Phase 1a: Find OLD documents (2026) not present in uuid_map / not in NEW.
Saves unmigrated_report.json with dates to catchup.

  PYTHONPATH=. python3 sweep_unmigrated.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ms_common import (
    BASE,
    NEW_TOKEN,
    OLD_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    is_uuid,
    load_umap,
    make_session,
    uid,
)

REPORT = SCRIPT_DIR / "unmigrated_report.json"
YEAR_FILTER = "moment>=2026-01-01 00:00:00;moment<=2026-12-31 23:59:59"

DOC_TYPES = [
    "customerorder",
    "demand",
    "invoiceout",
    "paymentin",
    "paymentout",
    "factureout",
]


def build_paymentout_map(old_s, new_s, umap: dict) -> dict[str, str]:
    """Build old→new map for paymentout via externalCode, then moment+org+sum."""
    print("  Building paymentout map...")
    old_pays = get_all(old_s, "paymentout", filt=YEAR_FILTER)
    new_pays = get_all(new_s, "paymentout", filt=YEAR_FILTER)
    print(f"    OLD paymentout={len(old_pays)} NEW paymentout={len(new_pays)}")

    po_map: dict[str, str] = {}

    # Pass 1: externalCode match
    new_by_ext: dict[str, str] = {}
    for nd in new_pays:
        ext = (nd.get("externalCode") or "").strip()
        if is_uuid(ext):
            new_by_ext[ext] = uid(nd["meta"]["href"])

    for od in old_pays:
        old_id = uid(od["meta"]["href"])
        if old_id in new_by_ext.values():
            continue
        if old_id in new_by_ext:
            po_map[old_id] = new_by_ext[old_id]

    # Pass 2: moment+org+sum for unmatched
    matched = set(po_map.keys())
    new_by_key: dict[tuple, str] = {}
    for nd in new_pays:
        m = (nd.get("moment") or "")[:19]
        org = uid((nd.get("organization") or {}).get("meta", {}).get("href", ""))
        s = nd.get("sum") or 0
        new_by_key[(m, org, s)] = uid(nd["meta"]["href"])

    org_map = umap.get("organization", {})
    for od in old_pays:
        old_id = uid(od["meta"]["href"])
        if old_id in matched:
            continue
        m = (od.get("moment") or "")[:19]
        old_org = uid((od.get("organization") or {}).get("meta", {}).get("href", ""))
        new_org = org_map.get(old_org, old_org)
        s = od.get("sum") or 0
        key = (m, new_org, s)
        if key in new_by_key:
            po_map[old_id] = new_by_key[key]
            matched.add(old_id)

    print(f"    Mapped paymentout: {len(po_map)}/{len(old_pays)}")
    return po_map


def main() -> None:
    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    # Build paymentout map if missing
    po_map = build_paymentout_map(old_s, new_s, umap)
    if po_map:
        umap.setdefault("paymentout", {}).update(po_map)
        from ms_common import save_umap
        save_umap(umap)
        print(f"  Saved {len(po_map)} paymentout entries to uuid_map.json")

    unmigrated: dict[str, list[dict]] = defaultdict(list)
    by_date: dict[str, set[str]] = defaultdict(set)

    for doc_type in DOC_TYPES:
        type_map = umap.get(doc_type, {})  # old_id → new_id
        print(f"\n=== {doc_type} (map size={len(type_map)}) ===")

        old_docs = get_all(old_s, doc_type, filt=YEAR_FILTER)
        print(f"  OLD: {len(old_docs)}")

        # NEW: index by externalCode and uuid
        new_docs = get_all(new_s, doc_type, filt=YEAR_FILTER)
        new_by_ext: dict[str, str] = {}
        new_uuids: set[str] = set()
        for nd in new_docs:
            nid = uid(nd["meta"]["href"])
            new_uuids.add(nid)
            ext = (nd.get("externalCode") or "").strip()
            if is_uuid(ext):
                new_by_ext[ext] = nid

        missing = 0
        for od in old_docs:
            old_id = uid(od["meta"]["href"])
            new_id = type_map.get(old_id)
            # Check: in uuid_map AND that new entity actually exists
            if new_id and new_id in new_uuids:
                continue
            # Check by externalCode fallback
            if old_id in new_by_ext:
                continue
            # Not found
            date_str = (od.get("moment") or "")[:10]
            name = od.get("name", "?")
            missing += 1
            unmigrated[doc_type].append({
                "old_id": old_id,
                "name": name,
                "moment": od.get("moment"),
                "date": date_str,
                "sum": od.get("sum"),
            })
            if date_str:
                by_date[date_str].add(doc_type)

        print(f"  Missing from NEW: {missing}")

    # Sort by date descending
    for dt in unmigrated:
        unmigrated[dt].sort(key=lambda x: x.get("moment") or "", reverse=True)

    dates_needing_catchup = sorted(by_date.keys(), reverse=True)
    print(f"\nDates needing catchup: {dates_needing_catchup[:20]}")

    report = {
        "captured_at": datetime.now().isoformat(),
        "total_missing": {k: len(v) for k, v in unmigrated.items()},
        "dates_needing_catchup": dates_needing_catchup,
        "unmigrated": {k: v for k, v in unmigrated.items()},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {REPORT}")


if __name__ == "__main__":
    main()
