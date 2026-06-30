"""MAX Bot — long polling обработчик.

Эмулирует Telegram-бота через MAX Bot API (platform-api.max.ru).
Inline-кнопки под каждым ответом создают «постоянную нижнюю панель».
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select

from app import amocrm_client, ui_text
from app.amocrm_client import get_delivery_statuses_from_amo
from app.config import get_settings
from app.database import get_session_factory
from app.max_api import (
    extract_phone_from_vcf,
    get_updates,
    send_message,
    verify_contact_hash,
)
from app.models import ContactBinding
from app.phone_utils import extract_phone_from_text, normalize_phone

log = logging.getLogger(__name__)

POLL_TIMEOUT = 30

# Сколько раз просим повторить номер, прежде чем отправить к менеджеру.
# Счётчик в памяти процесса: после перезапуска просто обнуляется (не критично,
# привязки клиентов хранятся в БД и не теряются).
MAX_PHONE_ATTEMPTS = 2
_phone_attempts: dict[int, int] = {}


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def _menu_kb() -> list[list[dict[str, Any]]]:
    """Стандартное меню под каждым сообщением."""
    s = get_settings()
    return [
        [
            {"type": "callback", "text": "📦 Мои доставки", "payload": ui_text.CB_MY_DELIVERIES},
            {"type": "link", "text": "💬 Менеджер", "url": ui_text.manager_max_link(s.manager_max_url)},
        ]
    ]


def _contact_kb() -> list[list[dict[str, Any]]]:
    """Кнопка запроса контакта — только на экране онбординга."""
    return [[{"type": "request_contact", "text": "📱 Поделиться номером"}]]


# ---------------------------------------------------------------------------
# Отправка
# ---------------------------------------------------------------------------

def _send(token: str, user_id: int | str, text: str, buttons: Optional[list] = None) -> None:
    kb = buttons if buttons is not None else _menu_kb()
    ok, err = send_message(token, user_id, text, buttons=kb)
    if not ok:
        log.warning("max_bot send_message failed user=%s err=%s", user_id, err)


# ---------------------------------------------------------------------------
# Бизнес-логика
# ---------------------------------------------------------------------------

def _handle_start(token: str, user_id: int) -> None:
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        s = get_settings()
        binding = sess.execute(
            select(ContactBinding).where(
                ContactBinding.telegram_chat_id == str(user_id),
                ContactBinding.channel == "max",
            )
        ).scalar_one_or_none()

        if binding:
            # Обновляем поля в amoCRM (MAX-специфичные)
            fld: dict[int, str | bool | int] = {}
            mcid_fid = int(s.amo_field_contact_max_chat_id or 0)
            if mcid_fid > 0:
                fld[mcid_fid] = str(user_id)
            active_fid = int(s.amo_field_contact_max_active or 0)
            if active_fid > 0:
                fld[active_fid] = True
            if fld:
                try:
                    amocrm_client.patch_contact_fields(sess, int(binding.amo_contact_id), fld)
                except Exception:
                    log.exception("max_bot handle_start: patch_contact_fields failed")

            _send(token, user_id, "Вы уже подключены ✅\n\nНажмите «📦 Мои доставки», чтобы посмотреть статус заказа.")
            return

        # Просим написать номер текстом — кнопки не нужны на этом шаге.
        _phone_attempts.pop(user_id, None)
        _send(token, user_id, ui_text.max_hello_text(), buttons=[])
    finally:
        sess.close()


def _handle_contact(token: str, user_id: int, payload: dict[str, Any]) -> None:
    vcf_info = payload.get("vcf_info", "") or ""
    received_hash = payload.get("hash", "") or ""

    # Отладочное логирование структуры payload (без секретов)
    payload_keys = list(payload.keys())
    max_info = payload.get("max_info") or {}
    log.info(
        "max_bot _handle_contact user=%s payload_keys=%s vcf_len=%d has_hash=%s max_info_keys=%s",
        user_id, payload_keys, len(vcf_info), bool(received_hash), list(max_info.keys()) if isinstance(max_info, dict) else None,
    )
    log.info("max_bot vcf_info raw: %r", vcf_info[:500])

    # Пытаемся достать телефон из разных мест:
    # 1. TEL из vcf_info (стандартный путь, если пользователь нажал «Поделиться номером»)
    # 2. прямое поле vcf_phone / phone в payload (MAX отдаёт vcf_phone)
    # 3. вложенное в max_info: phone / contact / tel
    phone_raw = extract_phone_from_vcf(vcf_info) if vcf_info else ""
    if not phone_raw:
        for k in ("vcf_phone", "phone", "tel", "phone_number"):
            v = payload.get(k)
            if v:
                phone_raw = str(v)
                log.info("max_bot phone from payload[%s]=%s", k, phone_raw)
                break
    if not phone_raw and isinstance(max_info, dict):
        for k in ("vcf_phone", "phone", "tel", "phone_number"):
            v = max_info.get(k)
            if v:
                phone_raw = str(v)
                log.info("max_bot phone from max_info[%s]=%s", k, phone_raw)
                break

    norm = normalize_phone(phone_raw) or extract_phone_from_text(vcf_info)
    if not norm:
        log.warning("max_bot: не удалось извлечь номер. vcf=%r payload=%s", vcf_info[:300], payload)
        _ask_phone_again(token, user_id)
        return

    # Проверяем hash только если vcf_info с TEL пришёл и hash был
    if vcf_info and received_hash:
        if not verify_contact_hash(token, vcf_info, received_hash):
            log.warning("max_bot: contact hash mismatch user=%s — продолжаем (телефон извлечён)", user_id)

    _phone_attempts.pop(user_id, None)
    _bind_phone(token, user_id, norm)


def _bind_phone(token: str, user_id: int, norm: str) -> None:
    """Привязать контакт по нормализованному телефону и показать доставки.

    Общий путь для нативного контакта (request_contact) и для номера,
    введённого текстом (фоллбэк, если кнопка MAX не сработала).
    """
    s = get_settings()
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        amo_items = amocrm_client.search_contacts_by_query(sess, norm)
        contact_id: Optional[int] = None
        if amo_items:
            cid0 = amo_items[0].get("id")
            if isinstance(cid0, int):
                contact_id = cid0

        if contact_id is None:
            mode = (s.onboarding_contact_not_found or "reject").lower().strip()
            if mode == "create":
                plus = norm if norm.startswith("+") else "+" + norm
                contact_id = amocrm_client.create_contact_phone(sess, phone_international_plus=plus)
            if contact_id is None:
                _send(token, user_id, ui_text.contact_no_match_text(s.manager_telegram_username))
                return

        # Патчим поля контакта в amoCRM (MAX-специфичные)
        fld: dict[int, str | bool | int] = {}
        mcid_fid = int(s.amo_field_contact_max_chat_id or 0)
        if mcid_fid > 0:
            fld[mcid_fid] = str(user_id)
        active_fid = int(s.amo_field_contact_max_active or 0)
        if active_fid > 0:
            fld[active_fid] = True
        if fld:
            amocrm_client.patch_contact_fields(sess, contact_id, fld)

        # Сохраняем привязку
        existing = sess.execute(
            select(ContactBinding).where(
                ContactBinding.telegram_chat_id == str(user_id),
                ContactBinding.channel == "max",
            )
        ).scalar_one_or_none()
        if existing:
            existing.amo_contact_id = int(contact_id)
            existing.phone_normalized = norm
            existing.is_blocked = False
        else:
            sess.add(ContactBinding(
                telegram_chat_id=str(user_id),
                channel="max",
                amo_contact_id=int(contact_id),
                phone_normalized=norm,
            ))
        sess.commit()

        # Получаем и показываем доставки одним сообщением (не дублируем «нет доставок»,
        # contact_accepted_text уже говорит о том, что уведомления придут автоматически).
        delivery_items = get_delivery_statuses_from_amo(sess, int(contact_id))
        _send(token, user_id, ui_text.contact_accepted_text(s.manager_telegram_username))
        if delivery_items:
            _send(token, user_id, ui_text.status_list_text(delivery_items, s.manager_telegram_username))
    finally:
        sess.close()


def _handle_my_deliveries(token: str, user_id: int) -> None:
    s = get_settings()
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        binding = sess.execute(
            select(ContactBinding).where(
                ContactBinding.telegram_chat_id == str(user_id),
                ContactBinding.channel == "max",
            )
        ).scalar_one_or_none()
        if not binding:
            _send(token, user_id, ui_text.status_no_binding_text())
            return
        items = get_delivery_statuses_from_amo(sess, int(binding.amo_contact_id))
        if items:
            _send(token, user_id, ui_text.status_list_text(items, s.manager_telegram_username))
        else:
            _send(token, user_id, ui_text.status_empty_text(s.manager_telegram_username))
    finally:
        sess.close()


def _handle_text(token: str, user_id: int, text: str) -> None:
    """Текстовое сообщение.

    Если клиент ещё не подключён — основной сценарий онбординга в MAX: ждём
    номер телефона текстом (кнопка «Поделиться номером» в MAX часто не отдаёт
    номер). Умно вытаскиваем номер из любого формата; если не вышло — просим
    прислать ещё раз, после нескольких неудач направляем к менеджеру.
    Если клиент уже подключён — обычный фоллбэк."""
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        already = sess.execute(
            select(ContactBinding).where(
                ContactBinding.telegram_chat_id == str(user_id),
                ContactBinding.channel == "max",
            )
        ).scalar_one_or_none()
    finally:
        sess.close()

    if already is not None:
        _handle_fallback(token, user_id)
        return

    # Не подключён → трактуем сообщение как попытку прислать номер.
    norm = extract_phone_from_text(text)
    if norm:
        log.info("max_bot: телефон из текста user=%s — привязываем", user_id)
        _phone_attempts.pop(user_id, None)
        _bind_phone(token, user_id, norm)
        return

    _ask_phone_again(token, user_id)


def _ask_phone_again(token: str, user_id: int) -> None:
    """Номер не распознан: просим прислать ещё раз, после порога — к менеджеру."""
    s = get_settings()
    attempts = _phone_attempts.get(user_id, 0) + 1
    _phone_attempts[user_id] = attempts
    if attempts >= MAX_PHONE_ATTEMPTS:
        _phone_attempts.pop(user_id, None)
        _send(token, user_id, ui_text.phone_give_up_max_text(s.manager_max_url))
    else:
        _send(token, user_id, ui_text.phone_retry_text(), buttons=[])


def _handle_fallback(token: str, user_id: int) -> None:
    s = get_settings()
    _send(token, user_id, ui_text.fallback_text(s.manager_telegram_username))


# ---------------------------------------------------------------------------
# Роутинг событий
# ---------------------------------------------------------------------------

def _process_event(token: str, event: dict[str, Any]) -> None:
    etype = event.get("update_type", "")
    log.info("max_bot event type=%s raw=%s", etype, str(event)[:1500])
    try:
        if etype == "bot_started":
            uid = (event.get("user") or {}).get("user_id")
            if uid:
                _handle_start(token, int(uid))

        elif etype == "message_created":
            msg = event.get("message") or {}
            sender = msg.get("sender") or {}
            user_id = sender.get("user_id")
            if not user_id:
                return

            # Контакт — attachment type=contact
            attachments = msg.get("attachments") or []
            contact_attachment = next(
                (a for a in attachments if a.get("type") == "contact"), None
            )
            if contact_attachment:
                _handle_contact(token, int(user_id), contact_attachment.get("payload") or {})
                return

            # Текст
            text = (msg.get("body") or {}).get("text", "").strip()
            if text in ("/start", "start"):
                _handle_start(token, int(user_id))
            else:
                _handle_text(token, int(user_id), text)

        elif etype == "message_callback":
            cb = event.get("callback") or {}
            payload_str = str(cb.get("payload") or "")
            user_id = (cb.get("user") or {}).get("user_id")
            if not user_id:
                return
            if payload_str == ui_text.CB_MY_DELIVERIES:
                _handle_my_deliveries(token, int(user_id))
            else:
                _handle_fallback(token, int(user_id))

    except Exception:
        log.exception("max_bot: error processing event %s", etype)


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

async def run_max_polling_forever() -> None:
    s = get_settings()
    token = (s.max_bot_token or "").strip()
    if not token:
        log.info("MAX_BOT_TOKEN не задан — MAX-бот не запущен")
        return

    log.info("MAX-бот запущен, long polling...")
    marker: Optional[int] = None
    while True:
        try:
            events, next_marker = await asyncio.to_thread(
                get_updates, token, marker=marker, timeout=POLL_TIMEOUT,
                types=["bot_started", "message_created", "message_callback"],
            )
            if next_marker is not None:
                marker = next_marker
            for event in events:
                await asyncio.to_thread(_process_event, token, event)
        except asyncio.CancelledError:
            log.info("MAX polling остановлен")
            return
        except Exception as exc:
            log.warning("MAX polling error: %s — повтор через 5 сек", exc)
            await asyncio.sleep(5)
