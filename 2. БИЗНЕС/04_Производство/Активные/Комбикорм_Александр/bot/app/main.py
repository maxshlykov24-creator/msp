"""Точка входа: инициализация БД, матчинга, STT и запуск мессенджера."""
from __future__ import annotations

import asyncio
import logging

from . import db
from .backup import daily_backup_loop
from .config import load_config
from .stt.nexara import NexaraClient
from .text.matcher import Matcher


def setup_logging(log_dir):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "bot.log", encoding="utf-8"),
        ],
    )


async def _amain():
    config = load_config()
    setup_logging(config.log_dir)
    log = logging.getLogger("main")

    if not config.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")

    conn = db.connect(config.db_path)
    db.init_db(conn)

    products = db.active_products(conn)
    if not products:
        log.warning(
            "Каталог пуст. Импортируйте номенклатуру: python -m scripts.build_catalog <csv>"
        )

    matcher = Matcher(db.aliases_for_matching(conn), config.match_threshold)
    nexara = NexaraClient(config.nexara_api_key)

    groq = None
    if config.llm_fallback_enabled:
        from .llm.groq_fallback import GroqFallback

        groq = GroqFallback(config.groq_api_key, config.groq_model)
        if groq.available:
            ok, msg = await groq.ping()
            if ok:
                log.info("Groq LLM fallback: OK (%s)", msg[:40])
            else:
                log.warning("Groq LLM fallback недоступен: %s", msg)
        else:
            log.warning("LLM_FALLBACK_ENABLED=1, но GROQ_API_KEY пустой")

    if config.messenger == "telegram":
        from .adapters.telegram import TelegramBot

        tg = TelegramBot(config, conn, matcher, nexara, groq=groq)
        asyncio.create_task(
            daily_backup_loop(
                tg.bot, config.db_path, config.data_dir / "backups", config.backup_chat_id
            )
        )
        await tg.run()
    else:
        raise SystemExit(f"Мессенджер '{config.messenger}' пока не поддержан (готовим MaxAdapter)")


def main():
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
