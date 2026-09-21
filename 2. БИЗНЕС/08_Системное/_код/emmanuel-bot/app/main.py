"""Точка входа: long polling + APScheduler."""

from __future__ import annotations

import asyncio
import logging
import sys


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    from aiogram.enums import UpdateType

    from app.bot import build_dispatcher
    from app.config import get_settings
    from app.database import init_db
    from app.mute import MuteBot
    from app.scheduler import start_scheduler
    from app.seed_load import load_seed_files

    settings = get_settings()

    lvl = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(lvl)

    await init_db()
    await load_seed_files()
    bot = MuteBot(settings.bot_token)
    me = await bot.get_me()
    log = logging.getLogger(__name__)
    log.info(
        "bot @%s id=%s mute=%s jobs=%s group=%s admin=%s",
        me.username,
        me.id,
        settings.outbound_mute,
        settings.jobs_enabled,
        settings.group_chat_id,
        settings.admin_tg_user_id,
    )
    if settings.outbound_mute:
        log.info("OUTBOUND_MUTE: sends only to admin_tg_user_id, never to group or others")
    dp = build_dispatcher()
    start_scheduler(bot)
    log.info("Bot polling…")
    await dp.start_polling(
        bot,
        allowed_updates=[
            UpdateType.MESSAGE,
            UpdateType.EDITED_MESSAGE,
            UpdateType.CALLBACK_QUERY,
            UpdateType.CHAT_MEMBER,
            UpdateType.MY_CHAT_MEMBER,
        ],
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
