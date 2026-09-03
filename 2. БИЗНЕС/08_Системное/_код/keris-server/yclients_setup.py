#!/usr/bin/env python3
"""Сопоставление мастеров/услуг/допов с YCLIENTS, выгрузка графика и полный каталог.

Источник истины — наша БД. Скрипт проецирует её в YCLIENTS:

1. `--sync-catalog` — создаёт/обновляет в YCLIENTS отдельную услугу **на каждый
   размер питомца** (без `price_min`/`price_max` диапазона — точная цена),
   сгруппированную по весовым категориям (см. `DOG_SIZE_LABELS`/`CAT_SIZE_LABELS`).
   Допы с фикс. ценой (не зависящие от размера) остаются одной услугой в
   «Допы · собаки/кошки»; допы-исключения (окрашивание) тоже размерные.
2. `--cleanup-old-catalog` — одноразово удаляет старые услуги-диапазоны
   (до перехода на размерные категории 2026-08-07).
3. Маппинг мастеров `Master.yclients_staff_id`.
4. Состав и график **читаем** из YCLIENTS (кто работает в журнале). Выгрузить
   наш старый цикл 4/3 обратно — только с `--push-sched`.
5. `--repush` — добрать будущие записи, которых ещё нет в YCLIENTS.

Запуск на сервере:
    cd /root/keris-server && ./venv/bin/python yclients_setup.py --cleanup-old-catalog
    ./venv/bin/python yclients_setup.py --sync-catalog
    ./venv/bin/python yclients_setup.py --days 180

Флаги:
    --sync-catalog       полный прайс + допы в YCLIENTS, по размерам (идемпотентно)
    --cleanup-old-catalog  удалить старые услуги-диапазоны (одноразово, деструктивно)
    --days N              горизонт графика (по умолчанию 180)
    --check               только состояние
    --skip-map             не трогать маппинг мастеров
    --skip-sched           без графика
    --push-sched           выгрузить наш цикл 4/3 в YCLIENTS (по умолчанию только читаем)
    --repush                добрать будущие записи без yclients_record_id
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta

from app.booking_logic import addon_duration, service_duration, shift_day_off
from app.db import SessionLocal
from app.models import Addon, Booking, BookingStatus, Master, MasterDayOff, PetType, SalonClosure, Service
from app import sync, yclients_client

STAFF_MAP = {
    "svetlana": 5824353,
    "alla": 5824356,
}

# Услуги-диапазоны образца 2026-08-07 (до перехода на размерные категории) — на удаление.
LEGACY_MIRROR_IDS = [30786306, 30798738, 30798741]

# Сетка ЦЕН разовых услуг (`size_grids.dogs_services` в price_grooming.yaml).
# Не путать с сеткой абонементов и длительностей (до5/5-10/10-25/25-45/от45):
# в YCLIENTS лежат цены разовых услуг, значит и вес в названии категории — их.
DOG_SIZE_LABELS = {
    "XS": "до 5 кг",
    "S": "5–10 кг",
    "M": "10–15 кг",
    "L": "15–25 кг",
    "XL": "от 25 кг",
}
CAT_SIZE_LABELS = {
    "short_small": "Короткошёрстные (до 5 кг)",
    "short_large": "Короткошёрстные (от 5 кг)",
    "long_small": "Длинношёрстные (до 5 кг)",
    "long_large": "Длинношёрстные (от 5 кг)",
}
# Прежние названия категорий — категория ищется по ним и переименовывается, а не дублируется.
# Кошки (до 2026-08-07, 11:30) — без веса, только «мелкие»/«крупные». Порог 5 кг взят из
# ПРАЙС_ГРУМИНГ.md (там же short_small/long_small = «до 5 кг»), по аналогии с XS у собак.
# Собаки (до 2026-08-25) — вес был подписан по сетке абонементов, хотя цены внутри разовые.
LEGACY_CATEGORY_TITLES = {
    "dog_XS": ["Собаки · Мини (до 5 кг)"],
    "dog_S": ["Собаки · Малые (5–10 кг)"],
    "dog_M": ["Собаки · Средние (10–25 кг)"],
    "dog_L": ["Собаки · Крупные (25–45 кг)"],
    "dog_XL": ["Собаки · Гигантские (от 45 кг)"],
    "cat_short_small": ["Кошки · короткошёрстные, мелкие"],
    "cat_short_large": ["Кошки · короткошёрстные, крупные"],
    "cat_long_small": ["Кошки · длинношёрстные, мелкие"],
    "cat_long_large": ["Кошки · длинношёрстные, крупные"],
}
ADDON_CATEGORY_TITLES = {
    "addons_dogs": "Допы · собаки",
    "addons_cats": "Допы · кошки",
}


def _size_labels(pet_type: PetType) -> dict[str, str]:
    return DOG_SIZE_LABELS if pet_type == PetType.dog else CAT_SIZE_LABELS


def _category_key(pet_type: PetType, size: str) -> str:
    return f"{pet_type.value}_{size}"


def _category_title(pet_type: PetType, size: str) -> str:
    pet = "Собаки" if pet_type == PetType.dog else "Кошки"
    label = _size_labels(pet_type).get(size, size)
    return f"{pet} · {label}"


def _round_seance_sec(sec: int) -> int:
    """YCLIENTS: длительность услуги кратна 300 с (5 мин)."""
    if sec <= 0:
        return 300
    return max(300, ((sec + 299) // 300) * 300)


def _duration_sec_addon_flat(addon: Addon) -> int:
    return _round_seance_sec(int(addon.duration_min) * 60)


def staff_ids_for_catalog(db=None) -> list[int]:
    ids = list(dict.fromkeys(STAFF_MAP.values()))
    if db is None:
        return ids
    for master in db.query(Master).filter(Master.yclients_staff_id.isnot(None), Master.active.is_(True)):
        sid = int(master.yclients_staff_id)
        if sid not in ids:
            ids.append(sid)
    return ids


def _staff_payload(seance_length: int, staff_ids: list[int] | None = None) -> list[dict]:
    ids = staff_ids if staff_ids is not None else list(STAFF_MAP.values())
    return [{"id": sid, "seance_length": seance_length} for sid in ids]


def ensure_categories() -> dict[str, int]:
    """Возвращает {key: category_id}. Создаёт недостающие по названию.

    Ключи: `dog_XS`…`dog_XL`, `cat_short_small`…`cat_long_large`, `addons_dogs`, `addons_cats`.
    """
    plan: list[tuple[str, str]] = [
        (_category_key(PetType.dog, size), _category_title(PetType.dog, size)) for size in DOG_SIZE_LABELS
    ] + [
        (_category_key(PetType.cat, size), _category_title(PetType.cat, size)) for size in CAT_SIZE_LABELS
    ] + list(ADDON_CATEGORY_TITLES.items())

    existing = {c.get("title"): int(c["id"]) for c in yclients_client.get_service_categories() if c.get("id")}
    out: dict[str, int] = {}
    for key, title in plan:
        if title in existing:
            out[key] = existing[title]
            print(f"  = категория {title!r} id={out[key]}")
            continue
        legacy_title = next((t for t in LEGACY_CATEGORY_TITLES.get(key, []) if t in existing), None)
        if legacy_title:
            cid = existing[legacy_title]
            yclients_client.update_service_category(cid, title)
            out[key] = cid
            print(f"  ~ категория переименована {legacy_title!r} → {title!r} id={cid}")
            time.sleep(0.3)
            continue
        created = yclients_client.create_service_category(title)
        cid = (created.get("data") or {}).get("id")
        if not cid:
            raise RuntimeError(f"не создалась категория {title}: {created}")
        out[key] = int(cid)
        print(f"  + категория {title!r} id={out[key]}")
        time.sleep(0.3)
    return out


def _upsert_yc_service(
    *,
    title: str,
    category_id: int,
    price_min: int,
    price_max: int,
    duration_sec: int,
    comment: str,
    existing_id: int | None,
    staff_ids: list[int] | None = None,
) -> int:
    payload = {
        "title": title,
        "category_id": category_id,
        "price_min": price_min,
        "price_max": price_max,
        "duration": duration_sec,
        "comment": (comment or "")[:255],
        "staff": _staff_payload(duration_sec, staff_ids),
        "is_online": 1,
        # POST часто создаёт active:0; в PUT поле принимается (проверено живьём).
        "active": 1,
    }
    if existing_id:
        yclients_client.update_service(existing_id, payload)
        # Повторный PUT со staff активирует услугу (active:1), см. справочник.
        return existing_id
    created = yclients_client.create_service(payload)
    new_id = (created.get("data") or {}).get("id")
    if not new_id:
        raise RuntimeError(f"не создалась услуга {title}: {created}")
    # create часто отдаёт active:0 — добиваем PUT с тем же staff.
    yclients_client.update_service(int(new_id), payload)
    return int(new_id)


def cleanup_old_catalog(db) -> None:
    """Одноразово: удаляет старые услуги-диапазоны (созданы 2026-08-07 до перехода
    на размерные категории). Деструктивно — вызывать явно через --cleanup-old-catalog."""
    print("--- Удаляю старые услуги-диапазоны ---")
    old_ids: set[int] = set(LEGACY_MIRROR_IDS)
    for service in db.query(Service).all():
        if service.yclients_service_id:
            old_ids.add(int(service.yclients_service_id))
    for addon in db.query(Addon).all():
        if addon.yclients_service_id:
            old_ids.add(int(addon.yclients_service_id))

    for yc_id in sorted(old_ids):
        try:
            yclients_client.delete_service(yc_id)
            print(f"  - удалена услуга yc {yc_id}")
        except yclients_client.YClientsError as e:
            print(f"  ! yc {yc_id}: {e}")
        time.sleep(0.2)

    for service in db.query(Service).all():
        service.yclients_service_id = None
    for addon in db.query(Addon).all():
        addon.yclients_service_id = None
    db.commit()
    print(f"  готово: {len(old_ids)} старых услуг обработано")


def sync_catalog(db) -> None:
    print("--- Каталог YCLIENTS: услуги по размерным категориям + допы ---")
    cats = ensure_categories()
    yc_by_id = {int(s["id"]): s for s in yclients_client.get_services() if s.get("id")}
    staff_ids = staff_ids_for_catalog(db)

    print("--- Услуги (своя позиция на каждый размер, без диапазона) ---")
    for service in db.query(Service).order_by(Service.id).all():
        if not service.active or not service.prices:
            continue
        ids_map: dict[str, int] = dict(service.yclients_service_ids or {})
        pet = "собака" if service.pet_type == PetType.dog else "кошка"
        for size, price in service.prices.items():
            cat_key = _category_key(service.pet_type, size)
            if cat_key not in cats:
                continue
            dur = _round_seance_sec(service_duration(service, size) * 60)
            existing = ids_map.get(size)
            if existing and existing not in yc_by_id:
                existing = None
            yc_id = _upsert_yc_service(
                title=f"{service.name} ({pet})",
                category_id=cats[cat_key],
                price_min=int(price),
                price_max=int(price),
                duration_sec=dur,
                comment=service.description or service.includes or "",
                existing_id=existing,
                staff_ids=staff_ids,
            )
            mark = "=" if ids_map.get(size) == yc_id else "+"
            print(f"  {mark} {service.id:<22} [{size:<11}] → yc {yc_id}  {price}₽  {dur // 60}мин")
            ids_map[size] = yc_id
            time.sleep(0.2)
        service.yclients_service_ids = ids_map
        db.commit()

    print("--- Допы с ценой по размеру (окрашивание) ---")
    for addon in db.query(Addon).order_by(Addon.id).all():
        if not addon.active or not addon.prices:
            continue
        ids_map = dict(addon.yclients_service_ids or {})
        pet = "собака" if addon.pet_type == PetType.dog else "кошка"
        for size, price in addon.prices.items():
            cat_key = _category_key(addon.pet_type, size)
            if cat_key not in cats:
                continue
            dur = _round_seance_sec(addon_duration(addon, size) * 60)
            existing = ids_map.get(size)
            if existing and existing not in yc_by_id:
                existing = None
            yc_id = _upsert_yc_service(
                title=f"{addon.name} ({pet})",
                category_id=cats[cat_key],
                price_min=int(price),
                price_max=int(price),
                duration_sec=dur,
                comment=addon.group or "",
                existing_id=existing,
                staff_ids=staff_ids,
            )
            mark = "=" if ids_map.get(size) == yc_id else "+"
            print(f"  {mark} {addon.id:<22} [{size:<11}] → yc {yc_id}  {price}₽  {dur // 60}мин")
            ids_map[size] = yc_id
            time.sleep(0.2)
        addon.yclients_service_ids = ids_map
        db.commit()

    print("--- Допы с фиксированной ценой (без размера) ---")
    for addon in db.query(Addon).order_by(Addon.id).all():
        if not addon.active or addon.prices:
            continue
        cat_key = "addons_dogs" if addon.pet_type == PetType.dog else "addons_cats"
        pet = "собака" if addon.pet_type == PetType.dog else "кошка"
        title = f"{addon.name} ({pet})"
        dur = _duration_sec_addon_flat(addon)
        ids_map = dict(addon.yclients_service_ids or {})
        existing = ids_map.get("flat")
        if existing and existing not in yc_by_id:
            existing = None
        yc_id = _upsert_yc_service(
            title=title,
            category_id=cats[cat_key],
            price_min=int(addon.price),
            price_max=int(addon.price),
            duration_sec=dur,
            comment=addon.group or "",
            existing_id=existing,
            staff_ids=staff_ids,
        )
        mark = "=" if ids_map.get("flat") == yc_id else "+"
        print(f"  {mark} {addon.id:<22} → yc {yc_id}  {addon.price}₽  {dur // 60}мин")
        ids_map["flat"] = yc_id
        addon.yclients_service_ids = ids_map
        db.commit()
        time.sleep(0.2)


def apply_staff_mapping(db) -> int:
    changed = 0
    print("--- Маппинг мастеров ---")
    for master_id, yc_id in STAFF_MAP.items():
        master = db.get(Master, master_id)
        if master is None:
            print(f"  ! мастера {master_id!r} нет в БД — пропуск")
            continue
        if master.yclients_staff_id == yc_id:
            print(f"  = {master_id:<10} {master.name:<10} уже {yc_id}")
            continue
        print(f"  + {master_id:<10} {master.name:<10} {master.yclients_staff_id} -> {yc_id}")
        master.yclients_staff_id = yc_id
        changed += 1
    if changed:
        db.commit()
    return changed


def build_schedules(db, days: int) -> tuple[list[dict], list[dict]]:
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(days)]
    closures = {row.date_iso for row in db.query(SalonClosure).all()}

    to_set: list[dict] = []
    to_delete: list[dict] = []
    for master_id, yc_id in STAFF_MAP.items():
        master = db.get(Master, master_id)
        if master is None or not master.active:
            continue
        personal_off = {
            row.date_iso for row in db.query(MasterDayOff).filter(MasterDayOff.master_id == master_id).all()
        }
        work_dates, off_dates = [], []
        for day in horizon:
            day_iso = day.isoformat()
            is_off = shift_day_off(master, day) or day_iso in closures or day_iso in personal_off
            (off_dates if is_off else work_dates).append(day_iso)
        slots = [{"from": master.work_start, "to": master.work_end}]
        if work_dates:
            to_set.append({"staff_id": yc_id, "dates": work_dates, "slots": slots})
        if off_dates:
            to_delete.append({"staff_id": yc_id, "dates": off_dates})
        print(f"  {master.name:<10} (yc {yc_id}): {len(work_dates)} смен, {len(off_dates)} выходных, "
              f"{master.work_start}-{master.work_end}, цикл {master.shift_on}/{master.shift_off} от {master.shift_start}")
        print(f"      первые смены: {', '.join(work_dates[:6])}")
    return to_set, to_delete


def repush_missing(db) -> None:
    now = datetime.now()
    pending = (
        db.query(Booking)
        .filter(Booking.status == BookingStatus.confirmed,
                Booking.yclients_record_id.is_(None),
                Booking.starts_at >= now)
        .order_by(Booking.starts_at)
        .all()
    )
    if not pending:
        print("  нет записей к доборке — зеркало совпадает")
        return
    for booking in pending:
        sync.push_booking_to_yclients(db, booking)
        db.refresh(booking)
        state = f"record {booking.yclients_record_id}" if booking.yclients_record_id else "НЕ УШЛА (см. sync_log)"
        print(f"  {booking.id} {booking.starts_at} {booking.owner_name}: {state}")


def show_state(db) -> None:
    print("--- Текущее состояние БД ---")
    for master in db.query(Master).all():
        print(f"  master {master.id:<10} {master.name:<10} yc={master.yclients_staff_id} active={master.active}")

    services = db.query(Service).filter(Service.active).all()
    expected_s = sum(len(s.prices or {}) for s in services)
    mapped_s = sum(len(s.yclients_service_ids or {}) for s in services)
    addons = db.query(Addon).filter(Addon.active).all()
    expected_a = sum(len(a.prices) if a.prices else 1 for a in addons)
    mapped_a = sum(len(a.yclients_service_ids or {}) for a in addons)
    print(f"  позиций услуг: {mapped_s}/{expected_s} · допов: {mapped_a}/{expected_a} сопоставлено с YCLIENTS")
    print("--- Услуги в YCLIENTS ---")
    try:
        for s in sorted(yclients_client.get_services(), key=lambda x: (x.get("category_id") or 0, x.get("title") or "")):
            print(f"  id={s.get('id')} cat={s.get('category_id')} active={s.get('active')} "
                  f"{s.get('title')}  {s.get('price_min')}-{s.get('price_max')}₽")
    except Exception as e:  # noqa: BLE001
        print(f"  ! не удалось получить: {type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skip-map", action="store_true")
    parser.add_argument("--skip-sched", action="store_true")
    parser.add_argument("--push-sched", action="store_true",
                        help="выгрузить наш цикл 4/3 в YCLIENTS; по умолчанию график только читаем")
    parser.add_argument("--sync-catalog", action="store_true")
    parser.add_argument("--cleanup-old-catalog", action="store_true")
    parser.add_argument("--repush", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.check:
            show_state(db)
            return 0

        if args.cleanup_old_catalog:
            cleanup_old_catalog(db)

        if args.sync_catalog:
            sync_catalog(db)

        if not args.skip_map:
            apply_staff_mapping(db)
            print("--- Сотрудники из YCLIENTS ---")
            print(sync.sync_staff_from_yclients(db))

        if args.push_sched:
            print(f"--- Выгружаю наш цикл в YCLIENTS на {args.days} дней ---")
            to_set, to_delete = build_schedules(db, args.days)
            if not to_set:
                print("  нечего выгружать")
            else:
                res = yclients_client.set_staff_schedule(to_set, to_delete)
                print(f"  ответ: success={res.get('success')} meta={res.get('meta')}")
        elif not args.skip_sched and not args.sync_catalog:
            print(f"--- Тяну график из YCLIENTS на {args.days} дней ---")
            print(sync.pull_staff_shifts(db, days=args.days))

        if args.repush:
            print("--- Доборка записей в YCLIENTS ---")
            repush_missing(db)

        print()
        show_state(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
