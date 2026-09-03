#!/usr/bin/env python3
"""
Печатает метаданные контрагента (доп. поля) и первую бонусную программу — чтобы заполнить .env (ATTR_*).
Запуск: cd loyalty-service && MS_TOKEN=... python scripts/discover_metadata.py
"""
from __future__ import annotations

import os
import sys

import requests

BASE = os.environ.get("API_BASE", "https://api.moysklad.ru/api/remap/1.2").rstrip("/")
TOKEN = os.environ.get("MS_TOKEN", "")
if not TOKEN:
    print("Нужен MS_TOKEN в окружении", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Accept-Encoding": "gzip",
}


def main() -> None:
    r = requests.get(f"{BASE}/entity/counterparty/metadata", headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    attrs = data.get("attributes") or []
    print("=== Доп. поля контрагента (attributes) ===")
    for a in attrs:
        print(f"- {a.get('name')} | id={a.get('id')} | type={a.get('type')}")

    r2 = requests.get(f"{BASE}/entity/bonusprogram", headers=HEADERS, params={"limit": 5}, timeout=60)
    r2.raise_for_status()
    rows = r2.json().get("rows") or []
    print("\n=== Бонусные программы ===")
    for row in rows:
        print(f"- {row.get('name')} | id={row.get('id')}")


if __name__ == "__main__":
    main()
