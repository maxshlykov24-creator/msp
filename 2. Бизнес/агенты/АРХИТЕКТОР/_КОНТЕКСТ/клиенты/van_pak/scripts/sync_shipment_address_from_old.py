#!/usr/bin/env python3
"""
Backfill shipmentAddress (Адрес доставки) OLD → NEW for mapped customerorder pairs.

Поле документа customerorder.shipmentAddress — не custom attribute.
migrate.py / catchup раньше не копировали его → массовый пропуск.

  python3 sync_shipment_address_from_old.py --dry
  python3 sync_shipment_address_from_old.py --from-date 2026-01-01
  python3 sync_shipment_address_from_old.py --date 2026-06-04
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
    YEAR_START,
    api,
    load_umap,
    make_session,
    old_api_available,
)

LOG_FILE = SCRIPT_DIR / "sync_shipment_address.log"
REPORT_JSON = SCRIPT_DIR / "sync_shipment_address_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def moment_ok(moment: str, from_d: str | None, to_d: str | None, day: str | None) -> bool:
    if day and not moment.startswith(day):
        return False
    if from_d and moment < from_d:
        return False
    if to_d and moment > (f"{to_d} 23:59:59" if len(to_d) == 10 else to_d):
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from-date", default=YEAR_START.split()[0])
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    if not old_api_available(make_session(OLD_TOKEN)):
        report = {"dry": args.dry, "blocked": "OLD API 403", "fixed": 0, "errors": 1}
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.error("BLOCKER: OLD API недоступен (403)")
        return

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    co_map = load_umap().get("customerorder", {})

    fixed = skipped = errors = 0
    actions: list[dict] = []

    for old_id, new_id in co_map.items():
        ro = api(old_s, "GET", f"{BASE}/entity/customerorder/{old_id}")
        if not ro.ok:
            errors += 1
            continue
        old_doc = ro.json()
        moment = old_doc.get("moment") or ""
        if not moment_ok(moment, args.from_date, args.to_date, args.date):
            continue

        old_addr = (old_doc.get("shipmentAddress") or "").strip()
        if not old_addr:
            skipped += 1
            continue

        rn = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_id}")
        if not rn.ok:
            errors += 1
            continue
        new_doc = rn.json()
        new_addr = (new_doc.get("shipmentAddress") or "").strip()
        if old_addr == new_addr:
            skipped += 1
            continue

        rec = {
            "old_name": old_doc.get("name"),
            "new_name": new_doc.get("name"),
            "new_id": new_id,
            "moment": moment,
        }
        if args.dry:
            fixed += 1
            rec["action"] = "dry_put"
        else:
            r = api(new_s, "PUT", f"{BASE}/entity/customerorder/{new_id}", json={"shipmentAddress": old_doc["shipmentAddress"]})
            if r.ok:
                fixed += 1
                rec["action"] = "put_ok"
            else:
                errors += 1
                rec["action"] = f"put_fail_{r.status_code}"
        actions.append(rec)
        log.info("%s %s → %s", "DRY" if args.dry else "PUT", old_doc.get("name"), new_doc.get("name"))

    report = {"dry": args.dry, "fixed": fixed, "skipped": skipped, "errors": errors, "actions": actions}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: fixed=%s skipped=%s errors=%s", fixed, skipped, errors)


if __name__ == "__main__":
    main()
