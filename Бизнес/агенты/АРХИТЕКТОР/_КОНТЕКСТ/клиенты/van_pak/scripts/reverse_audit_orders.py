#!/usr/bin/env python3
"""
Фаза 1 обратного переноса (read-only): точный список заказов, созданных только в NEW
и отсутствующих в OLD. Сопоставление — по КОНТРАГЕНТУ (ИНН) + СУММЕ (+позиции),
БЕЗ имени/номера и БЕЗ moment (нумерация OLD повреждена, заказ мог быть создан
вручную в обоих аккаунтах в разное время).

Алгоритм по каждому NEW-заказу с moment>=FROM:
  1. Если есть пара в OLD по uuid_map(new->old) или externalCode=old uuid → SKIP (уже связан).
  2. Резолвим агента кандидата в OLD по ИНН (через NEW-агента).
  3. Берём ВСЕ OLD-заказы этого агента (весь период).
  4. Ищем среди них заказ с той же суммой; при совпадении гибко сверяем позиции.
     - сумма совпала и позиции совпали → matched_in_old (НЕ создавать)
     - сумма совпала, позиции расходятся → ambiguous (ручная проверка)
     - агент не резолвится по ИНН → ambiguous (нельзя надёжно проверить дубли)
     - ничего не совпало → truly_absent (на перенос)

Ничего не пишет. Выход: reverse_audit_orders.json

  PYTHONPATH=. python3 reverse_audit_orders.py
  PYTHONPATH=. python3 reverse_audit_orders.py --from-date "2026-06-03 00:00:00"
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
    SCRIPT_DIR,
    api,
    get_all,
    is_uuid,
    load_umap,
    make_session,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "reverse_audit_orders.log"
REPORT = SCRIPT_DIR / "reverse_audit_orders.json"
DEFAULT_FROM = "2026-06-03 00:00:00"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def norm_inn(v) -> str:
    return (str(v).strip() if v else "")


def positions_key(rows: list) -> tuple:
    """Multiset of (assortment_uid, quantity) — direction-agnostic via reverse compare."""
    items = []
    for p in rows:
        a = uid((p.get("assortment") or {}).get("meta", {}).get("href", ""))
        items.append((a, float(p.get("quantity") or 0)))
    return tuple(sorted(items))


def positions_qty_only(rows: list) -> tuple:
    """Fallback: sorted quantities only (when product uuids differ across accounts)."""
    return tuple(sorted(float(p.get("quantity") or 0) for p in rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=DEFAULT_FROM)
    args = ap.parse_args()

    umap = load_umap()
    co_map = umap.get("customerorder", {})        # old->new
    inv_co = {v: k for k, v in co_map.items()}    # new->old
    rev_cp = {v: k for k, v in umap.get("counterparty", {}).items()}  # new->old
    rev_prod = {v: k for k, v in umap.get("product", {}).items()}
    rev_serv = {v: k for k, v in umap.get("service", {}).items()}
    rev_var = {v: k for k, v in umap.get("variant", {}).items()}

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    FROM = args.from_date
    new_orders = get_all(new_s, "customerorder", filt=f"moment>={FROM}")
    old_all = get_all(old_s, "customerorder")  # весь период
    log.info("NEW заказов с %s: %s | OLD заказов всего: %s", FROM, len(new_orders), len(old_all))

    old_ids = {uid(o["meta"]["href"]) for o in old_all}

    # OLD agents: id -> INN (fetch counterparties once, build inn index)
    old_cps = get_all(old_s, "counterparty")
    old_inn_to_id = {}
    for c in old_cps:
        inn = norm_inn(c.get("inn"))
        if inn:
            old_inn_to_id.setdefault(inn, []).append(uid(c["meta"]["href"]))
    log.info("OLD контрагентов: %s (с ИНН: %s)", len(old_cps), len(old_inn_to_id))

    # Index OLD orders by agent_uid
    old_by_agent = defaultdict(list)
    for o in old_all:
        au = uid((o.get("agent") or {}).get("meta", {}).get("href", ""))
        old_by_agent[au].append(o)

    # NEW agents: id -> INN (fetch once)
    new_cps = get_all(new_s, "counterparty")
    new_agent_inn = {uid(c["meta"]["href"]): norm_inn(c.get("inn")) for c in new_cps}
    new_agent_name = {uid(c["meta"]["href"]): c.get("name") for c in new_cps}

    def old_positions(old_id: str) -> list:
        r = api(old_s, "GET", f"{BASE}/entity/customerorder/{old_id}/positions", params={"limit": 1000})
        return r.json().get("rows", []) if r.ok else []

    def map_new_pos_to_old(rows: list) -> tuple:
        """NEW positions → (old_assortment_uid, qty) using reverse maps."""
        items = []
        for p in rows:
            a = uid((p.get("assortment") or {}).get("meta", {}).get("href", ""))
            old_a = rev_prod.get(a) or rev_serv.get(a) or rev_var.get(a)
            items.append((old_a or a, float(p.get("quantity") or 0)))
        return tuple(sorted(items))

    stats = {
        "from": FROM, "new_orders": len(new_orders),
        "already_paired": 0,
        "truly_absent": [], "matched_in_old": [], "ambiguous": [],
    }

    for n in new_orders:
        nid = uid(n["meta"]["href"])
        ext = (n.get("externalCode") or "").strip()
        # 1. already paired?
        if inv_co.get(nid) or (is_uuid(ext) and ext in old_ids):
            stats["already_paired"] += 1
            continue

        full = api(new_s, "GET", f"{BASE}/entity/customerorder/{nid}",
                   params={"expand": "agent,positions.assortment"}).json()
        ag_id = uid((full.get("agent") or {}).get("meta", {}).get("href", ""))
        nsum = full.get("sum") or 0
        npos = (full.get("positions") or {}).get("rows", [])
        rec = {"new_id": nid, "name": n.get("name"), "moment": n.get("moment"),
               "sum": nsum, "agent": new_agent_name.get(ag_id), "inn": new_agent_inn.get(ag_id)}

        # 2. resolve agent in OLD
        old_agent_ids = []
        old_a_via_map = rev_cp.get(ag_id)
        if old_a_via_map:
            old_agent_ids = [old_a_via_map]
        else:
            inn = new_agent_inn.get(ag_id)
            if inn and inn in old_inn_to_id:
                old_agent_ids = old_inn_to_id[inn]

        if not old_agent_ids:
            rec["reason"] = "agent_not_resolved_in_old"
            stats["ambiguous"].append(rec)
            continue

        # 3. OLD orders of this agent
        candidates_old = []
        for oa in old_agent_ids:
            candidates_old.extend(old_by_agent.get(oa, []))

        # 4. match by sum then positions
        sum_matches = [o for o in candidates_old if abs((o.get("sum") or 0) - nsum) < 1]
        if not sum_matches:
            stats["truly_absent"].append(rec)
            continue

        # positions cross-check (flexible): compare mapped product multiset, fallback to qty multiset
        new_key = map_new_pos_to_old(npos)
        new_qty = positions_qty_only(npos)
        confirmed = None
        for o in sum_matches:
            opos = old_positions(uid(o["meta"]["href"]))
            okey = positions_key(opos)
            oqty = positions_qty_only(opos)
            if new_key == okey or (new_qty == oqty and len(npos) == len(opos)):
                confirmed = o
                break
        if confirmed:
            rec["matched_old_id"] = uid(confirmed["meta"]["href"])
            rec["matched_old_name"] = confirmed.get("name")
            stats["matched_in_old"].append(rec)
        else:
            rec["sum_match_old"] = [{"id": uid(o["meta"]["href"]), "name": o.get("name"),
                                     "moment": o.get("moment")} for o in sum_matches[:5]]
            rec["reason"] = "sum_match_positions_differ"
            stats["ambiguous"].append(rec)

    REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ИТОГ: already_paired=%s truly_absent=%s matched_in_old=%s ambiguous=%s",
             stats["already_paired"], len(stats["truly_absent"]),
             len(stats["matched_in_old"]), len(stats["ambiguous"]))
    log.info("Отчёт: %s", REPORT)


if __name__ == "__main__":
    main()
