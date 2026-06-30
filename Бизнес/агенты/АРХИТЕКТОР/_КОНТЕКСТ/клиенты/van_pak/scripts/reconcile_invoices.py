#!/usr/bin/env python3
"""
Сверка счетов покупателя (invoiceout) OLD↔NEW за весь год.

Логика (OLD = источник истины):
  Для каждого OLD-счёта, связанного с заказом покупателя:
    1. Находим NEW-счёт (uuid_map[invoiceout] или externalCode = old uuid).
    2. Если NEW-счёт ЕСТЬ — проставляем/исправляем связь customerOrder
       на смаппированный NEW-заказ (имя/номер счёта НЕ трогаем).
    3. Если NEW-счёта НЕТ — создаём: имя = номер заказа покупателя,
       позиции/агент/организация из OLD, связь с заказом.
  Лишние NEW-счета (без пары в OLD) — только в отчёт, не трогаем.

Без дублей: счёт создаётся только если его реально нет в NEW.

  PYTHONPATH=. python3 reconcile_invoices.py --dry
  PYTHONPATH=. python3 reconcile_invoices.py --apply
  PYTHONPATH=. python3 reconcile_invoices.py --apply --from-date "2026-05-01 00:00:00" --to-date "2026-05-31 23:59:59"
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from ms_common import (
    BASE,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    YEAR_START,
    api,
    get_all,
    is_uuid,
    load_umap,
    make_session,
    meta_obj,
    old_api_available,
    save_umap,
    uid,
)

LOG_FILE = SCRIPT_DIR / "reconcile_invoices.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def map_positions(positions: list, umap: dict) -> tuple[list, bool]:
    """Map OLD invoice positions to NEW. Returns (rows, all_mapped)."""
    result = []
    ok = True
    for p in positions:
        a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
        old_a = uid(a_href)
        new_uuid = umap.get("product", {}).get(old_a)
        new_type = "product"
        if not new_uuid:
            new_uuid = umap.get("service", {}).get(old_a)
            new_type = "service"
        if not new_uuid:
            new_uuid = umap.get("variant", {}).get(old_a)
            new_type = "variant"
        if not new_uuid:
            ok = False
            continue
        np = {
            "assortment": meta_obj(new_type, new_uuid),
            "quantity": p.get("quantity", 1),
            "price": p.get("price", 0),
            "discount": p.get("discount", 0),
            "vat": p.get("vat", 0),
        }
        uom_href = (p.get("uom") or {}).get("meta", {}).get("href", "")
        new_uom = umap.get("uom", {}).get(uid(uom_href))
        if new_uom:
            np["uom"] = meta_obj("uom", new_uom)
        result.append(np)
    return result, ok


def build_invoice_body(old_full: dict, umap: dict, new_order_id: str, order_name: str) -> dict | None:
    """Build NEW invoiceout body from OLD full doc. name = order number."""
    org_old = uid((old_full.get("organization") or {}).get("meta", {}).get("href", ""))
    org_new = umap.get("organization", {}).get(org_old)
    agent_href = (old_full.get("agent") or {}).get("meta", {}).get("href", "")
    agent_t = agent_href.split("/entity/")[-1].split("/")[0] if "/entity/" in agent_href else "counterparty"
    agent_old = uid(agent_href)
    agent_new = umap.get(agent_t, {}).get(agent_old) or umap.get("counterparty", {}).get(agent_old)
    if not org_new or not agent_new:
        return None
    pos_data = old_full.get("positions") or {}
    rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else (pos_data or [])
    new_pos, all_ok = map_positions(rows, umap)
    if rows and not all_ok:
        return None
    body = {
        "name": order_name,
        "moment": old_full.get("moment"),
        "applicable": old_full.get("applicable", True),
        "externalCode": uid(old_full["meta"]["href"]),
        "organization": meta_obj("organization", org_new),
        "agent": meta_obj(agent_t if agent_t in ("counterparty", "organization") else "counterparty", agent_new),
        "customerOrder": meta_obj("customerorder", new_order_id),
        "positions": new_pos,
    }
    store_href = (old_full.get("store") or {}).get("meta", {}).get("href", "")
    new_store = umap.get("store", {}).get(uid(store_href))
    if new_store:
        body["store"] = meta_obj("store", new_store)
    proj_href = (old_full.get("project") or {}).get("meta", {}).get("href", "")
    new_proj = umap.get("project", {}).get(uid(proj_href))
    if new_proj:
        body["project"] = meta_obj("project", new_proj)
    con_href = (old_full.get("contract") or {}).get("meta", {}).get("href", "")
    new_con = umap.get("contract", {}).get(uid(con_href))
    if new_con:
        body["contract"] = meta_obj("contract", new_con)
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--from-date", default=YEAR_START)
    ap.add_argument("--to-date", default=None)
    args = ap.parse_args()
    if not args.dry and not args.apply:
        log.error("Specify --dry or --apply")
        sys.exit(1)
    dry = args.dry

    umap = load_umap()
    inv_map = umap.get("invoiceout", {})       # old -> new
    co_map = umap.get("customerorder", {})     # old -> new
    inv_inv = {v: k for k, v in inv_map.items()}  # new -> old

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    filt = f"moment>={args.from_date}"
    if args.to_date:
        filt += f";moment<={args.to_date}"
    log.info("=== Счета OLD↔NEW | %s | %s ===", filt, "DRY" if dry else "APPLY")

    old_inv = get_all(old_s, "invoiceout", filt=filt, expand="customerOrder")
    new_inv = get_all(new_s, "invoiceout", filt=filt, expand="customerOrder")
    new_orders = get_all(new_s, "customerorder", filt=f"moment>={YEAR_START}")
    log.info("OLD счетов=%s NEW счетов=%s NEW заказов=%s", len(old_inv), len(new_inv), len(new_orders))

    new_inv_by_id = {uid(i["meta"]["href"]): i for i in new_inv}
    new_inv_by_ext = {}
    for i in new_inv:
        ext = (i.get("externalCode") or "").strip()
        if is_uuid(ext):
            new_inv_by_ext[ext] = uid(i["meta"]["href"])
    new_order_name = {uid(o["meta"]["href"]): o.get("name") for o in new_orders}

    stats = {
        "dry": dry, "filter": filt,
        "old_invoices": len(old_inv), "new_invoices": len(new_inv),
        "linked": 0, "already_ok": 0, "created": 0,
        "no_order_in_old": 0, "order_not_mapped": [], "create_failed": [],
        "extra_in_new": [], "errors": 0,
    }
    put = 0
    paired_new_ids = set()

    for oi in old_inv:
        old_id = uid(oi["meta"]["href"])
        old_co = uid((oi.get("customerOrder") or {}).get("meta", {}).get("href", ""))
        if not old_co:
            stats["no_order_in_old"] += 1
            new_id0 = inv_map.get(old_id) or new_inv_by_ext.get(old_id)
            if new_id0:
                paired_new_ids.add(new_id0)
            continue
        want_order = co_map.get(old_co)
        if not want_order:
            stats["order_not_mapped"].append({"invoice": oi.get("name"), "old_order": old_co})
            continue

        new_id = inv_map.get(old_id) or new_inv_by_ext.get(old_id)
        if new_id and new_id in new_inv_by_id:
            paired_new_ids.add(new_id)
            ni = new_inv_by_id[new_id]
            cur_co = uid((ni.get("customerOrder") or {}).get("meta", {}).get("href", ""))
            if cur_co == want_order:
                stats["already_ok"] += 1
                continue
            if dry:
                stats["linked"] += 1
                continue
            r = api(new_s, "PUT", f"{BASE}/entity/invoiceout/{new_id}",
                    json={"customerOrder": meta_obj("customerorder", want_order)})
            put += 1
            if put % 80 == 0:
                log.info("  Пауза (лимит PUT)... linked=%s", stats["linked"])
                time.sleep(62)
            if r.ok:
                stats["linked"] += 1
            else:
                stats["errors"] += 1
                log.warning("  PUT invoice %s: %s %s", oi.get("name"), r.status_code, r.text[:150])
        else:
            order_name = new_order_name.get(want_order)
            if not order_name:
                stats["create_failed"].append({"invoice": oi.get("name"), "reason": "no_order_name"})
                continue
            if dry:
                stats["created"] += 1
                continue
            ro = api(old_s, "GET", f"{BASE}/entity/invoiceout/{old_id}", params={"expand": "positions"})
            if not ro.ok:
                stats["errors"] += 1
                continue
            body = build_invoice_body(ro.json(), umap, want_order, order_name)
            if not body:
                stats["create_failed"].append({"invoice": oi.get("name"), "reason": "body_unmapped"})
                continue
            r = api(new_s, "POST", f"{BASE}/entity/invoiceout", json=body)
            put += 1
            if put % 80 == 0:
                log.info("  Пауза (лимит PUT)... created=%s", stats["created"])
                time.sleep(62)
            if r.ok:
                nid = uid(r.json()["meta"]["href"])
                umap.setdefault("invoiceout", {})[old_id] = nid
                paired_new_ids.add(nid)
                stats["created"] += 1
            else:
                stats["errors"] += 1
                stats["create_failed"].append({"invoice": oi.get("name"), "reason": f"{r.status_code} {r.text[:120]}"})

    # Extra NEW invoices (no OLD pair) — leave alone, just report
    for nid, ni in new_inv_by_id.items():
        if nid in paired_new_ids:
            continue
        if inv_inv.get(nid):
            continue
        ext = (ni.get("externalCode") or "").strip()
        if is_uuid(ext) and ext in {uid(o["meta"]["href"]) for o in old_inv}:
            continue
        stats["extra_in_new"].append({"new_id": nid, "name": ni.get("name"), "moment": ni.get("moment")})

    if not dry:
        save_umap(umap)

    report_file = SCRIPT_DIR / "reconcile_invoices_report.json"
    report_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: linked=%s already=%s created=%s no_order_in_old=%s order_not_mapped=%s create_failed=%s extra_in_new=%s errors=%s",
        stats["linked"], stats["already_ok"], stats["created"], stats["no_order_in_old"],
        len(stats["order_not_mapped"]), len(stats["create_failed"]),
        len(stats["extra_in_new"]), stats["errors"],
    )
    log.info("Отчёт: %s", report_file)


if __name__ == "__main__":
    main()
