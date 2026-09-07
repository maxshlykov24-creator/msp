"""Код входа в кабинет «Мой Keris» (кнопка «Я уже клиент Keris Club»).

Каналы доставки: Telegram → MAX. SMS-канал (TargetSMS) с 2026-08-24 включён
только для записи и напоминаний: код входа стоит дороже сообщения о визите и
нужен реже, а бот бесплатен и заодно даёт клиенту кабинет с историей питомца.

Бот не привязан ни в одном мессенджере — это не ошибка: клиент получает ссылки
на боты (`need_bind`), делится там номером, и `deliver_pending(...)` из
telegram_bind/max_bind сразу присылает код в чат. Так путь без бота — три
нажатия, без ожидания минуты на повторную отправку.

В базе лежит только хеш кода, поэтому «досылка» — это всегда новый код, а не
повторная отправка старого. Живёт код TTL_SEC (10 минут) и от сессии браузера не
зависит: страница может закрыться, при возврате код с прошлой попытки подойдёт.

`sms_otp_text` оставлен под шаблон `Код для входа в Keris Club: %d`, согласованный
в ЛК TargetSMS: если решим включить SMS-канал для входа, текст уже готов.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, max_bind, max_http, telegram_bind
from .booking_logic import is_canonical_phone, normalize_phone
from .config import settings
from .models import SmsOtp
from .tg_http import tg_send_message

log = logging.getLogger("keris.sms_otp")

CODE_LEN = 4
TTL_SEC = 600
RESEND_SEC = 60
MAX_ATTEMPTS = 5


class OtpError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def sms_otp_text(code: str) -> str:
    return f"Код для входа в Keris Club: {code}"


def bot_otp_text(code: str) -> str:
    return (
        "🔐 <b>Код для входа в «Мой Keris»</b>\n\n"
        f"<b>{code}</b>\n\n"
        "Введите его на странице входа. Код действует 10 минут."
    )


def bot_links() -> dict[str, str]:
    return {"telegram": settings.telegram_bot_url, "max": settings.max_bot_url}


def _secret() -> str:
    return settings.otp_secret or settings.admin_api_key or "keris-otp-dev"


def _hash(phone: str, code: str) -> str:
    return hmac.new(_secret().encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _active(db: Session, phone: str) -> SmsOtp | None:
    now = clock.now()
    return db.execute(
        select(SmsOtp)
        .where(
            SmsOtp.phone == phone,
            SmsOtp.consumed_at.is_(None),
            SmsOtp.expires_at > now,
        )
        .order_by(SmsOtp.created_at.desc())
    ).scalars().first()


def _deliver(db: Session, phone: str, code: str) -> str:
    """Куда реально ушёл код: telegram / max / "" (некуда, нужен bind)."""
    chat_id = telegram_bind.chat_id_for_phone(db, phone)
    if chat_id and settings.client_bot_token:
        if tg_send_message(settings.client_bot_token, chat_id, bot_otp_text(code)):
            return "telegram"

    user_id = max_bind.user_id_for_phone(db, phone)
    if user_id and settings.max_bot_token:
        if max_http.send_message(int(user_id), bot_otp_text(code)):
            return "max"

    return ""


def _response(channel: str, *, ttl_sec: int, retry_after: int = RESEND_SEC) -> dict:
    """Один формат ответа на request-code: фронт по `channel` / `need_bind`
    решает, показать таймер или ссылки на боты."""
    return {
        "ok": bool(channel),
        "channel": channel,
        "need_bind": not channel,
        "links": bot_links(),
        "ttl_sec": ttl_sec,
        "retry_after": retry_after,
    }


def _issue(db: Session, phone: str) -> tuple[SmsOtp, str]:
    """Новый код: предыдущий активный гасим, чтобы одновременно жил только один."""
    now = clock.now()
    previous = _active(db, phone)
    if previous is not None:
        previous.consumed_at = now
    code = f"{secrets.randbelow(10 ** CODE_LEN):0{CODE_LEN}d}"
    row = SmsOtp(
        phone=phone,
        code_hash=_hash(phone, code),
        created_at=now,
        expires_at=now + timedelta(seconds=TTL_SEC),
    )
    db.add(row)
    db.flush()
    return row, code


def _issue_and_deliver(db: Session, phone: str) -> dict:
    row, code = _issue(db, phone)
    channel = _deliver(db, phone, code)
    row.channel = channel
    db.commit()
    log.info("OTP выдан phone=%s otp_id=%s канал=%s", phone, row.id, channel or "нет")
    return _response(channel, ttl_sec=TTL_SEC)


def request_code(db: Session, raw_phone: str) -> dict:
    phone = normalize_phone(raw_phone)
    if not is_canonical_phone(phone):
        raise OtpError("Введите полный номер телефона.")

    now = clock.now()
    existing = _active(db, phone)
    if existing is not None and existing.channel:
        waited = (now - existing.created_at).total_seconds()
        if waited < RESEND_SEC:
            # Код ушёл меньше минуты назад: нового не выдаём, тот, что у клиента
            # на руках, остаётся действительным. Фронт показывает таймер.
            return _response(
                existing.channel,
                ttl_sec=max(int((existing.expires_at - now).total_seconds()), 0),
                retry_after=max(int(RESEND_SEC - waited), 1),
            )

    # Либо кода нет, либо прошлый доставить было некуда (клиент как раз привязал
    # бота) — в обоих случаях выдаём свежий и пробуем все каналы заново.
    return _issue_and_deliver(db, phone)


def deliver_pending(db: Session, phone: str) -> str:
    """Клиент только что привязал бота: если он ждёт код — выдаём и присылаем.

    Вызывается из telegram_bind/max_bind. Возвращает канал доставки или "".
    """
    normalized = normalize_phone(phone)
    if not is_canonical_phone(normalized):
        return ""
    pending = _active(db, normalized)
    if pending is None or pending.channel:
        return ""
    row, code = _issue(db, normalized)
    channel = _deliver(db, normalized, code)
    row.channel = channel
    db.commit()
    if channel:
        log.info("OTP досылка после привязки phone=%s канал=%s", normalized, channel)
    return channel


def verify_code(db: Session, raw_phone: str, raw_code: str) -> dict:
    phone = normalize_phone(raw_phone)
    code = "".join(ch for ch in str(raw_code or "") if ch.isdigit())
    if not is_canonical_phone(phone):
        raise OtpError("Введите полный номер телефона.")
    if len(code) != CODE_LEN:
        raise OtpError("Код состоит из 4 цифр.")

    row = _active(db, phone)
    if row is None:
        raise OtpError("Код не найден или истек. Запросите новый.")
    if row.attempts >= MAX_ATTEMPTS:
        raise OtpError("Слишком много попыток. Запросите новый код.", 429)

    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, _hash(phone, code)):
        db.commit()
        raise OtpError("Неверный код.")

    row.consumed_at = clock.now()
    db.commit()
    return {"ok": True, "phone": phone}
