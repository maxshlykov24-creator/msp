"""Исходящая тишина: в группу и чужие лички не пишем."""

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
    return int(chat_id) not in coverage_recipient_ids()


class MuteBot(Bot):
    """send_message в группу и чужие лички при MUTE глотается, в лог пишется skip."""

    async def send_message(self, chat_id: int | str, *args: Any, **kwargs: Any):
        cid = int(chat_id)
        if is_outbound_blocked(cid):
            text = kwargs.get("text") or (args[0] if args else "")
            log.info("MUTE skip send_message chat_id=%s text=%s", cid, str(text)[:80])
            return None
        return await super().send_message(chat_id, *args, **kwargs)


class AdminOnlyPrivateMiddleware(BaseMiddleware):
    """При MUTE личка братьев игнорируется. Группа читается. Твоя личка работает."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = get_settings()
        if not settings.outbound_mute:
            return await handler(event, data)

        chat = None
        user = None
        if isinstance(event, Message):
            chat = event.chat
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            chat = event.message.chat if event.message else None

        if chat is not None and getattr(chat, "type", None) == "private":
            uid = user.id if user else None
            if not is_admin_user(uid):
                log.info("MUTE ignore private uid=%s", uid)
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return None
        return await handler(event, data)
