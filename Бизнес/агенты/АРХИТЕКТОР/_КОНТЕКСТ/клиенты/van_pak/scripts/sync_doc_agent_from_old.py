#!/usr/bin/env python3
"""
Sync agent field on TRUSTED/MOMENT_RESOLVED pairs from OLD → NEW.

  python3 sync_doc_agent_from_old.py --dry
  python3 sync_doc_agent_from_old.py --from-date 2026-05-01 --to-date 2026-06-30
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
    agent_type,
    agent_uid,
    api,
    is_uuid,
    load_umap,
    make_session,
    mapped_agent,
    meta_obj,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "sync_doc_agent.log"
REPORT_JSON = SCRIPT_DIR / "sync_doc_agent_report.json"
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


def load_pairs(pairs_file: Path, from_d: str | None, to_d: str | None) -> list[dict]:
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


def external_code_ok(new_doc: dict, old_id: str, doc_type: str, umap: dict) -> bool:
    ext = (new_doc.get("externalCode") or "").strip()
    new_id = uid(new_doc["meta"]["href"])
    inv = {v: k for k, v in umap.get(doc_type, {}).items()}
    if is_uuid(ext) and ext == old_id:
        return True
    if inv.get(new_id) == old_id:
        return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from-date", default=None)
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--pairs-file", default=str(PAIRS_FILE))
    ap.add_argument("--new-ids", default=None, help="comma-separated NEW uuids only")
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        report = {"dry": args.dry, "blocked": "OLD API 403", "fixed": 0, "skipped": 0, "errors": 1}
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API недоступен (403). Sync agent невозможен.")
        return
    pairs = load_pairs(Path(args.pairs_file), args.from_date, args.to_date)
    if args.new_ids:
        allow = set(args.new_ids.split(","))
        pairs = [p for p in pairs if p.get("new_id") in allow]

    fixed = skipped = errors = 0
    actions: list[dict] = []

    for p in pairs:
        doc_type = p["doc_type"]
        old_id = p.get("old_id")
        new_id = p.get("new_id")
        if not old_id or not new_id:
            skipped += 1
            continue

        ro = api(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_id}")
        rn = api(new_s, "GET", f"{BASE}/entity/{doc_type}/{new_id}")
        if not ro.ok or not rn.ok:
            errors += 1
            continue
        old_doc = ro.json()
        new_doc = rn.json()

        if not external_code_ok(new_doc, old_id, doc_type, umap):
            skipped += 1
            actions.append({"new_name": new_doc.get("name"), "action": "skip_ext_gate"})
            continue

        old_au = agent_uid(old_doc)
        at = agent_type(old_doc)
        expected = mapped_agent(old_au, at, umap)
        actual = agent_uid(new_doc)
        if not expected:
            skipped += 1
            actions.append({"new_name": new_doc.get("name"), "action": "skip_unmapped_agent"})
            continue
        if actual == expected:
            skipped += 1
            continue

        body = {"agent": meta_obj(at if at in ("organization", "counterparty") else "counterparty", expected)}
        rec = {
            "doc_type": doc_type,
            "new_name": new_doc.get("name"),
            "new_id": new_id,
            "old_agent": old_au,
            "from": actual,
            "to": expected,
        }
        if args.dry:
            fixed += 1
            rec["action"] = "dry_put"
        else:
            r = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_id}", json=body)
            if r.ok:
                fixed += 1
                rec["action"] = "put_ok"
            else:
                errors += 1
                rec["action"] = f"put_fail_{r.status_code}"
        actions.append(rec)
        log.info("%s %s agent %s→%s", "DRY" if args.dry else "PUT", new_doc.get("name"), actual[:8], expected[:8])

    report = {"dry": args.dry, "fixed": fixed, "skipped": skipped, "errors": errors, "actions": actions}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: fixed=%s skipped=%s errors=%s", fixed, skipped, errors)


if __name__ == "__main__":
    main()
