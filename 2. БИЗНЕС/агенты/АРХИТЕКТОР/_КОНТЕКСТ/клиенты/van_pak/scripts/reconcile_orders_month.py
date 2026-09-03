#!/usr/bin/env python3
"""
Per-order monthly reconciler: for each customerorder OLD↔NEW pair fix everything inline.

Per order fixes (OLD = source of truth, NEW keeps its corrected name/number):
  - agent (контрагент)
  - state (статус/этап)
  - owner + «Ответственный» (владелец/сотрудник)
  - salesChannel, project, store, contract, deliveryPlannedMoment
  - shipmentAddress + 4 order attributes
  - demand.customerOrder links (→ shippedSum)
  - payments: ensure each OLD paymentin linked to the order exists in NEW and is
    linked to the NEW order; create the payment if missing (→ payedSum)

Reports:
  - missing_in_new: OLD orders without NEW pair (need create — not auto-created here)
  - new_only: NEW orders without OLD pair (e.g. employee-created June ЗБ-xxxxx) — LEFT ALONE

  PYTHONPATH=. python3 reconcile_orders_month.py --month 2026-06 --dry
  PYTHONPATH=. python3 reconcile_orders_month.py --month 2026-06 --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from ms_common import (
    BASE,
    OLD_TOKEN,
    NEW_TOKEN,
    ORDER_ATTR_NAMES,
    SCRIPT_DIR,
    api,
    attr_val,
    get_all,
    is_uuid,
    load_umap,
    make_session,
    map_order_attributes,
    meta_obj,
    old_api_available,
    save_umap,
    uid,
)

LOG_FILE = SCRIPT_DIR / "reconcile_orders_month.log"
RESP_ATTR = "Ответственный"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def resp_emp_uid(doc: dict) -> str | None:
    """UUID of employee in «Ответственный» attribute (value.meta), not display name."""
    for a in doc.get("attributes") or []:
        if a.get("name") == RESP_ATTR:
            val = a.get("value")
            if isinstance(val, dict):
                href = (val.get("meta") or {}).get("href", "")
                if href:
                    return uid(href)
    return None


def month_filter(month: str) -> str:
    y, m = month.split("-")
    last = {"01": 31, "02": 28, "03": 31, "04": 30, "05": 31, "06": 30,
            "07": 31, "08": 31, "09": 30, "10": 31, "11": 30, "12": 31}[m]
    return f"moment>={month}-01 00:00:00;moment<={month}-{last} 23:59:59"


def attr_meta_href(s, doc_type: str, name: str) -> str | None:
    r = api(s, "GET", f"{BASE}/entity/{doc_type}/metadata/attributes")
    if not r.ok:
        return None
    for a in r.json().get("rows", []):
        if a.get("name") == name:
            return a["meta"]["href"]
    return None


def build_state_resolver(old_s, new_s, umap):
    cache: dict[tuple[str, str], dict | None] = {}
    new_states_cache: dict[str, list] = {}

    def resolve(entity_type: str, old_state: dict) -> dict | None:
        if not old_state:
            return None
        old_href = (old_state.get("meta") or {}).get("href", "")
        old_sid = uid(old_href)
        key = (entity_type, old_sid)
        if key in cache:
            return cache[key]
        ns = f"state_{entity_type}"
        cached_id = umap.get(ns, {}).get(old_sid)
        if cached_id:
            res = meta_obj("state", cached_id)
            res["meta"]["href"] = f"{BASE}/entity/{entity_type}/metadata/states/{cached_id}"
            cache[key] = res
            return res
        name = old_state.get("name")
        if not name:
            rs = api(old_s, "GET", old_href)
            name = rs.json().get("name") if rs.ok else None
        if not name:
            cache[key] = None
            return None
        if entity_type not in new_states_cache:
            r = api(new_s, "GET", f"{BASE}/entity/{entity_type}/metadata/states")
            new_states_cache[entity_type] = r.json().get("rows", []) if r.ok else []
        for st in new_states_cache[entity_type]:
            if st.get("name") == name:
                nid = uid(st["meta"]["href"])
                umap.setdefault(ns, {})[old_sid] = nid
                res = {"meta": {"href": f"{BASE}/entity/{entity_type}/metadata/states/{nid}", "type": "state", "mediaType": "application/json"}}
                cache[key] = res
                return res
        cache[key] = None
        return None

    return resolve


def index_payments_by_order(pays: list[dict]) -> dict[str, list[dict]]:
    """order_uid -> [payment docs] using operations."""
    idx: dict[str, list[dict]] = defaultdict(list)
    for p in pays:
        for op in p.get("operations") or []:
            href = (op.get("meta") or {}).get("href", "")
            if "/customerorder/" in href:
                idx[uid(href)].append(p)
    return idx


def map_payment_body_from_old(old_pay: dict, umap: dict, new_order_id: str) -> dict | None:
    """Build NEW paymentin body from OLD, linked to new_order_id."""
    org_old = uid((old_pay.get("organization") or {}).get("meta", {}).get("href", ""))
    org_new = umap.get("organization", {}).get(org_old)
    agent_href = (old_pay.get("agent") or {}).get("meta", {}).get("href", "")
    agent_type = agent_href.split("/entity/")[-1].split("/")[0] if "/entity/" in agent_href else "counterparty"
    agent_old = uid(agent_href)
    agent_new = umap.get(agent_type, {}).get(agent_old) or umap.get("counterparty", {}).get(agent_old)
    if not org_new or not agent_new:
        return None
    body = {
        "organization": meta_obj("organization", org_new),
        "agent": meta_obj(agent_type if agent_type in ("counterparty", "organization") else "counterparty", agent_new),
        "moment": old_pay.get("moment"),
        "sum": old_pay.get("sum", 0),
        "externalCode": uid(old_pay["meta"]["href"]),
        "incomingNumber": old_pay.get("incomingNumber", "") or "",
        "operations": [meta_obj("customerorder", new_order_id)],
    }
    if old_pay.get("paymentPurpose"):
        body["paymentPurpose"] = old_pay["paymentPurpose"]
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        log.error("Specify --dry or --apply")
        sys.exit(1)
    dry = args.dry

    report_file = SCRIPT_DIR / f"reconcile_orders_{args.month}.json"
    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    filt = month_filter(args.month)
    log.info("=== Месяц %s | %s ===", args.month, "DRY" if dry else "APPLY")

    # Fetch orders
    old_orders = get_all(old_s, "customerorder", filt=filt)
    new_orders = get_all(new_s, "customerorder", filt=filt)
    log.info("OLD orders=%s NEW orders=%s", len(old_orders), len(new_orders))

    co_map = umap.get("customerorder", {})  # old->new
    inv_co = {v: k for k, v in co_map.items()}  # new->old

    new_by_id = {uid(o["meta"]["href"]): o for o in new_orders}
    new_by_ext = {}
    for o in new_orders:
        ext = (o.get("externalCode") or "").strip()
        if is_uuid(ext):
            new_by_ext[ext] = uid(o["meta"]["href"])

    # Payments indices (month scope)
    old_pays = get_all(old_s, "paymentin", filt=filt, expand="operations")
    new_pays = get_all(new_s, "paymentin", filt=filt, expand="operations")
    old_pay_by_order = index_payments_by_order(old_pays)
    new_pay_by_order = index_payments_by_order(new_pays)
    pay_map = umap.get("paymentin", {})  # old->new
    inv_pay = {v: k for k, v in pay_map.items()}  # new->old
    new_pay_by_id = {uid(p["meta"]["href"]): p for p in new_pays}
    new_pay_by_ext = {}
    for p in new_pays:
        ext = (p.get("externalCode") or "").strip()
        if is_uuid(ext):
            new_pay_by_ext[ext] = uid(p["meta"]["href"])

    # Demands indices
    old_dems = get_all(old_s, "demand", filt=filt, expand="customerOrder")
    new_dems = get_all(new_s, "demand", filt=filt)
    dem_map = umap.get("demand", {})
    new_dem_by_id = {uid(d["meta"]["href"]): d for d in new_dems}

    resolve_state = build_state_resolver(old_s, new_s, umap)

    # Attr metadata hrefs in NEW
    co_resp_attr = attr_meta_href(new_s, "customerorder", RESP_ATTR)

    stats = {
        "month": args.month, "dry": dry,
        "old_orders": len(old_orders), "new_orders": len(new_orders),
        "paired": 0, "fixed_orders": 0, "missing_in_new": [], "new_only": [],
        "fields_fixed": defaultdict(int),
        "payments_linked": 0, "payments_created": 0, "payments_skipped": 0,
        "payments_extra_in_new": [],
        "payed_sum_gaps": [],
        "demands_linked": 0,
        "errors": 0,
    }

    # NEW-only orders (no OLD pair) → leave alone
    paired_new_ids = set()

    for old_o in old_orders:
        old_id = uid(old_o["meta"]["href"])
        new_id = co_map.get(old_id) or new_by_ext.get(old_id)
        if not new_id or new_id not in new_by_id:
            stats["missing_in_new"].append({"old_id": old_id, "name": old_o.get("name"), "moment": old_o.get("moment")})
            continue
        paired_new_ids.add(new_id)
        stats["paired"] += 1

        # Full fetch both
        ro = api(old_s, "GET", f"{BASE}/entity/customerorder/{old_id}", params={"expand": "attributes,state,owner,agent,project,store,salesChannel,contract"})
        rn = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_id}", params={"expand": "attributes,state,owner,agent,project,store,salesChannel,contract"})
        if not ro.ok or not rn.ok:
            stats["errors"] += 1
            continue
        old_doc, new_doc = ro.json(), rn.json()
        body: dict = {}

        # agent
        old_au = uid((old_doc.get("agent") or {}).get("meta", {}).get("href", ""))
        at = (old_doc.get("agent") or {}).get("meta", {}).get("type", "counterparty")
        exp_agent = umap.get(at, {}).get(old_au) or umap.get("counterparty", {}).get(old_au)
        cur_agent = uid((new_doc.get("agent") or {}).get("meta", {}).get("href", ""))
        if exp_agent and exp_agent != cur_agent:
            body["agent"] = meta_obj(at if at in ("counterparty", "organization") else "counterparty", exp_agent)
            stats["fields_fixed"]["agent"] += 1

        # state
        old_state = old_doc.get("state")
        if old_state:
            old_sid = uid((old_state.get("meta") or {}).get("href", ""))
            cur_sid = uid((new_doc.get("state") or {}).get("meta", {}).get("href", ""))
            exp_state_id = umap.get("state_customerorder", {}).get(old_sid)
            if not exp_state_id or exp_state_id != cur_sid:
                sm = resolve_state("customerorder", old_state)
                if sm and uid(sm["meta"]["href"]) != cur_sid:
                    body["state"] = sm
                    stats["fields_fixed"]["state"] += 1

        # owner
        old_owner = uid((old_doc.get("owner") or {}).get("meta", {}).get("href", ""))
        exp_owner = umap.get("employee", {}).get(old_owner)
        cur_owner = uid((new_doc.get("owner") or {}).get("meta", {}).get("href", ""))
        if exp_owner and exp_owner != cur_owner:
            body["owner"] = meta_obj("employee", exp_owner)
            stats["fields_fixed"]["owner"] += 1

        # salesChannel by name
        old_sc = (old_doc.get("salesChannel") or {}).get("name", "")
        new_sc = (new_doc.get("salesChannel") or {}).get("name", "")
        if old_sc and old_sc != new_sc:
            # resolve by name
            chans = get_all(new_s, "saleschannel")
            sc_id = next((uid(c["meta"]["href"]) for c in chans if c.get("name") == old_sc), None)
            if sc_id:
                body["salesChannel"] = meta_obj("saleschannel", sc_id)
                stats["fields_fixed"]["salesChannel"] += 1

        # project / store / contract
        for fld, ns in (("project", "project"), ("store", "store"), ("contract", "contract")):
            old_v = uid((old_doc.get(fld) or {}).get("meta", {}).get("href", ""))
            cur_v = uid((new_doc.get(fld) or {}).get("meta", {}).get("href", ""))
            exp_v = umap.get(ns, {}).get(old_v)
            if exp_v and exp_v != cur_v:
                body[fld] = meta_obj(ns, exp_v)
                stats["fields_fixed"][fld] += 1

        # deliveryPlannedMoment
        if old_doc.get("deliveryPlannedMoment") and old_doc["deliveryPlannedMoment"] != new_doc.get("deliveryPlannedMoment"):
            body["deliveryPlannedMoment"] = old_doc["deliveryPlannedMoment"]
            stats["fields_fixed"]["deliveryPlannedMoment"] += 1

        # shipmentAddress
        old_addr = (old_doc.get("shipmentAddress") or "").strip()
        new_addr = (new_doc.get("shipmentAddress") or "").strip()
        if old_addr and old_addr != new_addr:
            body["shipmentAddress"] = old_doc["shipmentAddress"]
            stats["fields_fixed"]["shipmentAddress"] += 1

        # order attributes (4) — only when values actually differ
        need_attrs = any(
            attr_val(old_doc, name) is not None and attr_val(old_doc, name) != attr_val(new_doc, name)
            for name in ORDER_ATTR_NAMES
        )
        resp_diff = False
        if co_resp_attr and exp_owner:
            resp_diff = resp_emp_uid(new_doc) != exp_owner
        if need_attrs or resp_diff:
            attrs_to_set = list(map_order_attributes(old_doc.get("attributes"), umap)) if need_attrs else []
            if resp_diff:
                attrs_to_set = [a for a in attrs_to_set if a["meta"]["href"] != co_resp_attr]
                attrs_to_set.append({"meta": {"href": co_resp_attr, "type": "attributemetadata", "mediaType": "application/json"}, "value": {"meta": meta_obj("employee", exp_owner)["meta"]}})
            if attrs_to_set:
                existing = new_doc.get("attributes") or []
                set_hrefs = {a["meta"]["href"] for a in attrs_to_set}
                kept = [a for a in existing if (a.get("meta") or {}).get("href") not in set_hrefs]
                body["attributes"] = kept + attrs_to_set
                stats["fields_fixed"]["attributes"] += 1

        # Apply order body
        if body:
            if dry:
                stats["fixed_orders"] += 1
            else:
                r = api(new_s, "PUT", f"{BASE}/entity/customerorder/{new_id}", json=body)
                if r.ok:
                    stats["fixed_orders"] += 1
                else:
                    stats["errors"] += 1
                    log.warning("  PUT order %s: %s %s", new_doc.get("name"), r.status_code, r.text[:150])

        # ── Payments for this order ──
        for old_pay in old_pay_by_order.get(old_id, []):
            old_pay_id = uid(old_pay["meta"]["href"])
            new_pay_id = pay_map.get(old_pay_id) or new_pay_by_ext.get(old_pay_id)
            if new_pay_id and new_pay_id in new_pay_by_id:
                # exists — ensure linked to new order
                np = new_pay_by_id[new_pay_id]
                linked = any("/customerorder/" in (op.get("meta") or {}).get("href", "") and uid((op.get("meta") or {}).get("href", "")) == new_id for op in np.get("operations") or [])
                if not linked:
                    ops = list(np.get("operations") or [])
                    ops.append(meta_obj("customerorder", new_id))
                    if dry:
                        stats["payments_linked"] += 1
                    else:
                        r = api(new_s, "PUT", f"{BASE}/entity/paymentin/{new_pay_id}", json={"operations": ops})
                        if r.ok:
                            stats["payments_linked"] += 1
                        else:
                            stats["errors"] += 1
            else:
                # create
                pbody = map_payment_body_from_old(old_pay, umap, new_id)
                if not pbody:
                    stats["payments_skipped"] += 1
                    continue
                if dry:
                    stats["payments_created"] += 1
                else:
                    r = api(new_s, "POST", f"{BASE}/entity/paymentin", json=pbody)
                    if r.ok:
                        npid = uid(r.json()["meta"]["href"])
                        umap.setdefault("paymentin", {})[old_pay_id] = npid
                        stats["payments_created"] += 1
                    else:
                        stats["errors"] += 1
                        log.warning("  POST paymentin %s: %s %s", old_pay.get("name"), r.status_code, r.text[:150])

        # ── Detect EXTRA payments in NEW linked to this order (no OLD counterpart) ──
        old_pay_ids = {uid(p["meta"]["href"]) for p in old_pay_by_order.get(old_id, [])}
        expected_new_pay_ids = {pay_map.get(opid) for opid in old_pay_ids if pay_map.get(opid)}
        for np in new_pay_by_order.get(new_id, []):
            npid = uid(np["meta"]["href"])
            if npid in expected_new_pay_ids:
                continue
            # is this NEW payment mapped to an OLD payment that links this order?
            mapped_old = inv_pay.get(npid)
            if mapped_old and mapped_old in old_pay_ids:
                continue
            # extra payment in NEW for this order
            stats["payments_extra_in_new"].append({
                "order": new_doc.get("name"), "new_order_id": new_id,
                "extra_payment_id": npid, "payment_name": np.get("name"),
                "sum": np.get("sum"), "mapped_old": mapped_old,
            })

        # ── payedSum gap record ──
        op = old_doc.get("payedSum") or 0
        np_sum = new_doc.get("payedSum") or 0
        if abs(op - np_sum) > 1:
            stats["payed_sum_gaps"].append({
                "order": new_doc.get("name"), "new_order_id": new_id,
                "old_payed": op, "new_payed": np_sum, "delta": np_sum - op,
            })

    # NEW-only orders
    for nid, o in new_by_id.items():
        if nid in paired_new_ids:
            continue
        if inv_co.get(nid):
            continue
        ext = (o.get("externalCode") or "").strip()
        if is_uuid(ext) and ext in {uid(x["meta"]["href"]) for x in old_orders}:
            continue
        stats["new_only"].append({"new_id": nid, "name": o.get("name"), "moment": o.get("moment")})

    # Demand links for the month
    old_dem_by_id = {uid(d["meta"]["href"]): d for d in old_dems}
    for old_dem in old_dems:
        old_dem_id = uid(old_dem["meta"]["href"])
        new_dem_id = dem_map.get(old_dem_id)
        if not new_dem_id or new_dem_id not in new_dem_by_id:
            continue
        old_co = uid((old_dem.get("customerOrder") or {}).get("meta", {}).get("href", ""))
        want = co_map.get(old_co) if old_co else None
        nd = new_dem_by_id[new_dem_id]
        cur = uid((nd.get("customerOrder") or {}).get("meta", {}).get("href", ""))
        if want and want != cur:
            if dry:
                stats["demands_linked"] += 1
            else:
                r = api(new_s, "PUT", f"{BASE}/entity/demand/{new_dem_id}", json={"customerOrder": meta_obj("customerorder", want)})
                if r.ok:
                    stats["demands_linked"] += 1
                else:
                    stats["errors"] += 1

    if not dry:
        save_umap(umap)

    stats["fields_fixed"] = dict(stats["fields_fixed"])
    report_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ %s: paired=%s fixed_orders=%s fields=%s pay_linked=%s pay_created=%s extra_pay=%s payed_gaps=%s dem_linked=%s missing_in_new=%s new_only=%s errors=%s",
             args.month, stats["paired"], stats["fixed_orders"], stats["fields_fixed"],
             stats["payments_linked"], stats["payments_created"], len(stats["payments_extra_in_new"]),
             len(stats["payed_sum_gaps"]), stats["demands_linked"],
             len(stats["missing_in_new"]), len(stats["new_only"]), stats["errors"])
    log.info("Отчёт: %s", report_file)


if __name__ == "__main__":
    main()
