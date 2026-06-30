#!/usr/bin/env python3
"""
Фаза 4 обратного переноса: связанные документы для 19 перенесённых заказов.

Переносит из NEW в OLD: invoiceout (счёт), demand (отгрузка), paymentin (входящий
платёж), factureout (счёт-фактура выданная, если есть у отгрузки). Все привязываются
к соответствующему OLD-заказу. externalCode = UUID NEW-документа (идемпотентность).
Номера не задаём — OLD автонумерует (нормализация номеров — отдельно).

  PYTHONPATH=. python3 reverse_migrate_linked.py --dry
  PYTHONPATH=. python3 reverse_migrate_linked.py --apply
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

MIG_REPORT = SCRIPT_DIR / "reverse_migrate_orders.json"
REPORT = SCRIPT_DIR / "reverse_migrate_linked.json"
LOG_FILE = SCRIPT_DIR / "reverse_migrate_linked.log"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def inv(d):
    return {v: k for k, v in d.items()}


def rows(x):
    return (x or {}).get("rows", []) if isinstance(x, dict) else (x or [])


def asg_type(href):
    return href.split("/entity/")[-1].split("/")[0] if "/entity/" in href else "product"


class LinkedMigrator:
    def __init__(self, umap, old_s, new_s, dry):
        self.umap = umap; self.old_s = old_s; self.new_s = new_s; self.dry = dry
        self.r_org = inv(umap.get("organization", {}))
        self.r_store = inv(umap.get("store", {}))
        self.r_project = inv(umap.get("project", {}))
        self.r_sc = inv(umap.get("saleschannel", {}))
        self.r_emp = inv(umap.get("employee", {}))
        self.r_group = inv(umap.get("group", {}))
        self.r_cp = inv(umap.get("counterparty", {}))
        self.r_prod = inv(umap.get("product", {}))
        self.r_var = inv(umap.get("variant", {}))
        self.r_serv = inv(umap.get("service", {}))
        self.co_new2old = inv(umap.get("customerorder", {}))
        self._states = {}
        self.unresolved = []

    def state_for(self, doctype, new_state_id):
        if doctype not in self._states:
            rn = api(self.new_s, "GET", f"{BASE}/entity/{doctype}/metadata")
            ro = api(self.old_s, "GET", f"{BASE}/entity/{doctype}/metadata")
            new_states = {uid(s["meta"]["href"]): s["name"] for s in rn.json().get("states", [])}
            old_by_name = {s["name"]: uid(s["meta"]["href"]) for s in ro.json().get("states", [])}
            self._states[doctype] = {nid: old_by_name.get(nm) for nid, nm in new_states.items()}
        old = self._states[doctype].get(new_state_id)
        if old:
            return {"meta": {"href": f"{BASE}/entity/{doctype}/metadata/states/{old}",
                             "type": "state", "mediaType": "application/json"}}
        return None

    def ref(self, obj, rmap, etype, kind):
        if not isinstance(obj, dict):
            return None
        nid = uid(obj.get("meta", {}).get("href", ""))
        old = rmap.get(nid)
        if not old:
            self.unresolved.append({"kind": kind, "new_id": nid, "name": obj.get("name")})
            return None
        return meta_obj(etype, old)

    def positions(self, rws):
        out = []
        for p in rws:
            href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
            nid = uid(href); t = asg_type(href)
            if t == "variant":
                old, et = self.r_var.get(nid), "variant"
            elif t == "service":
                old, et = self.r_serv.get(nid), "service"
            else:
                old, et = self.r_prod.get(nid), "product"
            if not old:
                self.unresolved.append({"kind": f"assortment/{t}", "new_id": nid}); continue
            pos = {"quantity": p.get("quantity"), "price": p.get("price"),
                   "discount": p.get("discount") or 0, "vat": p.get("vat") or 0,
                   "assortment": meta_obj(et, old)}
            if p.get("reserve"):
                pos["reserve"] = p["reserve"]
            out.append(pos)
        return out

    def common(self, full, doctype, old_order_meta):
        """Общие поля для invoiceout/demand."""
        b = {"moment": full.get("moment"), "applicable": full.get("applicable", True),
             "vatEnabled": full.get("vatEnabled", True), "vatIncluded": full.get("vatIncluded", True),
             "externalCode": uid(full["meta"]["href"]),
             "customerOrder": old_order_meta}
        org = self.ref(full.get("organization"), self.r_org, "organization", "organization")
        ag = self.ref(full.get("agent"), self.r_cp, "counterparty", "agent")
        if org: b["organization"] = org
        if ag: b["agent"] = ag
        for src, rmap, et, key, kind in [
            (full.get("store"), self.r_store, "store", "store", "store"),
            (full.get("project"), self.r_project, "project", "project", "project"),
            (full.get("salesChannel"), self.r_sc, "saleschannel", "salesChannel", "saleschannel"),
            (full.get("owner"), self.r_emp, "employee", "owner", "employee"),
            (full.get("group"), self.r_group, "group", "group", "group"),
        ]:
            m = self.ref(src, rmap, et, kind)
            if m: b[key] = m
        st = full.get("state")
        if isinstance(st, dict):
            sm = self.state_for(doctype, uid(st["meta"]["href"]))
            if sm: b["state"] = sm
        if full.get("description"): b["description"] = full["description"]
        if full.get("shipmentAddress"): b["shipmentAddress"] = full["shipmentAddress"]
        b["positions"] = self.positions(rows(full.get("positions")))
        return b


def post(s, doctype, body):
    return api(s, "POST", f"{BASE}/entity/{doctype}", json=body)


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

    mig = json.loads(MIG_REPORT.read_text(encoding="utf-8"))
    pairs = [(c["new"], c["old"], c["name"]) for c in mig["created"] if c.get("old")]
    log.info("Заказов-пар для обвязки: %s", len(pairs))

    # existing externalCodes per doctype for idempotency
    existing = {}
    for dt in ["invoiceout", "demand", "paymentin", "factureout"]:
        rws = get_all(old_s, dt, filt="moment>=2026-01-01 00:00:00")
        existing[dt] = {(r.get("externalCode") or "").strip() for r in rws if (r.get("externalCode") or "").strip()}

    lm = LinkedMigrator(umap, old_s, new_s, args.dry)
    rep = {"dry": args.dry, "invoiceout": [], "demand": [], "paymentin": [], "factureout": [], "blocked": []}

    def create(doctype, body, new_id, order_name, extra=None):
        if new_id in existing.get(doctype, set()):
            rep[doctype].append({"new": new_id, "order": order_name, "why": "already_in_old"}); return None
        if args.dry:
            rec = {"new": new_id, "order": order_name, "sum": body.get("sum"),
                   "fields": sorted(body.keys())}
            if extra: rec.update(extra)
            rep[doctype].append(rec); return "DRY"
        r = post(old_s, doctype, body)
        if not r.ok:
            rep["blocked"].append({"doctype": doctype, "new": new_id, "order": order_name,
                                   "error": f"{r.status_code} {r.text[:200]}"})
            log.error("POST %s fail (%s): %s %s", doctype, order_name, r.status_code, r.text[:200])
            return None
        old_id = uid(r.json()["meta"]["href"])
        umap.setdefault(doctype, {})[old_id] = new_id
        rec = {"new": new_id, "old": old_id, "order": order_name, "old_name": r.json().get("name")}
        rep[doctype].append(rec)
        log.info("СОЗДАН %s OLD %s ← заказ %s", doctype, r.json().get("name"), order_name)
        return old_id

    for new_oid, old_oid, oname in pairs:
        old_order_meta = meta_obj("customerorder", old_oid)
        full = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_oid}",
                   params={"expand": "demands,invoicesOut,payments"}).json()

        # --- invoiceout ---
        for invref in rows(full.get("invoicesOut")):
            iid = uid(invref["meta"]["href"])
            iv = api(new_s, "GET", f"{BASE}/entity/invoiceout/{iid}",
                     params={"expand": "positions.assortment"}).json()
            lm.unresolved = []
            body = lm.common(iv, "invoiceout", old_order_meta)
            if iv.get("paymentPlannedMoment"): body["paymentPlannedMoment"] = iv["paymentPlannedMoment"]
            if lm.unresolved:
                rep["blocked"].append({"doctype": "invoiceout", "new": iid, "order": oname, "unresolved": lm.unresolved}); continue
            create("invoiceout", body, iid, oname)

        # --- demand (+ factureout) ---
        for dref in rows(full.get("demands")):
            did = uid(dref["meta"]["href"])
            dm = api(new_s, "GET", f"{BASE}/entity/demand/{did}",
                     params={"expand": "positions.assortment,factureOut"}).json()
            lm.unresolved = []
            body = lm.common(dm, "demand", old_order_meta)
            if lm.unresolved:
                rep["blocked"].append({"doctype": "demand", "new": did, "order": oname, "unresolved": lm.unresolved}); continue
            old_dem = create("demand", body, did, oname)
            # factureout linked to this demand
            fo = dm.get("factureOut")
            if isinstance(fo, dict) and old_dem and old_dem != "DRY":
                fid = uid(fo["meta"]["href"])
                ff = api(new_s, "GET", f"{BASE}/entity/factureout/{fid}").json()
                lm.unresolved = []
                fb = {"moment": ff.get("moment"), "externalCode": fid,
                      "demands": [meta_obj("demand", old_dem)]}
                org = lm.ref(ff.get("organization"), lm.r_org, "organization", "organization")
                ag = lm.ref(ff.get("agent"), lm.r_cp, "counterparty", "agent")
                if org: fb["organization"] = org
                if ag: fb["agent"] = ag
                if not lm.unresolved:
                    create("factureout", fb, fid, oname)
            elif isinstance(fo, dict) and args.dry:
                rep["factureout"].append({"new": uid(fo["meta"]["href"]), "order": oname, "note": "dry-skip(demand not created)"})

        # --- paymentin ---
        for pref in rows(full.get("payments")):
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
            for src, rmap, et, key, kind in [
                (pm.get("salesChannel"), lm.r_sc, "saleschannel", "salesChannel", "saleschannel"),
                (pm.get("project"), lm.r_project, "project", "project", "project"),
                (pm.get("owner"), lm.r_emp, "employee", "owner", "employee"),
                (pm.get("group"), lm.r_group, "group", "group", "group"),
            ]:
                m = lm.ref(src, rmap, et, kind)
                if m: pb[key] = m
            st = pm.get("state")
            if isinstance(st, dict):
                sm = lm.state_for("paymentin", uid(st["meta"]["href"]))
                if sm: pb["state"] = sm
            for f in ["paymentPurpose", "incomingNumber", "incomingDate"]:
                if pm.get(f) is not None:
                    pb[f] = pm[f]
            # operations → linked to OLD order
            ops = []
            for op in pm.get("operations", []):
                if op["meta"]["type"] == "customerorder":
                    ops.append({"meta": old_order_meta["meta"], "linkedSum": op.get("linkedSum", pm.get("sum"))})
            if ops:
                pb["operations"] = ops
            if lm.unresolved:
                rep["blocked"].append({"doctype": "paymentin", "new": pid, "order": oname, "unresolved": lm.unresolved}); continue
            create("paymentin", pb, pid, oname)

    if args.apply:
        save_umap(umap)
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: inv=%s dem=%s pay=%s fact=%s blocked=%s",
             len(rep["invoiceout"]), len(rep["demand"]), len(rep["paymentin"]),
             len(rep["factureout"]), len(rep["blocked"]))
    if rep["blocked"]:
        log.warning("BLOCKED: %s", [(b["doctype"], b["order"]) for b in rep["blocked"]])
    log.info("Отчёт: %s", REPORT)


if __name__ == "__main__":
    main()
