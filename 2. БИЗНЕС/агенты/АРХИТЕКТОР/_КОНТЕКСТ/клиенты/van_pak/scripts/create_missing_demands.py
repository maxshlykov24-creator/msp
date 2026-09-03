#!/usr/bin/env python3
"""
Создать недостающие demand в NEW по списку OLD uuid (или авто-аудит 2026).

  python3 create_missing_demands.py --dry
  python3 create_missing_demands.py --auto-2026
  python3 create_missing_demands.py --old-ids uuid1,uuid2
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from catchup_today import TodayCatchup, uuid_from_href
from ms_common import YEAR_START, get_all, load_umap, make_session, old_api_available, uid
from ms_common import OLD_TOKEN

SCRIPT_DIR = Path(__file__).parent
REPORT = SCRIPT_DIR / "create_missing_demands_report.json"
LOG = SCRIPT_DIR / "create_missing_demands.log"

# OLD uuid → shared NEW wrongly (need own document)
DUP_SECONDARY = [
    "d7ec0193-5dc0-11f1-0a80-0e180038b74b",  # ВА-18532
    "c214f6dc-5e7f-11f1-0a80-15a0001076dd",  # АФ-00004
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def find_auto_2026() -> list[str]:
    old_s = make_session(OLD_TOKEN)
    if not old_api_available(old_s):
        raise RuntimeError("OLD API 403")
    filt = f"moment>={YEAR_START};moment<=2026-12-31 23:59:59"
    old_rows = get_all(old_s, "demand", filt=filt)
    umap = load_umap().get("demand", {})
    need: list[str] = []
    for d in old_rows:
        oid = uid(d["meta"]["href"])
        if oid not in umap:
            need.append(oid)
    for oid in DUP_SECONDARY:
        if oid not in need:
            need.append(oid)
    return need


def create_one(c: TodayCatchup, old_uuid: str, dry: bool, force: bool) -> dict:
    doc_type = "demand"
    has_positions = True
    if force and c.umap.has(doc_type, old_uuid):
        old_nid = c.umap.get(doc_type, old_uuid)
        log.info("  force: снять map %s → %s", old_uuid[:8], (old_nid or "")[:8])
        if not dry:
            c.umap.data.setdefault(doc_type, {}).pop(old_uuid, None)

    try:
        full = c._get_full_old(doc_type, old_uuid, has_positions)
    except Exception as e:
        return {"old_uuid": old_uuid, "action": "error", "reason": str(e)[:200]}

    name = full.get("name", "?")
    try:
        old_attrs = c.src.get(f"entity/{doc_type}/metadata/attributes").get("rows", [])
    except Exception:
        old_attrs = []
    attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

    c._ensure_agents_in_doc(full)
    body = c._build_doc_body(full, doc_type, has_positions, attrs_index)
    if body is None:
        return {"old_uuid": old_uuid, "name": name, "action": "error", "reason": "no body"}

    body = c._extend_body(full, doc_type, body)
    new_uuid = c.umap.get(doc_type, old_uuid)

    if dry:
        return {"old_uuid": old_uuid, "name": name, "action": "dry_create"}

    if new_uuid:
        return {"old_uuid": old_uuid, "name": name, "action": "skip", "reason": "already mapped"}

    try:
        r = c.dst.post(f"entity/{doc_type}", body)
        new_uuid = uuid_from_href(r["meta"]["href"])
        c.umap.set(doc_type, old_uuid, new_uuid, save=False)
        log.info("  ✓ POST demand/%s → %s", name, new_uuid[:8])
        return {"old_uuid": old_uuid, "name": name, "action": "created", "new_uuid": new_uuid}
    except Exception as e:
        err = str(e)
        if "3006" in err and full.get("name"):
            existing = c._find_existing_doc(doc_type, full["name"])
            if existing and (full.get("externalCode") or old_uuid) == old_uuid:
                c.umap.set(doc_type, old_uuid, existing, save=False)
                return {"old_uuid": old_uuid, "name": name, "action": "linked_existing", "new_uuid": existing}
        log.warning("  ✗ POST demand/%s: %s", name, err[:250])
        return {"old_uuid": old_uuid, "name": name, "action": "error", "reason": err[:250]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--auto-2026", action="store_true")
    ap.add_argument("--old-ids", default=None, help="comma-separated OLD uuids")
    args = ap.parse_args()

    if args.auto_2026:
        old_ids = find_auto_2026()
    elif args.old_ids:
        old_ids = [x.strip() for x in args.old_ids.split(",") if x.strip()]
    else:
        old_ids = find_auto_2026()

    log.info("Создание demand: %s шт, dry=%s", len(old_ids), args.dry)
    c = TodayCatchup()
    actions = []
    for oid in old_ids:
        force = oid in DUP_SECONDARY
        actions.append(create_one(c, oid, args.dry, force=force))

    if not args.dry:
        c.umap.save()

    created = sum(1 for a in actions if a.get("action") == "created")
    errors = sum(1 for a in actions if a.get("action") == "error")
    report = {"dry": args.dry, "requested": len(old_ids), "created": created, "errors": errors, "actions": actions}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: created=%s errors=%s → %s", created, errors, REPORT)


if __name__ == "__main__":
    main()
