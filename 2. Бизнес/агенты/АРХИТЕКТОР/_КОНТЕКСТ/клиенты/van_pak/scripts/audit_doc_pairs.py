#!/usr/bin/env python3
"""
Audit OLD↔NEW document pairs for 2026 (agent-bearing types).

  python3 audit_doc_pairs.py --from-date 2026-01-01 --to-date 2026-06-30
  python3 audit_doc_pairs.py --from-date 2026-05-01 --to-date 2026-05-31
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from ms_common import (
    BASE,
    DOC_TYPES_WITH_AGENT,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    YEAR_START,
    api,
    classify_pair,
    fetch_old_doc,
    get_all,
    is_uuid,
    load_umap,
    make_session,
    old_api_available,
    resolve_moment_pairs,
    uid,
)

LOG_FILE = SCRIPT_DIR / "audit_doc_pairs.log"
REPORT_JSON = SCRIPT_DIR / "audit_doc_pairs_report.json"
REPORT_CSV = SCRIPT_DIR / "audit_doc_pairs_suspect.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def date_filter(from_d: str, to_d: str | None) -> str:
    f = f"moment>={from_d} 00:00:00" if len(from_d) == 10 else f"moment>={from_d}"
    if to_d:
        t = f"{to_d} 23:59:59" if len(to_d) == 10 else to_d
        f += f";moment<={t}"
    return f


def resolve_old_id(new_doc: dict, inv_map: dict) -> str | None:
    new_uid = uid(new_doc["meta"]["href"])
    ext = (new_doc.get("externalCode") or "").strip()
    if is_uuid(ext):
        return ext
    return inv_map.get(new_uid)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=YEAR_START[:10])
    ap.add_argument("--to-date", default=None)
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    old_api_ok = old_api_available(old_s)
    log.info("OLD API доступен: %s", old_api_ok)
    filt = date_filter(args.from_date, args.to_date)

    all_pairs: list[dict] = []
    suspect_rows: list[dict] = []
    status_counts: Counter = Counter()

    for doc_type in DOC_TYPES_WITH_AGENT:
        doc_map = umap.get(doc_type, {})
        if not doc_map:
            continue
        inv_map = {v: k for k, v in doc_map.items()}

        log.info("=== %s ===", doc_type)
        new_docs = get_all(new_s, doc_type, filt=filt)
        old_docs = get_all(old_s, doc_type, filt=filt) if old_api_ok else []
        log.info("  new=%s old=%s", len(new_docs), len(old_docs))

        paired_old: set[str] = set()
        paired_new: set[str] = set()
        pending_suspect: list[dict] = []

        for nd in new_docs:
            new_id = uid(nd["meta"]["href"])
            old_id = resolve_old_id(nd, inv_map)
            old_doc = None
            if old_id and old_api_ok:
                old_doc, fetch_st = fetch_old_doc(old_s, doc_type, old_id)
                if fetch_st == "old_api_403":
                    old_api_ok = False
                    log.error("OLD API 403 — прекращаю fetch по документам (anchor-only)")

            status, reason = classify_pair(old_doc, nd, old_id, umap, doc_type)
            if status in ("TRUSTED", "TRUSTED_RENAME") and old_id:
                paired_old.add(old_id)
                paired_new.add(new_id)

            rec = {
                "doc_type": doc_type,
                "new_id": new_id,
                "new_name": nd.get("name"),
                "old_id": old_id,
                "old_name": old_doc.get("name") if old_doc else None,
                "moment": nd.get("moment"),
                "status": status,
                "reason": reason,
            }
            all_pairs.append(rec)
            status_counts[status] += 1
            if status == "SUSPECT":
                pending_suspect.append(nd)
                suspect_rows.append(rec)

        # moment+org auto-resolve (only when OLD list available)
        if old_api_ok:
            resolutions = resolve_moment_pairs(
                pending_suspect, old_docs, new_docs, umap, paired_old, paired_new
            )
            for new_id, (old_id, res_reason) in resolutions.items():
                for rec in all_pairs:
                    if rec["new_id"] == new_id and rec["status"] == "SUSPECT":
                        old_doc, _ = fetch_old_doc(old_s, doc_type, old_id)
                        nd = next(x for x in new_docs if uid(x["meta"]["href"]) == new_id)
                        st, _ = classify_pair(old_doc, nd, old_id, umap, doc_type)
                        if st in ("TRUSTED", "TRUSTED_RENAME") or (
                            old_doc and old_doc.get("moment") == nd.get("moment")
                        ):
                            rec["status"] = "MOMENT_RESOLVED"
                            rec["old_id"] = old_id
                            rec["old_name"] = old_doc.get("name") if old_doc else None
                            rec["reason"] = res_reason
                            status_counts["SUSPECT"] -= 1
                            status_counts["MOMENT_RESOLVED"] += 1
                        break

    fixable = [p for p in all_pairs if p["status"] in (
        "TRUSTED", "TRUSTED_RENAME", "MOMENT_RESOLVED"
    )]
    blockers = [p for p in all_pairs if p["status"] in ("SUSPECT", "UNMAPPED_NEW")]

    report = {
        "filter": filt,
        "old_api_ok": old_api_ok,
        "status_counts": dict(status_counts),
        "fixable_count": len(fixable),
        "blocker_count": len(blockers),
        "pairs": all_pairs,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with REPORT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "doc_type", "new_name", "old_name", "moment", "status", "reason", "new_id", "old_id",
        ])
        w.writeheader()
        for row in blockers:
            w.writerow({k: row.get(k) for k in w.fieldnames})

    log.info("ИТОГ: fixable=%s blockers=%s counts=%s",
             len(fixable), len(blockers), dict(status_counts))
    log.info("Отчёт: %s", REPORT_JSON)


if __name__ == "__main__":
    main()
