from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.collector import run_collection
from app.config import settings
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("scheduler")


def _job() -> None:
    try:
        run_collection()
    except Exception:  # noqa: BLE001 — расписание не должно падать целиком
        log.exception("job error (продолжаем по расписанию)")


def main() -> None:
    init_db()
    log.info("worker запущен. Первый сбор сразу, далее каждые %s мин в часы %s (%s)",
             settings.collect_interval_min, settings.collect_hours, settings.tz)

    # стартовый прогон, чтобы данные появились сразу
    _job()

    sched = BlockingScheduler(timezone=settings.tz)
    sched.add_job(
        _job,
        CronTrigger(
            minute=f"*/{settings.collect_interval_min}",
            hour=settings.collect_hours,
            timezone=settings.tz,
        ),
        id="collect",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    sched.start()


if __name__ == "__main__":
    main()
