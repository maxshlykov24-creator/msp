"""Бэкфилл истории груминга в amoCRM.

Записи с KERIS-1001 живут в нашей БД, а в amoCRM их нет: воронка «Груминг»
появилась позже. Скрипт прогоняет все записи по тому же пути, что и живой поток
(`sync.sync_booking_to_amocrm`), поэтому карточки получаются те же, а не «почти
такие же»: контакт по телефону, питомец-компания, сделка на фактическом этапе,
метрики клиента.

    python3 amocrm_backfill.py --dry-run           отчёт, ничего не пишет
    python3 amocrm_backfill.py --skip-cancelled    живой прогон без отмен (решение владельца
                                                   22.08.2026: 32 отмены из 46 — отладочные)
    python3 amocrm_backfill.py --limit 3           первые 3 записи (проба на живом)

Идемпотентно: запись с заполненным `amocrm_lead_id` пропускается, если не
передан `--force` (тогда существующая сделка обновляется, но не дублируется).
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app import amocrm_client, amocrm_metrics, amocrm_stages, clock, sync
from app.config import settings
from app.db import SessionLocal
from app.models import Booking, BookingStatus


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Бэкфилл записей груминга в amoCRM")
    p.add_argument("--dry-run", action="store_true", help="только отчёт, без записи в amoCRM")
    p.add_argument("--limit", type=int, default=0, help="сколько записей обработать")
    p.add_argument("--force", action="store_true", help="обновлять и те, у которых сделка уже есть")
    p.add_argument("--skip-cancelled", action="store_true",
                   help="без отменённых: в базе много отмен от отладки, в CRM они не нужны")
    return p.parse_args()


def report(db, now, bookings: list[Booking]) -> None:
    """Что будет сделано. Показывается владельцу до записи в живой аккаунт."""
    phones = {b.owner_phone for b in bookings}
    pets = {(b.owner_phone, (b.pet_name or "").strip().lower()) for b in bookings if b.pet_name}
    with_lead = [b for b in bookings if b.amocrm_lead_id]

    print(f"Записей в БД: {len(bookings)}")
    print(f"  из них сделка в amoCRM уже есть: {len(with_lead)}")
    print(f"Уникальных клиентов: {len(phones)}")
    print(f"Уникальных питомцев: {len(pets)}")

    found = 0
    for phone in sorted(phones):
        try:
            contact = amocrm_client.find_contact_by_phone(phone)
        except Exception as e:  # noqa: BLE001
            print(f"  ! поиск контакта {phone}: {e}")
            continue
        if contact:
            found += 1
    print(f"  контакты найдутся в amoCRM: {found}, создадутся новые: {len(phones) - found}")

    by_stage: dict[str, int] = {}
    for b in bookings:
        stage = amocrm_stages.target_stage(b, now)
        by_stage[stage] = by_stage.get(stage, 0) + 1
    print("\nСделки встанут на этапы:")
    for stage, count in sorted(by_stage.items(), key=lambda kv: -kv[1]):
        print(f"  {stage:20} {count}")

    print("\nСтатусы клиентов после пересчёта метрик:")
    by_status: dict[str, int] = {}
    for phone in phones:
        status = str(amocrm_metrics.client_metrics(db, phone, now).get("Груминг: статус клиента"))
        by_status[status] = by_status.get(status, 0) + 1
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:20} {count}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    if not settings.amocrm_ready:
        print("AMOCRM_LONG_LIVED_TOKEN не задан — нечего делать")
        return 1

    db = SessionLocal()
    try:
        account = amocrm_client.check_account()
        print(f"amoCRM: {account.get('name')} ({account.get('id')}), {settings.amocrm_base_url}")

        now = clock.now().replace(tzinfo=None)
        bookings = list(db.execute(select(Booking).order_by(Booking.starts_at)).scalars().all())
        if args.skip_cancelled:
            before = len(bookings)
            bookings = [b for b in bookings if b.status != BookingStatus.cancelled]
            print(f"--skip-cancelled: отменённых пропущено {before - len(bookings)}")
        if args.limit:
            bookings = bookings[: args.limit]

        report(db, now, bookings)
        if args.dry_run:
            print("\n--dry-run: в amoCRM ничего не записано")
            return 0

        print("\nЖивой прогон:")
        ok = skipped = failed = 0
        for booking in bookings:
            if booking.amocrm_lead_id and not args.force:
                skipped += 1
                continue
            if sync.sync_booking_to_amocrm(db, booking, note="Перенос истории из базы Keris"):
                ok += 1
                print(f"  + {booking.id} → сделка {booking.amocrm_lead_id}, этап «{booking.amocrm_stage}»")
            else:
                failed += 1
                print(f"  ! {booking.id} — не удалось, подробности в sync_log")
        print(f"\nГотово: перенесено {ok}, пропущено {skipped}, с ошибкой {failed}")
        return 0 if failed == 0 else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
