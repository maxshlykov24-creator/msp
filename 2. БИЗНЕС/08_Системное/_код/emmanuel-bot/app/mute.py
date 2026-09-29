"""Исходящая тишина: сводка в группу идёт отдельным вызовом, остальной шум в чат молчит."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import get_settings

log = logging.getLogger(__name__)


def is_admin_user(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id == get_settings().admin_tg_user_id


def coverage_recipient_ids() -> set[int]:
    settings = get_settings()
    ids = {int(settings.admin_tg_user_id)}
    raw = (settings.coverage_dm_user_ids or "").strip()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return ids


def is_outbound_blocked(chat_id: int) -> bool:
    settings = get_settings()
    if not settings.outbound_mute:
        return False
    # Личка открыта: мастер отчёта и напоминание в 20:00. Группа молчит, кроме сводки.
    return int(chat_id) == int(settings.group_chat_id)


async def send_message_live(bot: Bot, chat_id: int, text: str, **kwargs: Any):
    """Сводка в группу. MuteBot.send_message её не глотает."""
    return await Bot.send_message(bot, chat_id, text=text, **kwargs)


class MuteBot(Bot):
    """Обычный send_message в группу при MUTE глотается. Сводка идёт через send_message_live."""

    async def send_message(self, chat_id: int | str, *args: Any, **kwargs: Any):
        cid = int(chat_id)
        if is_outbound_blocked(cid):
            text = kwargs.get("text") or (args[0] if args else "")
            log.info("MUTE skip send_message chat_id=%s text=%s", cid, str(text)[:80])
            return None
        return await super().send_message(chat_id, *args, **kwargs)


class AdminOnlyPrivateMiddleware(BaseMiddleware):
    """Личка открыта всем из группы: иначе напоминание ведёт в немого бота."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        return await handler(event, data)
