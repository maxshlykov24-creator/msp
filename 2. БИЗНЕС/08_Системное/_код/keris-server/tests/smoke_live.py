#!/usr/bin/env python3
"""Проверка живого сервера снаружи: каталог, слоты, промо, запись, отмена.

Канал до хоста нестабилен, поэтому каждый запрос повторяется до 4 раз.

Запуск: KERIS_BASE=https://194.87.118.214.sslip.io python3 tests/smoke_live.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

BASE = os.environ.get("KERIS_BASE", "https://194.87.118.214.sslip.io").rstrip("/")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def call(method: str, path: str, body: dict = None, admin: bool = False, attempts: int = 4):
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-Admin-Key"] = ADMIN_KEY
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for i in range(attempts):
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw[:300]
        except Exception as e:  # noqa: BLE001 — сеть до хоста рвётся
            last = e
            print(f"  {path}: попытка {i + 1} — {type(e).__name__}", file=sys.stderr)
            time.sleep(3)
    raise SystemExit(f"{path}: недоступно ({last})")


def main() -> int:
    status, health = call("GET", "/health")
    print("health:", status, health)

    _, masters = call("GET", "/api/masters")
    print("мастера:", ", ".join(f"{m['name']} ({m['id']})" for m in masters))
    assert masters, "в онлайн-записи нет мастеров"
    assert any(m["id"] == "svetlana" for m in masters), masters

    _, services = call("GET", "/api/services?pet_type=dog")
    complex_cut = next(s for s in services if s["id"] == "dog_complex_cut")
    print("услуг для собак:", len(services), "· комплекс со стрижкой XS:", complex_cut["prices"]["XS"], "₽")

    _, rules = call("GET", "/api/rules")
    print("лид-тайм:", rules["min_lead_hours"], "ч · горизонт:", rules["horizon_days"], "дней")

    # график из YCLIENTS: за неделю должен найтись хотя бы один свободный слот
    day = date.today() + timedelta(days=1)
    covered = 0
    for i in range(7):
        d = (day + timedelta(days=i)).isoformat()
        _, slots = call("GET", f"/api/slots?date_iso={d}&service_id=dog_complex_cut&size=XS&addon_ids=")
        working = [mid for mid, free in slots["masters"].items() if free]
        print(f"  {d}: работают {', '.join(working) or 'никто'}")
        covered += bool(working)
    assert covered >= 1, "на неделю вперёд нет свободных слотов"

    phone = "+79990000777"
    _, promo = call("GET", f"/api/promo-preview?phone={urllib.parse.quote(phone)}&service_id=dog_complex_cut")
    print("промо:", promo.get("promos"))

    iso = (date.today() + timedelta(days=4)).isoformat()
    _, slots = call("GET", f"/api/slots?date_iso={iso}&service_id=dog_complex_cut&size=XS&addon_ids=")
    mid, free = next((m, s) for m, s in slots["masters"].items() if s)
    status, created = call("POST", "/api/bookings", {
        "owner_name": "Проверка деплоя", "owner_phone": phone, "pet_name": "Тест",
        "pet_type": "dog", "pet_breed": "мальтипу", "pet_size": "XS",
        "service_id": "dog_complex_cut", "addon_ids": [], "master_id": mid,
        "date_iso": iso, "time_hhmm": free[-1],
        "personal_data_consent": True, "marketing_consent": False, "media_consent": False,
        "comment": "смоук живого сервера",
    })
    print("запись:", status, created)
    assert status == 200, created

    if ADMIN_KEY:
        status, adm = call("GET", f"/admin/bookings?when={iso}", admin=True)
        print("админ видит записей:", len(adm) if isinstance(adm, list) else adm)

    status, _ = call("POST", f"/api/bookings/{created['booking_id']}/cancel")
    print("отмена тестовой записи:", status)

    print("\nOK: живой сервер отвечает, слоты есть, запись создаётся")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
