#!/usr/bin/env python3
"""
Фаза 3 обратного переноса: создание заказов покупателя в OLD на основе NEW.

Полнота полей: organization, agent, store, project, salesChannel, state(по имени),
owner, group, описание, shipmentAddress, vatEnabled/vatIncluded, applicable, moment,
атрибуты (инверсия attr_customerorder + customentity), позиции (variant/product/service).

Идемпотентность: externalCode OLD-заказа = UUID NEW-заказа. Перед созданием проверяем
externalCode и uuid_map(new->old) — если уже есть, пропускаем.

Нумерация: name НЕ задаём — OLD проставит свой автономер (нормализация номеров — отдельно).

Новые контрагенты (ТРАПЕЗА, СПУТНИК) создаются в OLD с реквизитами из NEW (по решению юзера).

  PYTHONPATH=. python3 reverse_migrate_orders.py --dry
  PYTHONPATH=. python3 reverse_migrate_orders.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, is_uuid, load_umap, make_session, meta_obj,
    old_api_available, save_umap, uid,
)

AUDIT = SCRIPT_DIR / "reverse_audit_orders.json"
REPORT = SCRIPT_DIR / "reverse_migrate_orders.json"
LOG_FILE = SCRIPT_DIR / "reverse_migrate_orders.log"

# ambiguous-заказы, одобренные юзером к переносу (по name)
APPROVED_AMBIGUOUS = {"АФ-00013", "ВА-02675", "ПЕ-00435"}
# новые контрагенты к созданию в OLD (по ИНН)
NEW_CLIENTS_INN = {"7842022946", "7722418791"}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def inv(d: dict) -> dict:
    return {v: k for k, v in d.items()}


def asg_type(href: str) -> str:
    if "/entity/" in href:
        return href.split("/entity/")[-1].split("/")[0]
    return "product"


class Migrator:
    def __init__(self, umap, old_s, new_s, dry):
        self.umap = umap
        self.old_s = old_s
        self.new_s = new_s
        self.dry = dry
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
        self.r_attr = inv(umap.get("attr_customerorder", {}))
        self.r_ceval = inv(umap.get("customentity_value", {}))
        self.r_cedict = inv(umap.get("customentity_dict", {}))
        self.co_new2old = inv(umap.get("customerorder", {}))
        self.unresolved = []
        self._state_map = None  # new_state_id -> old_state_id (by name)

    def state_map(self):
        if self._state_map is not None:
            return self._state_map
        rn = api(self.new_s, "GET", f"{BASE}/entity/customerorder/metadata")
        ro = api(self.old_s, "GET", f"{BASE}/entity/customerorder/metadata")
        new_states = {uid(s["meta"]["href"]): s["name"] for s in rn.json().get("states", [])}
        old_by_name = {s["name"]: uid(s["meta"]["href"]) for s in ro.json().get("states", [])}
        self._state_map = {nid: old_by_name.get(nm) for nid, nm in new_states.items() if nm in old_by_name}
        return self._state_map

    def map_ref(self, obj, kind, rmap, etype):
        if not isinstance(obj, dict):
            return None
        nid = uid(obj.get("meta", {}).get("href", ""))
        if not nid:
            return None
        old = rmap.get(nid)
        if not old:
            self.unresolved.append({"kind": kind, "new_id": nid, "name": obj.get("name")})
            return None
        return meta_obj(etype, old)

    def map_positions(self, rows):
        out = []
        for p in rows:
            asg = p.get("assortment") or {}
            href = asg.get("meta", {}).get("href", "")
            nid = uid(href); t = asg_type(href)
            if t == "variant":
                old = self.r_var.get(nid); et = "variant"
            elif t == "service":
                old = self.r_serv.get(nid); et = "service"
            else:
                old = self.r_prod.get(nid); et = "product"
            if not old:
                self.unresolved.append({"kind": f"assortment/{t}", "new_id": nid})
                continue
            pos = {
                "quantity": p.get("quantity"),
                "price": p.get("price"),
                "discount": p.get("discount") or 0,
                "vat": p.get("vat") or 0,
                "assortment": meta_obj(et, old),
            }
            if p.get("reserve"):
                pos["reserve"] = p["reserve"]
            out.append(pos)
        return out

    def map_attributes(self, attrs):
        out = []
        for a in attrs or []:
            nid = uid(a.get("meta", {}).get("href", ""))
            old_attr = self.r_attr.get(nid)
            if not old_attr:
                continue  # атрибут без обратной пары — пропускаем
            atype = a.get("type")
            value = a.get("value")
            if atype == "customentity" and isinstance(value, dict):
                vhref = value.get("meta", {}).get("href", "")
                old_val = self.r_ceval.get(uid(vhref))
                parts = vhref.split("/")
                old_dict = self.r_cedict.get(parts[-2]) if len(parts) >= 2 else None
                if not old_val or not old_dict:
                    continue
                value = {"meta": {
                    "href": f"{BASE}/entity/customentity/{old_dict}/{old_val}",
                    "type": "customentity", "mediaType": "application/json"}}
            out.append({"meta": {
                "href": f"{BASE}/entity/customerorder/metadata/attributes/{old_attr}",
                "type": "attributemetadata", "mediaType": "application/json"}, "value": value})
        return out

    def ensure_new_client(self, new_agent_id):
        """Создать контрагента в OLD из NEW-карточки (для ТРАПЕЗА/СПУТНИК). Возвращает old_id."""
        if new_agent_id in self.r_cp:
            return self.r_cp[new_agent_id]
        c = api(self.new_s, "GET", f"{BASE}/entity/counterparty/{new_agent_id}").json()
        if (c.get("inn") or "") not in NEW_CLIENTS_INN:
            return None  # не одобренный новый клиент
        body = {"name": c.get("name"), "companyType": c.get("companyType")}
        for f in ["inn", "kpp", "ogrn", "ogrnip", "okpo", "legalTitle", "legalAddress",
                  "actualAddress", "email", "phone", "fax", "code", "description"]:
            if c.get(f):
                body[f] = c[f]
        if self.dry:
            log.info("[dry] создать контрагента OLD: %s inn=%s", body["name"], body.get("inn"))
            return "DRY_NEW_CLIENT"
        r = api(self.old_s, "POST", f"{BASE}/entity/counterparty", json=body)
        if not r.ok:
            log.error("Не удалось создать контрагента %s: %s %s", body["name"], r.status_code, r.text[:300])
            return None
        old_id = uid(r.json()["meta"]["href"])
        self.umap.setdefault("counterparty", {})[old_id] = new_agent_id
        self.r_cp[new_agent_id] = old_id
        log.info("Создан контрагент OLD %s ← %s", old_id, body["name"])
        return old_id

    def build_body(self, nid):
        full = api(self.new_s, "GET", f"{BASE}/entity/customerorder/{nid}",
                   params={"expand": "positions.assortment"}).json()
        before = len(self.unresolved)
        agent_obj = full.get("agent") or {}
        agent_new = uid(agent_obj.get("meta", {}).get("href", ""))
        old_agent = self.r_cp.get(agent_new) or self.ensure_new_client(agent_new)
        if not old_agent:
            self.unresolved.append({"kind": "agent", "new_id": agent_new, "name": agent_obj.get("name")})
            org = None
        else:
            org = meta_obj("counterparty", old_agent) if old_agent != "DRY_NEW_CLIENT" else "DRY"

        org_meta = self.map_ref(full.get("organization"), "organization", self.r_org, "organization")
        body = {
            "moment": full.get("moment"),
            "applicable": full.get("applicable", True),
            "vatEnabled": full.get("vatEnabled", True),
            "vatIncluded": full.get("vatIncluded", True),
            "externalCode": nid,  # якорь идемпотентности
        }
        if org_meta:
            body["organization"] = org_meta
        if org and org != "DRY":
            body["agent"] = org
        elif org == "DRY":
            body["agent"] = {"_dry_new_client": agent_obj.get("name")}
        if full.get("description"):
            body["description"] = full["description"]
        if full.get("shipmentAddress"):
            body["shipmentAddress"] = full["shipmentAddress"]
        if full.get("deliveryPlannedMoment"):
            body["deliveryPlannedMoment"] = full["deliveryPlannedMoment"]
        for src, kind, rmap, et, key in [
            (full.get("store"), "store", self.r_store, "store", "store"),
            (full.get("project"), "project", self.r_project, "project", "project"),
            (full.get("salesChannel"), "saleschannel", self.r_sc, "saleschannel", "salesChannel"),
            (full.get("owner"), "employee", self.r_emp, "employee", "owner"),
            (full.get("group"), "group", self.r_group, "group", "group"),
        ]:
            mref = self.map_ref(src, kind, rmap, et)
            if mref:
                body[key] = mref
        # state by name
        st = full.get("state")
        if isinstance(st, dict):
            sid = uid(st["meta"]["href"])
            old_state = self.state_map().get(sid)
            if old_state:
                body["state"] = {"meta": {
                    "href": f"{BASE}/entity/customerorder/metadata/states/{old_state}",
                    "type": "state", "mediaType": "application/json"}}
            else:
                self.unresolved.append({"kind": "state", "new_id": sid})
        body["positions"] = self.map_positions((full.get("positions") or {}).get("rows", []))
        attrs = self.map_attributes(full.get("attributes"))
        if attrs:
            body["attributes"] = attrs
        new_unres = self.unresolved[before:]
        return full, body, new_unres


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        print("Specify --dry or --apply"); sys.exit(1)

    umap = load_umap()
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403"); sys.exit(1)

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    to_migrate = list(audit["truly_absent"])
    for r in audit["ambiguous"]:
        if r.get("name") in APPROVED_AMBIGUOUS:
            to_migrate.append(r)
    log.info("К переносу: %s заказов (16 truly_absent + %s одобренных ambiguous)",
             len(to_migrate), len(to_migrate) - len(audit["truly_absent"]))

    # OLD externalCodes для проверки идемпотентности
    old_all = get_all(old_s, "customerorder")
    old_ext = {(o.get("externalCode") or "").strip(): uid(o["meta"]["href"])
               for o in old_all if (o.get("externalCode") or "").strip()}
    old_ids = {uid(o["meta"]["href"]) for o in old_all}

    mig = Migrator(umap, old_s, new_s, args.dry)
    rep = {"dry": args.dry, "created": [], "skipped": [], "blocked": []}

    for r in to_migrate:
        nid = r["new_id"]
        # idempotency
        existing = old_ext.get(nid) or mig.co_new2old.get(nid)
        if existing and existing in old_ids:
            rep["skipped"].append({"new": nid, "name": r.get("name"), "old": existing, "why": "already_in_old"})
            continue
        full, body, unres = mig.build_body(nid)
        if unres:
            rep["blocked"].append({"new": nid, "name": r.get("name"), "sum": r.get("sum"),
                                   "unresolved": unres})
            log.warning("BLOCKED %s: %s", r.get("name"), unres)
            continue
        if args.dry:
            rep["created"].append({"new": nid, "name": r.get("name"), "sum": full.get("sum"),
                                   "positions": len(body.get("positions", [])),
                                   "fields": sorted(body.keys())})
            continue
        resp = api(old_s, "POST", f"{BASE}/entity/customerorder", json=body)
        if not resp.ok:
            rep["blocked"].append({"new": nid, "name": r.get("name"),
                                   "error": f"{resp.status_code} {resp.text[:300]}"})
            log.error("POST fail %s: %s %s", r.get("name"), resp.status_code, resp.text[:300])
            continue
        old_id = uid(resp.json()["meta"]["href"])
        umap.setdefault("customerorder", {})[old_id] = nid
        rep["created"].append({"new": nid, "name": r.get("name"), "old": old_id,
                               "old_name": resp.json().get("name"), "sum": resp.json().get("sum")})
        log.info("СОЗДАН OLD %s (%s) ← NEW %s", resp.json().get("name"), old_id, r.get("name"))

    if args.apply:
        save_umap(umap)

    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: created=%s skipped=%s blocked=%s",
             len(rep["created"]), len(rep["skipped"]), len(rep["blocked"]))
    if rep["blocked"]:
        log.warning("BLOCKED заказы: %s", [b.get("name") for b in rep["blocked"]])
    log.info("Отчёт: %s", REPORT)


if __name__ == "__main__":
    main()
