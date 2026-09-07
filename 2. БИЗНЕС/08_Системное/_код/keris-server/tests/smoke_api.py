#!/usr/bin/env python3
"""Сквозная проверка API на чистой sqlite: запись → уведомление → админ-правки.

Запуск: DATABASE_URL=sqlite:////tmp/keris_smoke.db ADMIN_API_KEY=smoke python3 tests/smoke_api.py
Гоняется перед деплоем, чтобы прод не был первым запуском новой схемы.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

DB = "/tmp/keris_smoke.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB}")
os.environ.setdefault("ADMIN_API_KEY", "smoke")
Path(DB).unlink(missing_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
PHONE = "+79990000001"


def _seed_past_visit(master_id: str, date_iso: str, time_hhmm: str) -> None:
    """Состоявшийся визит нельзя создать через API (лид-тайм) — пишем в базу напрямую."""
    from datetime import datetime, time as dtime

    from app.db import SessionLocal
    from app.models import Booking, BookingStatus, PetType

    starts = datetime.combine(date.fromisoformat(date_iso), dtime.fromisoformat(time_hhmm))
    db = SessionLocal()
    try:
        db.add(Booking(
            id="KERIS-PAST", owner_name="Тест Смоук", owner_phone=PHONE, pet_name="Барс",
            pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut", addon_ids=[],
            master_id=master_id, starts_at=starts, ends_at=starts + timedelta(minutes=90),
            price=7800, status=BookingStatus.completed, personal_data_consent=True,
        ))
        db.commit()
    finally:
        db.close()


def main() -> int:
    with TestClient(app) as c:
        assert c.get("/health").json()["ok"]

        rules = c.get("/api/rules").json()
        print("правила:", rules)
        assert rules["min_lead_hours"] == 2 and rules["horizon_days"] == 60

        # горизонт: дальше 60 дней слотов не даём
        far = (date.today() + timedelta(days=rules["horizon_days"] + 1)).isoformat()
        r = c.get("/api/slots", params={"date_iso": far, "service_id": "dog_complex_cut", "size": "XS"})
        assert r.status_code == 422, f"горизонт не сработал: {r.status_code}"

        day = master_id = free = None
        for extra in range(8):
            day = (date.today() + timedelta(days=3 + extra)).isoformat()
            slots = c.get("/api/slots", params={"date_iso": day, "service_id": "dog_complex_cut",
                                                "size": "XS", "addon_ids": ""}).json()
            found = next(((mid, s) for mid, s in (slots.get("masters") or {}).items() if s), None)
            if found:
                master_id, free = found
                break
        assert master_id and free, "нет свободных слотов у активных мастеров"
        time_hhmm = free[0]
        print(f"слоты {day}: мастер {master_id}, первый {time_hhmm}, всего {len(free)}, "
              f"длительность {slots['duration']} мин")

        promo = c.get("/api/promo-preview", params={"phone": PHONE, "service_id": "dog_complex_cut"}).json()
        print("промо новому клиенту:", promo)
        assert not promo.get("free_addons"), f"автоподарок снова включился: {promo}"

        payload = {
            "owner_name": "Тест Смоук", "owner_phone": PHONE,
            "pet_name": "Барс", "pet_type": "dog", "pet_breed": "Мальтипу", "pet_size": "XS",
            "service_id": "dog_complex_cut", "addon_ids": [],
            "master_id": "any", "date_iso": day, "time_hhmm": time_hhmm,
            "personal_data_consent": True, "marketing_consent": True, "media_consent": False,
            "comment": "смоук-тест",
        }
        r = c.post("/api/bookings", json=payload)
        assert r.status_code == 200, f"запись не создалась: {r.status_code} {r.text[:300]}"
        booking = r.json()
        print("запись:", booking["booking_id"], booking["starts_at"], booking["price"], "₽")
        assert booking["price"] == 7800, f"ожидали 7800 ₽, получили {booking['price']}"
        assert not booking.get("free_addon_ids"), f"в запись дописался подарок: {booking.get('free_addon_ids')}"

        # тот же слот второй раз — 409, без гонок
        r2 = c.post("/api/bookings", json=payload)
        assert r2.status_code == 409, f"двойная запись прошла: {r2.status_code}"

        # без согласия на ПДн — отказ
        r3 = c.post("/api/bookings", json={**payload, "personal_data_consent": False, "time_hhmm": free[2]})
        assert r3.status_code in (400, 422), f"запись без согласия прошла: {r3.status_code}"

        adm = c.get("/admin/bookings", params={"when": day}, headers=ADMIN).json()
        assert adm and not adm[0].get("gifts"), f"в админ-списке снова подарки: {adm}"
        print("админ-список:", adm[0]["time"], adm[0]["service"])

        # отказ в услуге по оферте — корректировка суммы
        bid = booking["booking_id"]
        r = c.patch(f"/admin/bookings/{bid}/price", json={"price": 1500, "reason": "отказ: агрессия"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        print("корректировка:", r.json()["was"], "→", r.json()["price"], "₽; минимум по оферте:",
              r.json()["min_refusal_fee"])

        # второй визит того же клиента — welcome-подарок не повторяется
        promo2 = c.get("/api/promo-preview", params={"phone": PHONE, "service_id": "dog_complex_cut"}).json()
        assert not any("Welcome" in p for p in promo2.get("promos", [])), f"подарок повторился: {promo2}"
        print("второй визит:", promo2)

        # личный кабинет «Мой Keris»: телефон в любом формате → тот же профиль
        profile = c.get("/api/client", params={"phone": "8" + PHONE[2:]}).json()
        assert profile["phone"] == PHONE, profile["phone"]
        assert profile["name"] == "Тест Смоук"
        assert [p["name"] for p in profile["pets"]] == ["Барс"]
        assert len(profile["visits"]) == 1 and profile["visits"][0]["status"] == "upcoming"
        assert profile["visits"][0]["petId"] == profile["pets"][0]["id"]
        print("кабинет:", profile["name"], profile["pets"][0]["name"], profile["visits"][0]["date"])

        # отзыв до визита запрещён, после — принимается и виден в /api/masters
        deny = c.post("/api/reviews", json={"phone": PHONE, "master_id": master_id, "stars": 5, "text": "рано"})
        assert deny.status_code == 403, f"отзыв без визита прошёл: {deny.status_code}"
        past = (date.today() - timedelta(days=2)).isoformat()
        past_booking = {**payload, "date_iso": past, "time_hhmm": time_hhmm, "master_id": master_id}
        with_past = c.post("/api/bookings", json=past_booking)
        if with_past.status_code != 200:  # лид-тайм не пускает запись в прошлое — ставим напрямую
            _seed_past_visit(master_id, past, time_hhmm)
        ok = c.post("/api/reviews", json={"phone": PHONE, "master_id": master_id, "stars": 5,
                                          "text": "Бережно и красиво."})
        assert ok.status_code == 200, f"отзыв после визита не принят: {ok.status_code} {ok.text[:200]}"
        masters = {m["id"]: m for m in c.get("/api/masters").json()}
        assert masters[master_id]["reviews"], "отзыв не появился в профиле мастера"
        print("отзыв:", masters[master_id]["reviews"][0])

    print("\nOK: правила, горизонт, слоты, промо, запись, антигонка, согласие, админ-правки, "
          "кабинет клиента, отзывы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
