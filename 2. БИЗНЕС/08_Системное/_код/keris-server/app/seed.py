"""Загрузка seed_data.json в БД (идемпотентно — upsert по id).

Позиции, которых больше нет в seed, деактивируются (active=False), а не удаляются:
на них могут ссылаться прошлые записи.
"""
from __future__ import annotations

import json
import csv
import io
import os
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Addon, Master, PetType, Service, SubscriptionPlan

SEED_PATH = Path(__file__).with_name("seed_data.json")
_price_lock = threading.Lock()
price_sheet_status: dict = {"enabled": False, "last_success": None, "error": None}


def price_sheet_enabled() -> bool:
    return bool(os.environ.get("KERIS_PRICE_SHEET_ID"))


def load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return json.load(f)


def apply_seed(db: Session) -> None:
    data = load_seed()
    managed = price_sheet_enabled()
    seen_services: set[str] = set()
    seen_addons: set[str] = set()

    for pet_type in ("dog", "cat"):
        for svc in data[pet_type]["services"]:
            row = db.get(Service, svc["id"])
            if row is None:
                row = Service(id=svc["id"], pet_type=PetType(pet_type))
                db.add(row)
            row.name = svc["name"]
            if not managed or not row.prices:
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
            if not managed or row.price is None:
                row.price = ad["price"]
                row.prices = ad.get("prices")
                row.durations = ad.get("durations")
                row.duration_min = ad["duration"]
            row.price_from = bool(ad.get("price_from", False))
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
        if not managed or not row.prices:
            row.prices = plan["prices"]
        row.bonus = plan.get("bonus", "")
        row.bonus_spa = int(plan.get("bonus_spa", 0))

    db.commit()


def _catalog_entries(db: Session) -> dict:
    result = {}
    for kind, cls in (("Услуга", Service), ("Допуслуга", Addon), ("Абонемент", SubscriptionPlan)):
        for row in db.execute(select(cls)).scalars():
            if hasattr(row, "active") and not row.active:
                continue
            for size in (row.prices or {"flat": 0}):
                result[(kind, row.id, size)] = row
    return result


def parse_price_sheet(text: str, expected: set) -> dict:
    """Полная проверка перед изменением БД. Пустой/частичный лист не прайс."""
    header = ["Услуга", "Питомец", "Категория", "Цена, ₽", "Время, мин", "Тип",
              "Код позиции", "Ключ категории", "ID YCLIENTS"]
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or rows[0][:9] != header:
        raise ValueError("Не найдены заголовки прайса")
    parsed = {}
    for index, cells in enumerate(rows[1:], 2):
        if not any(cells):
            continue
        if len(cells) < 8:
            raise ValueError(f"Строка {index}: нет служебного кода")
        key = (cells[5].strip(), cells[6].strip(), cells[7].strip())
        if key not in expected or key in parsed:
            raise ValueError(f"Строка {index}: неизвестная или повторная позиция")
        def number(value, minimum, maximum):
            raw = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
            from decimal import Decimal, InvalidOperation
            try:
                n = Decimal(raw)
                if not n.is_finite() or n != n.to_integral_value() or not minimum <= n <= maximum:
                    raise ValueError
                return int(n)
            except (InvalidOperation, ValueError):
                raise ValueError(f"Строка {index}: требуется целое число {minimum}…{maximum}") from None
        price = number(cells[3], 0, 10000000)
        minutes = None if key[0] == "Абонемент" else number(cells[4], 1, 1440)
        parsed[key] = (price, minutes)
    if set(parsed) != expected:
        raise ValueError("Прайс неполный: отсутствуют позиции каталога")
    return parsed


def refresh_price_sheet(db: Session, *, text: str | None = None, sync_yclients: bool = True) -> dict:
    """Google → каталог БД → каталог YCLIENTS. Таблицу bookings не меняем."""
    import requests
    from . import yclients_client
    if text is None and not price_sheet_enabled():
        return {"enabled": False}
    with _price_lock:
        price_sheet_status["enabled"] = True
        try:
            if text is None:
                sheet_id = os.environ["KERIS_PRICE_SHEET_ID"]
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
                response = requests.get(url, params={"format": "csv", "gid": "0"}, timeout=20)
                response.raise_for_status()
                response.encoding = "utf-8"
                text = response.text
            entries = _catalog_entries(db)
            parsed = parse_price_sheet(text, set(entries))
            changed = 0
            for key, (price, minutes) in parsed.items():
                row = entries[key]
                size = key[2]
                if size == "flat":
                    changed += int(row.price != price or row.duration_min != minutes)
                    row.price, row.duration_min = price, minutes
                else:
                    prices = dict(row.prices or {})
                    durations = dict(getattr(row, "durations", None) or {})
                    changed += int(prices.get(size) != price or (minutes is not None and durations.get(size) != minutes))
                    prices[size] = price
                    row.prices = prices
                    if minutes is not None:
                        durations[size] = minutes
                        row.durations = durations
            db.commit()
            # Даже после частичного сбоя YCLIENTS следующая сверка догоняет каталог.
            updated = 0
            if sync_yclients:
                current = {int(r["id"]): r for r in yclients_client.get_services()}
                for key, (price, minutes) in parsed.items():
                    if minutes is None:
                        continue
                    row = entries[key]
                    yc_id = (row.yclients_service_ids or {}).get(key[2])
                    if not yc_id or int(yc_id) not in current:
                        raise ValueError(f"Не сопоставлена услуга YCLIENTS: {key[1]} {key[2]}")
                    svc = current[int(yc_id)]
                    seconds = ((minutes * 60 + 299) // 300) * 300
                    staff = svc.get("staff") or []
                    if (float(svc.get("price_min") or 0) == price
                            and float(svc.get("price_max") or 0) == price
                            and int(svc.get("duration") or 0) == seconds
                            and all(int(s.get("seance_length") or 0) == seconds for s in staff)):
                        continue
                    payload = {k: svc[k] for k in ("title", "category_id", "comment", "is_online", "active") if k in svc}
                    payload.update(price_min=price, price_max=price, duration=seconds,
                                   staff=[{"id": s["id"], "seance_length": seconds} for s in staff])
                    yclients_client.update_service(int(yc_id), payload)
                    updated += 1
            price_sheet_status.update(last_success=clock.now().isoformat(), error=None)
            return {"enabled": True, "changed": changed, "yclients_updated": updated}
        except Exception as exc:
            db.rollback()
            price_sheet_status["error"] = type(exc).__name__ + ": " + str(exc)[:200]
            logging.getLogger("keris.price").warning("Прайс не обновлён: %s", type(exc).__name__)
            return {"enabled": True, "error": price_sheet_status["error"]}


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
