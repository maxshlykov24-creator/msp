#!/usr/bin/env python3
"""
Фаза D: выравнивание нативных полей ручных заказов РОП по NEW-источнику.

Для 24 сопоставленных заказов (из reverse_verify_manual.json; АФ-00065 пропускаем —
решение по нему отдельно) проставляет нативные поля как в NEW:
  salesChannel, owner, store, state.
Атрибуты НЕ трогаем (канал/ответственный у РОП уже в атрибутах — норма OLD).

  PYTHONPATH=. python3 reverse_align_fields.py --dry
  PYTHONPATH=. python3 reverse_align_fields.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, load_umap, make_session, meta_obj, old_api_available, uid,
)

VERIFY = SCRIPT_DIR / "reverse_verify_manual.json"
REPORT = SCRIPT_DIR / "reverse_align_fields.json"
LOG_FILE = SCRIPT_DIR / "reverse_align_fields.log"
SKIP_ORDERS = {"АФ-00065"}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def inv(d):
    return {v: k for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        print("Specify --dry or --apply"); sys.exit(1)

    umap = load_umap()
    r_sc = inv(umap.get("saleschannel", {}))
    r_emp = inv(umap.get("employee", {}))
    r_store = inv(umap.get("store", {}))
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403"); sys.exit(1)

    # state map new->old (by name)
    rn = api(new_s, "GET", f"{BASE}/entity/customerorder/metadata").json()
    ro = api(old_s, "GET", f"{BASE}/entity/customerorder/metadata").json()
    new_states = {uid(s["meta"]["href"]): s["name"] for s in rn.get("states", [])}
    old_by_name = {s["name"]: uid(s["meta"]["href"]) for s in ro.get("states", [])}
    state_map = {nid: old_by_name.get(nm) for nid, nm in new_states.items()}

    verify = json.loads(VERIFY.read_text(encoding="utf-8"))
    targets = [(o["old_id"], o["new_id"], o["order"]) for o in verify["orders"]
               if o.get("new_id") and o["order"] not in SKIP_ORDERS]
    log.info("Заказов к выравниванию: %s", len(targets))

    rep = {"dry": args.dry, "patched": [], "skipped_field": [], "blocked": []}

    def ref(obj, rmap, et):
        if not isinstance(obj, dict):
            return None, None
        nid = uid(obj.get("meta", {}).get("href", ""))
        old = rmap.get(nid)
        return (meta_obj(et, old) if old else None), obj.get("name")

    for old_oid, new_oid, oname in targets:
        sf = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_oid}",
                 params={"expand": "salesChannel,owner,store,state"}).json()
        body = {}; set_fields = []; miss = []
        sc, scn = ref(sf.get("salesChannel"), r_sc, "saleschannel")
        if sc: body["salesChannel"] = sc; set_fields.append(f"канал={scn}")
        elif sf.get("salesChannel"): miss.append("salesChannel")
        ow, own = ref(sf.get("owner"), r_emp, "employee")
        if ow: body["owner"] = ow; set_fields.append(f"ответств={own}")
        elif sf.get("owner"): miss.append("owner")
        stre, strn = ref(sf.get("store"), r_store, "store")
        if stre: body["store"] = stre; set_fields.append(f"склад={strn}")
        elif sf.get("store"): miss.append("store")
        st = sf.get("state")
        if isinstance(st, dict):
            old_state = state_map.get(uid(st["meta"]["href"]))
            if old_state:
                body["state"] = {"meta": {"href": f"{BASE}/entity/customerorder/metadata/states/{old_state}",
                                          "type": "state", "mediaType": "application/json"}}
                set_fields.append(f"статус={st.get('name')}")
            else:
                miss.append(f"state:{st.get('name')}")
        if miss:
            rep["skipped_field"].append({"order": oname, "no_map": miss})
        if not body:
            continue
        if args.dry:
            rep["patched"].append({"order": oname, "set": set_fields}); continue
        r = api(old_s, "PUT", f"{BASE}/entity/customerorder/{old_oid}", json=body)
        if not r.ok:
            rep["blocked"].append({"order": oname, "error": f"{r.status_code} {r.text[:200]}"})
            log.error("PUT fail %s: %s %s", oname, r.status_code, r.text[:200]); continue
        rep["patched"].append({"order": oname, "set": set_fields})
        log.info("ВЫРОВНЕН %s: %s", oname, ", ".join(set_fields))

    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: patched=%s skipped_field=%s blocked=%s",
             len(rep["patched"]), len(rep["skipped_field"]), len(rep["blocked"]))
    for s in rep["skipped_field"]:
        log.info("  нет в карте (%s): %s", s["order"], s["no_map"])
    if rep["blocked"]:
        log.warning("BLOCKED: %s", rep["blocked"])
    log.info("Отчёт: %s", REPORT)


if __name__ == "__main__":
    main()
