"""Загрузка seed_data.json в БД (идемпотентно — upsert по id).

Позиции, которых больше нет в seed, деактивируются (active=False), а не удаляются:
на них могут ссылаться прошлые записи.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Addon, Master, PetType, Service, SubscriptionPlan

SEED_PATH = Path(__file__).with_name("seed_data.json")


def load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return json.load(f)


def apply_seed(db: Session) -> None:
    data = load_seed()
    seen_services: set[str] = set()
    seen_addons: set[str] = set()

    for pet_type in ("dog", "cat"):
        for svc in data[pet_type]["services"]:
            row = db.get(Service, svc["id"])
            if row is None:
                row = Service(id=svc["id"], pet_type=PetType(pet_type))
                db.add(row)
            row.name = svc["name"]
            row.prices = svc["prices"]
            row.durations = svc.get("durations")
            row.duration_min = svc["duration"]
            row.group = svc.get("group", "base")
            row.description = svc.get("desc", "")
            row.includes = svc.get("includes", "")
            row.active = True
            seen_services.add(svc["id"])

        for ad in data[pet_type]["addons"]:
            row = db.get(Addon, ad["id"])
            if row is None:
                row = Addon(id=ad["id"], pet_type=PetType(pet_type))
                db.add(row)
            row.name = ad["name"]
            row.price = ad["price"]
            row.prices = ad.get("prices")
            row.durations = ad.get("durations")
            row.price_from = bool(ad.get("price_from", False))
            row.duration_min = ad["duration"]
            row.group = ad.get("group", "")
            row.active = True
            seen_addons.add(ad["id"])

    for row in db.execute(select(Service)).scalars().all():
        if row.id not in seen_services:
            row.active = False
    for row in db.execute(select(Addon)).scalars().all():
        if row.id not in seen_addons:
            row.active = False

    seen_masters = set()
    for m in data["masters"]:
        row = db.get(Master, m["id"])
        if row is None:
            row = Master(id=m["id"])
            db.add(row)
        row.name = m["name"]
        if not row.caption:
            row.caption = m.get("caption", "")
        # Seed — запасной состав для тестов. Сопоставленного с YCLIENTS
        # не затираем: active и часы ведёт график филиала.
        if not row.yclients_staff_id:
            row.active = bool(m.get("active", True))
            row.work_start = m["schedule"]["start"]
            row.work_end = m["schedule"]["end"]
            row.shift_on = int(m["schedule"].get("shift_on", 0))
            row.shift_off = int(m["schedule"].get("shift_off", 0))
            row.shift_start = m["schedule"].get("shift_start", "")
        seen_masters.add(m["id"])
    # Тестовые заготовки без YCLIENTS скрываем. Мастеров из филиала не трогаем:
    # их состав обновляет sync_staff_from_yclients.
    for row in db.execute(select(Master)).scalars().all():
        if row.id not in seen_masters and not row.yclients_staff_id:
            row.active = False

    for plan in data["subscriptions"]["packages"]:
        row = db.get(SubscriptionPlan, plan["id"])
        if row is None:
            row = SubscriptionPlan(id=plan["id"])
            db.add(row)
        row.name = plan["name"]
        row.visits = plan["visits"]
        row.validity_months = plan["months"]
        row.prices = plan["prices"]
        row.bonus = plan.get("bonus", "")
        row.bonus_spa = int(plan.get("bonus_spa", 0))

    db.commit()


def subscriptions_config() -> dict:
    return load_seed()["subscriptions"]


def subscription_coefficients() -> dict[str, float]:
    return subscriptions_config()["coefficients"]


def subscription_included_addons() -> list[str]:
    return subscriptions_config().get("included_addons", [])


def min_balance_visits() -> float:
    return float(subscriptions_config().get("min_balance_visits", 0.5))


def promos() -> dict:
    return load_seed().get("promos", {})


def booking_rules() -> dict:
    return load_seed()["booking_rules"]


def slot_step_min() -> int:
    return load_seed()["slot_step_min"]


def salon_close_min() -> int:
    return load_seed()["salon_close_min"]


def salon_open_min() -> int:
    return load_seed().get("salon_open_min", 600)


def compute_expiry(months: int, now: datetime | None = None) -> datetime:
    now = now or clock.now()
    # без внешних зависимостей (dateutil) — приближение по 30 дней/месяц, приемлемо для сроков действия абонемента
    return now + timedelta(days=30 * months)
