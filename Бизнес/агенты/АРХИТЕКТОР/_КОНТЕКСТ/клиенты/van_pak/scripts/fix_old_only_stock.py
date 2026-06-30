#!/usr/bin/env python3
"""
Миграция old_only товаров из audit_stock_total + оприходование/списание остатков.

  PYTHONPATH=. python3 fix_old_only_stock.py --dry-run
  PYTHONPATH=. python3 fix_old_only_stock.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ms_common import (
    BASE,
    MAP_FILE,
    NEW_TOKEN,
    OLD_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    load_umap,
    make_session,
    meta_obj,
    save_umap,
    uid,
)

AUDIT_FILE = SCRIPT_DIR / "audit_stock_total_report.json"
LOG_FILE = SCRIPT_DIR / "fix_old_only_stock.log"
REPORT_JSON = SCRIPT_DIR / "fix_old_only_stock_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def map_product_body(item: dict, umap: dict) -> dict:
    body = {
        "name": item.get("name"),
        "code": item.get("code"),
        "description": item.get("description"),
        "externalCode": uid(item["meta"]["href"]),
    }
    article = item.get("article")
    if article is not None:
        body["article"] = str(article)
    if item.get("archived"):
        body["archived"] = True
    folder_href = (item.get("productFolder") or {}).get("meta", {}).get("href", "")
    if folder_href:
        new_folder = umap.get("productfolder", {}).get(uid(folder_href))
        if new_folder:
            body["productFolder"] = meta_obj("productfolder", new_folder)
    uom_href = (item.get("uom") or {}).get("meta", {}).get("href", "")
    if uom_href:
        new_uom = umap.get("uom", {}).get(uid(uom_href))
        if new_uom:
            body["uom"] = meta_obj("uom", new_uom)
    return body


def ensure_product(old_s, new_s, umap: dict, old_uuid: str) -> str | None:
    if old_uuid in umap.get("product", {}):
        return umap["product"][old_uuid]
    r = api(old_s, "GET", f"{BASE}/entity/product/{old_uuid}")
    if not r.ok:
        log.error("OLD product %s: %s", old_uuid[:8], r.status_code)
        return None
    item = r.json()
    body = map_product_body(item, umap)
    r2 = api(new_s, "POST", f"{BASE}/entity/product", json=body)
    if not r2.ok:
        log.error("POST product %s: %s %s", item.get("code"), r2.status_code, r2.text[:200])
        return None
    new_uuid = uid(r2.json()["meta"]["href"])
    umap.setdefault("product", {})[old_uuid] = new_uuid
    log.info("Migrated product %s → %s (%s)", item.get("code"), new_uuid[:8], item.get("name", "")[:40])
    return new_uuid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit-file", default=str(AUDIT_FILE))
    ap.add_argument(
        "--moment",
        default=(datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        log.error("Укажите --dry-run или --apply")
        sys.exit(1)

    audit = json.loads(Path(args.audit_file).read_text(encoding="utf-8"))
    old_only = audit.get("buckets", {}).get("old_only", [])
    if not old_only:
        log.info("old_only пуст — skip")
        return

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = load_umap()
    store_map = umap.get("store", {})

    orgs = get_all(new_s, "organization")
    org_uid_val = uid(orgs[0]["meta"]["href"]) if orgs else ""
    tag = date.today().isoformat()

    actions = []
    migrated = 0

    for row in old_only:
        old_uuid = row["old_assortment_id"]
        old_store = row["old_store"]
        new_store = store_map.get(old_store)
        if not new_store:
            log.error("Нет store map для %s", old_store[:8])
            continue
        delta = row["delta"]
        code = row.get("code", "")

        rec = {"code": code, "old_uuid": old_uuid, "delta": delta, "old_store": old_store, "new_store": new_store}

        if args.dry_run:
            rec["action"] = "dry_migrate_and_reconcile"
            rec["doc_type"] = "enter" if delta > 0 else "loss"
            rec["quantity"] = abs(delta)
            actions.append(rec)
            continue

        new_uuid = ensure_product(old_s, new_s, umap, old_uuid)
        if not new_uuid:
            rec["action"] = "migrate_fail"
            actions.append(rec)
            continue
        migrated += 1

        if abs(delta) < 1e-6:
            rec["action"] = "migrate_only"
            actions.append(rec)
            continue

        doc_type = "enter" if delta > 0 else "loss"
        body = {
            "moment": args.moment,
            "applicable": True,
            "organization": meta_obj("organization", org_uid_val),
            "store": meta_obj("store", new_store),
            "description": f"old_only сверка {tag}: {code} ({doc_type})",
            "positions": [{"assortment": meta_obj("product", new_uuid), "quantity": abs(delta)}],
        }
        r = api(new_s, "POST", f"{BASE}/entity/{doc_type}", json=body)
        if r.ok:
            rec["action"] = f"{doc_type}_ok"
            rec["new_uuid"] = new_uuid
            rec["doc_id"] = uid(r.json().get("meta", {}).get("href", ""))
            rec["doc_name"] = r.json().get("name", "")
        else:
            rec["action"] = f"{doc_type}_fail_{r.status_code}"
            rec["new_uuid"] = new_uuid
            rec["error"] = r.text[:200]
        actions.append(rec)

    if args.apply and migrated:
        save_umap(umap)

    report = {"dry_run": args.dry_run, "old_only_count": len(old_only), "migrated": migrated, "actions": actions}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: old_only=%s migrated=%s → %s", len(old_only), migrated, REPORT_JSON)


if __name__ == "__main__":
    main()
