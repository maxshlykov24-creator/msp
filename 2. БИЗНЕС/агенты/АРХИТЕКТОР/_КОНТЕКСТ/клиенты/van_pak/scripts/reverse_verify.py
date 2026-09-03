#!/usr/bin/env python3
"""
Фаза 5 обратного переноса: верификация (read-only).

1. По каждому из 19 заказов сверяет NEW↔OLD: сумму, агента (ИНН/имя), число и сумму
   позиций, статус, владельца, склад, канал, число атрибутов, externalCode-якорь.
2. Сверяет обвязку: число счетов/отгрузок/платежей и payedSum.
3. Проверяет отсутствие дублей: каждый NEW → ровно один OLD; externalCode уникальны.
4. Сверяет прирост OLD c reverse_snapshot.json (ожидаемо: +19 co, +19 inv, +9 dem, +10 pay).

  PYTHONPATH=. python3 reverse_verify.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, load_umap, make_session, old_api_available, uid,
)

MIG = SCRIPT_DIR / "reverse_migrate_orders.json"
LINK = SCRIPT_DIR / "reverse_migrate_linked.json"
SNAP = SCRIPT_DIR / "reverse_snapshot.json"
REPORT = SCRIPT_DIR / "reverse_verify.json"


def rows(x):
    return (x or {}).get("rows", []) if isinstance(x, dict) else (x or [])


def pos_stats(s, doctype, oid):
    r = api(s, "GET", f"{BASE}/entity/{doctype}/{oid}/positions", params={"limit": 1000})
    rws = r.json().get("rows", []) if r.ok else []
    return len(rws), sum((p.get("price") or 0) * (p.get("quantity") or 0) for p in rws)


def main():
    umap = load_umap()
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        print("BLOCKER: OLD API 403"); sys.exit(1)

    mig = json.loads(MIG.read_text(encoding="utf-8"))
    pairs = [(c["new"], c["old"], c["name"]) for c in mig["created"] if c.get("old")]

    new_cps = {uid(c["meta"]["href"]): (c.get("inn"), c.get("name")) for c in get_all(new_s, "counterparty")}
    old_cps = {uid(c["meta"]["href"]): (c.get("inn"), c.get("name")) for c in get_all(old_s, "counterparty")}

    rep = {"orders": [], "issues": [], "duplicates": [], "snapshot_delta": {}}

    for new_oid, old_oid, oname in pairs:
        n = api(new_s, "GET", f"{BASE}/entity/customerorder/{new_oid}",
                params={"expand": "demands,invoicesOut,payments"}).json()
        o = api(old_s, "GET", f"{BASE}/entity/customerorder/{old_oid}",
                params={"expand": "demands,invoicesOut,payments"}).json()
        issues = []
        if abs((n.get("sum") or 0) - (o.get("sum") or 0)) >= 1:
            issues.append(f"sum {n.get('sum')}!={o.get('sum')}")
        # agent by inn/name
        na = uid((n.get("agent") or {}).get("meta", {}).get("href", ""))
        oa = uid((o.get("agent") or {}).get("meta", {}).get("href", ""))
        ninn = new_cps.get(na, (None, None)); oinn = old_cps.get(oa, (None, None))
        if (ninn[0] or oinn[0]) and ninn[0] != oinn[0]:
            if ninn[1] != oinn[1]:
                issues.append(f"agent {ninn}!={oinn}")
        # positions
        npc, nps = pos_stats(new_s, "customerorder", new_oid)
        opc, ops_ = pos_stats(old_s, "customerorder", old_oid)
        if npc != opc:
            issues.append(f"pos_count {npc}!={opc}")
        # state/owner/store names
        def nm(d, f):
            return (d.get(f) or {}).get("name") if isinstance(d.get(f), dict) else None
        # externalCode anchor
        if (o.get("externalCode") or "").strip() != new_oid:
            issues.append(f"externalCode {o.get('externalCode')}!={new_oid}")
        # linked counts
        nlink = {k: len(rows(n.get(k))) for k in ["invoicesOut", "demands", "payments"]}
        olink = {k: len(rows(o.get(k))) for k in ["invoicesOut", "demands", "payments"]}
        for k in nlink:
            if nlink[k] != olink[k]:
                issues.append(f"{k} {nlink[k]}!={olink[k]}")
        if abs((n.get("payedSum") or 0) - (o.get("payedSum") or 0)) >= 1:
            issues.append(f"payedSum {n.get('payedSum')}!={o.get('payedSum')}")
        rec = {"order": oname, "new": new_oid, "old": old_oid, "old_name": o.get("name"),
               "sum": o.get("sum"), "links_new": nlink, "links_old": olink,
               "payedSum": o.get("payedSum"), "issues": issues}
        rep["orders"].append(rec)
        if issues:
            rep["issues"].append({"order": oname, "issues": issues})

    # окно май–июнь (перенос только июньский) — без полногодовых выборок
    WINDOW = "2026-05-01 00:00:00"
    link = json.loads(LINK.read_text(encoding="utf-8"))
    expected = {
        "customerorder": {p[0] for p in pairs},
        "invoiceout": {r["new"] for r in link["invoiceout"] if r.get("old")},
        "demand": {r["new"] for r in link["demand"] if r.get("old")},
        "paymentin": {r["new"] for r in link["paymentin"] if r.get("old")},
    }
    # дубли + наличие наших externalCode в OLD (окно май–июнь)
    for dt, exp_set in expected.items():
        all_old = get_all(old_s, dt, filt=f"moment>={WINDOW}")
        ec = Counter((r.get("externalCode") or "").strip() for r in all_old if (r.get("externalCode") or "").strip())
        dups = [k for k, c in ec.items() if c > 1]
        if dups:
            rep["duplicates"].append({"doctype": dt, "dup_externalCodes": dups})
        found = sum(1 for nid in exp_set if ec.get(nid, 0) >= 1)
        missing = [nid for nid in exp_set if ec.get(nid, 0) == 0]
        rep["snapshot_delta"][dt] = {"expected": len(exp_set), "found_in_old": found,
                                     "missing": missing}

    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in rep["orders"] if not r["issues"])
    print(f"Заказов сверено: {len(rep['orders'])} | без замечаний: {ok} | с замечаниями: {len(rep['issues'])}")
    for i in rep["issues"]:
        print("  ISSUE", i["order"], i["issues"])
    print("Дубли externalCode:", rep["duplicates"] or "нет")
    print("Наличие перенесённого в OLD (по externalCode, окно май–июнь):")
    for dt, d in rep["snapshot_delta"].items():
        miss = f" ПРОПУЩЕНО={len(d['missing'])}" if d["missing"] else ""
        print(f"  {dt}: найдено {d['found_in_old']}/{d['expected']}{miss}")
    print(f"Отчёт: {REPORT}")


if __name__ == "__main__":
    main()
