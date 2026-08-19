"""Выгрузка сотрудников из старого аккаунта Wangpack → users.csv"""
import csv
import json
import sys
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
BASE = "https://api.moysklad.ru/api/remap/1.2"
OUT = Path(__file__).parent / "users.csv"


def main():
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {OLD_TOKEN}",
        "Accept-Encoding": "gzip",
    })
    rows = []
    offset = 0
    while True:
        r = session.get(
            f"{BASE}/entity/employee",
            params={"limit": 100, "offset": offset, "expand": "group"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("rows", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "uid", "archived", "group"])
        for e in rows:
            group = (e.get("group") or {}).get("name", "")
            w.writerow([
                e.get("name", ""),
                e.get("email", ""),
                e.get("uid", ""),
                e.get("archived", False),
                group,
            ])
    print(f"Сохранено {len(rows)} сотрудников → {OUT}")
    for e in rows:
        print(f"  - {e.get('name','?')} | {e.get('email','—')} | {e.get('uid','')}")


if __name__ == "__main__":
    main()
