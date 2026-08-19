"""Inline keyboards for moderator and participant screens."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def role_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📺 Я экран для стола", callback_data="role:participant")
    b.button(text="🎯 Я модератор", callback_data="role:moderator")
    b.adjust(1)
    return b.as_markup()


def deck_keyboard(used_count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if used_count > 0:
        b.button(
            text=f"▶ Продолжить колоду (уже сыграно: {used_count})",
            callback_data="deck:continue",
        )
    b.button(text="🔄 Начать сначала", callback_data="deck:reset")
    b.adjust(1)
    return b.as_markup()


def preset_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🌱 Встреча 1  (лёгкие + тёплые)", callback_data="preset:meeting1")
    b.button(text="🔥 Полная колода  (все вопросы)", callback_data="preset:full")
    b.adjust(1)
    return b.as_markup()


def participant_question_keyboard(replace_available: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if replace_available:
        b.button(text="↺ Заменить вопрос  (1 раз)", callback_data="question:replace")
    b.adjust(1)
    return b.as_markup()


def participant_ready_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="▶ Получить вопрос", callback_data="question:get")
    b.adjust(1)
    return b.as_markup()


def moderator_active_keyboard(has_undo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ответил", callback_data="answer:done")
    b.button(text="⏭ Пропустить", callback_data="answer:skip")
    if has_undo:
        b.button(text="↩ Отмена", callback_data="answer:undo")
        b.button(text="🏁 Завершить встречу", callback_data="session:finish")
        b.adjust(2, 1, 1)
    else:
        b.button(text="🏁 Завершить встречу", callback_data="session:finish")
        b.adjust(2, 1)
    return b.as_markup()


def moderator_waiting_keyboard(has_undo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_undo:
        b.button(text="↩ Отменить последнее", callback_data="answer:undo")
    b.button(text="🏁 Завершить встречу", callback_data="session:finish")
    b.adjust(1)
    return b.as_markup()
