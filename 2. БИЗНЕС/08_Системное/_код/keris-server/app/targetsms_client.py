"""Клиент TargetSMS — SMS-уведомления клиентам.

Один эндпоинт `sendsmsjson.php`, тип запроса задаётся полем `type`:
`sms` — отправка, `state` — статус доставки, `balance` — остаток.

Авторизация не через login/password из публичной документации, а через заголовок
`Authorization: Bearer <токен>` — именно так 24.08.2026 ушли тестовые SMS
на номер владельца. Пара `nikolas/nikolas` (вход в кабинет) шлюз не принимает.

Имя отправителя `KerisClub` прошло модерацию, поэтому `sender` передаётся всегда:
без него шлюз отвечает «Нет отправителя» и в очередь сообщение не ставит.

Канал платный и выключен по умолчанию: без `TARGETSMS_ENABLED=true` плюс токена
`send_sms()` поднимает `TargetSMSNotConfigured` (вызывающий код в `reminders.py`
ловит её сам и просто пропускает SMS, не роняя Telegram/MAX).

Ошибки шлюза приходят двумя путями: `{"error": ...}` — проблема с самим запросом,
текст в `action` вместо `send` — проблема с конкретным сообщением. Оба случая
поднимают `TargetSMSError`.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from .config import settings

log = logging.getLogger("keris.targetsms")

BASE_URL = "https://sms.targetsms.ru/sendsmsjson.php"

# Название рассылки в кабинете: по умолчанию шлюз пишет «Шлюз», по нашему видно,
# что сообщение ушло из сервера, а не руками из ЛК.
DELIVERY_NAME = "keris-server"

SENT_ACTION = "send"
FINAL_DELIVERED = "deliver"


class TargetSMSError(Exception):
    pass


class TargetSMSNotConfigured(TargetSMSError):
    pass


def _require_ready() -> None:
    if not settings.targetsms_ready:
        raise TargetSMSNotConfigured(
            "TargetSMS не настроен: нужны TARGETSMS_ENABLED=true и TARGETSMS_TOKEN"
        )


def to_targetsms_phone(phone: str) -> str:
    """+79991234567 / 89991234567 / 79991234567 -> "79991234567" (11 цифр, без "+")."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.targetsms_token}",
    }


def _post(payload: dict[str, Any]) -> dict:
    """POST на шлюз. Токен уходит только в заголовке, в логи его не пишем."""
    resp = requests.post(BASE_URL, json=payload, headers=_headers(), timeout=15)
    if resp.status_code >= 500 or resp.status_code == 429:
        raise TargetSMSError(f"{payload.get('type')} -> {resp.status_code} (retryable): {resp.text[:300]}")
    if resp.status_code >= 400:
        raise TargetSMSError(f"{payload.get('type')} -> {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError:
        raise TargetSMSError(f"{payload.get('type')} -> ответ не JSON: {resp.text[:300]}") from None
    if isinstance(data, dict) and data.get("error"):
        raise TargetSMSError(f"{payload.get('type')} -> {data['error']}")
    return data


def send_sms(phone: str, text: str) -> dict:
    """Одно SMS. Возвращает элемент `sms[0]` ответа (`{"number_sms", "id_sms", "parts", "action"}`).

    Поднимает `TargetSMSError`/`TargetSMSNotConfigured` — вызывающая сторона
    (`reminders.send_sms`) сама ловит исключение, чтобы сбой SMS не ронял
    остальную рассылку (Telegram/MAX).
    """
    _require_ready()
    message = {
        "type": "sms",
        "sender": settings.targetsms_sender,
        "text": text,
        "name_delivery": DELIVERY_NAME,
        "abonent": [{"phone": to_targetsms_phone(phone), "number_sms": "1"}],
    }
    data = _post({"type": "sms", "message": [message]})
    rows = data.get("sms") or []
    row = rows[0] if rows else {}
    action = row.get("action")
    if action != SENT_ACTION:
        # `action` не «send» — это текст ошибки по конкретному сообщению
        # (закончились SMS, стоп-лист, отклонено модератором, нет отправителя).
        raise TargetSMSError(f"SMS не принята шлюзом: {action or 'пустой ответ'}")
    return row


def get_state(ids: list[str]) -> dict[str, str]:
    """Статусы отправленных SMS по `id_sms`: send / deliver / not_deliver / expired /
    partly_deliver. Нужны для разбора «не дошло» — в логах у нас только id."""
    _require_ready()
    if not ids:
        return {}
    data = _post({"type": "state", "get_state": [str(i) for i in ids]})
    return {str(row.get("id_sms")): str(row.get("state") or "") for row in (data.get("state") or [])}


def get_balance() -> float:
    """Баланс аккаунта в рублях — для проверки конфигурации и алертов о низком балансе."""
    _require_ready()
    data = _post({"type": "balance"})
    try:
        return float((data.get("money") or {}).get("value"))
    except (TypeError, ValueError):
        return 0.0
