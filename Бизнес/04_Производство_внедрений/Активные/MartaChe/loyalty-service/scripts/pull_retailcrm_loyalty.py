#!/usr/bin/env python3
"""
Выгрузка ВСЕЙ программы лояльности из RetailCRM (уход с RetailCRM у MartaChe).

Тянет:
  * /api/v5/loyalty/accounts  — участники ПЛ: уровень, баланс бонусов, сумма покупок,
                                статус, дата регистрации, телефон, ФИО, id клиента;
  * /api/v5/customers         — карточки клиентов (для даты рождения и доп. телефонов).

Сохраняет (в data/, gitignored — там ПДн):
  data/loyalty_accounts_raw.json   — сырьё аккаунтов ПЛ
  data/loyalty_customers_raw.json  — сырьё клиентов (id -> карточка)
  data/loyalty_all.csv             — нормализованная таблица (все поля, все статусы)

Доступы читаются из loyalty-service/.env:
  RETAILCRM_API_URL, RETAILCRM_API_KEY, RETAILCRM_LOYALTY_ID (опц.)

Запуск:
    python scripts/pull_retailcrm_loyalty.py
    python scripts/pull_retailcrm_loyalty.py --loyalty-id 2   # только одна программа
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
load_dotenv(ROOT / ".env")

TIER_BY_LEVEL_NAME = {"знакомство": 0, "дружба": 1, "любовь": 2}


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Заполните {name} в loyalty-service/.env")
    return val


def norm_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("8") and len(digits) >= 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def tier_from_level(level_name: str) -> str:
    key = (level_name or "").strip().lower()
    for name, num in TIER_BY_LEVEL_NAME.items():
        if name in key:
            return str(num)
    return ""


class RetailCRM:
    def __init__(self, base: str, key: str) -> None:
        self.base = base.rstrip("/") + "/api/v5"
        self.key = key
        self.s = requests.Session()

    def get(self, path: str, params: dict) -> dict:
        last = None
        for attempt in range(1, 8):
            try:
                r = self.s.get(self.base + path, params=dict(params, apiKey=self.key), timeout=(15, 180))
            except requests.exceptions.RequestException as exc:
                last = exc
                wait = min(2 ** attempt, 30)
                print(f"  сеть ({type(exc).__name__}) retry #{attempt} через {wait}s {path}", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 30)
                print(f"  [{r.status_code}] retry #{attempt} через {wait}s {path}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"RetailCRM {r.status_code} на {path}: {r.text[:300]}")
        raise SystemExit(f"Не удалось получить {path}: {last}")

    def paginate(self, path: str, list_key: str, params: dict | None = None, limit: int = 100) -> list[dict]:
        params = dict(params or {})
        page = 1
        out: list[dict] = []
        while True:
            data = self.get(path, dict(params, limit=limit, page=page))
            rows = data.get(list_key) or []
            out.extend(rows)
            pg = data.get("pagination") or {}
            total_pages = pg.get("totalPageCount") or 1
            print(f"  {path}: страница {page}/{total_pages} (+{len(rows)}, всего {len(out)})")
            if page >= total_pages or not rows:
                break
            page += 1
            time.sleep(0.1)
        return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loyalty-id", type=int, default=int(os.environ.get("RETAILCRM_LOYALTY_ID", "0") or 0),
                        help="Фильтр по id программы (0 = все программы)")
    args = parser.parse_args()

    api = RetailCRM(_require("RETAILCRM_API_URL"), _require("RETAILCRM_API_KEY"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Тяну участников ПЛ (/loyalty/accounts)...")
    accounts = api.paginate("/loyalty/accounts", "loyaltyAccounts")
    if args.loyalty_id:
        accounts = [a for a in accounts if (a.get("loyalty") or {}).get("id") == args.loyalty_id]
        print(f"  после фильтра loyalty_id={args.loyalty_id}: {len(accounts)}")
    (DATA_DIR / "loyalty_accounts_raw.json").write_text(
        json.dumps(accounts, ensure_ascii=False, indent=1), encoding="utf-8")

    print("Тяну карточки клиентов (/customers) для даты рождения и доп. телефонов...")
    customers = api.paginate("/customers", "customers")
    cust_by_id = {c["id"]: c for c in customers if "id" in c}
    (DATA_DIR / "loyalty_customers_raw.json").write_text(
        json.dumps(cust_by_id, ensure_ascii=False, indent=1), encoding="utf-8")

    csv_path = DATA_DIR / "loyalty_all.csv"
    fieldnames = [
        "account_id", "loyalty_id", "active", "status", "customer_id",
        "last_name", "first_name", "patronymic",
        "phone_raw", "phone_norm", "birthday", "birth_day", "birth_month",
        "level_name", "tier", "bonus_balance", "orders_sum", "created_at",
    ]
    n_no_phone = 0
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for a in accounts:
            cust = a.get("customer") or {}
            cid = cust.get("id")
            full = cust_by_id.get(cid, {})
            phone_raw = a.get("phoneNumber") or ""
            if not phone_raw:
                phs = full.get("phones") or []
                phone_raw = phs[0].get("number") if phs else ""
            phone_norm = norm_phone(phone_raw)
            if len(phone_norm) < 10:
                n_no_phone += 1
            birthday = full.get("birthday") or ""
            bday_d, bday_m = "", ""
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", birthday)
            if m:
                bday_m, bday_d = m.group(2), m.group(3)
            level = a.get("level") or {}
            level_name = level.get("name") or ""
            w.writerow({
                "account_id": a.get("id", ""),
                "loyalty_id": (a.get("loyalty") or {}).get("id", ""),
                "active": a.get("active", ""),
                "status": a.get("status", ""),
                "customer_id": cid or "",
                "last_name": cust.get("lastName") or full.get("lastName") or "",
                "first_name": cust.get("firstName") or full.get("firstName") or "",
                "patronymic": cust.get("patronymic") or full.get("patronymic") or "",
                "phone_raw": phone_raw,
                "phone_norm": phone_norm,
                "birthday": birthday,
                "birth_day": bday_d,
                "birth_month": bday_m,
                "level_name": level_name,
                "tier": tier_from_level(level_name),
                "bonus_balance": a.get("amount", 0),
                "orders_sum": a.get("ordersSum", 0),
                "created_at": a.get("createdAt") or "",
            })

    print(
        f"\nГотово. Участников ПЛ: {len(accounts)}; клиентов в справочнике: {len(cust_by_id)}; "
        f"без валидного телефона: {n_no_phone}"
    )
    print(f"CSV:  {csv_path}")
    print(f"Сырьё: {DATA_DIR}/loyalty_accounts_raw.json, {DATA_DIR}/loyalty_customers_raw.json")


if __name__ == "__main__":
    main()
