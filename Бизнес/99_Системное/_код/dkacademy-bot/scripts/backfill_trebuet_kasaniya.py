#!/usr/bin/env python3
"""Бэкфилл «Требует касания» за прошлый период.

Создаёт сделки в «Повторные продажи → Требует касания» тем клиентам, у кого
успех был >= REPEAT_TOUCH_DEAL_DAYS дней назад (по умолчанию 40), нет открытой
сделки и ещё нет сделки на этапе «Требует касания». Сообщения НЕ шлёт.

Идемпотентно (дедуп через ProcessedEvent). По умолчанию DRY_RUN=true.

Запуск внутри контейнера api:

  DRY_RUN=true LOOKBACK_DAYS=70 docker compose -f docker-compose.yml exec -T api \
    python3 -m scripts.backfill_trebuet_kasaniya

  # боевой:
  DRY_RUN=false LOOKBACK_DAYS=70 docker compose -f docker-compose.yml exec -T api \
    python3 -m scripts.backfill_trebuet_kasaniya
"""
from __future__ import annotations

import logging
import os

from app.database import get_session_factory, init_db
from app.repeat_worker import process_repeat_touch


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    dry = os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")
    lookback = int(os.environ.get("LOOKBACK_DAYS", "70"))

    init_db()
    SessLocal = get_session_factory()
    db = SessLocal()
    try:
        stats = process_repeat_touch(
            db,
            send_messages=False,
            create_deals=True,
            lookback_days=lookback,
            dry_run=dry,
        )
        print(f"\nИтог бэкфилла (DRY_RUN={dry}, окно {lookback}д): {stats}")
        if dry:
            print("Это был сухой прогон. Для боевого запуска: DRY_RUN=false")
    finally:
        db.close()


if __name__ == "__main__":
    main()
