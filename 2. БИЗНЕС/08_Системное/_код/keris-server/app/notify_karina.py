"""Push-уведомления в личный Telegram-бот Карины напрямую с сервера.

Один и тот же BOT_TOKEN обслуживает и push (это модуль, вызывается из
main.py/sync.py), и pull-команды (keris-admin-bot/bot.py, long polling).
Так сервер остаётся единственным источником истины и API.

Тексты уведомлений — общие для notify_karina и notify_admins (см. notify_admins.py).
"""
from __future__ import annotations

import html
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


def _lookups(booking) -> tuple[str, str, dict]:
    """Имена из каталога. В карточку не должны попадать dog_hygiene и svetlana."""
    from sqlalchemy.orm import object_session

    from .models import Addon, Master, Service

    service_name = ""
    master_name = ""
    addon_titles: dict[str, str] = {}
    db = object_session(booking)
    if db is None:
        return service_name, master_name, addon_titles
    service = db.get(Service, booking.service_id) if booking.service_id else None
    master = db.get(Master, booking.master_id) if booking.master_id else None
    if service is not None and service.name:
        service_name = service.name
    if master is not None and master.name:
        master_name = master.name
    for addon_id in list(booking.addon_ids or []) + list(booking.free_addon_ids or []):
        addon = db.get(Addon, addon_id)
        if addon is not None and addon.name:
            addon_titles[addon_id] = addon.name
    return service_name, master_name, addon_titles


def booking_created_text(
    booking,
    service_title: str = "",
    addon_titles: Optional[dict] = None,
    master_name: str = "",
) -> str:
    """Блоки разделены пустой строкой — премиальный, читаемый вид в Telegram."""
    titles = addon_titles or {}
    looked_service, looked_master, looked_addons = _lookups(booking)
    titles = {**looked_addons, **titles}
    service_title = service_title or looked_service or booking.service_id
    master_name = master_name or looked_master or booking.master_id
    blocks = [f"🆕 <b>Новая запись {booking.id}</b>"]

    blocks.append(
        f"{booking.owner_name}, {booking.owner_phone}\n{service_title}"
    )

    addon_lines = []
    if booking.addon_ids:
        addon_lines.append("Допы: " + ", ".join(titles.get(a, a) for a in booking.addon_ids))
    if booking.free_addon_ids:
        addon_lines.append("🎁 Бесплатно: " + ", ".join(titles.get(a, a) for a in booking.free_addon_ids))
    if addon_lines:
        blocks.append("\n".join(addon_lines))

    blocks.append(f"Мастер: {master_name}\n{fmt_when(booking.starts_at, bold=True)}")

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


def _card(booking, title: str) -> str:
    """Полная карточка: после удаления старого сообщения слот не теряется."""
    who = booking.owner_name or "клиент"
    phone = booking.owner_phone or ""
    service_name, master_name, addon_titles = _lookups(booking)
    service = service_name or "услуга"
    master = master_name or "мастер"
    lines = [
        f"<b>{title} {booking.id}</b>",
        "",
        f"{who}, {phone}",
        service,
    ]
    addon_lines = []
    if booking.addon_ids:
        addon_lines.append("Допы: " + ", ".join(addon_titles.get(a, a) for a in booking.addon_ids))
    if booking.free_addon_ids:
        addon_lines.append("Бесплатно: " + ", ".join(addon_titles.get(a, a) for a in booking.free_addon_ids))
    if addon_lines:
        lines.append("\n".join(addon_lines))
    lines.append(f"Мастер: {master}")
    lines.append(fmt_when(booking.starts_at, bold=True))
    if booking.subscription_id:
        money = f"по абонементу (списано визитов: {booking.visits_charged})"
        if booking.price:
            money += f", доплата {booking.price} ₽"
    else:
        money = f"{booking.price} ₽" if booking.price else "сумма не указана"
    lines.append(f"💰 {money}")
    if booking.comment:
        lines.append(f"💬 {booking.comment}")
    return "\n".join(lines)


def review_text(review, booking) -> str:
    """Отзыв клиента: кто, о ком, какой визит, оценка и сам текст."""
    service_name, master_name, _addons = _lookups(booking)
    service = html.escape(service_name or "услуга")
    master = html.escape(master_name or "мастер")
    who = html.escape(getattr(review, "author_name", "") or booking.owner_name or "Клиент")
    phone = html.escape(getattr(review, "owner_phone", "") or booking.owner_phone or "")
    pet = html.escape((booking.pet_name or "").strip())
    body = html.escape((getattr(review, "text", "") or "").strip())
    stars = max(1, min(5, int(getattr(review, "stars", 0) or 0)))
    mark = "★" * stars + "☆" * (5 - stars)
    who_line = f"{who}, {phone}" if phone else who
    service_line = f"{service}, {pet}" if pet else service
    lines = [
        f"★ <b>Новый отзыв {html.escape(str(booking.id))}</b>",
        "",
        who_line,
        f"Мастер: {master}",
        service_line,
        fmt_when(booking.starts_at, bold=True),
        f"Оценка: {mark} {stars} из 5",
        body or "Текста нет, только оценка.",
    ]
    return "\n".join(lines)


def booking_cancelled_text(booking) -> str:
    return _card(booking, "Отмена")


def booking_no_show_text(booking) -> str:
    return _card(booking, "Не пришёл") + "\n\nСлот снова свободен."


def booking_rescheduled_text(booking) -> str:
    return _card(booking, "Перенос")
