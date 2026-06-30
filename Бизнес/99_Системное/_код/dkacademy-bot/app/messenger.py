"""Единый слой отправки уведомлений клиентам (Telegram или MAX)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app import max_api, telegram_api, ui_text
from app.config import get_settings
from app.models import ContactBinding

log = logging.getLogger(__name__)


def _menu_buttons_max(manager_username: str) -> list[list[dict[str, Any]]]:
    return [
        [
            {"type": "callback", "text": "📦 Мои доставки", "payload": ui_text.CB_MY_DELIVERIES},
            {"type": "link", "text": "💬 Менеджер", "url": ui_text.manager_max_link(manager_username)},
        ]
    ]


def _menu_buttons_tg(manager_username: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "📦 Мои доставки", "callback_data": ui_text.CB_MY_DELIVERIES},
                {"text": "💬 Менеджер", "url": ui_text.manager_link(manager_username)},
            ]
        ]
    }


def notify(
    binding: ContactBinding,
    text: str,
) -> tuple[bool, Optional[str]]:
    """Отправить уведомление клиенту в нужный мессенджер (с кнопками меню)."""
    s = get_settings()
    channel = (binding.channel or "telegram").strip()

    if channel == "max":
        token = (s.max_bot_token or "").strip()
        if not token:
            log.warning("notify: MAX_BOT_TOKEN не задан, пропуск")
            return False, "no_token"
        buttons = _menu_buttons_max(s.manager_telegram_username)
        return max_api.send_message(token, binding.telegram_chat_id, text, buttons=buttons)

    # telegram (default)
    token = (s.telegram_bot_token or "").strip()
    if not token:
        log.warning("notify: TELEGRAM_BOT_TOKEN не задан, пропуск")
        return False, "no_token"
    reply_markup = _menu_buttons_tg(s.manager_telegram_username)
    return telegram_api.send_message_sync(
        token, binding.telegram_chat_id, text, reply_markup=reply_markup
    )
