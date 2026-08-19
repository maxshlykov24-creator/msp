#!/usr/bin/env python3
"""
Фаза C: дополнение недостающих документов для ручных заказов РОП в OLD.

По отчёту reverse_verify_manual.json берёт пары (OLD-заказ ↔ NEW-источник) и создаёт
в OLD те связанные документы (demand/paymentin/factureout), которые есть в NEW, но
отсутствуют в OLD, привязывая их к СУЩЕСТВУЮЩЕМУ OLD-заказу. Счета уже на месте.

Якорь идемпотентности: externalCode OLD-документа = UUID NEW-документа. Если документ
с таким externalCode уже есть в OLD — пропуск. Номера не задаём (автонумерация OLD).

  PYTHONPATH=. python3 reverse_attach_docs.py --dry
  PYTHONPATH=. python3 reverse_attach_docs.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, load_umap, make_session, meta_obj,
    old_api_available, save_umap, uid,
)
from reverse_migrate_linked import LinkedMigrator, rows

VERIFY = SCRIPT_DIR / "reverse_verify_manual.json"
REPORT = SCRIPT_DIR / "reverse_attach_docs.json"
LOG_FILE = SCRIPT_DIR / "reverse_attach_docs.log"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        print("Specify --dry or --apply"); sys.exit(1)

    umap = load_umap()
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403"); sys.exit(1)

    verify = json.loads(VERIFY.read_text(encoding="utf-8"))
    pairs = [(o["old_id"], o["new_id"], o["order"], o.get("missing", []))
             for o in verify["orders"] if o.get("new_id") and o.get("missing")]
    log.info("Заказов с недостающими документами: %s", len(pairs))

    existing = {}
    for dt in ["demand", "paymentin", "factureout", "invoiceout"]:
        rws = get_all(old_s, dt, filt="moment>=2026-05-01 00:00:00")
        existing[dt] = {(r.get("externalCode") or "").strip() for r in rws if (r.get("externalCode") or "").strip()}

    lm = LinkedMigrator(umap, old_s, new_s, args.dry)
    rep = {"dry": args.dry, "demand": [], "paymentin": [], "factureout": [], "skipped": [], "blocked": []}

    def create(doctype, body, new_id, oname):
        if new_id in existing.get(doctype, set()):
            rep["skipped"].append({"doctype": doctype, "new": new_id, "order": oname, "why": "already_in_old"})
            return None
        if args.dry:
            rep[doctype].append({"new": new_id, "order": oname, "fields": sorted(body.keys())})
            return "DRY"
        r = api(old_s, "POST", f"{BASE}/entity/{doctype}", json=body)
        if not r.ok:
            rep["blocked"].append({"doctype": doctype, "new": new_id, "order": oname,
                                   "error": f"{r.status_code} {r.text[:200]}"})
            log.error("POST %s fail (%s): %s %s", doctype, oname, r.status_code, r.text[:200])
            return None
        old_id = uid(r.json()["meta"]["href"])
        umap.setdefault(doctype, {})[old_id] = new_id
        rep[doctype].append({"new": new_id, "old": old_id, "order": oname, "old_name": r.json().get("name")})
        log.info("СОЗДАН %s OLD %s ← заказ %s", doctype, r.json().get("name"), oname)
        return old_id

    for old_oid, new_oid, oname, missing in pairs:
        old_order_meta = meta_obj("customerorder", old_oid)
        sf = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_oid}",
                 params={"expand": "demands,payments"}).json()

        if any(x.startswith("demand") for x in missing):
            for dref in rows(sf.get("demands")):
                did = uid(dref["meta"]["href"])
                dm = api(new_s, "GET", f"{BASE}/entity/demand/{did}",
                         params={"expand": "positions.assortment,factureOut"}).json()
                lm.unresolved = []
                body = lm.common(dm, "demand", old_order_meta)
                if lm.unresolved:
                    rep["blocked"].append({"doctype": "demand", "new": did, "order": oname, "unresolved": lm.unresolved}); continue
                old_dem = create("demand", body, did, oname)
                fo = dm.get("factureOut")
                if isinstance(fo, dict) and old_dem and old_dem != "DRY":
                    fid = uid(fo["meta"]["href"])
                    ff = api(new_s, "GET", f"{BASE}/entity/factureout/{fid}").json()
                    lm.unresolved = []
                    fb = {"moment": ff.get("moment"), "externalCode": fid, "demands": [meta_obj("demand", old_dem)]}
                    org = lm.ref(ff.get("organization"), lm.r_org, "organization", "organization")
                    ag = lm.ref(ff.get("agent"), lm.r_cp, "counterparty", "agent")
                    if org: fb["organization"] = org
                    if ag: fb["agent"] = ag
                    if not lm.unresolved:
                        create("factureout", fb, fid, oname)

        if any(x.startswith("paymentin") for x in missing):
            for pref in rows(sf.get("payments")):
                if pref["meta"]["type"] != "paymentin":
                    continue
                pid = uid(pref["meta"]["href"])
                pm = api(new_s, "GET", f"{BASE}/entity/paymentin/{pid}").json()
                lm.unresolved = []
                pb = {"moment": pm.get("moment"), "sum": pm.get("sum"),
                      "applicable": pm.get("applicable", True), "externalCode": pid}
                org = lm.ref(pm.get("organization"), lm.r_org, "organization", "organization")
                ag = lm.ref(pm.get("agent"), lm.r_cp, "counterparty", "agent")
                if org: pb["organization"] = org
                if ag: pb["agent"] = ag
                st = pm.get("state")
                if isinstance(st, dict):
                    sm = lm.state_for("paymentin", uid(st["meta"]["href"]))
                    if sm: pb["state"] = sm
                for f in ["paymentPurpose", "incomingNumber", "incomingDate"]:
                    if pm.get(f) is not None: pb[f] = pm[f]
                ops = [{"meta": old_order_meta["meta"], "linkedSum": op.get("linkedSum", pm.get("sum"))}
                       for op in pm.get("operations", []) if op["meta"]["type"] == "customerorder"]
                if ops: pb["operations"] = ops
                if lm.unresolved:
                    rep["blocked"].append({"doctype": "paymentin", "new": pid, "order": oname, "unresolved": lm.unresolved}); continue
                create("paymentin", pb, pid, oname)

    if args.apply:
        save_umap(umap)
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: demand=%s paymentin=%s factureout=%s skipped=%s blocked=%s",
             len(rep["demand"]), len(rep["paymentin"]), len(rep["factureout"]),
             len(rep["skipped"]), len(rep["blocked"]))
    if rep["blocked"]:
        log.warning("BLOCKED: %s", [(b["doctype"], b["order"]) for b in rep["blocked"]])
    log.info("Отчёт: %s", REPORT)


if __name__ == "__main__":
    main()
