"""Inline keyboards for moderator and participant screens."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def role_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📺 Я экран для стола", callback_data="role:participant")
    b.button(text="🎯 Я модератор", callback_data="role:moderator")
    b.button(text="📊 История вопросов", callback_data="history:played")
    b.adjust(1)
    return b.as_markup()


def deck_keyboard(used_count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if used_count > 0:
        b.button(
            text=f"▶ Продолжить колоду (уже ответили: {used_count})",
            callback_data="deck:continue",
        )
        b.button(text="🔄 Начать сначала", callback_data="deck:reset")
    else:
        b.button(text="▶ Начать колоду", callback_data="deck:reset_yes")
    b.adjust(1)
    return b.as_markup()


def confirm_reset_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="▶ Нет, продолжить колоду", callback_data="deck:continue")
    b.button(text="🔄 Да, начать с нуля", callback_data="deck:reset_yes")
    b.adjust(1)
    return b.as_markup()


def preset_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🌱 Лёгкие и тёплые", callback_data="preset:meeting1")
    b.button(text="🔥 Вся колода, включая глубокие", callback_data="preset:full")
    b.adjust(1)
    return b.as_markup()


def participant_question_keyboard(replace_available: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if replace_available:
        b.button(text="↺ Другой вопрос", callback_data="question:replace")
    b.adjust(1)
    return b.as_markup()


def participant_ready_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="▶ Мой вопрос", callback_data="question:get")
    b.adjust(1)
    return b.as_markup()


def moderator_active_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ответил", callback_data="answer:done")
    b.button(text="⏭ Пропустить", callback_data="answer:skip")
    b.button(text="↩ Не тот человек", callback_data="answer:wrong")
    b.button(text="🏁 Завершить встречу", callback_data="session:finish")
    b.adjust(2, 1, 1)
    return b.as_markup()


def moderator_waiting_keyboard(has_undo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_undo:
        b.button(text="↩ Отменить последнее", callback_data="answer:undo")
    b.button(text="🏁 Завершить встречу", callback_data="session:finish")
    b.adjust(1)
    return b.as_markup()
