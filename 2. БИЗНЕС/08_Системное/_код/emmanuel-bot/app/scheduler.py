from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot import job_midnight_digest, job_twenty_reminder
from app.config import get_settings

log = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    settings = get_settings()
    sched = AsyncIOScheduler(timezone="Europe/Moscow")

    if settings.jobs_enabled:
        sched.add_job(
            job_midnight_digest,
            "cron",
            args=[bot],
            hour=0,
            minute=0,
            id="midnight_digest",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        sched.add_job(
            job_twenty_reminder,
            "cron",
            args=[bot],
            hour=20,
            minute=0,
            id="twenty_reminder",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        log.info("APScheduler jobs 00:00/20:00 enabled")
    else:
        log.info("APScheduler 00:00/20:00 not started (JOBS_ENABLED=false)")

    sched.start()
    log.info(
        "APScheduler started (Europe/Moscow) mute=%s jobs_enabled=%s",
        settings.outbound_mute,
        settings.jobs_enabled,
    )
    return sched
