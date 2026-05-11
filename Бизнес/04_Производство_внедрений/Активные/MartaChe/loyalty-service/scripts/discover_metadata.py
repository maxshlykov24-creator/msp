#!/usr/bin/env python3
"""
Печатает метаданные:
  - доп. поля контрагента (entity/counterparty/metadata)
  - доп. поля заказа покупателя (entity/customerorder/metadata)
  - первые бонусные программы

Запуск:
  cd loyalty-service && MS_TOKEN=... python scripts/discover_metadata.py
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


def _print_attrs(title: str, path: str) -> None:
    r = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    raw_attrs = data.get("rows") or data.get("attributes") or []
    attrs = list(raw_attrs.get("rows") or []) if isinstance(raw_attrs, dict) else list(raw_attrs)
    print(f"=== {title} ({len(attrs)}) ===")
    for a in attrs:
        print(f"- {a.get('name')!r:<40} | id={a.get('id')} | type={a.get('type')}")


def main() -> None:
    _print_attrs("Доп. поля контрагента (counterparty)", "/entity/counterparty/metadata/attributes")
    print()
    _print_attrs("Доп. поля заказа покупателя (customerorder)", "/entity/customerorder/metadata/attributes")
    print()
    r2 = requests.get(f"{BASE}/entity/bonusprogram", headers=HEADERS, params={"limit": 5}, timeout=60)
    r2.raise_for_status()
    rows = r2.json().get("rows") or []
    print("=== Бонусные программы ===")
    for row in rows:
        print(f"- {row.get('name')} | id={row.get('id')}")


if __name__ == "__main__":
    main()
