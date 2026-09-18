#!/usr/bin/env python3
"""После оплаты: остаток >1 → сборка, 0 → производство, витрина и 1 → остаётся.

Только новая воронка Продажи 2MY, этап Оплачен. Старую не трогает.
По умолчанию сухой прогон.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lib

SKIP_NAMES = {"доставка", "delivery"}


def load_vitrina() -> tuple[list[str], set[str]]:
    raw = json.loads(Path(__file__).with_name("витрина_48.json").read_text(encoding="utf-8"))
    names = [x.lower().replace("ё", "е") for x in raw["items"]]
    ids: set[str] = set()
    mp = Path(__file__).with_name("витрина_map.json")
    if mp.is_file():
        data = json.loads(mp.read_text(encoding="utf-8"))
        for rec in data.get("mapped") or []:
            for h in rec.get("hits") or []:
                if h.get("id"):
                    ids.add(h["id"])
    return names, ids


def is_vitrina(name: str, assortment_id: str, names: list[str], ids: set[str]) -> bool:
    if assortment_id and assortment_id in ids:
        return True
    n = (name or "").lower().replace("ё", "е")
    return any(item.split(",")[0].strip() in n for item in names)


def stock_msk(ms: lib.MS, ass_id: str, ass_type: str) -> dict:
    key = "variant" if ass_type == "variant" else "product"
    st, b = ms.req("/report/stock/all", {
        "filter": f"store={lib.MS_BASE}/entity/store/{lib.STORE_MSK};{key}={lib.MS_BASE}/entity/{key}/{ass_id}",
        "limit": 5,
    })
    rows = b.get("rows") or [] if 200 <= st < 300 else []
    if not rows and ass_type != "variant":
        st, b = ms.req("/report/stock/all", {
            "filter": f"store={lib.MS_BASE}/entity/store/{lib.STORE_MSK};variant={lib.MS_BASE}/entity/variant/{ass_id}",
            "limit": 5,
        })
        rows = b.get("rows") or [] if 200 <= st < 300 else []
    return rows[0] if rows else {}


def decide_line(ms: lib.MS, pos: dict, names: list[str], ids: set[str]) -> str:
    ass = pos.get("assortment") or {}
    name = ass.get("name") or ""
    if name.lower() in SKIP_NAMES:
        return "skip"
    aid = ass.get("id") or lib.href_id(ass.get("meta"))
    atype = (ass.get("meta") or {}).get("type") or "product"
    qty = float(pos.get("quantity") or 0)
    reserve = float(pos.get("reserve") or 0)
    row = stock_msk(ms, aid, atype) if aid else {}
    stock = float(row.get("stock") or 0)
    before = stock + reserve
    vitr = is_vitrina(name, aid, names, ids)
    if before > 1:
        return "pack"
    if before <= 0:
        return "prod"
    if vitr and before == 1:
        return "hold"
    return "pack"


def decide_order(ms: lib.MS, order: dict, names: list[str], ids: set[str]) -> str:
    href = ((order.get("positions") or {}).get("meta") or {}).get("href")
    if not href:
        return "hold"
    st, b = ms.req(href.replace(lib.MS_BASE, ""), {"expand": "assortment", "limit": 50})
    rows = b.get("rows") or [] if 200 <= st < 300 else []
    flags = [decide_line(ms, p, names, ids) for p in rows]
    flags = [f for f in flags if f != "skip"]
    if not flags:
        return "hold"
    if "hold" in flags:
        return "hold"
    if "prod" in flags:
        return "prod"
    return "pack"


def find_ms_order(ms: lib.MS, amo: lib.Amo, lead: dict) -> dict | None:
    num = amo.cf(lead, lib.FIELD_MS_ORDER_NUM)
    oid = amo.cf(lead, lib.FIELD_MS_ORDER_ID)
    if oid:
        st, b = ms.req(f"/entity/customerorder/{oid}")
        if 200 <= st < 300:
            return b
    if num:
        st, b = ms.req("/entity/customerorder", {"filter": f"name={num}", "limit": 5})
        rows = b.get("rows") or [] if 200 <= st < 300 else []
        if rows:
            return rows[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    names, ids = load_vitrina()
    amo = lib.Amo()
    ms = lib.MS()
    leads = amo.iter_leads(
        f"?filter[pipeline_id]={lib.PIPELINE_SALES_NEW}&filter[statuses][0][pipeline_id]={lib.PIPELINE_SALES_NEW}&filter[statuses][0][status_id]={lib.ST['paid']}"
    )
    print(f"Оплачен в Продажи 2MY: {len(leads)}  витрина id={len(ids)}  apply={args.apply}")
    for lead in leads:
        order = find_ms_order(ms, amo, lead)
        if not order:
            print(f"  {lead['id']}: нет заказа МС, оставляю оплачен")
            continue
        decision = decide_order(ms, order, names, ids)
        target = {"pack": lib.ST["pack"], "prod": lib.ST["prod"]}.get(decision)
        print(f"  {lead['id']} заказ {order.get('name')} → {decision}")
        if not target or not args.apply:
            continue
        st, _ = amo.req("PATCH", f"/api/v4/leads/{lead['id']}", {
            "status_id": target,
            "pipeline_id": lib.PIPELINE_SALES_NEW,
        })
        print(f"    patch [{st}]")


if __name__ == "__main__":
    main()
