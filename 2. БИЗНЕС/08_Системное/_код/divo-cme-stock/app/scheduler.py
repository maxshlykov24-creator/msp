from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.sync import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("scheduler")


def _job() -> None:
    try:
        run_once()
    except Exception:  # noqa: BLE001 — расписание не должно падать
        log.exception("job error")


def main() -> None:
    log.info(
        "divo-cme-stock: первый прогон сразу, далее каждые %s мин (%s)",
        settings.sync_interval_min,
        settings.tz,
    )
    _job()
    sched = BlockingScheduler(timezone=settings.tz)
    sched.add_job(
        _job,
        CronTrigger(
            minute=f"*/{settings.sync_interval_min}",
            timezone=settings.tz,
        ),
        id="cme_stock",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    sched.start()


if __name__ == "__main__":
    main()
