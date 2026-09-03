"""Ежедневный бэкап БД: локальная копия + опционально в Telegram-чат владельца."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def make_local_backup(db_path: Path, backups_dir: Path, keep: int = 14) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = backups_dir / f"kombikorm-{stamp}.db"
    shutil.copy2(db_path, dest)
    # чистим старые
    files = sorted(backups_dir.glob("kombikorm-*.db"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
    return dest


async def daily_backup_loop(bot, db_path: Path, backups_dir: Path, chat_id: str, interval_sec: int = 86400):
    from aiogram.types import FSInputFile

    while True:
        await asyncio.sleep(interval_sec)
        try:
            dest = make_local_backup(db_path, backups_dir)
            if chat_id:
                await bot.send_document(int(chat_id), FSInputFile(dest), caption="Бэкап БД комбикорма")
            log.info("Бэкап создан: %s", dest)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка бэкапа")
