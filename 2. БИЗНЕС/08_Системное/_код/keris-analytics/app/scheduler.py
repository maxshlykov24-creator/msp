"""Worker: пересборка среза по расписанию."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from app.collector import run_collection, snapshot_path
from app.config import settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("scheduler")


def _job() -> None:
    log.info("старт сбора")
    state = run_collection()
    if state.get("last_error"):
        log.error("сбор с ошибкой: %s", state["last_error"])
    else:
        log.info(
            "сбор ок: %s сделок, %s записей за %s сек",
            state.get("leads_count"),
            state.get("bookings_count"),
            state.get("duration_sec"),
        )


def _trigger() -> IntervalTrigger:
    return IntervalTrigger(minutes=settings.collect_interval_min, timezone=settings.tz)


def _first_run_at() -> datetime:
    now = datetime.now(ZoneInfo(settings.tz))
    if snapshot_path().exists() and not settings.collect_on_start:
        return now + timedelta(minutes=5)
    if snapshot_path().exists():
        return now + timedelta(seconds=8)
    return now


def start_background() -> BackgroundScheduler:
    first = _first_run_at()
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(
        _job,
        _trigger(),
        id="collect",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(60, settings.collect_interval_min * 60),
        next_run_time=first,
    )
    sched.start()
    log.info(
        "расписание: каждые %s мин (%s), первый прогон %s",
        settings.collect_interval_min,
        settings.tz,
        first.isoformat(timespec="seconds"),
    )
    return sched


def main() -> None:
    first = _first_run_at()
    sched = BlockingScheduler(timezone=settings.tz)
    sched.add_job(
        _job,
        _trigger(),
        id="collect",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(60, settings.collect_interval_min * 60),
        next_run_time=first,
    )
    log.info(
        "расписание: каждые %s мин (%s), первый прогон %s",
        settings.collect_interval_min,
        settings.tz,
        first.isoformat(timespec="seconds"),
    )
    sched.start()


if __name__ == "__main__":
    main()
