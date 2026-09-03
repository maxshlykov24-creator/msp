#!/usr/bin/env python3
"""
Sync document fields from OLD → NEW on TRUSTED/MOMENT_RESOLVED pairs:
  - salesChannel (by name)
  - project (via uuid_map)
  - store (via uuid_map)
  - contract (via uuid_map)
  - deliveryPlannedMoment (customerorder only)

  PYTHONPATH=. python3 sync_doc_fields_from_old.py --dry
  PYTHONPATH=. python3 sync_doc_fields_from_old.py --from-date 2026-01-01 --to-date 2026-06-30
  PYTHONPATH=. python3 sync_doc_fields_from_old.py --doc-type demand
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
    is_uuid,
    load_umap,
    make_session,
    meta_obj,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "sync_doc_fields_from_old.log"
REPORT_JSON = SCRIPT_DIR / "sync_doc_fields_report.json"
PAIRS_FILE = SCRIPT_DIR / "audit_doc_pairs_report.json"
FIXABLE = frozenset({"TRUSTED", "TRUSTED_RENAME", "MOMENT_RESOLVED"})

DOC_TYPES = [
    "customerorder",
    "demand",
    "invoiceout",
    "paymentin",
    "paymentout",
    "factureout",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def build_sales_channel_map(new_s) -> dict[str, str]:
    channels = get_all(new_s, "saleschannel")
    return {c["name"]: uid(c["meta"]["href"]) for c in channels if c.get("name")}


def load_pairs(pairs_file: Path, from_d: str | None, to_d: str | None, doc_type: str | None) -> list[dict]:
    data = json.loads(pairs_file.read_text(encoding="utf-8"))
    rows = [p for p in data.get("pairs", []) if p.get("status") in FIXABLE]
    if doc_type:
        rows = [p for p in rows if p.get("doc_type") == doc_type]
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


def fields_differ(old_doc: dict, new_doc: dict, umap: dict, sc_map: dict, doc_type: str) -> dict:
    """Return body dict with fields that need updating, empty if all match."""
    body: dict = {}

    # salesChannel (by name)
    old_sc = (old_doc.get("salesChannel") or {}).get("name", "")
    new_sc = (new_doc.get("salesChannel") or {}).get("name", "")
    if old_sc and old_sc != new_sc:
        new_sc_id = sc_map.get(old_sc)
        if new_sc_id:
            body["salesChannel"] = meta_obj("saleschannel", new_sc_id)
        else:
            log.warning("  salesChannel '%s' not found in NEW", old_sc)

    # project
    old_proj = uid((old_doc.get("project") or {}).get("meta", {}).get("href", ""))
    new_proj_on_doc = uid((new_doc.get("project") or {}).get("meta", {}).get("href", ""))
    if old_proj:
        mapped_proj = umap.get("project", {}).get(old_proj)
        if mapped_proj and mapped_proj != new_proj_on_doc:
            body["project"] = meta_obj("project", mapped_proj)

    # store (only for types that have it at doc level)
    if doc_type in ("demand", "customerorder", "invoiceout", "factureout"):
        old_store = uid((old_doc.get("store") or {}).get("meta", {}).get("href", ""))
        new_store_on_doc = uid((new_doc.get("store") or {}).get("meta", {}).get("href", ""))
        if old_store:
            mapped_store = umap.get("store", {}).get(old_store)
            if mapped_store and mapped_store != new_store_on_doc:
                body["store"] = meta_obj("store", mapped_store)

    # contract
    old_contract = uid((old_doc.get("contract") or {}).get("meta", {}).get("href", ""))
    new_contract_on_doc = uid((new_doc.get("contract") or {}).get("meta", {}).get("href", ""))
    if old_contract:
        mapped_contract = umap.get("contract", {}).get(old_contract)
        if mapped_contract and mapped_contract != new_contract_on_doc:
            body["contract"] = meta_obj("contract", mapped_contract)

    # deliveryPlannedMoment (customerorder only)
    if doc_type == "customerorder":
        old_dpm = old_doc.get("deliveryPlannedMoment")
        new_dpm = new_doc.get("deliveryPlannedMoment")
        if old_dpm and old_dpm != new_dpm:
            body["deliveryPlannedMoment"] = old_dpm

    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from-date", default=None)
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--doc-type", default=None, choices=DOC_TYPES)
    ap.add_argument("--pairs-file", default=str(PAIRS_FILE))
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    pairs_path = Path(args.pairs_file)
    if not pairs_path.exists():
        log.error("No pairs file: %s — run audit_doc_pairs.py first", pairs_path)
        sys.exit(1)

    sc_map = build_sales_channel_map(new_s)
    log.info("salesChannels in NEW: %s", len(sc_map))

    pairs = load_pairs(pairs_path, args.from_date, args.to_date, args.doc_type)
    log.info("Pairs to process: %s", len(pairs))

    fixed = skipped = errors = already = 0
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

        # Anchor gate
        ext = (new_doc.get("externalCode") or "").strip()
        inv = {v: k for k, v in umap.get(doc_type, {}).items()}
        if not ((is_uuid(ext) and ext == old_id) or inv.get(new_id) == old_id):
            skipped += 1
            continue

        body = fields_differ(old_doc, new_doc, umap, sc_map, doc_type)
        if not body:
            already += 1
            continue

        rec = {
            "doc_type": doc_type,
            "new_name": new_doc.get("name"),
            "new_id": new_id,
            "fields": list(body.keys()),
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
                rec["error"] = r.text[:200]
        actions.append(rec)
        log.info(
            "%s %s/%s fields=%s",
            "DRY" if args.dry else "PUT",
            doc_type,
            new_doc.get("name"),
            list(body.keys()),
        )

    report = {
        "dry": args.dry,
        "pairs_processed": len(pairs),
        "fixed": fixed,
        "already": already,
        "skipped": skipped,
        "errors": errors,
        "actions": actions,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: fixed=%s already=%s skipped=%s errors=%s", fixed, already, skipped, errors)


if __name__ == "__main__":
    main()
