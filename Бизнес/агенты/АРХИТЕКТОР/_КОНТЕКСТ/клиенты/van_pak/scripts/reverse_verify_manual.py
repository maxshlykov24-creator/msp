#!/usr/bin/env python3
"""
Фаза A: сверка 25 заказов, перенесённых вручную РОП в OLD, с их NEW-источниками.

Сопоставление: контрагент (карта old->new / ИНН / имя) + сумма + позиции.
Для каждого OLD-заказа сверяет: сумму, контрагента (ИНН), позиции (товар+кол-во+цена),
канал продаж, владельца, склад, статус, атрибуты; и считает недостающие документы
(отгрузка/платёж/счёт-фактура), которые есть в NEW, но отсутствуют в OLD.

Read-only. Выход: reverse_verify_manual.json

  PYTHONPATH=. python3 reverse_verify_manual.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ms_common import (
    BASE, OLD_TOKEN, NEW_TOKEN, SCRIPT_DIR,
    api, get_all, load_umap, make_session, old_api_available, uid,
)

REPORT = SCRIPT_DIR / "reverse_verify_manual.json"
NAMES = ["АФ-00043"] + [f"АФ-000{n}" for n in range(61, 85)]
WINDOW = "2026-05-01 00:00:00"


def rows(x):
    return (x or {}).get("rows", []) if isinstance(x, dict) else (x or [])


def nm(d, f):
    v = d.get(f)
    return v.get("name") if isinstance(v, dict) else None


def pos_multiset(s, doctype, oid):
    r = api(s, "GET", f"{BASE}/entity/{doctype}/{oid}/positions",
            params={"limit": 1000, "expand": "assortment"})
    out = []
    for p in r.json().get("rows", []) if r.ok else []:
        a = (p.get("assortment") or {}).get("name", "?")
        out.append((a, round(p.get("quantity") or 0, 3), round(p.get("price") or 0)))
    return Counter(out)


def attrs_set(d):
    out = set()
    for a in d.get("attributes") or []:
        v = a.get("value")
        if isinstance(v, dict):
            v = v.get("name") or uid((v.get("meta") or {}).get("href", ""))
        out.add((a.get("name"), str(v)))
    return out


def main():
    umap = load_umap()
    cp_o2n = umap.get("counterparty", {})  # old->new
    old_s = make_session(OLD_TOKEN); new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        print("BLOCKER: OLD API 403"); sys.exit(1)

    # NEW counterparties index by inn/name (for unmapped agents)
    new_cps = get_all(new_s, "counterparty")
    new_by_inn = defaultdict(list); new_by_name = defaultdict(list)
    new_cp_meta = {}
    for c in new_cps:
        cid = uid(c["meta"]["href"]); new_cp_meta[cid] = c
        if c.get("inn"): new_by_inn[c["inn"].strip()].append(cid)
        if c.get("name"): new_by_name[c["name"].strip()].append(cid)
    old_cp_meta = {uid(c["meta"]["href"]): c for c in get_all(old_s, "counterparty")}

    # NEW orders since May indexed by (new_agent, sum)
    new_orders = get_all(new_s, "customerorder", filt=f"moment>={WINDOW}")
    new_idx = defaultdict(list)
    for n in new_orders:
        au = uid((n.get("agent") or {}).get("meta", {}).get("href", ""))
        new_idx[(au, round(n.get("sum") or 0))].append(n)

    rep = {"orders": [], "matched": 0, "with_issues": [], "unmatched": [], "missing_docs": {}}

    for name in NAMES:
        r = api(old_s, "GET", f"{BASE}/entity/customerorder",
                params={"filter": f"name={name}",
                        "expand": "agent,state,owner,store,salesChannel,invoicesOut,demands,payments"})
        olist = r.json().get("rows", [])
        if not olist:
            rep["unmatched"].append({"order": name, "reason": "not_found_in_old"}); continue
        o = olist[0]; oid = uid(o["meta"]["href"]); osum = round(o.get("sum") or 0)
        old_agent = uid((o.get("agent") or {}).get("meta", {}).get("href", ""))
        old_inn = (old_cp_meta.get(old_agent, {}).get("inn") or "").strip()
        old_aname = (o.get("agent") or {}).get("name")

        # resolve NEW agent id
        new_agent = cp_o2n.get(old_agent)
        if not new_agent:
            if old_inn and new_by_inn.get(old_inn):
                new_agent = new_by_inn[old_inn][0]
            elif old_aname and new_by_name.get(old_aname.strip()):
                new_agent = new_by_name[old_aname.strip()][0]

        cands = new_idx.get((new_agent, osum), []) if new_agent else []
        old_pos = pos_multiset(old_s, "customerorder", oid)

        # disambiguate by positions when several candidates
        src = None
        if len(cands) == 1:
            src = cands[0]
        elif len(cands) > 1:
            for c in cands:
                if pos_multiset(new_s, "customerorder", uid(c["meta"]["href"])) == old_pos:
                    src = c; break
            src = src or cands[0]

        rec = {"order": name, "old_id": oid, "old_sum": osum / 100, "agent": old_aname,
               "links_old": {k: len(rows(o.get(k))) for k in ["invoicesOut", "demands", "payments"]},
               "issues": [], "missing": []}

        if not src:
            # try sum-only fallback
            sum_only = [n for n in new_orders if round(n.get("sum") or 0) == osum]
            rec["issues"].append(f"NEW-источник не найден (sum-only кандидатов: {len(sum_only)})")
            rep["unmatched"].append({"order": name, "agent": old_aname, "sum": osum / 100,
                                     "sum_only_candidates": len(sum_only)})
            rep["orders"].append(rec); continue

        sid = uid(src["meta"]["href"])
        sf = api(new_s, "GET", f"{BASE}/entity/customerorder/{sid}",
                 params={"expand": "agent,state,owner,store,salesChannel,invoicesOut,demands,payments"}).json()
        rec["new_src"] = src.get("name"); rec["new_id"] = sid

        # field checks
        if round(sf.get("sum") or 0) != osum:
            rec["issues"].append(f"sum NEW={round(sf.get('sum') or 0)/100} OLD={osum/100}")
        new_pos = pos_multiset(new_s, "customerorder", sid)
        if new_pos != old_pos:
            only_new = list((new_pos - old_pos).elements())
            only_old = list((old_pos - new_pos).elements())
            rec["issues"].append({"positions_diff": {"в NEW нет в OLD": only_old, "в OLD нет в NEW": only_new}})
        for f in ["salesChannel", "owner", "store", "state"]:
            if nm(sf, f) != nm(o, f):
                rec["issues"].append(f"{f}: NEW={nm(sf, f)!r} OLD={nm(o, f)!r}")
        if attrs_set(sf) != attrs_set(o):
            rec["issues"].append({"attrs_diff": {"только в NEW": sorted(attrs_set(sf) - attrs_set(o)),
                                                  "только в OLD": sorted(attrs_set(o) - attrs_set(sf))}})

        # missing linked docs (NEW has, OLD lacks)
        n_inv = len(rows(sf.get("invoicesOut"))); n_dem = len(rows(sf.get("demands"))); n_pay = len(rows(sf.get("payments")))
        o_inv = rec["links_old"]["invoicesOut"]; o_dem = rec["links_old"]["demands"]; o_pay = rec["links_old"]["payments"]
        n_fact = 0
        for dref in rows(sf.get("demands")):
            dd = api(new_s, "GET", f"{BASE}/entity/demand/{uid(dref['meta']['href'])}").json()
            if isinstance(dd.get("factureOut"), dict): n_fact += 1
        if n_dem > o_dem: rec["missing"].append(f"demand x{n_dem - o_dem}")
        if n_pay > o_pay: rec["missing"].append(f"paymentin x{n_pay - o_pay}")
        if n_inv > o_inv: rec["missing"].append(f"invoiceout x{n_inv - o_inv}")
        if n_fact > 0: rec["missing"].append(f"factureout x{n_fact}")
        rec["links_new"] = {"invoicesOut": n_inv, "demands": n_dem, "payments": n_pay, "factureout": n_fact}

        rep["matched"] += 1
        if rec["issues"]:
            rep["with_issues"].append({"order": name, "issues": rec["issues"]})
        if rec["missing"]:
            rep["missing_docs"][name] = rec["missing"]
        rep["orders"].append(rec)

    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сверено: {len(NAMES)} | сопоставлено: {rep['matched']} | не найден источник: {len(rep['unmatched'])}")
    print(f"\n=== Расхождения полей ({len(rep['with_issues'])}) ===")
    for x in rep["with_issues"]:
        print(f"  {x['order']}: {x['issues']}")
    print(f"\n=== Недостающие документы ({len(rep['missing_docs'])}) ===")
    for k, v in rep["missing_docs"].items():
        print(f"  {k}: {', '.join(v)}")
    print(f"\n=== Не сопоставлены ({len(rep['unmatched'])}) ===")
    for x in rep["unmatched"]:
        print(f"  {x}")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
