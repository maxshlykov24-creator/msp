#!/usr/bin/env python3
"""
Audit agent + customerorder attributes on TRUSTED/MOMENT_RESOLVED pairs.

  python3 audit_doc_agent.py
  python3 audit_doc_agent.py --from-date 2026-05-01 --to-date 2026-05-31
  python3 audit_doc_agent.py --final
  python3 audit_doc_agent.py --pairs-file audit_doc_pairs_report.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from ms_common import (
    BASE,
    DOC_TYPES_WITH_AGENT,
    ORDER_ATTR_NAMES,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    YEAR_START,
    agent_type,
    agent_uid,
    api,
    attr_val,
    get_all,
    load_umap,
    make_session,
    mapped_agent,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "audit_doc_agent.log"
REPORT_JSON = SCRIPT_DIR / "audit_doc_agent_report.json"
FINAL_JSON = SCRIPT_DIR / "audit_agent_final.json"
PAIRS_FILE = SCRIPT_DIR / "audit_doc_pairs_report.json"

FIXABLE = frozenset({"TRUSTED", "TRUSTED_RENAME", "MOMENT_RESOLVED"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def agent_name(doc: dict) -> str:
    ag = doc.get("agent") or {}
    return ag.get("name") or uid((ag.get("meta") or {}).get("href", ""))


def compare_order_attrs(old_doc: dict, new_doc: dict) -> list[dict]:
    mismatches = []
    for name in ORDER_ATTR_NAMES:
        ov = attr_val(old_doc, name)
        nv = attr_val(new_doc, name)
        if ov != nv:
            mismatches.append({"field": name, "old": ov, "new": nv})
    return mismatches


def load_fixable_pairs(pairs_file: Path, from_d: str | None, to_d: str | None) -> list[dict]:
    data = json.loads(pairs_file.read_text(encoding="utf-8"))
    rows = [p for p in data.get("pairs", []) if p.get("status") in FIXABLE]
    if from_d or to_d:
        filtered = []
        for p in rows:
            m = p.get("moment") or ""
            if from_d and m < from_d:
                continue
            if to_d and m > (f"{to_d} 23:59:59" if len(to_d) == 10 else to_d):
                continue
            filtered.append(p)
        rows = filtered
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=None)
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--pairs-file", default=str(PAIRS_FILE))
    ap.add_argument("--spot", default="ТУ-00887")
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    old_api_ok = old_api_available(old_s)
    pairs = load_fixable_pairs(Path(args.pairs_file), args.from_date, args.to_date)
    log.info("Fixable pairs to audit: %s | OLD API: %s", len(pairs), old_api_ok)

    if not old_api_ok:
        spot_check = None
        if args.spot:
            for p in pairs:
                if p.get("new_name") == args.spot and p.get("doc_type") == "customerorder":
                    rn = api(new_s, "GET", f"{BASE}/entity/customerorder/{p['new_id']}")
                    if rn.ok:
                        nd = rn.json()
                        spot_check = {
                            "name": args.spot,
                            "new_agent": agent_name(nd),
                            "new_attrs": {n: attr_val(nd, n) for n in ORDER_ATTR_NAMES},
                            "old_api_ok": False,
                            "note": "OLD недоступен — ожидаемый agent/attrs не сверены",
                        }
                    break
        report = {
            "pairs_audited": 0,
            "agent_mismatch_count": None,
            "attr_mismatch_count": None,
            "errors": 0,
            "old_api_ok": False,
            "spot_check": spot_check,
            "status": "BLOCKED_OLD_API",
        }
        out = FINAL_JSON if args.final else REPORT_JSON
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API 403 — полный audit agent невозможен")
        return

    agent_mismatches: list[dict] = []
    attr_mismatches: list[dict] = []
    errors = 0
    spot_check = None

    for i, p in enumerate(pairs):
        doc_type = p["doc_type"]
        old_id = p.get("old_id")
        new_id = p.get("new_id")
        if not old_id or not new_id:
            continue
        ro = api(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_id}")
        rn = api(new_s, "GET", f"{BASE}/entity/{doc_type}/{new_id}")
        if not ro.ok or not rn.ok:
            errors += 1
            continue
        old_doc = ro.json()
        new_doc = rn.json()

        if args.spot and p.get("new_name") == args.spot:
            spot_check = {
                "name": args.spot,
                "old_agent": agent_name(old_doc),
                "new_agent": agent_name(new_doc),
                "old_attrs": {n: attr_val(old_doc, n) for n in ORDER_ATTR_NAMES},
                "new_attrs": {n: attr_val(new_doc, n) for n in ORDER_ATTR_NAMES},
            }

        old_au = agent_uid(old_doc)
        at = agent_type(old_doc)
        expected = mapped_agent(old_au, at, umap)
        actual = agent_uid(new_doc)
        if expected and actual != expected:
            agent_mismatches.append({
                "doc_type": doc_type,
                "new_name": new_doc.get("name"),
                "new_id": new_id,
                "old_id": old_id,
                "moment": new_doc.get("moment"),
                "old_agent_uid": old_au,
                "old_agent_name": agent_name(old_doc),
                "new_agent_name": agent_name(new_doc),
                "expected_new_uid": expected,
                "actual_new_uid": actual,
            })

        if doc_type == "customerorder":
            for mm in compare_order_attrs(old_doc, new_doc):
                attr_mismatches.append({
                    "doc_type": doc_type,
                    "new_name": new_doc.get("name"),
                    "new_id": new_id,
                    "old_id": old_id,
                    **mm,
                })

        if (i + 1) % 200 == 0:
            log.info("  ... %s/%s", i + 1, len(pairs))

    by_type = Counter(m["doc_type"] for m in agent_mismatches)
    report = {
        "pairs_audited": len(pairs),
        "agent_mismatch_count": len(agent_mismatches),
        "attr_mismatch_count": len(attr_mismatches),
        "errors": errors,
        "agent_by_type": dict(by_type),
        "agent_mismatches": agent_mismatches,
        "attr_mismatches": attr_mismatches,
        "spot_check": spot_check,
        "status": "OK" if not agent_mismatches and not attr_mismatches else "MISMATCH",
    }

    out = FINAL_JSON if args.final else REPORT_JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: agent_mismatch=%s attr_mismatch=%s errors=%s → %s",
        len(agent_mismatches), len(attr_mismatches), errors, out,
    )


if __name__ == "__main__":
    main()
