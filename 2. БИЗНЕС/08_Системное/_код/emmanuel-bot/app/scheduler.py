from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot import job_monday_coverage_evening, job_monday_coverage_morning
from app.config import get_settings

log = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    settings = get_settings()
    sched = AsyncIOScheduler(timezone="Europe/Moscow")

    if settings.jobs_enabled:
        sched.add_job(
            job_monday_coverage_morning,
            "cron",
            args=[bot],
            day_of_week="mon",
            hour=9,
            minute=0,
            id="monday_coverage_morning",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        sched.add_job(
            job_monday_coverage_evening,
            "cron",
            args=[bot],
            day_of_week="mon",
            hour=20,
            minute=0,
            id="monday_coverage_evening",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        log.info("APScheduler: пн 09:00 личка, пн 20:00 личка и сводка в группу")
    else:
        log.info("APScheduler 00:00/20:00 not started (JOBS_ENABLED=false)")

    sched.start()
    log.info(
        "APScheduler started (Europe/Moscow) mute=%s jobs_enabled=%s",
        settings.outbound_mute,
        settings.jobs_enabled,
    )
    return sched
