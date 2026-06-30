#!/usr/bin/env python3
"""
Phase 6: Full verification OLD↔NEW — per-doc and aggregated.

Checks per TRUSTED/MOMENT_RESOLVED pair:
  - agent, state, owner/Ответственный
  - salesChannel, project, store
  - shipmentAddress + 4 order attributes
  - sum; for customerorder: payedSum, shippedSum
  - demand.customerOrder link
  - payment.operations links
  - factureout.basedOn links

Aggregates 2026 by type: count, Σsum, ΣpayedSum, ΣshippedSum.

  PYTHONPATH=. python3 verify_full.py
  PYTHONPATH=. python3 verify_full.py --from-date 2026-01-01 --to-date 2026-06-30
  PYTHONPATH=. python3 verify_full.py --final
"""
from __future__ import annotations

import argparse
import csv
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
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "verify_full.log"
REPORT_JSON = SCRIPT_DIR / "verify_full_report.json"
FINAL_JSON = SCRIPT_DIR / "verify_full_final.json"
CSV_FILE = SCRIPT_DIR / "verify_full_diff.csv"
PAIRS_FILE = SCRIPT_DIR / "audit_doc_pairs_report.json"
FIXABLE = frozenset({"TRUSTED", "TRUSTED_RENAME", "MOMENT_RESOLVED"})

DOC_TYPES = [
    "customerorder",
    "demand",
    "invoiceout",
    "paymentin",
    "paymentout",
    "factureout",
]
YEAR_FILTER = "moment>=2026-01-01 00:00:00;moment<=2026-12-31 23:59:59"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def pairs_from_file(pairs_path: Path, from_d: str | None, to_d: str | None) -> list[dict]:
    data = json.loads(pairs_path.read_text(encoding="utf-8"))
    rows = [p for p in data.get("pairs", []) if p.get("status") in FIXABLE]
    if from_d or to_d:
        filtered = []
        for p in rows:
            m = p.get("moment") or ""
            if from_d and m < from_d:
                continue
            if to_d and m > (f"{to_d} 23:59:59" if len(to_d) == 10 else to_d):
                continue
            filtered.append(p)
        rows = filtered
    return rows


def check_agent(old_doc, new_doc, umap) -> str | None:
    old_au = uid((old_doc.get("agent") or {}).get("meta", {}).get("href", ""))
    new_au = uid((new_doc.get("agent") or {}).get("meta", {}).get("href", ""))
    if not old_au:
        return None
    at = (old_doc.get("agent") or {}).get("meta", {}).get("type", "counterparty")
    expected = umap.get(at, {}).get(old_au) or umap.get("counterparty", {}).get(old_au)
    if expected and expected != new_au:
        return f"agent: expected={expected[:8]} got={new_au[:8]}"
    return None


def check_state(old_doc, new_doc, umap, doc_type) -> str | None:
    old_state = old_doc.get("state")
    new_state = new_doc.get("state")
    if not old_state:
        return None
    old_sid = uid((old_state.get("meta") or {}).get("href", ""))
    new_sid = uid((new_state.get("meta") or {}).get("href", "")) if new_state else ""
    ns = f"state_{doc_type}"
    expected = umap.get(ns, {}).get(old_sid)
    if expected and expected != new_sid:
        return f"state: old={old_state.get('name')} expected_new_id={expected[:8]} got={new_sid[:8] if new_sid else 'null'}"
    if not expected and old_state:
        return f"state: unmapped old_state={old_state.get('name')} new={new_state.get('name') if new_state else 'null'}"
    return None


def check_owner(old_doc, new_doc, umap) -> str | None:
    old_owner = uid((old_doc.get("owner") or {}).get("meta", {}).get("href", ""))
    new_owner = uid((new_doc.get("owner") or {}).get("meta", {}).get("href", ""))
    if not old_owner:
        return None
    expected = umap.get("employee", {}).get(old_owner)
    if expected and expected != new_owner:
        return f"owner: expected={expected[:8]} got={new_owner[:8] if new_owner else 'null'}"
    return None


def check_responsible_attr(old_doc, new_doc, umap) -> str | None:
    RESP = "Ответственный"
    old_resp = attr_val(old_doc, RESP)
    new_resp = attr_val(new_doc, RESP)
    if not old_resp:
        return None
    if isinstance(old_resp, str) and len(old_resp) == 36:
        expected = umap.get("employee", {}).get(old_resp)
        if expected:
            if isinstance(new_resp, str) and new_resp != expected:
                return f"attr_responsible: expected={expected[:8]} got={new_resp[:8] if new_resp else 'null'}"
    return None


def check_sales_channel(old_doc, new_doc) -> str | None:
    old_sc = (old_doc.get("salesChannel") or {}).get("name", "")
    new_sc = (new_doc.get("salesChannel") or {}).get("name", "")
    if old_sc and old_sc != new_sc:
        return f"salesChannel: old={old_sc!r} new={new_sc!r}"
    return None


def check_project(old_doc, new_doc, umap) -> str | None:
    old_proj = uid((old_doc.get("project") or {}).get("meta", {}).get("href", ""))
    new_proj = uid((new_doc.get("project") or {}).get("meta", {}).get("href", ""))
    if not old_proj:
        return None
    expected = umap.get("project", {}).get(old_proj)
    if expected and expected != new_proj:
        return f"project: expected={expected[:8]} got={new_proj[:8] if new_proj else 'null'}"
    return None


def check_store(old_doc, new_doc, umap) -> str | None:
    old_store = uid((old_doc.get("store") or {}).get("meta", {}).get("href", ""))
    new_store = uid((new_doc.get("store") or {}).get("meta", {}).get("href", ""))
    if not old_store:
        return None
    expected = umap.get("store", {}).get(old_store)
    if expected and expected != new_store:
        return f"store: expected={expected[:8]} got={new_store[:8] if new_store else 'null'}"
    return None


def check_order_attrs(old_doc, new_doc) -> list[str]:
    issues = []
    old_addr = (old_doc.get("shipmentAddress") or "").strip()
    new_addr = (new_doc.get("shipmentAddress") or "").strip()
    if old_addr and old_addr != new_addr:
        issues.append(f"shipmentAddress: old={old_addr[:40]!r} new={new_addr[:40]!r}")
    for name in ORDER_ATTR_NAMES:
        ov = attr_val(old_doc, name)
        nv = attr_val(new_doc, name)
        if ov is not None and ov != nv:
            issues.append(f"attr[{name}]: old={str(ov)[:30]} new={str(nv)[:30]}")
    return issues


def check_sum(old_doc, new_doc, doc_type) -> list[str]:
    issues = []
    os_ = old_doc.get("sum") or 0
    ns_ = new_doc.get("sum") or 0
    if os_ and abs(os_ - ns_) > 1:
        issues.append(f"sum: old={os_} new={ns_} Δ={ns_-os_:+}")
    if doc_type == "customerorder":
        op = old_doc.get("payedSum") or 0
        np_ = new_doc.get("payedSum") or 0
        if op and abs(op - np_) > 1:
            issues.append(f"payedSum: old={op} new={np_} Δ={np_-op:+}")
        os2 = old_doc.get("shippedSum") or 0
        ns2 = new_doc.get("shippedSum") or 0
        if os2 and abs(os2 - ns2) > 1:
            issues.append(f"shippedSum: old={os2} new={ns2} Δ={ns2-os2:+}")
    return issues


def check_demand_link(old_doc, new_doc, umap) -> str | None:
    old_co = uid((old_doc.get("customerOrder") or {}).get("meta", {}).get("href", ""))
    new_co = uid((new_doc.get("customerOrder") or {}).get("meta", {}).get("href", ""))
    if not old_co:
        if new_co:
            return f"demand.customerOrder: old=null new={new_co[:8]} (extra link)"
        return None
    expected = umap.get("customerorder", {}).get(old_co)
    if expected and expected != new_co:
        return f"demand.customerOrder: expected={expected[:8]} got={new_co[:8] if new_co else 'null'}"
    return None


def check_payment_ops(old_doc, new_doc, umap) -> list[str]:
    issues = []
    old_ops = old_doc.get("operations") or []
    new_ops = new_doc.get("operations") or []
    old_set: set[str] = set()
    for op in old_ops:
        op_href = (op.get("meta") or {}).get("href", "")
        op_type = op_href.split("/entity/")[-1].split("/")[0] if "/entity/" in op_href else ""
        op_id = uid(op_href)
        expected = umap.get(op_type, {}).get(op_id)
        if expected:
            old_set.add(expected)
    new_set = {uid((op.get("meta") or {}).get("href", "")) for op in new_ops}
    missing = old_set - new_set
    extra = new_set - old_set
    if missing:
        issues.append(f"payment.ops missing={len(missing)}: {','.join(list(missing)[:3])}")
    if extra:
        issues.append(f"payment.ops extra={len(extra)}: {','.join(list(extra)[:3])}")
    return issues


def check_factureout_based_on(old_doc, new_doc, umap) -> list[str]:
    issues = []
    old_bo = old_doc.get("basedOn") or []
    new_bo = new_doc.get("basedOn") or []
    old_set: set[str] = set()
    for bo in old_bo:
        bo_href = (bo.get("meta") or {}).get("href", "")
        bo_type = bo_href.split("/entity/")[-1].split("/")[0] if "/entity/" in bo_href else ""
        bo_id = uid(bo_href)
        expected = umap.get(bo_type, {}).get(bo_id)
        if expected:
            old_set.add(expected)
    new_set = {uid((bo.get("meta") or {}).get("href", "")) for bo in new_bo}
    missing = old_set - new_set
    if missing:
        issues.append(f"factureout.basedOn missing={len(missing)}")
    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--from-date", default="2026-01-01")
    ap.add_argument("--to-date", default="2026-12-31")
    ap.add_argument("--pairs-file", default=str(PAIRS_FILE))
    args = ap.parse_args()

    umap = load_umap()
    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)

    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    # ── Aggregate counts & sums ───────────────────────────────────────
    agg_old: dict[str, dict] = {}
    agg_new: dict[str, dict] = {}
    for doc_type in DOC_TYPES:
        old_rows = get_all(old_s, doc_type, filt=YEAR_FILTER)
        new_rows = get_all(new_s, doc_type, filt=YEAR_FILTER)
        agg_old[doc_type] = {
            "count": len(old_rows),
            "sum": sum(r.get("sum") or 0 for r in old_rows),
            "payedSum": sum(r.get("payedSum") or 0 for r in old_rows) if doc_type == "customerorder" else None,
            "shippedSum": sum(r.get("shippedSum") or 0 for r in old_rows) if doc_type == "customerorder" else None,
        }
        agg_new[doc_type] = {
            "count": len(new_rows),
            "sum": sum(r.get("sum") or 0 for r in new_rows),
            "payedSum": sum(r.get("payedSum") or 0 for r in new_rows) if doc_type == "customerorder" else None,
            "shippedSum": sum(r.get("shippedSum") or 0 for r in new_rows) if doc_type == "customerorder" else None,
        }
        log.info(
            "%s: OLD count=%s sum=%s | NEW count=%s sum=%s",
            doc_type,
            agg_old[doc_type]["count"], agg_old[doc_type]["sum"],
            agg_new[doc_type]["count"], agg_new[doc_type]["sum"],
        )

    # ── Per-doc pair check ────────────────────────────────────────────
    pairs_path = Path(args.pairs_file)
    pairs_issues: list[dict] = []
    unmapped_new: list[dict] = []

    if pairs_path.exists():
        all_pairs = pairs_from_file(pairs_path, args.from_date, args.to_date)
        log.info("Checking %s fixable pairs...", len(all_pairs))

        unmapped_new = [
            p for p in json.loads(pairs_path.read_text(encoding="utf-8")).get("pairs", [])
            if p.get("status") == "UNMAPPED_NEW"
        ]

        for p in all_pairs:
            doc_type = p["doc_type"]
            old_id = p.get("old_id")
            new_id = p.get("new_id")
            if not old_id or not new_id:
                continue

            expand = "attributes"
            if doc_type == "demand":
                expand += ",customerOrder"
            elif doc_type in ("paymentin", "paymentout"):
                expand += ",operations"
            elif doc_type == "factureout":
                expand += ",basedOn"

            ro = api(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_id}", params={"expand": expand})
            rn = api(new_s, "GET", f"{BASE}/entity/{doc_type}/{new_id}", params={"expand": expand})
            if not ro.ok or not rn.ok:
                continue

            old_doc = ro.json()
            new_doc = rn.json()

            issues: list[str] = []
            chk = check_agent(old_doc, new_doc, umap)
            if chk:
                issues.append(chk)
            chk = check_state(old_doc, new_doc, umap, doc_type)
            if chk:
                issues.append(chk)
            chk = check_owner(old_doc, new_doc, umap)
            if chk:
                issues.append(chk)
            chk = check_responsible_attr(old_doc, new_doc, umap)
            if chk:
                issues.append(chk)
            chk = check_sales_channel(old_doc, new_doc)
            if chk:
                issues.append(chk)
            chk = check_project(old_doc, new_doc, umap)
            if chk:
                issues.append(chk)
            chk = check_store(old_doc, new_doc, umap)
            if chk:
                issues.append(chk)
            if doc_type == "customerorder":
                issues.extend(check_order_attrs(old_doc, new_doc))
            issues.extend(check_sum(old_doc, new_doc, doc_type))
            if doc_type == "demand":
                chk = check_demand_link(old_doc, new_doc, umap)
                if chk:
                    issues.append(chk)
            if doc_type in ("paymentin", "paymentout"):
                issues.extend(check_payment_ops(old_doc, new_doc, umap))
            if doc_type == "factureout":
                issues.extend(check_factureout_based_on(old_doc, new_doc, umap))

            if issues:
                pairs_issues.append({
                    "doc_type": doc_type,
                    "new_name": new_doc.get("name"),
                    "old_name": old_doc.get("name"),
                    "new_id": new_id,
                    "old_id": old_id,
                    "moment": new_doc.get("moment"),
                    "issues": issues,
                })
                log.warning("  DIFF %s/%s: %s", doc_type, new_doc.get("name"), "; ".join(issues[:3]))

    # aggregate gaps
    agg_gaps: dict[str, dict] = {}
    all_ok = True
    for dt in DOC_TYPES:
        oc = agg_old[dt]["count"]
        nc = agg_new[dt]["count"]
        os_ = agg_old[dt]["sum"]
        ns_ = agg_new[dt]["sum"]
        gap = {
            "count_old": oc, "count_new": nc, "count_delta": nc - oc,
            "sum_old": os_, "sum_new": ns_, "sum_delta": ns_ - os_,
        }
        if dt == "customerorder":
            gap["payedSum_old"] = agg_old[dt]["payedSum"]
            gap["payedSum_new"] = agg_new[dt]["payedSum"]
            gap["payedSum_delta"] = (agg_new[dt]["payedSum"] or 0) - (agg_old[dt]["payedSum"] or 0)
            gap["shippedSum_old"] = agg_old[dt]["shippedSum"]
            gap["shippedSum_new"] = agg_new[dt]["shippedSum"]
            gap["shippedSum_delta"] = (agg_new[dt]["shippedSum"] or 0) - (agg_old[dt]["shippedSum"] or 0)
        agg_gaps[dt] = gap
        if nc != oc or abs(ns_ - os_) > 1:
            all_ok = False

    per_doc_ok = len(pairs_issues) == 0
    status = "OK" if (all_ok and per_doc_ok and not unmapped_new) else "DIFF"

    report = {
        "status": status,
        "all_aggregates_ok": all_ok,
        "per_doc_issues_count": len(pairs_issues),
        "unmapped_new_count": len(unmapped_new),
        "aggregates": agg_gaps,
        "per_doc_issues": pairs_issues[:200],
        "unmapped_new": unmapped_new[:50],
    }

    out = FINAL_JSON if args.final else REPORT_JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # CSV
    with CSV_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_type", "new_name", "old_name", "moment", "new_id", "old_id", "issues"])
        for pi in pairs_issues:
            w.writerow([
                pi["doc_type"], pi["new_name"], pi["old_name"],
                pi["moment"], pi["new_id"], pi["old_id"],
                "; ".join(pi["issues"]),
            ])

    log.info(
        "ИТОГ: status=%s agg_ok=%s per_doc_issues=%s unmapped_new=%s → %s",
        status, all_ok, len(pairs_issues), len(unmapped_new), out,
    )


if __name__ == "__main__":
    main()
