from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_WRITE = "📋 Написать отчёт"
BTN_ADD_REV = "💡 Добавить откровение"
BTN_MY_REV = "📖 Откровения недели"
BTN_ARCHIVE = "📚 Архив откровений"
BTN_STATS = "📈 Моя статистика"
BTN_PRAYER = "🙏 Молитвенный лист"
BTN_EDIT_WEEK = "✏️ Править отчёт недели"
BTN_PROFILE = "🧭 Мой профиль"
BTN_REPORT = "📋 Отчёт"
BTN_REVELATIONS = "📖 Откровения"

MAIN_MENU_BUTTONS: tuple[str, ...] = (
    BTN_REPORT,
    BTN_REVELATIONS,
    BTN_STATS,
    BTN_PRAYER,
    # legacy (fallback если Telegram закешировал старую клавиатуру)
    BTN_WRITE,
    BTN_EDIT_WEEK,
    BTN_ADD_REV,
    BTN_MY_REV,
    BTN_ARCHIVE,
)


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_REVELATIONS)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_PRAYER)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def report_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Написать отчёт", callback_data="rep_menu:write")],
            [InlineKeyboardButton(text="✏️ Править отчёт недели", callback_data="rep_menu:edit")],
            [InlineKeyboardButton(text="← Назад", callback_data="rep_menu:back")],
        ]
    )


def revelations_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💡 Добавить откровение", callback_data="rev:add")],
            [InlineKeyboardButton(text="📖 Откровения недели", callback_data="rev:week")],
            [InlineKeyboardButton(text="📚 Архив откровений", callback_data="rev:archive")],
            [InlineKeyboardButton(text="← Назад", callback_data="rev:back")],
        ]
    )


def add_rev_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="rev:add_more")],
            [InlineKeyboardButton(text="✅ Готово", callback_data="rev:done")],
        ]
    )


def prayer_hours_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="0.5 ч", callback_data="q1:0.5"),
            InlineKeyboardButton(text="1 ч", callback_data="q1:1"),
            InlineKeyboardButton(text="1.5 ч", callback_data="q1:1.5"),
        ],
        [
            InlineKeyboardButton(text="2 ч", callback_data="q1:2"),
            InlineKeyboardButton(text="2.5 ч", callback_data="q1:2.5"),
            InlineKeyboardButton(text="3 ч", callback_data="q1:3"),
        ],
        [
            InlineKeyboardButton(text="3.5 ч", callback_data="q1:3.5"),
            InlineKeyboardButton(text="4 ч", callback_data="q1:4+"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bible_days_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0", callback_data="q1b:0"),
                InlineKeyboardButton(text="1", callback_data="q1b:1"),
                InlineKeyboardButton(text="2", callback_data="q1b:2"),
                InlineKeyboardButton(text="3", callback_data="q1b:3"),
            ],
            [
                InlineKeyboardButton(text="4", callback_data="q1b:4"),
                InlineKeyboardButton(text="5", callback_data="q1b:5"),
                InlineKeyboardButton(text="6", callback_data="q1b:6"),
                InlineKeyboardButton(text="7", callback_data="q1b:7"),
            ],
        ]
    )


def sermons_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0", callback_data="q2:0"),
                InlineKeyboardButton(text="1", callback_data="q2:1"),
                InlineKeyboardButton(text="2", callback_data="q2:2"),
            ],
            [
                InlineKeyboardButton(text="3", callback_data="q2:3"),
                InlineKeyboardButton(text="4", callback_data="q2:4"),
                InlineKeyboardButton(text="5", callback_data="q2:5+"),
            ],
        ]
    )


def fast_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="q3:yes"),
                InlineKeyboardButton(text="Нет", callback_data="q3:no"),
            ],
        ]
    )


def meetings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="0", callback_data="q6:0"),
                InlineKeyboardButton(text="1", callback_data="q6:1"),
                InlineKeyboardButton(text="2", callback_data="q6:2"),
            ],
            [
                InlineKeyboardButton(text="3", callback_data="q6:3"),
                InlineKeyboardButton(text="4", callback_data="q6:4"),
                InlineKeyboardButton(text="5", callback_data="q6:5+"),
            ],
        ]
    )


def q5_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data="q5:done")]]
    )


def confirm_kb(*, show_reset: bool = True) -> InlineKeyboardMarkup:
    top = (
        [
            InlineKeyboardButton(text="✅ Отправить", callback_data="cf:send"),
            InlineKeyboardButton(text="🔄 Заново", callback_data="cf:reset"),
        ]
        if show_reset
        else [InlineKeyboardButton(text="✅ Отправить", callback_data="cf:send")]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            top,
            [
                InlineKeyboardButton(text="✏️1 молитва", callback_data="edit:1"),
                InlineKeyboardButton(text="✏️2 библия", callback_data="edit:2"),
                InlineKeyboardButton(text="✏️3 проповеди", callback_data="edit:3"),
            ],
            [
                InlineKeyboardButton(text="✏️4 обхват", callback_data="edit:4"),
                InlineKeyboardButton(text="✏️5 пост", callback_data="edit:5"),
                InlineKeyboardButton(text="✏️6 нужда", callback_data="edit:6"),
            ],
            [InlineKeyboardButton(text="✏️7 откровения", callback_data="edit:7")],
            [InlineKeyboardButton(text="❌ Отменить отчёт", callback_data="rep:cancel")],
        ]
    )


def cancel_report_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить отчёт", callback_data="rep:cancel")]]
    )


def with_cancel_kb(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *markup.inline_keyboard,
            [InlineKeyboardButton(text="❌ Отменить отчёт", callback_data="rep:cancel")],
        ]
    )


def add_rev_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data="addrev:cancel")]]
    )
