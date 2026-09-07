"""Push-уведомления в личный Telegram-бот Карины напрямую с сервера.

Один и тот же BOT_TOKEN обслуживает и push (это модуль, вызывается из
main.py/sync.py), и pull-команды (keris-admin-bot/bot.py, long polling).
Так сервер остаётся единственным источником истины и API.

Тексты уведомлений — общие для notify_karina и notify_admins (см. notify_admins.py).
"""
from __future__ import annotations

import logging
from typing import Optional

from .config import settings
from .tg_http import tg_send_message

log = logging.getLogger("keris.notify_karina")

_DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def notify(text: str) -> None:
    if not settings.karina_bot_token or not settings.karina_telegram_ids:
        log.info("Karina bot не настроен — уведомление пропущено: %s", text[:120])
        return
    for chat_id in settings.karina_telegram_ids:
        ok = tg_send_message(settings.karina_bot_token, chat_id, text)
        if not ok:
            log.warning("notify_karina не доставлено chat_id=%s", chat_id)


def fmt_when(dt, bold: bool = False) -> str:
    text = f"{dt.strftime('%d.%m')} ({_DOW[dt.weekday()]}) {dt.strftime('%H:%M')}"
    return f"<b>{text}</b>" if bold else text


def booking_created_text(
    booking,
    service_title: str = "",
    addon_titles: Optional[dict] = None,
    master_name: str = "",
) -> str:
    """Блоки разделены пустой строкой — премиальный, читаемый вид в Telegram."""
    titles = addon_titles or {}
    blocks = [f"🆕 <b>Новая запись {booking.id}</b>"]

    blocks.append(
        f"{booking.owner_name}, {booking.owner_phone}\n{service_title or booking.service_id}"
    )

    addon_lines = []
    if booking.addon_ids:
        addon_lines.append("Допы: " + ", ".join(titles.get(a, a) for a in booking.addon_ids))
    if booking.free_addon_ids:
        addon_lines.append("🎁 Бесплатно: " + ", ".join(titles.get(a, a) for a in booking.free_addon_ids))
    if addon_lines:
        blocks.append("\n".join(addon_lines))

    blocks.append(f"Мастер: {master_name or booking.master_id}\n{fmt_when(booking.starts_at, bold=True)}")

    if booking.subscription_id:
        money = f"по абонементу (списано визитов: {booking.visits_charged})"
        if booking.price:
            money += f", доплата {booking.price} ₽"
        blocks.append(f"💰 {money}")
    else:
        blocks.append(f"💰 {booking.price} ₽")

    if booking.comment:
        blocks.append(f"💬 {booking.comment}")

    return "\n\n".join(blocks)


def booking_cancelled_text(booking) -> str:
    return f"❌ <b>Отмена записи {booking.id}</b>\n{booking.owner_name} · {fmt_when(booking.starts_at)}"


def booking_no_show_text(booking) -> str:
    return (f"🚫 <b>Не пришёл — {booking.id}</b>\n{booking.owner_name} · {fmt_when(booking.starts_at)}"
            "\nСлот в онлайн-записи снова свободен.")


def booking_rescheduled_text(booking) -> str:
    return f"🔁 <b>Перенос записи {booking.id}</b>\n{booking.owner_name} → {fmt_when(booking.starts_at, bold=True)}"
