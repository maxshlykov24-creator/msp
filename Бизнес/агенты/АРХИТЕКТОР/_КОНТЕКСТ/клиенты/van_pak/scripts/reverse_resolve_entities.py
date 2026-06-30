#!/usr/bin/env python3
"""
Фаза 2 обратного переноса: РЕЗОЛВ справочников для кандидатов (truly_absent + ambiguous).
Справочники не менялись — только сопоставляем NEW→OLD, в OLD ничего не создаём.

- Контрагенты без reverse-map: ищем в OLD по ИНН, затем по точному имени.
- Каналы продаж: по имени.
- Найдено → пишем в uuid_map (old->new). Не найдено → в отчёт (спросить пользователя).

  PYTHONPATH=. python3 reverse_resolve_entities.py --dry
  PYTHONPATH=. python3 reverse_resolve_entities.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ms_common import (
    BASE,
    OLD_TOKEN,
    NEW_TOKEN,
    SCRIPT_DIR,
    api,
    get_all,
    load_umap,
    make_session,
    old_api_available,
    save_umap,
    uid,
)

AUDIT = SCRIPT_DIR / "reverse_audit_orders.json"
REPORT = SCRIPT_DIR / "reverse_resolve_entities.json"


def norm(v) -> str:
    return str(v).strip() if v else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry and not args.apply:
        print("Specify --dry or --apply"); sys.exit(1)

    umap = load_umap()
    rev_cp = {v: k for k, v in umap.get("counterparty", {}).items()}   # new->old
    rev_sc = {v: k for k, v in umap.get("saleschannel", {}).items()}

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    if not old_api_available(old_s):
        print("BLOCKER: OLD API 403"); sys.exit(1)

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    scope = audit["truly_absent"] + audit["ambiguous"]
    new_ids = [r["new_id"] for r in scope]
    print(f"Кандидатов в работе (truly_absent + ambiguous): {len(new_ids)}")

    # Collect unique NEW agents + channels from these orders
    agents = {}   # new_uid -> {name, inn, kpp}
    channels = {} # new_uid -> name
    for nid in new_ids:
        full = api(new_s, "GET", f"{BASE}/entity/customerorder/{nid}",
                   params={"expand": "agent,salesChannel"}).json()
        ag = full.get("agent") or {}
        au = uid(ag.get("meta", {}).get("href", ""))
        if au and au not in agents:
            # fetch counterparty for inn
            rc = api(new_s, "GET", f"{BASE}/entity/counterparty/{au}")
            c = rc.json() if rc.ok else {}
            agents[au] = {"name": c.get("name") or ag.get("name"), "inn": norm(c.get("inn")), "kpp": norm(c.get("kpp"))}
        sc = full.get("salesChannel") or {}
        su = uid(sc.get("meta", {}).get("href", ""))
        if su and su not in channels:
            channels[su] = sc.get("name")

    # OLD counterparties index by INN and name
    old_cps = get_all(old_s, "counterparty")
    by_inn, by_name = {}, {}
    for c in old_cps:
        cid = uid(c["meta"]["href"])
        if norm(c.get("inn")):
            by_inn.setdefault(norm(c["inn"]), []).append(cid)
        if c.get("name"):
            by_name.setdefault(c["name"].strip(), []).append(cid)

    old_channels = get_all(old_s, "saleschannel")
    sc_by_name = {c["name"].strip(): uid(c["meta"]["href"]) for c in old_channels if c.get("name")}

    rep = {"dry": args.dry, "agents_resolved": [], "agents_unresolved": [],
           "channels_resolved": [], "channels_unresolved": []}

    for au, info in agents.items():
        if au in rev_cp:
            continue  # already mapped
        old_id = None; how = None
        if info["inn"]:
            # есть ИНН → матч строго по ИНН (имя-фолбэк запрещён: разные юрлица могут быть тёзками)
            if info["inn"] in by_inn:
                ids = by_inn[info["inn"]]
                old_id = ids[0]; how = f"inn ({len(ids)} совп.)"
        else:
            # нет ИНН (физлицо) → допускаем точное имя
            if info["name"] and info["name"].strip() in by_name:
                ids = by_name[info["name"].strip()]
                old_id = ids[0]; how = f"name-only ({len(ids)} совп.)"
        if old_id:
            umap.setdefault("counterparty", {})[old_id] = au
            rep["agents_resolved"].append({"new": au, "old": old_id, "name": info["name"], "inn": info["inn"], "how": how})
        else:
            rep["agents_unresolved"].append({"new": au, "name": info["name"], "inn": info["inn"]})

    for su, name in channels.items():
        if su in rev_sc:
            continue
        if name and name.strip() in sc_by_name:
            old_id = sc_by_name[name.strip()]
            umap.setdefault("saleschannel", {})[old_id] = su
            rep["channels_resolved"].append({"new": su, "old": old_id, "name": name})
        else:
            rep["channels_unresolved"].append({"new": su, "name": name})

    if args.apply:
        save_umap(umap)

    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nКонтрагенты: резолв={len(rep['agents_resolved'])} не найдено={len(rep['agents_unresolved'])}")
    for x in rep["agents_resolved"]:
        print(f"  OK {x['name']} ← {x['how']}")
    for x in rep["agents_unresolved"]:
        print(f"  ?? НЕ НАЙДЕН: {x['name']} inn={x['inn'] or '—'}")
    print(f"Каналы: резолв={len(rep['channels_resolved'])} не найдено={len(rep['channels_unresolved'])}")
    for x in rep["channels_unresolved"]:
        print(f"  ?? канал не найден: {x['name']}")
    print(f"{'(DRY — uuid_map не сохранён)' if args.dry else 'uuid_map обновлён'}")
    print(f"Отчёт: {REPORT}")


if __name__ == "__main__":
    main()
