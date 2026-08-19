#!/usr/bin/env python3
"""
Sync customerorder fields from OLD → NEW on fixable pairs:
  - shipmentAddress (Адрес доставки — поле документа, не custom attr)
  - 4 custom attributes (Способ поставки, Оплачен менеджеру, …)

  python3 sync_order_fields_from_old.py --dry
  python3 sync_order_fields_from_old.py --from-date 2026-05-01 --to-date 2026-06-30
  python3 sync_order_fields_from_old.py --date 2026-06-04
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE,
    ORDER_ATTR_NAMES,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    api,
    attr_val,
    is_uuid,
    load_umap,
    make_session,
    map_order_attributes,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "sync_order_fields.log"
REPORT_JSON = SCRIPT_DIR / "sync_order_fields_report.json"
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


def load_order_pairs(pairs_file: Path, from_d: str | None, to_d: str | None) -> list[dict]:
    data = json.loads(pairs_file.read_text(encoding="utf-8"))
    rows = [
        p for p in data.get("pairs", [])
        if p.get("status") in FIXABLE and p.get("doc_type") == "customerorder"
    ]
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


def merge_attributes(existing: list, mapped: list) -> list[dict]:
    """Replace mapped attr names; keep other attrs."""
    by_name: dict[str, dict] = {}
    name_by_href: dict[str, str] = {}
    for a in existing or []:
        href = (a.get("meta") or {}).get("href", "")
        name_by_href[href] = a.get("name", "")
        by_name[a.get("name", href)] = a
    for ma in mapped:
        href = ma["meta"]["href"]
        # find name from ORDER_ATTR_NAMES via mapped attrs — use value merge
        by_name[href] = ma
    # Rebuild: keep non-order attrs + mapped order attrs
    order_hrefs = {m["meta"]["href"] for m in mapped}
    result = [a for a in (existing or []) if (a.get("meta") or {}).get("href") not in order_hrefs]
    result.extend(mapped)
    return result


def fields_need_update(old_doc: dict, new_doc: dict) -> bool:
    old_addr = (old_doc.get("shipmentAddress") or "").strip()
    new_addr = (new_doc.get("shipmentAddress") or "").strip()
    if old_addr and old_addr != new_addr:
        return True
    for name in ORDER_ATTR_NAMES:
        if attr_val(old_doc, name) != attr_val(new_doc, name):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from-date", default=None)
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--date", default=None, help="Single day YYYY-MM-DD (moment filter on pairs)")
    ap.add_argument("--pairs-file", default=str(PAIRS_FILE))
    ap.add_argument("--new-ids", default=None)
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        report = {"dry": args.dry, "blocked": "OLD API 403", "fixed": 0, "skipped": 0, "errors": 1}
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API недоступен (403). Sync order attrs невозможен.")
        return
    pairs = load_order_pairs(Path(args.pairs_file), args.from_date, args.to_date)
    if args.date:
        pairs = [p for p in pairs if (p.get("moment") or "").startswith(args.date)]
    if args.new_ids:
        allow = set(args.new_ids.split(","))
        pairs = [p for p in pairs if p.get("new_id") in allow]

    fixed = skipped = errors = unmapped = 0
    actions: list[dict] = []

    for p in pairs:
        old_id = p.get("old_id")
        new_id = p.get("new_id")
        if not old_id or not new_id:
            skipped += 1
            continue
        ro = api(old_s, "GET", f"{BASE}/entity/customerorder/{old_id}")
        rn = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_id}")
        if not ro.ok or not rn.ok:
            errors += 1
            continue
        old_doc = ro.json()
        new_doc = rn.json()

        ext = (new_doc.get("externalCode") or "").strip()
        inv = {v: k for k, v in umap.get("customerorder", {}).items()}
        if not ((is_uuid(ext) and ext == old_id) or inv.get(new_id) == old_id):
            skipped += 1
            continue

        if not fields_need_update(old_doc, new_doc):
            skipped += 1
            continue

        body: dict = {}
        old_addr = (old_doc.get("shipmentAddress") or "").strip()
        new_addr = (new_doc.get("shipmentAddress") or "").strip()
        if old_addr and old_addr != new_addr:
            body["shipmentAddress"] = old_doc["shipmentAddress"]

        mapped = map_order_attributes(old_doc.get("attributes"), umap)
        if mapped:
            existing = new_doc.get("attributes") or []
            mapped_hrefs = {m["meta"]["href"] for m in mapped}
            kept = [a for a in existing if (a.get("meta") or {}).get("href") not in mapped_hrefs]
            body["attributes"] = kept + mapped
        elif not body:
            unmapped += 1
            actions.append({"new_name": new_doc.get("name"), "action": "unmapped_attr_values"})
            continue

        rec = {
            "new_name": new_doc.get("name"),
            "new_id": new_id,
            "shipmentAddress": bool(body.get("shipmentAddress")),
            "mapped_count": len(mapped) if mapped else 0,
        }

        if args.dry:
            fixed += 1
            rec["action"] = "dry_put"
        else:
            r = api(new_s, "PUT", f"{BASE}/entity/customerorder/{new_id}", json=body)
            if r.ok:
                fixed += 1
                rec["action"] = "put_ok"
            else:
                errors += 1
                rec["action"] = f"put_fail_{r.status_code}"
        actions.append(rec)
        parts = []
        if body.get("shipmentAddress"):
            parts.append("addr")
        if mapped:
            parts.append(f"attrs×{len(mapped)}")
        log.info("%s %s %s", "DRY" if args.dry else "PUT", new_doc.get("name"), " ".join(parts))

    report = {
        "dry": args.dry,
        "fixed": fixed,
        "skipped": skipped,
        "unmapped": unmapped,
        "errors": errors,
        "actions": actions,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: fixed=%s skipped=%s unmapped=%s errors=%s", fixed, skipped, unmapped, errors)


if __name__ == "__main__":
    main()
