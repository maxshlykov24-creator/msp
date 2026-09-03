#!/usr/bin/env python3
"""
Sync document state (этап/статус) from OLD → NEW on TRUSTED/MOMENT_RESOLVED pairs.

  PYTHONPATH=. python3 sync_state_from_old.py --dry
  PYTHONPATH=. python3 sync_state_from_old.py --from-date 2026-01-01 --to-date 2026-06-30
  PYTHONPATH=. python3 sync_state_from_old.py --doc-type customerorder
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
    is_uuid,
    load_umap,
    make_session,
    old_api_available,
    save_umap,
    uid,
)

LOG_FILE = SCRIPT_DIR / "sync_state_from_old.log"
REPORT_JSON = SCRIPT_DIR / "sync_state_report.json"
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


def resolve_state(old_s, new_s, umap: dict, entity_type: str, old_state: dict) -> dict | None:
    """Resolve OLD state meta → NEW state meta, cache in uuid_map."""
    if not old_state:
        return None
    old_href = (old_state.get("meta") or {}).get("href", "")
    if not old_href:
        return None
    old_state_id = uid(old_href)
    state_name = old_state.get("name")
    if not state_name:
        rs = api(old_s, "GET", old_href)
        if rs.ok:
            state_name = rs.json().get("name")
    if not state_name:
        return None

    ns = f"state_{entity_type}"
    cached = umap.get(ns, {}).get(old_state_id)
    if cached:
        return {"meta": {"href": f"{BASE}/entity/{entity_type}/metadata/states/{cached}", "type": "state", "mediaType": "application/json"}}

    # Fetch NEW states
    r = api(new_s, "GET", f"{BASE}/entity/{entity_type}/metadata/states")
    if not r.ok:
        return None
    for s in r.json().get("rows", []):
        if s.get("name") == state_name:
            new_id = uid(s["meta"]["href"])
            umap.setdefault(ns, {})[old_state_id] = new_id
            return {"meta": {"href": f"{BASE}/entity/{entity_type}/metadata/states/{new_id}", "type": "state", "mediaType": "application/json"}}

    # Create state in NEW
    body = {
        "name": state_name,
        "stateType": old_state.get("stateType", "Regular"),
        "color": old_state.get("color", 15106326),
    }
    rc = api(new_s, "POST", f"{BASE}/entity/{entity_type}/metadata/states", json=body)
    if rc.ok:
        new_id = uid(rc.json()["meta"]["href"])
        umap.setdefault(ns, {})[old_state_id] = new_id
        log.info("  + state %s/%s → %s", entity_type, state_name, new_id[:8])
        return {"meta": {"href": f"{BASE}/entity/{entity_type}/metadata/states/{new_id}", "type": "state", "mediaType": "application/json"}}
    log.warning("  ✗ create state %s/%s: %s", entity_type, state_name, rc.text[:120])
    return None


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

        # Anchor gate
        ro = api(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_id}")
        rn = api(new_s, "GET", f"{BASE}/entity/{doc_type}/{new_id}")
        if not ro.ok or not rn.ok:
            errors += 1
            continue

        old_doc = ro.json()
        new_doc = rn.json()

        # Verify anchor
        ext = (new_doc.get("externalCode") or "").strip()
        inv = {v: k for k, v in umap.get(doc_type, {}).items()}
        if not ((is_uuid(ext) and ext == old_id) or inv.get(new_id) == old_id):
            skipped += 1
            continue

        old_state = old_doc.get("state")
        new_state = new_doc.get("state")

        old_state_id = uid((old_state.get("meta") or {}).get("href", "")) if old_state else None
        new_state_id = uid((new_state.get("meta") or {}).get("href", "")) if new_state else None

        ns = f"state_{doc_type}"
        expected_new_id = umap.get(ns, {}).get(old_state_id) if old_state_id else None

        if old_state_id and expected_new_id and new_state_id == expected_new_id:
            already += 1
            continue
        if not old_state:
            skipped += 1
            continue

        new_state_meta = resolve_state(old_s, new_s, umap, doc_type, old_state)
        if not new_state_meta:
            skipped += 1
            actions.append({"doc_type": doc_type, "new_name": new_doc.get("name"), "action": "skip_no_state_map"})
            continue

        rec = {"doc_type": doc_type, "new_name": new_doc.get("name"), "new_id": new_id,
               "old_state": old_state.get("name"), "new_state_before": (new_state or {}).get("name")}

        if args.dry:
            fixed += 1
            rec["action"] = "dry_put"
        else:
            r = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_id}", json={"state": new_state_meta})
            if r.ok:
                fixed += 1
                rec["action"] = "put_ok"
            else:
                errors += 1
                rec["action"] = f"put_fail_{r.status_code}"
                rec["error"] = r.text[:200]
        actions.append(rec)
        log.info("%s %s/%s state→%s", "DRY" if args.dry else "PUT", doc_type, new_doc.get("name"), old_state.get("name"))

    save_umap(umap)

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
