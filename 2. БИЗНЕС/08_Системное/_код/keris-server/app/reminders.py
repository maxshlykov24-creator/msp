"""Напоминания клиенту за 24 ч и 3 ч до визита (часы — из booking_rules.reminder_hours).

Шлются в Telegram и/или MAX по связке телефон → chat_id / user_id, а с 2026-08-24 —
всегда ещё и SMS через TargetSMS (см. targetsms_client), независимо от того, привязан
ли клиент к боту: SMS — не замена Telegram/MAX, а параллельный канал для всех.
SMS best-effort и молчит, пока TARGETSMS_ENABLED не включён (settings.targetsms_ready).
Отправленные отметки лежат в bookings.reminders_sent, поэтому перезапуск сервиса
не приводит к повторной рассылке.

Тем же циклом идут два сообщения, которые иначе делает администратор руками:
поздравление с днём рождения питомца и возврат «давно не был» (REACTIVATION_DAYS,
по умолчанию 60). Защита от повтора — таблица client_outreach: ДР уникален по
питомцу и году, возврат — по дате последнего визита.

К каждому напоминанию в Telegram/MAX, если запись ещё не подтверждена, добавляется
кнопка «Подтверждаю приход» (keris-bot / keris-max-bot → POST /api/bookings/{id}/confirm).
После подтверждения — best-effort attendance=2 в YCLIENTS, кнопка больше не шлётся
ни на 24ч, ни на 3ч (см. is_confirmed). В SMS кнопок нет — просто короткий текст.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from . import clock, max_bind, max_http, sync, targetsms_client, telegram_bind, yclients_client
from .config import settings
from .models import Booking, BookingStatus, ClientOutreach, Master, Service
from .targetsms_client import TargetSMSError
from .seed import booking_rules
from .tg_http import tg_send_message
from .yclients_client import YClientsError

log = logging.getLogger("keris.reminders")

CHECK_INTERVAL_SEC = 300
# Окно поиска: напоминание считается «пора», если до визита осталось
# от (H - 0.5 ч) до H. Шире окна проверки, чтобы ничего не проскочило.
WINDOW_MIN = 30


def reminder_text(booking: Booking, service_name: str, hours: int,
                  ask_confirm: bool = False) -> str:
    """Текст напоминания (HTML). Блоки: заголовок → детали → примечание → (опц.) CTA к кнопке."""
    when = booking.starts_at.strftime("%d.%m в %H:%M")
    pet = booking.pet_name or "ваш питомец"
    if hours >= 24:
        head = f"🐾 <b>Напоминание о визите</b>\n\nЗавтра, <b>{when}</b>\nмы ждём вас в Keris Club."
        free_until = booking.starts_at - timedelta(
            hours=booking_rules().get("free_reschedule_hours", 24)
        )
        note = (f"Перенести или отменить без последствий можно до "
                f"<b>{free_until:%d.%m %H:%M}</b>.")
    else:
        head = f"🐾 <b>Сегодня визит</b>\n\n<b>{when}</b> — ждём вас в Keris Club."
        note = "Опоздание больше 15 минут может сократить программу ухода."

    details = [f"Питомец: <b>{pet}</b>", f"Услуга: {service_name}"]
    if booking.free_addon_ids:
        details.append("🎁 В визит входят подарки от салона")

    parts = [head, "\n".join(details), note]
    if ask_confirm:
        parts.append("Подтвердите приход кнопкой ниже 👇")
    return "\n\n".join(parts)


def booking_created_client_text(booking: Booking, service_name: str) -> str:
    """Клиенту в Telegram сразу после успешной записи — короткое «спасибо».
    Отдельно от reminder_text (это не напоминание, а сразу после оформления),
    но тот же тон и вёрстка: жирный заголовок, блоки через пустую строку."""
    when = booking.starts_at.strftime("%d.%m в %H:%M")
    pet = booking.pet_name or "ваш питомец"
    head = f"🐾 <b>Спасибо, что записались!</b>\n\nЖдём вас <b>{when}</b> в Keris Club."

    details = [f"Питомец: <b>{pet}</b>", f"Услуга: {service_name}"]
    if booking.free_addon_ids:
        details.append("🎁 В визит входят подарки от салона")

    if booking.subscription_id:
        money = f"По абонементу (списано визитов: {booking.visits_charged:g})" if booking.visits_charged else "По абонементу"
        if booking.price:
            money += f", доплата {booking.price} ₽"
    else:
        money = f"{booking.price} ₽"

    footer = (f"Номер записи: {booking.id}\n"
              "Напомним о визите за 24 часа и за 3 часа до него.")

    return "\n\n".join([head, "\n".join(details), f"💰 {money}", footer])


def _master_name(db: Session, booking: Booking) -> str:
    master = db.get(Master, booking.master_id)
    return master.name if master else ""


# Тексты ниже — рабочие тексты для шаблонов TargetSMS, зарегистрированных в ЛК с
# переменными %w (имя мастера) / %d (дата, время). Менять формулировки здесь
# без переутверждения того же шаблона в ЛК targetsms не стоит — несовпадающий с
# шаблоном текст уходит на ручную модерацию (задержка до получаса).
# Префикс с названием клуба остаётся в тексте, хотя имя отправителя KerisClub его
# дублирует: именно в таком виде сообщения дошли на проверке 24.08.2026.

SMS_PREFIX = "Keris Club. "


def sms_booking_created_text(booking: Booking, master_name: str) -> str:
    when = booking.starts_at.strftime("%d.%m %H:%M")
    return f"{SMS_PREFIX}Спасибо за запись. Мастер {master_name}, {when}."


def sms_reminder_text(booking: Booking, master_name: str, hours: int) -> str:
    time_str = booking.starts_at.strftime("%H:%M")
    if hours >= 24:
        return f"{SMS_PREFIX}Завтра ждём вас. Мастер {master_name}, {time_str}."
    return f"{SMS_PREFIX}Уже скоро ждём вас. Мастер {master_name}, {time_str}."


def send_sms(booking: Booking, text: str) -> bool:
    """SMS клиенту через TargetSMS. Best-effort: пока канал не включён
    (settings.targetsms_ready) — тихо возвращает False, ошибки API только
    логируются и не должны ронять рассылку в Telegram/MAX."""
    if not settings.targetsms_ready:
        return False
    try:
        result = targetsms_client.send_sms(booking.owner_phone, text)
    except TargetSMSError:
        log.warning("TargetSMS: ошибка отправки для %s", booking.id, exc_info=True)
        return False
    log.info(
        "TargetSMS отправлено: booking=%s id_sms=%s parts=%s",
        booking.id, result.get("id_sms"), result.get("parts"),
    )
    return True


def confirm_keyboard(booking: Booking) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Подтверждаю", "callback_data": f"rsvp:{booking.id}"}]]}


def confirm_buttons_max(booking: Booking) -> list[list[dict]]:
    """Кнопка подтверждения в формате MAX Bot API (не Telegram web_app / open_app)."""
    return [[{"type": "callback", "text": "✅ Подтверждаю", "payload": f"rsvp:{booking.id}"}]]


def is_confirmed(db: Session, booking: Booking) -> bool:
    """Уже подтверждено — локально (клиент нажал кнопку) или в YCLIENTS (attendance=2,
    например админ подтвердил вручную в журнале). Второй случай кэшируем в БД,
    чтобы не дёргать YCLIENTS повторно на каждый прогон."""
    if booking.client_confirmed_at:
        return True
    if not booking.yclients_record_id:
        return False
    try:
        data = yclients_client.get_record(booking.yclients_record_id)
        attendance = (data.get("data") or {}).get("attendance")
    except YClientsError:
        log.info("не удалось проверить attendance в YCLIENTS для %s", booking.id, exc_info=True)
        return False
    if attendance == 2:
        booking.client_confirmed_at = clock.now()
        db.commit()
        return True
    return False


def send_to_client(chat_id: int, text: str, keyboard: dict | None = None) -> bool:
    if not settings.client_bot_token:
        log.info("CLIENT_BOT_TOKEN не задан — напоминание не отправлено (chat_id=%s)", chat_id)
        return False
    return tg_send_message(
        settings.client_bot_token,
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


def send_to_max(user_id: int, text: str, buttons: list | None = None) -> bool:
    return max_http.send_message(user_id, text, buttons)


def _thanks_list(booking: Booking) -> list[str]:
    return list(booking.thanks_channels or [])


def _add_thanks_channel(booking: Booking, channel: str) -> None:
    channels = _thanks_list(booking)
    if channel not in channels:
        channels.append(channel)
        booking.thanks_channels = channels


def _bound_thanks_channels(db: Session, booking: Booking) -> list[str]:
    """Какие клиентские каналы сейчас есть, куда «спасибо» вообще можно послать."""
    bound: list[str] = []
    if booking.telegram_chat_id or telegram_bind.chat_id_for_phone(db, booking.owner_phone):
        bound.append("telegram")
    if booking.max_user_id or max_bind.user_id_for_phone(db, booking.owner_phone):
        bound.append("max")
    return bound


def refresh_thanks_pending(db: Session, booking: Booking) -> None:
    sent = set(_thanks_list(booking))
    booking.thanks_pending = any(ch not in sent for ch in _bound_thanks_channels(db, booking))


def notify_client(
    db: Session,
    booking: Booking,
    text: str,
    *,
    ask_confirm: bool = False,
    only: str | None = None,
    record_thanks: bool = False,
) -> bool:
    """Шлём в Telegram и MAX, если клиент привязал канал по телефону.

    only='telegram' / 'max' — один канал (досылка после bind, без дубля во второй).
    only=None — оба, если оба авторизованы.
    record_thanks — писать удачные каналы в thanks_channels (только «спасибо»,
    не напоминания 24ч/3ч).
    """
    want_tg = only in (None, "telegram")
    want_max = only in (None, "max")
    tg = (
        booking.telegram_chat_id or telegram_bind.chat_id_for_phone(db, booking.owner_phone)
        if want_tg else None
    )
    mx = (
        booking.max_user_id or max_bind.user_id_for_phone(db, booking.owner_phone)
        if want_max else None
    )
    ok = False
    if tg:
        kb = confirm_keyboard(booking) if ask_confirm else None
        if send_to_client(tg, text, kb):
            ok = True
            booking.telegram_chat_id = int(tg)
            if record_thanks:
                _add_thanks_channel(booking, "telegram")
    if mx:
        buttons = confirm_buttons_max(booking) if ask_confirm else None
        if send_to_max(int(mx), text, buttons):
            ok = True
            booking.max_user_id = int(mx)
            if record_thanks:
                _add_thanks_channel(booking, "max")
    if record_thanks:
        refresh_thanks_pending(db, booking)
    return ok


def send_booking_created(booking: Booking, service_name: str, *, only: str | None = None) -> bool:
    """«Спасибо, что записались» сразу после оформления. Telegram/MAX — если привязаны;
    SMS — всегда (см. модуль). `only` используется только для досылки в канал, который
    клиент привязал только что (после первого запроса) — SMS в этом случае не дублируем,
    её уже отправили при первой попытке."""
    text = booking_created_client_text(booking, service_name)
    db = object_session(booking)
    if db is not None:
        ok = notify_client(db, booking, text, ask_confirm=False, only=only, record_thanks=True)
        if only is None:
            sms_text = sms_booking_created_text(booking, _master_name(db, booking))
            ok = send_sms(booking, sms_text) or ok
        db.commit()
        return ok
    ok = False
    if only in (None, "telegram") and booking.telegram_chat_id:
        if send_to_client(booking.telegram_chat_id, text):
            ok = True
            _add_thanks_channel(booking, "telegram")
    if only in (None, "max") and booking.max_user_id:
        if send_to_max(booking.max_user_id, text):
            ok = True
            _add_thanks_channel(booking, "max")
    return ok


def send_thanks_for_attached(db: Session, booking_ids: list[str], *, only: str) -> None:
    """Досылка «спасибо» в канал, который клиент только что привязал."""
    if not booking_ids:
        return
    from .models import Service

    for bid in booking_ids:
        booking = db.get(Booking, bid)
        if booking is None:
            continue
        service = db.get(Service, booking.service_id)
        name = service.name if service is not None else (booking.service_id or "визит")
        send_booking_created(booking, name, only=only)


def retry_pending_thanks(db: Session, now: datetime) -> int:
    """Догоняет «спасибо», которое не ушло из-за таймаута Telegram. SMS не трогает."""
    rows = db.execute(
        select(Booking).where(
            Booking.thanks_pending.is_(True),
            Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
            Booking.starts_at > now,
        )
    ).scalars().all()
    sent_count = 0
    for booking in rows:
        service = db.get(Service, booking.service_id)
        name = service.name if service is not None else (booking.service_id or "визит")
        text = booking_created_client_text(booking, name)
        already = set(_thanks_list(booking))
        for channel in _bound_thanks_channels(db, booking):
            if channel in already:
                continue
            if notify_client(db, booking, text, only=channel, record_thanks=True):
                sent_count += 1
        refresh_thanks_pending(db, booking)
    if sent_count or rows:
        db.commit()
        if sent_count:
            log.info("дослано «спасибо»: %d", sent_count)
    return sent_count


def due_reminders(db: Session, now: datetime) -> list[tuple[Booking, int]]:
    hours_list = sorted(booking_rules().get("reminder_hours", [24, 3]), reverse=True)
    if not hours_list:
        return []
    horizon = now + timedelta(hours=hours_list[0])
    rows = db.execute(
        select(Booking).where(
            # Только живые записи: отменённые и помеченные «Не пришел» (в том числе
            # заранее, как отмена в журнале YCLIENTS) напоминаний не получают.
            Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
            Booking.starts_at > now,
            Booking.starts_at <= horizon,
            # Раньше отбирали только привязанных к Telegram/MAX — с подключением SMS
            # (см. targetsms_client) напоминание теперь актуально для всех живых записей,
            # даже без бота: run_once сам решит, что реально удастся отправить.
        )
    ).scalars().all()

    out: list[tuple[Booking, int]] = []
    for b in rows:
        sent = set(b.reminders_sent or [])
        left_min = (b.starts_at - now).total_seconds() / 60
        for h in hours_list:
            if h in sent:
                continue
            if h * 60 - WINDOW_MIN <= left_min <= h * 60:
                out.append((b, h))
                break
    return out


def confirm_booking(db: Session, booking: Booking) -> None:
    """Клиент нажал «Подтверждаю» в напоминании: фиксируем локально и best-effort
    пушим attendance=2 в YCLIENTS (не роняем запрос, если YCLIENTS недоступен —
    статус всё равно останется подтверждён у нас)."""
    booking.client_confirmed_at = clock.now()
    db.commit()
    sync.push_confirm_to_yclients(db, booking)


# ---------------------------------------------------------------------------
# То, что администратор обещает делать выгрузками руками: ДР питомца и возврат
# «давно не был». Здесь это идёт само, по тем же каналам и без напоминалок.
# ---------------------------------------------------------------------------

# Днём, а не ночью: сообщение должно приходить в рабочие часы салона.
OUTREACH_HOURS = range(10, 21)


def parse_pet_birth_date(raw: str) -> tuple[int, int] | None:
    """(день, месяц) из строки. Формат нестрогий: наша форма даёт ГГГГ-ММ-ДД,
    доп. поле YCLIENTS — часто ДД.ММ.ГГГГ. Год нам не нужен."""
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.day, parsed.month
    return None


def birthday_text(pet_name: str) -> str:
    pet = pet_name or "ваш питомец"
    return "\n\n".join([
        f"🎂 <b>С днём рождения, {pet}!</b>",
        "Поздравляем вас и желаем питомцу здоровья и лёгкого настроения.",
        "В честь праздника ждём вас на уход — напишите нам, подберём удобное время.",
    ])


def reactivation_text(pet_name: str, days: int) -> str:
    pet = pet_name or "ваш питомец"
    return "\n\n".join([
        f"🐾 <b>Давно не виделись</b>",
        f"С последнего визита {pet} прошло больше {days} дней. Шерсть за это время"
        " успевает сваляться, а колтуны потом снимаются только машинкой.",
        "Напишите нам — подберём время, пока есть удобные слоты.",
    ])


def notify_phone(db: Session, phone: str, text: str, sms_text: str = "") -> list[str]:
    """Сообщение клиенту по телефону, без привязки к записи: Telegram, MAX, SMS."""
    channels: list[str] = []
    chat_id = telegram_bind.chat_id_for_phone(db, phone)
    if chat_id and send_to_client(int(chat_id), text):
        channels.append("telegram")
    user_id = max_bind.user_id_for_phone(db, phone)
    if user_id and send_to_max(int(user_id), text):
        channels.append("max")
    # SMS только если канал включён: текст вне зарегистрированных шаблонов уходит
    # на ручную модерацию, поэтому это резерв, а не основной путь.
    if not channels and sms_text and settings.targetsms_ready:
        try:
            targetsms_client.send_sms(phone, sms_text)
            channels.append("sms")
        except TargetSMSError:
            log.warning("TargetSMS: не отправили сообщение на %s", phone, exc_info=True)
    return channels


def _already_sent(db: Session, phone: str, kind: str, tag: str) -> bool:
    return db.execute(
        select(ClientOutreach).where(
            ClientOutreach.phone == phone,
            ClientOutreach.kind == kind,
            ClientOutreach.tag == tag,
        )
    ).scalars().first() is not None


def _mark_sent(db: Session, phone: str, kind: str, tag: str, channels: list[str]) -> None:
    db.add(ClientOutreach(
        phone=phone, kind=kind, tag=tag,
        channels=",".join(channels), sent_at=clock.now(),
    ))
    db.commit()


def _client_pets(db: Session) -> list[Booking]:
    """Последняя запись по каждому питомцу: из неё берём кличку и дату рождения."""
    rows = db.execute(
        select(Booking).where(Booking.owner_phone != "").order_by(Booking.starts_at)
    ).scalars().all()
    latest: dict[tuple[str, str], Booking] = {}
    for b in rows:
        latest[(b.owner_phone, (b.pet_name or "").strip().lower())] = b
    return list(latest.values())


def send_birthday_greetings(db: Session, now: datetime) -> int:
    if now.hour not in OUTREACH_HOURS:
        return 0
    sent = 0
    for booking in _client_pets(db):
        day_month = parse_pet_birth_date(booking.pet_birth_date)
        if day_month != (now.day, now.month):
            continue
        pet = (booking.pet_name or "").strip()
        tag = f"{pet.lower()}:{now.year}"
        if _already_sent(db, booking.owner_phone, "birthday", tag):
            continue
        channels = notify_phone(
            db, booking.owner_phone, birthday_text(pet),
            sms_text=f"С днем рождения, {pet or 'ваш питомец'}! Keris Club.",
        )
        if channels:
            _mark_sent(db, booking.owner_phone, "birthday", tag, channels)
            sent += 1
            log.info("ДР питомца: поздравили %s (%s) → %s", pet, booking.owner_phone, channels)
    return sent


def send_reactivations(db: Session, now: datetime) -> int:
    """Клиент без визита дольше REACTIVATION_DAYS — одно сообщение на затишье."""
    if not settings.reactivation_enabled or now.hour not in OUTREACH_HOURS:
        return 0
    days = settings.reactivation_days
    if days <= 0:
        return 0
    rows = db.execute(
        select(Booking).where(
            Booking.status.notin_([BookingStatus.cancelled, BookingStatus.no_show]),
        ).order_by(Booking.starts_at)
    ).scalars().all()

    last_visit: dict[str, Booking] = {}
    has_future: set[str] = set()
    for b in rows:
        if b.starts_at > now:
            has_future.add(b.owner_phone)
        else:
            last_visit[b.owner_phone] = b  # порядок по времени, остаётся последний

    sent = 0
    for phone, booking in last_visit.items():
        if phone in has_future:
            continue
        if (now - booking.starts_at).days < days:
            continue
        # Тег — дата последнего визита: пришёл и снова пропал, сообщение придёт заново.
        tag = booking.starts_at.date().isoformat()
        if _already_sent(db, phone, "reactivation", tag):
            continue
        pet = (booking.pet_name or "").strip()
        channels = notify_phone(
            db, phone, reactivation_text(pet, days),
            sms_text="Давно не виделись. Ждем вас в Keris Club.",
        )
        if channels:
            _mark_sent(db, phone, "reactivation", tag, channels)
            sent += 1
            log.info("возврат: написали %s (последний визит %s) → %s", phone, tag, channels)
    return sent


def run_once(db: Session, now: datetime = None) -> int:
    now = now or clock.now()
    sent_count = 0
    try:
        sent_count += retry_pending_thanks(db, now)
    except Exception:  # noqa: BLE001 — досылка «спасибо» не должна ронять 24ч/3ч
        log.warning("досылка «спасибо»: ошибка прохода", exc_info=True)
    for booking, hours in due_reminders(db, now):
        service = db.get(Service, booking.service_id)
        ask = not is_confirmed(db, booking)
        text = reminder_text(
            booking, service.name if service else booking.service_id, hours, ask_confirm=ask,
        )
        ok = notify_client(db, booking, text, ask_confirm=ask)
        sms_text = sms_reminder_text(booking, _master_name(db, booking), hours)
        ok = send_sms(booking, sms_text) or ok
        if ok:
            booking.reminders_sent = sorted(set(booking.reminders_sent or []) | {hours})
            sent_count += 1
    if sent_count:
        db.commit()
        log.info("напоминаний отправлено: %d", sent_count)
    # Тем же проходом: ДР питомца и возврат. Защита от повтора — в client_outreach,
    # поэтому пятиминутный цикл ничего не дублирует.
    try:
        sent_count += send_birthday_greetings(db, now)
        sent_count += send_reactivations(db, now)
    except Exception:  # noqa: BLE001 — рассылка напоминаний важнее этих двух
        log.warning("ДР/возврат: ошибка прохода", exc_info=True)
    return sent_count
