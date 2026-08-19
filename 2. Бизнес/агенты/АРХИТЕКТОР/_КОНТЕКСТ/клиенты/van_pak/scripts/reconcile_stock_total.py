#!/usr/bin/env python3
"""
Выравнивание остатков NEW под OLD по полному отчёту audit_stock_total.py.

  PYTHONPATH=. python3 audit_stock_total.py
  PYTHONPATH=. python3 reconcile_stock_total.py --dry-run
  PYTHONPATH=. python3 reconcile_stock_total.py --apply
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
    make_session,
    meta_obj,
    uid,
)

LOG_FILE = SCRIPT_DIR / "reconcile_stock_total.log"
DRY_RUN_JSON = SCRIPT_DIR / "reconcile_stock_total_dry_run.json"
REPORT_JSON = SCRIPT_DIR / "reconcile_stock_total_report.json"
AUDIT_FILE = SCRIPT_DIR / "audit_stock_total_report.json"

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


def collect_reconcilable_lines(audit: dict) -> list[dict]:
    lines: list[dict] = []
    for bucket in ("mapped_both", "new_only"):
        for x in audit.get("buckets", {}).get(bucket, []):
            if abs(x.get("delta", 0)) < 1e-6:
                continue
            if bucket == "old_only":
                continue
            store = x.get("store")
            et = x.get("assortment_type")
            au = x.get("assortment_id")
            if not store or not et or not au:
                log.warning("skip incomplete line: %s", x)
                continue
            lines.append(x)
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Только план, без POST")
    ap.add_argument("--apply", action="store_true", help="Создать документы в NEW")
    ap.add_argument("--audit-file", default=str(AUDIT_FILE))
    ap.add_argument(
        "--moment",
        default=(datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        log.error("Укажите --dry-run или --apply")
        sys.exit(1)

    audit_path = Path(args.audit_file)
    if not audit_path.exists():
        log.error("Нет файла аудита: %s — сначала audit_stock_total.py", audit_path)
        sys.exit(1)

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    stop = audit.get("stop_conditions", {})
    if stop.get("bad_mapping_with_stock"):
        log.error("STOP: bad_mapping — исправьте uuid_map перед reconcile")
        sys.exit(1)

    old_only = audit.get("buckets", {}).get("old_only", [])
    if old_only:
        log.warning(
            "old_only: %s позиций (qty=%.2f) — не будут оприходованы (нет в NEW)",
            len(old_only),
            sum(x.get("qty_old", 0) for x in old_only),
        )

    lines = collect_reconcilable_lines(audit)
    if not lines:
        log.info("Нет reconcilable расхождений")
        out = DRY_RUN_JSON if args.dry_run else REPORT_JSON
        out.write_text(
            json.dumps({"dry_run": args.dry_run, "enter_docs": 0, "loss_docs": 0}, ensure_ascii=False, indent=2),
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

    for x in lines:
        delta = x["delta"]
        store = x["store"]
        et = x["assortment_type"]
        au = x["assortment_id"]
        pos = {"assortment": meta_obj(et, au), "quantity": abs(delta)}
        if delta > 0:
            by_store_enter[store].append(pos)
        else:
            by_store_loss[store].append(pos)

    created = {"enter_docs": 0, "loss_docs": 0, "enter_lines": 0, "loss_lines": 0}
    errors = 0
    actions = []
    tag = date.today().isoformat()
    dry = args.dry_run

    for store, positions in sorted(by_store_enter.items()):
        body = {
            "moment": args.moment,
            "applicable": True,
            "organization": meta_obj("organization", org_uid_val),
            "store": meta_obj("store", store),
            "description": f"Сверка TOTAL {tag}: оприходование ({snames.get(store, store[:8])})",
            "positions": positions,
        }
        rec = {
            "type": "enter",
            "store": store,
            "store_name": snames.get(store, ""),
            "positions": len(positions),
            "total_qty": sum(p["quantity"] for p in positions),
        }
        if dry:
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
        log.info("%s enter [%s] pos=%s qty=%.2f", "DRY" if dry else "POST", snames.get(store), len(positions), rec["total_qty"])

    for store, positions in sorted(by_store_loss.items()):
        body = {
            "moment": args.moment,
            "applicable": True,
            "organization": meta_obj("organization", org_uid_val),
            "store": meta_obj("store", store),
            "description": f"Сверка TOTAL {tag}: списание ({snames.get(store, store[:8])})",
            "positions": positions,
        }
        rec = {
            "type": "loss",
            "store": store,
            "store_name": snames.get(store, ""),
            "positions": len(positions),
            "total_qty": sum(p["quantity"] for p in positions),
        }
        if dry:
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
        log.info("%s loss [%s] pos=%s qty=%.2f", "DRY" if dry else "POST", snames.get(store), len(positions), rec["total_qty"])

    report = {
        "dry_run": dry,
        "apply": args.apply,
        "moment": args.moment,
        "audit_file": str(audit_path),
        "audit_totals": audit.get("totals", {}),
        "reconcile_preview": audit.get("reconcile_preview", {}),
        "created": created,
        "errors": errors,
        "actions": actions,
        "old_only_skipped": len(old_only),
    }
    out = DRY_RUN_JSON if dry else REPORT_JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: enter_docs=%s (%s строк) loss_docs=%s (%s строк) errors=%s → %s",
        created["enter_docs"],
        created["enter_lines"],
        created["loss_docs"],
        created["loss_lines"],
        errors,
        out,
    )


if __name__ == "__main__":
    main()
