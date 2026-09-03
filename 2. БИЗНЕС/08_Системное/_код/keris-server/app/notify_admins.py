"""Push-уведомления в отдельный бот-уведомитель для админов (@kerisclub_notify_bot).

Доп. канал к notify_karina.py: свой токен, свой список chat_id (шире круга,
не только Карина). Тексты те же, что у Карины, без ссылки на журнал YCLIENTS.
"""
from __future__ import annotations

import logging

from .config import settings
from .tg_http import tg_send_message

log = logging.getLogger("keris.notify_admins")


def notify(text: str) -> None:
    if not settings.admin_notify_bot_token or not settings.admin_notify_chat_ids:
        log.info("Admin-notify бот не настроен — уведомление пропущено: %s", text[:120])
        return
    for chat_id in settings.admin_notify_chat_ids:
        ok = tg_send_message(settings.admin_notify_bot_token, chat_id, text)
        if not ok:
            log.warning("notify_admins не доставлено chat_id=%s", chat_id)
