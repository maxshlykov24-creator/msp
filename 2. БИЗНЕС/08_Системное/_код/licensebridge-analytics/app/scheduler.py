"""Worker: пересборка среза Kommo по расписанию."""
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
            "сбор ок: %s сделок за %s сек",
            state.get("leads_count"),
            state.get("duration_sec"),
        )


def _trigger() -> IntervalTrigger:
    return IntervalTrigger(minutes=settings.collect_interval_min, timezone=settings.tz)


def _first_run_at() -> datetime:
    """На VPS 1 ГБ нельзя стартовать сбор в ту же секунду, что и Docker:
    пик памяти Kommo-выгрузки вместе с подъёмом остальных контейнеров
    валит sshd/Caddy (так легли 31.07). Если срез уже есть — ждём 5 мин."""
    now = datetime.now(ZoneInfo(settings.tz))
    if snapshot_path().exists():
        return now + timedelta(minutes=5)
    return now


def start_background() -> BackgroundScheduler:
    """Сбор внутри процесса API: на VPS с 1 ГБ памяти отдельный worker-контейнер
    себя не окупает, а данных мало (полная перевыгрузка меньше минуты)."""
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
