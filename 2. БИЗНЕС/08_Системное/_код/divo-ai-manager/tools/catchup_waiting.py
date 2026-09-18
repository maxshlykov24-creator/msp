"""Ответить чатам, которые ждут после сбоя OpenRouter.

  PYTHONPATH=. LLM_PROXY=socks5://127.0.0.1:1080 python tools/catchup_waiting.py
"""
from __future__ import annotations

import asyncio
import logging

from bot.autoru import Autoru
from bot.autoru_loop import AutoruChannel
from bot.avito import Avito
from bot.avito_loop import AvitoChannel
from bot.config import settings
from bot.main import CHANNELS, catchup_waiting
from bot.tg import Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main() -> None:
    tg = Telegram(settings.telegram_token)
    avito = Avito()
    autoru = Autoru() if settings.autoru_enabled else None
    CHANNELS["tg"] = tg
    CHANNELS["avito"] = AvitoChannel(avito, tg)
    if autoru is not None:
        CHANNELS["autoru"] = AutoruChannel(autoru, tg)
    try:
        sent = await catchup_waiting()
        print("ответил %d" % sent)
    finally:
        await tg.close()
        await avito.close()
        if autoru is not None:
            await autoru.close()


if __name__ == "__main__":
    asyncio.run(main())
