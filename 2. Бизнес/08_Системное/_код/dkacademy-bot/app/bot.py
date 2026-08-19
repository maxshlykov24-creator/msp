from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import select

from app import amocrm_client, ui_text
from app.config import get_settings
from app.database import get_session_factory
from app.amocrm_client import get_delivery_statuses_from_amo
from app.models import ContactBinding
from app.phone_utils import normalize_phone

log = logging.getLogger(__name__)
router = Router()

BTN_MY_DELIVERIES = "📦 Мои доставки"
BTN_MANAGER = "💬 Менеджер"


def _digits_to_plus(norm: str) -> str:
    return norm if norm.startswith("+") else "+" + norm


def _request_contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _persistent_kb() -> ReplyKeyboardMarkup:
    """Постоянная нижняя клавиатура — после привязки."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MY_DELIVERIES), KeyboardButton(text=BTN_MANAGER)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _status_inline_kb(manager_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_MY_DELIVERIES, callback_data=ui_text.CB_MY_DELIVERIES
                ),
                InlineKeyboardButton(
                    text="💬 Менеджер", url=ui_text.manager_link(manager_username)
                ),
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    chat_id = str(message.chat.id)
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        already_bound = sess.execute(
            select(ContactBinding).where(
                ContactBinding.telegram_chat_id == chat_id,
                ContactBinding.channel == "telegram",
            )
        ).scalar_one_or_none()
    finally:
        sess.close()

    if already_bound:
        s = get_settings()
        # Обновляем telegram_chat_id в amoCRM — мог не записаться при первой привязке
        try:
            fld_update: dict[int, str | bool | int] = {}
            tcid_fid = int(s.amo_field_contact_telegram_chat_id or 0)
            if tcid_fid > 0:
                fld_update[tcid_fid] = str(message.chat.id)
            tuid_fid = int(s.amo_field_contact_telegram_user_id or 0)
            fu = message.from_user
            if tuid_fid > 0 and fu:
                fld_update[tuid_fid] = str(fu.id)
            active_fid = int(s.amo_field_contact_bot_active or 0)
            if active_fid > 0:
                fld_update[active_fid] = True
            log.info(
                "cmd_start already_bound: contact_id=%s fld_update=%s",
                already_bound.amo_contact_id,
                fld_update,
            )
            if fld_update:
                SessLocal2 = get_session_factory()
                sess2 = SessLocal2()
                try:
                    amocrm_client.patch_contact_fields(
                        sess2, int(already_bound.amo_contact_id), fld_update
                    )
                    log.info("cmd_start already_bound: patch_contact_fields OK")
                finally:
                    sess2.close()
        except Exception:
            log.exception("cmd_start already_bound: patch_contact_fields FAILED")

        await message.answer(
            f"Вы уже подключены ✅\n\n"
            f"Нажмите «📦 Мои доставки», чтобы посмотреть статус заказа.\n\n"
            f"Есть вопросы? {ui_text.manager_handle(s.manager_telegram_username)}",
            reply_markup=_persistent_kb(),
        )
        return

    await message.answer(ui_text.hello_text(), reply_markup=_request_contact_kb())


@router.message(F.contact)
async def handle_contact(message: Message) -> None:
    s = get_settings()
    contact = message.contact
    fu = message.from_user
    if not contact or not fu:
        return
    if contact.user_id != fu.id:
        await message.answer(ui_text.contact_wrong_text(), reply_markup=_request_contact_kb())
        return

    norm = normalize_phone(contact.phone_number or "")
    if not norm:
        await message.answer(
            ui_text.contact_bad_phone_text(), reply_markup=_request_contact_kb()
        )
        return

    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        amo_items = amocrm_client.search_contacts_by_query(sess, norm)
        if len(amo_items) > 1:
            log.warning("Несколько контактов amo по query=%s*** — берём первый", norm[:5])

        contact_id: int | None = None
        if amo_items:
            cid0 = amo_items[0].get("id")
            if isinstance(cid0, int):
                contact_id = cid0

        if contact_id is None:
            mode = (s.onboarding_contact_not_found or "reject").lower().strip()
            if mode == "create":
                plus = _digits_to_plus(norm)
                contact_id = amocrm_client.create_contact_phone(
                    sess, phone_international_plus=plus
                )
                if contact_id is None:
                    await message.answer(
                        ui_text.contact_error_text(s.manager_telegram_username),
                        reply_markup=_persistent_kb(),
                    )
                    return
            else:
                # reject — мягко: контакт не привязываем, но даём бесшумную клавиатуру и контакт менеджера
                await message.answer(
                    ui_text.contact_no_match_text(s.manager_telegram_username),
                    reply_markup=_persistent_kb(),
                )
                return

        fld: dict[int, str | bool | int] = {}
        if int(s.amo_field_contact_telegram_chat_id or 0) > 0:
            fld[int(s.amo_field_contact_telegram_chat_id)] = str(message.chat.id)
        if int(s.amo_field_contact_telegram_user_id or 0) > 0:
            fld[int(s.amo_field_contact_telegram_user_id)] = str(fu.id)
        if int(s.amo_field_contact_bot_started_at or 0) > 0:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            ftype = (s.amo_field_contact_bot_started_at_type or "text").lower().strip()
            if ftype == "date":
                fld[int(s.amo_field_contact_bot_started_at)] = int(now.timestamp())
            else:
                fld[int(s.amo_field_contact_bot_started_at)] = now.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
        if int(s.amo_field_contact_bot_active or 0) > 0:
            fld[int(s.amo_field_contact_bot_active)] = True
        if fld:
            log.info("handle_contact: patch_contact_fields contact_id=%s fld=%s", contact_id, fld)
            amocrm_client.patch_contact_fields(sess, contact_id, fld)

        existing = (
            sess.execute(
                select(ContactBinding).where(
                    ContactBinding.telegram_chat_id == str(message.chat.id),
                    ContactBinding.channel == "telegram",
                )
            )
            .scalar_one_or_none()
        )
        if existing:
            existing.amo_contact_id = int(contact_id)
            existing.phone_normalized = norm
            existing.telegram_user_id = str(fu.id)
            existing.is_blocked = False
        else:
            sess.add(
                ContactBinding(
                    telegram_chat_id=str(message.chat.id),
                    channel="telegram",
                    amo_contact_id=int(contact_id),
                    phone_normalized=norm,
                    telegram_user_id=str(fu.id),
                )
            )
        sess.commit()

        # Сразу показываем статусы доставок (если есть открытые сделки с треком)
        try:
            delivery_items = get_delivery_statuses_from_amo(sess, int(contact_id))
        except Exception:
            log.exception("get_delivery_statuses_from_amo after onboarding failed")
            delivery_items = []

        if delivery_items:
            await message.answer(
                ui_text.contact_accepted_text(s.manager_telegram_username),
                reply_markup=_persistent_kb(),
            )
            await message.answer(
                ui_text.status_list_text(delivery_items, s.manager_telegram_username),
                reply_markup=_persistent_kb(),
            )
        else:
            await message.answer(
                ui_text.contact_accepted_text(s.manager_telegram_username),
                reply_markup=_persistent_kb(),
            )
    except Exception:
        log.exception("on_contact handler failed")
        await message.answer(
            ui_text.contact_error_text(s.manager_telegram_username),
            reply_markup=_persistent_kb(),
        )
        try:
            sess.rollback()
        except Exception:
            pass
    finally:
        sess.close()


def _gather_status_lines_for_chat(chat_id: int) -> list[tuple[str, str]] | None:
    """Возвращает список (track, status_text) для chat_id или None, если контакт не привязан.

    Читает поле Статус LiveInform из открытых сделок amoCRM (без обращения в LI API).
    """
    SessLocal = get_session_factory()
    sess = SessLocal()
    try:
        binding = sess.execute(
            select(ContactBinding).where(ContactBinding.telegram_chat_id == str(chat_id))
        ).scalar_one_or_none()
        if not binding:
            return None
        return get_delivery_statuses_from_amo(sess, int(binding.amo_contact_id))
    finally:
        sess.close()


async def _send_status(target_chat_id: int, send_callable) -> None:
    s = get_settings()
    try:
        items = await asyncio.to_thread(_gather_status_lines_for_chat, target_chat_id)
    except Exception:
        log.exception("status: amo lookup failed")
        await send_callable(ui_text.status_error_text(s.manager_telegram_username))
        return
    if items is None:
        await send_callable(ui_text.status_no_binding_text())
        return
    if not items:
        await send_callable(ui_text.status_empty_text(s.manager_telegram_username))
        return
    await send_callable(ui_text.status_list_text(items, s.manager_telegram_username))


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await _send_status(message.chat.id, lambda txt: message.answer(txt, reply_markup=_persistent_kb()))


@router.message(F.text == BTN_MY_DELIVERIES)
async def reply_my_deliveries(message: Message) -> None:
    await _send_status(message.chat.id, lambda txt: message.answer(txt, reply_markup=_persistent_kb()))


@router.message(F.text == BTN_MANAGER)
async def reply_manager(message: Message) -> None:
    s = get_settings()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать менеджеру 💬", url=ui_text.manager_link(s.manager_telegram_username))]
        ]
    )
    await message.answer(
        ui_text.manager_contact_text(s.manager_telegram_username),
        reply_markup=kb,
    )


@router.callback_query(F.data == ui_text.CB_MY_DELIVERIES)
async def cb_my_deliveries(cq: CallbackQuery) -> None:
    chat_id = cq.message.chat.id if cq.message else None
    if not chat_id:
        await cq.answer()
        return
    await _send_status(chat_id, lambda txt: cq.message.answer(txt, reply_markup=_persistent_kb()))
    try:
        await cq.answer()
    except Exception:
        pass


@router.message()
async def fallback(message: Message) -> None:
    s = get_settings()
    await message.answer(
        ui_text.fallback_text(s.manager_telegram_username),
        reply_markup=_persistent_kb(),
    )


async def run_bot_forever() -> None:
    s = get_settings()
    tok = (s.telegram_bot_token or "").strip()
    if not tok:
        log.warning("TELEGRAM_BOT_TOKEN пуст — Telegram polling не запущен")
        while True:
            await asyncio.sleep(3600)

    bot = Bot(tok)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
