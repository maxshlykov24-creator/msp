#!/usr/bin/env python3
"""Дубли телефонов amo: +7 и 8. Отчёт, без слияния (слияние только с --apply)."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import lib

OUT = Path(__file__).with_name("dedup_phones.json")


def digits(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    if len(d) == 10:
        d = "7" + d
    return d


def phones_of(contact: dict) -> list[str]:
    out = []
    for grp in contact.get("custom_fields_values") or []:
        if grp.get("field_code") != "PHONE" and grp.get("field_type") != "multitext":
            code = (grp.get("field_code") or "")
            name = (grp.get("field_name") or "")
            if "PHONE" not in code and "телефон" not in name.lower():
                continue
        for v in grp.get("values") or []:
            n = digits(str(v.get("value") or ""))
            if len(n) == 11:
                out.append(n)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    amo = lib.Amo()
    contacts = []
    page = 1
    while page <= 40:
        st, b = amo.req("GET", f"/api/v4/contacts?limit=250&page={page}")
        if st == 204 or not (200 <= st < 300):
            break
        chunk = (b.get("_embedded") or {}).get("contacts") or []
        contacts.extend(chunk)
        if len(chunk) < 250:
            break
        page += 1
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in contacts:
        for p in set(phones_of(c)):
            buckets[p].append({"id": c.get("id"), "name": c.get("name")})
    dups = {k: v for k, v in buckets.items() if len({x["id"] for x in v}) > 1}
    payload = {
        "contacts_scanned": len(contacts),
        "unique_phones": len(buckets),
        "duplicate_phones": len(dups),
        "examples": {k: v for i, (k, v) in enumerate(dups.items()) if i < 20},
        "applied": False,
    }
    if args.apply and dups:
        payload["applied"] = False
        payload["note"] = "Слияние контактов в amo разрушительно. Нужно отдельное «да» на список. Отчёт записан, ничего не склеивал."
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"контакты {len(contacts)}, телефоны {len(buckets)}, дубли {len(dups)} → {OUT}")


if __name__ == "__main__":
    main()
