from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot import (
    job_dropout_alert,
    job_midnight_digest,
    job_monday_digest,
    job_sunday_dm_reminder,
    job_sunday_group_reminder,
    job_twenty_reminder,
)
from app.config import get_settings

log = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    settings = get_settings()
    sched = AsyncIOScheduler(timezone="Europe/Moscow")

    # Старые джобы остаются в коде, на MUTE / JOBS_ENABLED=false не стреляют.
    sched.add_job(
        job_sunday_group_reminder,
        "cron",
        args=[bot],
        day_of_week="sun",
        hour=17,
        minute=0,
        id="sunday_group_17",
        replace_existing=True,
    )
    sched.add_job(
        job_sunday_dm_reminder,
        "cron",
        args=[bot],
        day_of_week="sun",
        hour=22,
        minute=0,
        id="sunday_dm_22",
        replace_existing=True,
    )
    sched.add_job(
        job_monday_digest,
        "cron",
        args=[bot],
        day_of_week="mon",
        hour=9,
        minute=0,
        id="monday_digest_9",
        replace_existing=True,
    )
    sched.add_job(
        job_dropout_alert,
        "cron",
        args=[bot],
        day_of_week="mon",
        hour=10,
        minute=0,
        id="monday_dropout_10",
        replace_existing=True,
    )
    if settings.jobs_enabled:
        sched.add_job(
            job_midnight_digest,
            "cron",
            args=[bot],
            hour=0,
            minute=0,
            id="midnight_digest",
            replace_existing=True,
        )
        sched.add_job(
            job_twenty_reminder,
            "cron",
            args=[bot],
            hour=20,
            minute=0,
            id="twenty_reminder",
            replace_existing=True,
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
