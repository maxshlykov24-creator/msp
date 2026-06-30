from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot import job_dropout_alert, job_monday_digest, job_sunday_dm_reminder, job_sunday_group_reminder

log = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="Europe/Moscow")
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
    # Каждый понедельник в 10:00 — алерт тем, кто пропустил 2+ недели подряд
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
    sched.start()
    log.info("APScheduler started (Europe/Moscow).")
    return sched
