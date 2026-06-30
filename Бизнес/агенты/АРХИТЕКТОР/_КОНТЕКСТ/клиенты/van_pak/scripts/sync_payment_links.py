#!/usr/bin/env python3
"""
Сверка связей входящих платежей (paymentin.operations → customerorder) OLD↔NEW за год.

Для каждого NEW-платежа, смаппированного с OLD:
  - целевой набор заказов = OLD-платёж.operations(customerorder) → NEW (через co_map)
  - текущий набор заказов NEW-платежа
  - добавить недостающие связи, убрать лишние (только customerorder-операции;
    прочие операции — demand/invoiceout/return — НЕ трогаем)

Платежи без пары в OLD (созданы в NEW) — не трогаем.
Связи на заказы вне периода (OLD-заказ не в co_map) — убираем как лишние в NEW.

  PYTHONPATH=. python3 sync_payment_links.py --dry
  PYTHONPATH=. python3 sync_payment_links.py --apply
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
    load_umap,
    make_session,
    meta_obj,
    old_api_available,
    uid,
)

LOG_FILE = SCRIPT_DIR / "sync_payment_links.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def order_ops(p: dict) -> list[str]:
    """customerorder uids linked in payment operations."""
    res = []
    for op in p.get("operations") or []:
        href = (op.get("meta") or {}).get("href", "")
        if "/customerorder/" in href:
            res.append(uid(href))
    return res


def non_order_ops(p: dict) -> list[dict]:
    """operations that are NOT customerorder — keep as-is."""
    res = []
    for op in p.get("operations") or []:
        href = (op.get("meta") or {}).get("href", "")
        if "/customerorder/" not in href:
            res.append(op)
    return res


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
    pay_map = umap.get("paymentin", {})        # old -> new
    inv_pay = {v: k for k, v in pay_map.items()}  # new -> old
    co_map = umap.get("customerorder", {})     # old -> new

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403")
        sys.exit(1)

    filt = f"moment>={args.from_date}"
    if args.to_date:
        filt += f";moment<={args.to_date}"
    log.info("=== Связи платежей OLD↔NEW | %s | %s ===", filt, "DRY" if dry else "APPLY")

    new_pays = get_all(new_s, "paymentin", filt=filt, expand="operations")
    old_pays = get_all(old_s, "paymentin", filt=filt, expand="operations")
    old_by_id = {uid(p["meta"]["href"]): p for p in old_pays}
    log.info("NEW платежей=%s OLD платежей=%s", len(new_pays), len(old_pays))

    stats = {
        "dry": dry, "filter": filt,
        "new_payments": len(new_pays), "old_payments": len(old_pays),
        "no_old_pair": 0, "already_ok": 0, "fixed": 0,
        "links_added": 0, "links_removed": 0,
        "removed_detail": [], "added_detail": [], "errors": 0,
    }
    put = 0

    for np_ in new_pays:
        nid = uid(np_["meta"]["href"])
        old_id = inv_pay.get(nid)
        if not old_id or old_id not in old_by_id:
            stats["no_old_pair"] += 1
            continue
        op = old_by_id[old_id]

        # target NEW order set = OLD order ops mapped via co_map
        want = set()
        for ooid in order_ops(op):
            n = co_map.get(ooid)
            if n:
                want.add(n)
        cur = set(order_ops(np_))

        if cur == want:
            stats["already_ok"] += 1
            continue

        to_add = want - cur
        to_remove = cur - want
        # rebuild operations: keep non-order ops + desired order ops
        new_ops = non_order_ops(np_)
        for oid in sorted(want):
            new_ops.append(meta_obj("customerorder", oid))

        stats["links_added"] += len(to_add)
        stats["links_removed"] += len(to_remove)
        for x in to_remove:
            stats["removed_detail"].append({"payment": np_.get("name"), "sum": np_.get("sum"), "order_uid": x})
        for x in to_add:
            stats["added_detail"].append({"payment": np_.get("name"), "sum": np_.get("sum"), "order_uid": x})

        if dry:
            stats["fixed"] += 1
            continue
        r = api(new_s, "PUT", f"{BASE}/entity/paymentin/{nid}", json={"operations": new_ops})
        put += 1
        if put % 80 == 0:
            log.info("  Пауза (лимит PUT)... fixed=%s", stats["fixed"])
            time.sleep(62)
        if r.ok:
            stats["fixed"] += 1
        else:
            stats["errors"] += 1
            log.warning("  PUT paymentin %s: %s %s", np_.get("name"), r.status_code, r.text[:150])

    report_file = SCRIPT_DIR / "sync_payment_links_report.json"
    report_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "ИТОГ: fixed=%s links_added=%s links_removed=%s already_ok=%s no_old_pair=%s errors=%s",
        stats["fixed"], stats["links_added"], stats["links_removed"],
        stats["already_ok"], stats["no_old_pair"], stats["errors"],
    )
    log.info("Отчёт: %s", report_file)


if __name__ == "__main__":
    main()
