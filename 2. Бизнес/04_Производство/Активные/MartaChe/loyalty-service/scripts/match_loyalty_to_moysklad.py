#!/usr/bin/env python3
"""
Сопоставление выгрузки ПЛ из RetailCRM (data/loyalty_all.csv, см. pull_retailcrm_loyalty.py)
с контрагентами МойСклад по телефону. Дубли НЕ объединяются автоматически.

Вход:
  data/loyalty_all.csv — из pull_retailcrm_loyalty.py

Выход (data/):
  loyalty_matched.csv      — однозначные пары RetailCRM-аккаунт <-> контрагент МС,
                              готовы к переносу через migrate_full_loyalty.py
  loyalty_duplicates_report.csv — телефон встречается у НЕСКОЛЬКИХ участников ПЛ
                              в RetailCRM. Для ручного объединения перед переносом.
  loyalty_attention.csv    — спорные/непонятные случаи: нет валидного телефона,
                              контрагент в МойСклад не найден, найдено НЕСКОЛЬКО
                              контрагентов с этим телефоном (дубль на стороне МС),
                              не распознан уровень ПЛ.

Запуск (из каталога loyalty-service):
    python scripts/match_loyalty_to_moysklad.py
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.moysklad_client import MoySkladClient  # noqa: E402

DATA_DIR = ROOT / "data"
IN_CSV = DATA_DIR / "loyalty_all.csv"


def norm_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("8") and len(digits) >= 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def build_counterparty_phone_index(client: MoySkladClient) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    offset = 0
    total = None
    while True:
        data = client.get("/entity/counterparty", params={"limit": 1000, "offset": offset})
        rows = list(data.get("rows") or [])
        total = (data.get("meta") or {}).get("size", total)
        for row in rows:
            phone = norm_phone(str(row.get("phone") or ""))
            cp_id = str(row.get("id") or "")
            if len(phone) < 10 or not cp_id:
                continue
            name = str(row.get("name") or "")
            index[phone].append((cp_id, name))
            index[phone[-10:]].append((cp_id, name))
        offset += len(rows)
        print(f"  МойСклад /entity/counterparty: {offset}/{total}")
        if len(rows) < 1000:
            break
    # схлопнуть дубли (одна и та же пара может попасть дважды из-за двух ключей)
    for key, vals in index.items():
        seen: dict[str, str] = {}
        for cp_id, name in vals:
            seen[cp_id] = name
        index[key] = list(seen.items())
    return index


def main() -> None:
    if not IN_CSV.is_file():
        raise SystemExit(f"Нет {IN_CSV}. Сначала запустите pull_retailcrm_loyalty.py")

    rows = list(csv.DictReader(IN_CSV.open(encoding="utf-8")))
    print(f"Строк в {IN_CSV.name}: {len(rows)}")

    print("Строю индекс телефонов МойСклад (все контрагенты)...")
    client = MoySkladClient()
    phone_index = build_counterparty_phone_index(client)
    print(f"  контрагентов с телефоном (уникальных ключей): {len(phone_index)}")

    by_phone: dict[str, list[dict]] = defaultdict(list)
    invalid_phone: list[dict] = []
    for r in rows:
        p = r.get("phone_norm") or ""
        if len(p) < 10:
            invalid_phone.append(r)
        else:
            by_phone[p].append(r)

    matched: list[dict] = []
    duplicates: list[dict] = []
    attention: list[dict] = []

    for r in invalid_phone:
        attention.append({**r, "reason": "invalid_phone", "ms_candidates": ""})

    for phone, group in by_phone.items():
        if len(group) > 1:
            # дубль на стороне RetailCRM — не мержим, отдаём на ручной разбор
            for r in group:
                duplicates.append({
                    **r,
                    "reason": "retailcrm_duplicate_phone",
                    "duplicate_group_size": len(group),
                    "duplicate_account_ids": ";".join(str(x.get("account_id")) for x in group),
                })
            continue

        r = group[0]
        if not r.get("tier"):
            attention.append({**r, "reason": "tier_unmapped", "ms_candidates": r.get("level_name", "")})
            continue

        candidates = phone_index.get(phone) or phone_index.get(phone[-10:]) or []
        if not candidates:
            attention.append({**r, "reason": "ms_not_found", "ms_candidates": ""})
            continue
        if len(candidates) > 1:
            names = "; ".join(f"{cid}:{name}" for cid, name in candidates[:5])
            attention.append({**r, "reason": "ms_ambiguous_counterparty", "ms_candidates": names})
            continue

        ms_id, ms_name = candidates[0]
        matched.append({**r, "ms_agent_id": ms_id, "ms_agent_name": ms_name})

    matched_fields = list(rows[0].keys()) + ["ms_agent_id", "ms_agent_name"] if rows else []
    with (DATA_DIR / "loyalty_matched.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=matched_fields)
        w.writeheader()
        w.writerows(matched)

    # utf-8-sig — чтобы Excel на Windows корректно показывал кириллицу (эти файлы для менеджера)
    dup_fields = (list(rows[0].keys()) if rows else []) + ["reason", "duplicate_group_size", "duplicate_account_ids"]
    with (DATA_DIR / "loyalty_duplicates_report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=dup_fields)
        w.writeheader()
        w.writerows(duplicates)

    att_fields = (list(rows[0].keys()) if rows else []) + ["reason", "ms_candidates"]
    with (DATA_DIR / "loyalty_attention.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=att_fields)
        w.writeheader()
        w.writerows(attention)

    print(
        f"\nГотово. matched={len(matched)}  "
        f"duplicates(RetailCRM, строк)={len(duplicates)} ({sum(1 for r in duplicates)/2 if duplicates else 0:.0f} групп)  "
        f"attention={len(attention)}"
    )
    print(f"  {DATA_DIR / 'loyalty_matched.csv'}")
    print(f"  {DATA_DIR / 'loyalty_duplicates_report.csv'}")
    print(f"  {DATA_DIR / 'loyalty_attention.csv'}")


if __name__ == "__main__":
    main()
