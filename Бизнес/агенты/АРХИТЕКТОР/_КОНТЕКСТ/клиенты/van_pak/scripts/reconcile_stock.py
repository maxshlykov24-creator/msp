#!/usr/bin/env python3
"""
Подравнивание остатков NEW под OLD: один enter и один loss на склад (все строки внутри).

  python3 audit_stock.py          # сначала
  python3 reconcile_stock.py --dry
  python3 reconcile_stock.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from ms_common import (
    BASE,
    NEW_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    load_umap,
    make_session,
    meta_obj,
    uid,
)

LOG_FILE = SCRIPT_DIR / "reconcile_stock.log"
REPORT_JSON = SCRIPT_DIR / "reconcile_stock_report.json"
AUDIT_FILE = SCRIPT_DIR / "audit_stock_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def store_names(new_s) -> dict[str, str]:
    return {uid(s["meta"]["href"]): s.get("name", "?") for s in get_all(new_s, "store")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--audit-file", default=str(AUDIT_FILE))
    ap.add_argument(
        "--moment",
        default=(datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
        help="Момент документа (не в будущем — иначе остатки не меняются)",
    )
    args = ap.parse_args()

    audit = json.loads(Path(args.audit_file).read_text(encoding="utf-8"))
    mismatches = audit.get("mismatches", [])
    if not mismatches:
        log.info("Нет расхождений — skip")
        REPORT_JSON.write_text(
            json.dumps({"dry": args.dry, "enter_docs": 0, "loss_docs": 0}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    new_s = make_session(NEW_TOKEN)
    orgs = get_all(new_s, "organization")
    if not orgs:
        log.error("Нет organization в NEW")
        sys.exit(1)
    org_uid_val = uid(orgs[0]["meta"]["href"])
    snames = store_names(new_s)

    by_store_enter: dict[str, list] = defaultdict(list)
    by_store_loss: dict[str, list] = defaultdict(list)

    for m in mismatches:
        delta = m["delta"]
        store = m["store"]
        et = m["assortment_type"]
        au = m["assortment_id"]
        if abs(delta) < 1e-6:
            continue
        pos = {
            "assortment": meta_obj(et, au),
            "quantity": abs(delta),
        }
        if delta > 0:
            by_store_enter[store].append(pos)
        else:
            by_store_loss[store].append(pos)

    created = {"enter_docs": 0, "loss_docs": 0, "enter_lines": 0, "loss_lines": 0}
    errors = 0
    actions = []
    tag = date.today().isoformat()

    for store, positions in sorted(by_store_enter.items()):
        body = {
            "moment": args.moment,
            "applicable": True,
            "organization": meta_obj("organization", org_uid_val),
            "store": meta_obj("store", store),
            "description": f"Сверка остатков {tag}: оприходование ({snames.get(store, store[:8])})",
            "positions": positions,
        }
        rec = {
            "type": "enter",
            "store": store,
            "store_name": snames.get(store, ""),
            "positions": len(positions),
        }
        if args.dry:
            created["enter_docs"] += 1
            created["enter_lines"] += len(positions)
            rec["action"] = "dry_post"
        else:
            r = api(new_s, "POST", f"{BASE}/entity/enter", json=body)
            if r.ok:
                created["enter_docs"] += 1
                created["enter_lines"] += len(positions)
                rec["action"] = "post_ok"
                rec["id"] = uid(r.json().get("meta", {}).get("href", ""))
                rec["name"] = r.json().get("name", "")
            else:
                errors += 1
                rec["action"] = f"post_fail_{r.status_code}"
                rec["error"] = r.text[:300]
        actions.append(rec)
        log.info(
            "%s enter [%s] pos=%s",
            "DRY" if args.dry else "POST",
            snames.get(store, store[:8]),
            len(positions),
        )

    for store, positions in sorted(by_store_loss.items()):
        body = {
            "moment": args.moment,
            "applicable": True,
            "organization": meta_obj("organization", org_uid_val),
            "store": meta_obj("store", store),
            "description": f"Сверка остатков {tag}: списание ({snames.get(store, store[:8])})",
            "positions": positions,
        }
        rec = {
            "type": "loss",
            "store": store,
            "store_name": snames.get(store, ""),
            "positions": len(positions),
        }
        if args.dry:
            created["loss_docs"] += 1
            created["loss_lines"] += len(positions)
            rec["action"] = "dry_post"
        else:
            r = api(new_s, "POST", f"{BASE}/entity/loss", json=body)
            if r.ok:
                created["loss_docs"] += 1
                created["loss_lines"] += len(positions)
                rec["action"] = "post_ok"
                rec["id"] = uid(r.json().get("meta", {}).get("href", ""))
                rec["name"] = r.json().get("name", "")
            else:
                errors += 1
                rec["action"] = f"post_fail_{r.status_code}"
                rec["error"] = r.text[:300]
        actions.append(rec)
        log.info(
            "%s loss [%s] pos=%s",
            "DRY" if args.dry else "POST",
            snames.get(store, store[:8]),
            len(positions),
        )

    report = {
        "dry": args.dry,
        "moment": args.moment,
        "created": created,
        "errors": errors,
        "actions": actions,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: enter_docs=%s (%s строк) loss_docs=%s (%s строк) errors=%s",
        created["enter_docs"],
        created["enter_lines"],
        created["loss_docs"],
        created["loss_lines"],
        errors,
    )


if __name__ == "__main__":
    main()
