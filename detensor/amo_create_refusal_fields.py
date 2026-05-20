"""
Создание 3 полей «Причина отказа» на сделках amoCRM Detensor (spineshop).
Только POST custom_fields — без изменения сделок и старых полей.

Запуск:
  export AMO_SUBDOMAIN=spineshop
  export AMO_LONG_LIVED_TOKEN=...
  python3 amo_create_refusal_fields.py --dry-run
  python3 amo_create_refusal_fields.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("AMO_LONG_LIVED_TOKEN") or os.environ.get("AMO_TOKEN", "")
SUBDOMAIN = os.environ.get("AMO_SUBDOMAIN", "spineshop")
BASE = f"https://{SUBDOMAIN}.amocrm.ru"
OUT = Path(__file__).parent / "amo_audit_out"
DRY_RUN = "--dry-run" in sys.argv

# Точные name из ТЗ (разделы 2.1–2.3)
FIELDS_SPEC = [
    {
        "name": "Причина отказа (Обращения)",
        "type": "select",
        "code": "REFUSAL_INQUIRY",
        "sort": 801,
        "enums": [
            "Дорого",
            "Отказ банка",
            "Выбрали других",
            "Кто-то переубедил",
            "Не устроили условия",
            "Не удалось связаться",
            "Пропала потребность",
            "Отложенная покупка",
            "Нецелевой клиент",
            "Бот / Спам",
            "Оптовая закупка",
        ],
        "pipeline_id": 9020986,
    },
    {
        "name": "Причина отказа (Пробная)",
        "type": "select",
        "code": "REFUSAL_TRIAL",
        "sort": 802,
        "enums": [
            "Не пришёл на пробную",
            "Перенёс пробу и не вернулся",
            "Не почувствовал эффекта",
            "Дорого",
            "Выбрали других",
            "Пропала потребность",
            "Не удалось связаться",
        ],
        "pipeline_id": 9849838,
    },
    {
        "name": "Причина отказа (Аренда)",
        "type": "select",
        "code": "REFUSAL_RENT",
        "sort": 803,
        "enums": [
            "Не почувствовал эффекта",
            "Не выдержал болевой эффект",
            "Не успел попробовать",
            "Не хватило денег на выкуп",
            "Выбрали других",
            "Вернул аренду без объяснений",
            "Не удалось связаться",
        ],
        "pipeline_id": 9033890,
    },
]


def request(method: str, path: str, body=None) -> dict:
    if not TOKEN:
        raise SystemExit("Задай AMO_LONG_LIVED_TOKEN или AMO_TOKEN")
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    time.sleep(0.25)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} → {e.code}\n{e.read().decode()[:800]}") from e


def fetch_all_field_names() -> set[str]:
    names: set[str] = set()
    page = 1
    while True:
        data = request("GET", f"/api/v4/leads/custom_fields?limit=250&page={page}")
        for f in data.get("_embedded", {}).get("custom_fields", []):
            names.add(f.get("name") or "")
        if page >= data.get("_page_count", 1):
            break
        page += 1
    return names


def build_payload() -> list[dict]:
    payload = []
    for spec in FIELDS_SPEC:
        enums = [{"value": v, "sort": (i + 1) * 10} for i, v in enumerate(spec["enums"])]
        payload.append({
            "name": spec["name"],
            "type": spec["type"],
            "code": spec["code"],
            "sort": spec["sort"],
            "enums": enums,
        })
    return payload


def main() -> None:
    print("=" * 70)
    print("amoCRM: создание полей «Причина отказа»")
    print(f"Subdomain: {SUBDOMAIN} | Режим: {'DRY-RUN' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 70)

    acc = request("GET", "/api/v4/account")
    print(f"Аккаунт: {acc.get('name')} (id={acc.get('id')})\n")

    existing = fetch_all_field_names()
    payload = build_payload()

    for item in payload:
        name = item["name"]
        if name in existing:
            print(f"  SKIP (уже есть): {name}")
        else:
            print(f"  CREATE: {name} ({len(item['enums'])} значений)")

    to_create = [p for p in payload if p["name"] not in existing]
    if not to_create:
        print("\nВсе 3 поля уже существуют — POST не нужен.")
        return

    print(f"\nК созданию: {len(to_create)} полей\n")
    print(json.dumps(to_create, ensure_ascii=False, indent=2))

    if DRY_RUN:
        print("\n[DRY-RUN] POST не выполнялся.")
        return

    result = request("POST", "/api/v4/leads/custom_fields", to_create)
    OUT.mkdir(exist_ok=True)
    created = []
    for f in result.get("_embedded", {}).get("custom_fields", []):
        spec = next(s for s in FIELDS_SPEC if s["name"] == f["name"])
        row = {
            "name": f["name"],
            "field_id": f["id"],
            "code": f.get("code"),
            "pipeline_id_ui": spec["pipeline_id"],
            "enums": [
                {"id": e["id"], "value": e["value"]}
                for e in (f.get("enums") or [])
            ],
        }
        created.append(row)
        print(f"\n  OK  #{f['id']}  {f['name']}")
        for e in f.get("enums") or []:
            print(f"      enum #{e['id']}: {e['value']}")

    out_path = OUT / "refusal_fields_created.json"
    json.dump(created, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")
    print("\nДальше вручную в UI: показать каждое поле только в своей воронке.")


if __name__ == "__main__":
    main()
