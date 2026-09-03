"""Entry point: long polling."""

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
    from app.config import get_settings
    from app.database import init_db
    from app.bot import build_dispatcher
    from aiogram import Bot

    settings = get_settings()

    lvl = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(lvl)

    await init_db()

    bot = Bot(settings.bot_token)
    dp = build_dispatcher()

    logging.getLogger(__name__).info("Bot polling…")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
