from __future__ import annotations

import html
import logging
import random
import re
from datetime import timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import selectinload

from app import groq_client
from app.config import get_settings
from app.database import get_session_factory
from app import keyboards as kb
from app.models import Revelation, RevelationArchive, Report, User
from app.states import HashtagStates, WeeklyReportStates
from app.time_utils import (
    digest_week_monday_on_digest_day,
    now_msk,
    streak_after_submit,
    submitted_on_time,
    week_start_from_date,
)

log = logging.getLogger(__name__)
router = Router()


class AddRevelationStates(StatesGroup):
    waiting_text = State()


HELP_NUMERIC = "\n\nВыберите кнопку или напишите число."
HELP_YESNO = "\n\nВыберите кнопку или напишите да или нет."

MSG_FINISH_OR_CANCEL = 'Сначала закончи шаг или нажми «Отменить отчёт».'

ERR_CHOOSE_NUMBER = "Выберите кнопку или напишите число."
ERR_YESNO_SHORT = "Выберите кнопку или напишите да или нет."


def _is_menu_press(text: str | None) -> bool:
    if text is None:
        return False
    return text in kb.MAIN_MENU_BUTTONS


def suggested_hashtag_from_user(from_user) -> str:
    last = (from_user.last_name or "").strip()
    first = (from_user.first_name or "").strip()
    username = (from_user.username or "").strip()
    base = f"{last}{first}".replace(" ", "") or username or "Имя"
    return re.sub(r"^[#]+", "", base)[:120] or "Имя"


async def ask_for_hashtag(message: Message, state: FSMContext, *, after: str = "menu") -> None:
    suggested = suggested_hashtag_from_user(message.from_user)
    await state.set_state(HashtagStates.waiting_hashtag)
    await state.update_data(after_hashtag=after)
    await message.answer(
        "<b>Как подписывать твои отчёты?</b>\n\n"
        f"Предлагаю: <code>#{html.escape(suggested)}</code>\n\n"
        "Если подходит — просто отправь это имя. Если хочешь иначе — напиши свой вариант одним сообщением.\n"
        "Например: <code>Артём</code> или <code>АртёмД</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.main_menu_kb(),
    )


def _parse_hours_for_sum(s: str) -> float:
    t = s.strip().replace(",", ".").removesuffix("+")
    try:
        return float(t)
    except ValueError:
        return 0.0


def parse_free_hours_to_q1(raw: str) -> str | None:
    t = raw.strip().lower().replace("часа", "").replace("часы", "").replace("ч.", "").strip()
    t = t.replace(",", ".").strip()
    if not t:
        return None
    if t.endswith("+"):
        try:
            float(t[:-1])
            return t.replace(" ", "")
        except ValueError:
            return None
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return t
    try:
        return str(float(t))
    except ValueError:
        pass
    return None


def parse_free_int_sermons_meet(raw: str) -> str | None:
    t = raw.strip().lower().replace(",", ".")
    if re.fullmatch(r"\d+", t):
        return t
    if re.fullmatch(r"\d+\+", t):
        return t
    return None


def parse_free_fast(raw: str) -> bool | None:
    x = raw.strip().lower()
    if x in ("да", "д", "yes", "y", "1"):  # noqa: SIM102
        return True
    if x in ("нет", "н", "no", "n", "0"):  # noqa: SIM102
        return False
    return None


def parse_free_plan_pct(raw: str) -> str | None:
    t = raw.strip().replace(",", ".").replace("%", "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", t):
        num = float(t)
        if 0 <= num <= 100:
            return f"{int(round(num))}%"
    return None


def _format_hours_display(s: str) -> str:
    v = s.strip().removesuffix("+").replace(".", ",")
    return f"{v} часа"


def _format_sermons_display(s: str) -> str:
    v = s.strip().removesuffix("+")
    if v.endswith("проповеди") or v.endswith("проповедей") or v.endswith("проповедь"):
        return v
    return f"{v} проповеди"


def build_report_message_html(user: User, d: dict) -> str:
    ht = html.escape(user.report_hashtag())
    q1_h = html.escape(_format_hours_display(d["q1"]))
    q2_h = html.escape(_format_sermons_display(str(d["q2"])))
    q3 = "Да" if d["q3"] else "Нет"
    q4_h = html.escape(d["q4"].strip())
    q5_plain = (d.get("q5") or "").strip() or "—"
    q5_h = html.escape(q5_plain)
    q6_h = html.escape(str(d["q6"]).strip().removesuffix("+"))
    q7_h = html.escape(str(d["q7"]).strip())
    q8_h = html.escape(d["q8"].strip())
    lines = [
        f"<b>{ht}</b>",
        "",
        "<b>1. Сколько времени потратили на личный духовный рост (часы молитвы)</b>",
        q1_h,
        "",
        "<b>2. Сколько проповедей посмотрел на неделе?</b>",
        q2_h,
        "",
        "<b>3. Постился ли на неделе?</b>",
        html.escape(q3),
        "",
        "<b>4. В чем мы как команда можем тебе помочь? (О чем молиться за тебя, в чем нуждаешься?)</b>",
        q4_h,
        "",
        "<b>5. Какие откровения получил на неделе?</b>",
        q5_h.replace("\r\n", "\n"),
        "",
        "<b>6. Сколько людей обхватил? (Личные встречи)</b>",
        q6_h,
        "",
        "<b>7. Процент выполнения плана прошедшей недели?</b>",
        q7_h,
        "",
        "<b>8. План на след неделю?</b>",
        q8_h,
    ]
    return "\n".join(lines)


async def is_allowed_member(bot: Bot, user_id: int) -> bool:
    settings = get_settings()
    if settings.group_chat_id == 0:
        return True
    try:
        m = await bot.get_chat_member(settings.group_chat_id, user_id)
    except Exception as e:
        log.warning("get_chat_member failed: %s", e)
        return False
    if m.status == ChatMemberStatus.LEFT or m.status == ChatMemberStatus.KICKED:
        return False
    return m.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
    )


async def get_or_create_user(session, from_user) -> User:
    tg_id = from_user.id
    u = await session.scalar(select(User).where(User.tg_user_id == tg_id))
    if u:
        u.tg_first_name = from_user.first_name
        u.tg_last_name = from_user.last_name
        u.tg_username = from_user.username
        return u
    u = User(
        tg_user_id=tg_id,
        tg_first_name=from_user.first_name,
        tg_last_name=from_user.last_name,
        tg_username=from_user.username,
        streak=0,
        is_bot_admin=False,
        created_at=now_msk(),
    )
    session.add(u)
    await session.flush()
    return u


async def replace_user_revelations_from_text(session, user_id: int, blob: str) -> None:
    await session.execute(delete(Revelation).where(Revelation.user_id == user_id))
    for line in blob.replace("\r", "").split("\n"):
        t = line.strip().lstrip("-•●").strip()
        if not t:
            continue
        session.add(Revelation(user_id=user_id, text=t, created_at=now_msk()))
    await session.flush()


async def replace_revelations_from_saved_q5(session, user_id: int, q5_blob: str) -> None:
    """Разобрать сохранённое поле отчёта (п.5) обратно в черновые строки откровений."""
    await session.execute(delete(Revelation).where(Revelation.user_id == user_id))
    blob = (q5_blob or "").strip()
    if not blob or blob == "—":
        await session.flush()
        return
    for line in blob.replace("\r", "").split("\n"):
        t = line.strip().lstrip("-•●").strip()
        if not t or t == "—":
            continue
        session.add(Revelation(user_id=user_id, text=t, created_at=now_msk()))
    await session.flush()


async def send_report_preview(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    data = await state.get_data()
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, from_user)
        rows = (
            await session.scalars(
                select(Revelation).where(Revelation.user_id == u.id).order_by(Revelation.created_at)
            )
        ).all()
        await session.commit()
    q5_text = "\n".join(f"- {r.text.strip()}" for r in rows) if rows else "—"
    merged = {**data, "q5": q5_text}
    await state.set_data(merged)
    await state.set_state(WeeklyReportStates.confirm)

    html_body = build_report_message_html(u, merged)
    await bot.send_message(chat_id, html_body, parse_mode=ParseMode.HTML)
    sd = await state.get_data()
    show_reset = sd.get("resubmit_report_id") is None
    await bot.send_message(
        chat_id,
        "Текст ниже уже как увидят братья. Нужно что-то поменять — жми ✏️ с номером. "
        'Всё устраивает — «Отправить».',
        reply_markup=kb.confirm_kb(show_reset=show_reset),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer(
            "Доступ только для участников группы. Попросите администратора добавить бота в чат и напишите из личной переписки."
        )
        return
    await state.clear()

    appointed_admin = False
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        has_admin = await session.scalar(select(func.count()).select_from(User).where(User.is_bot_admin.is_(True)))
        if has_admin == 0:
            u.is_bot_admin = True
            appointed_admin = True
        await session.commit()

    extra_admin = ""
    if appointed_admin:
        extra_admin = (
            "\n\nТы назначен администратором бота (правило: кто первый нажал /start — тот админ). "
            "Пока это зафиксировано в базе для будущих команд.\n\n"
        )

    tag_needed = not (u.report_hashtag_override or "").strip()
    if tag_needed:
        await ask_for_hashtag(message, state)
        return

    group_note = ""
    if get_settings().group_chat_id == 0:
        group_note = (
            "Внимание: сейчас тестовый режим без группы (GROUP_CHAT_ID=0).\n\n"
        )

    await message.answer(
        group_note + "Привет! Здесь ты заполняешь еженедельный отчёт и ведёшь заметки по откровениям. "
        "Напоминания в группу — по воскресеньям.\n\n"
        "Дедлайн серии «вовремя»: до полуночи понедельника (00:00 МСК), то есть до начала новой недели после отчётного воскресенья.\n\n"
        "Личное напоминание придёт только если ты уже написал боту /start."
        + extra_admin,
        reply_markup=kb.main_menu_kb(),
    )


@router.message(Command("chatid", "id"))
async def cmd_chat_id(message: Message):
    if message.chat.type == "private":
        await message.answer(f"ID этой лички: <code>{message.chat.id}</code>", parse_mode=ParseMode.HTML)
        return
    title = html.escape(message.chat.title or "чат")
    await message.answer(f"ID чата «{title}»: <code>{message.chat.id}</code>", parse_mode=ParseMode.HTML)


@router.message(StateFilter(HashtagStates.waiting_hashtag), F.text)
async def msg_hashtag_enter(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    if _is_menu_press(message.text):
        await ask_for_hashtag(message, state, after=(await state.get_data()).get("after_hashtag", "menu"))
        return
    raw = (message.text or "").strip()
    raw = re.sub(r"^[#]+", "", raw).strip()
    if len(raw) < 2:
        await message.answer("Слишком коротко — минимум 2 символа.")
        return
    if len(raw) > 120:
        raw = raw[:120]
    data = await state.get_data()
    after_hashtag = data.get("after_hashtag", "menu")
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        u.report_hashtag_override = raw
        await session.commit()
        tag_demo = u.report_hashtag()
    await state.clear()
    if after_hashtag == "write":
        await message.answer(f"Сохранено: {tag_demo}\n\nНачинаем отчёт.", reply_markup=kb.main_menu_kb())
        await push_report_step_1(message.chat.id, state, bot)
        return
    if after_hashtag == "edit_week":
        await message.answer(
            f"Сохранено: {tag_demo}\n\nТеперь нажми «✏️ Править отчёт недели».",
            reply_markup=kb.main_menu_kb(),
        )
        return
    await message.answer(f"Сохранено: {tag_demo}\n\nТеперь можно писать отчёт.", reply_markup=kb.main_menu_kb())


@router.message(Command("tag", "имя"))
async def cmd_tag(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        async with get_session_factory()() as session:
            u = await get_or_create_user(session, message.from_user)
            cur = u.report_hashtag_override or "(как в Telegram)"
            await session.commit()
        await message.answer(f"Сейчас в отчёте: {cur}\n\nЧтобы задать хэштег, напиши:\n/tag ШлыковМаксим")
        return
    raw = parts[1].strip()
    raw = re.sub(r"^[#]+", "", raw)
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        u.report_hashtag_override = raw[:120] if raw else None
        await session.commit()
    await state.clear()
    await message.answer(f"Готово. В отчётах будет: {u.report_hashtag()}")


@router.message(Command("admin"))
async def cmd_admin(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        admins = (await session.scalars(select(User).where(User.is_bot_admin.is_(True)))).all()
        await session.commit()
    if u.is_bot_admin:
        await message.answer("Ты администратор бота (первый /start).")
        return
    if not admins:
        await message.answer("Админ ещё не назначен — нажми /start первым в базе.")
        return
    a = admins[0]
    name = (a.tg_first_name or "") + " " + (a.tg_last_name or "")
    await message.answer(
        f"Администратор бота: {name.strip() or a.tg_username or a.tg_user_id}"
    )


@router.message(Command("circle"))
async def cmd_circle(message: Message, bot: Bot):
    """Сводка сохранённых откровений из отчётов (не сообщения общего Telegram-чата)."""
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    async with get_session_factory()() as session:
        u_admin = await get_or_create_user(session, message.from_user)
        await session.commit()
    if not u_admin.is_bot_admin:
        await message.answer("Эта команда только для назначенного администратора бота.")
        return

    async with get_session_factory()() as session:
        rows = (
            await session.scalars(
                select(Report)
                .options(selectinload(Report.user))
                .order_by(desc(Report.submitted_at))
                .limit(220)
            )
        ).all()

        seen: set[int] = set()
        blocks: list[str] = [
            "<b>Срез сохранённых откровений (последний отряд братьев в базе бота).</b>",
            "<i>Сообщения Telegram-бот не читает: это текст из сохранённых отчётов.</i>",
            "",
        ]

        added = 0
        max_brothers = 20
        for rp in rows:
            usr = rp.user
            uid = usr.id if usr else None
            if uid is None or uid in seen:
                continue
            seen.add(uid)
            ht = html.escape((usr.report_hashtag() if usr else "#?").strip())
            excerpt = html.escape(rp.q5_revelations.strip()[:520]).strip()
            blocks.append(f"<b>{ht}</b>\n{excerpt or '—'}")
            blocks.append("")
            added += 1
            if added >= max_brothers:
                break

        await session.commit()

    if added == 0:
        await message.answer("Пока нет сохранённых отчётов с откровениями.")
        return

    body_rest = "\n".join(blocks).strip()

    chunk_size = 3800
    for i in range(0, len(body_rest), chunk_size):
        await message.answer(body_rest[i : i + chunk_size], parse_mode=ParseMode.HTML)


async def push_report_step_1(chat_id: int, state: FSMContext, bot: Bot) -> None:
    await state.set_state(WeeklyReportStates.q1_prayer)
    await bot.send_message(
        chat_id,
        "1. Молитва — сколько часов было уделено за неделю?" + HELP_NUMERIC,
        reply_markup=kb.with_cancel_kb(kb.prayer_hours_kb()),
    )


async def can_start_report(bot: Bot, from_user) -> tuple[bool, str | None]:
    if not await is_allowed_member(bot, from_user.id):
        return False, "Нет доступа."
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, from_user)
        ws = week_start_from_date(now_msk().date())
        existing = await session.scalar(
            select(Report.id).where(Report.user_id == u.id, Report.week_start == ws)
        )
        await session.commit()
    if existing:
        return False, "Ты уже отправил отчёт за текущую неделю."
    return True, None


async def db_user_hashtag_filled(from_user) -> bool:
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, from_user)
        ok = bool((u.report_hashtag_override or "").strip())
        await session.commit()
    return ok


@router.message(F.text == kb.BTN_WRITE)
async def btn_write(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await db_user_hashtag_filled(message.from_user):
        await ask_for_hashtag(message, state, after="write")
        return
    ok, err = await can_start_report(bot, message.from_user)
    if not ok:
        await message.answer(err or "Не могу начать.")
        return
    await state.clear()
    await push_report_step_1(message.chat.id, state, bot)


@router.message(F.text == kb.BTN_EDIT_WEEK)
async def btn_edit_week(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    if not await db_user_hashtag_filled(message.from_user):
        await ask_for_hashtag(message, state, after="edit_week")
        return
    ws = week_start_from_date(now_msk().date())
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        rep = await session.scalar(
            select(Report).where(Report.user_id == u.id, Report.week_start == ws)
        )
        if rep:
            await replace_revelations_from_saved_q5(session, u.id, rep.q5_revelations)
        await session.commit()
    if not rep:
        await message.answer(
            "За текущую неделю ещё нет сохранённого отчёта — нажми «📋 Написать отчёт» или дождись ближайшего окна недели.",
        )
        return
    await state.clear()
    await state.update_data(
        q1=rep.q1_prayer_hours,
        q2=rep.q2_sermons,
        q3=rep.q3_fasted,
        q4=rep.q4_help,
        q6=rep.q6_meetings,
        q7=rep.q7_plan_pct,
        q8=rep.q8_next_plan.strip(),
        resubmit_report_id=rep.id,
        editing_field=None,
    )
    await state.set_state(WeeklyReportStates.confirm)
    await send_report_preview(bot, message.chat.id, message.from_user, state)


@router.callback_query(
    StateFilter(
        WeeklyReportStates.q1_prayer,
        WeeklyReportStates.q2_sermons,
        WeeklyReportStates.q3_fast,
        WeeklyReportStates.q4_help,
        WeeklyReportStates.q5_revelations,
        WeeklyReportStates.q6_meetings,
        WeeklyReportStates.q7_plan_pct,
        WeeklyReportStates.q8_next_plan,
        WeeklyReportStates.editing,
        WeeklyReportStates.confirm,
    ),
    F.data == "rep:cancel",
)
async def cb_cancel_report(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.answer("Отчёт отменён.", reply_markup=kb.main_menu_kb())
    await query.answer()


# --- переход после ответов (основная ветка + редактирование) ---
async def _after_q1(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing_field") == 1:
        await state.update_data(editing_field=None)
        await send_report_preview(bot, chat_id, from_user, state)
        return
    await state.set_state(WeeklyReportStates.q2_sermons)
    await bot.send_message(
        chat_id, "2. Проповеди — сколько посмотрел за неделю?" + HELP_NUMERIC, reply_markup=kb.with_cancel_kb(kb.sermons_kb())
    )


async def _after_q2(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing_field") == 2:
        await state.update_data(editing_field=None)
        await send_report_preview(bot, chat_id, from_user, state)
        return
    await state.set_state(WeeklyReportStates.q3_fast)
    await bot.send_message(chat_id, "3. Был ли пост на неделе?" + HELP_YESNO, reply_markup=kb.with_cancel_kb(kb.fast_kb()))


async def _after_q3(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing_field") == 3:
        await state.update_data(editing_field=None)
        await send_report_preview(bot, chat_id, from_user, state)
        return
    await state.set_state(WeeklyReportStates.q4_help)
    await bot.send_message(
        chat_id,
        "4. В чём братьям помолиться или помочь? Напиши одним сообщением.",
        reply_markup=kb.cancel_report_kb(),
    )


async def _after_q6(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing_field") == 6:
        await state.update_data(editing_field=None)
        await send_report_preview(bot, chat_id, from_user, state)
        return
    await state.set_state(WeeklyReportStates.q7_plan_pct)
    await bot.send_message(chat_id, "7. На сколько ты закрыл свой план на прошедшей неделе (%)?" + HELP_NUMERIC, reply_markup=kb.with_cancel_kb(kb.plan_pct_kb()))


async def _after_q7(bot: Bot, chat_id: int, from_user, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing_field") == 7:
        await state.update_data(editing_field=None)
        await send_report_preview(bot, chat_id, from_user, state)
        return
    await state.set_state(WeeklyReportStates.q8_next_plan)
    await bot.send_message(
        chat_id,
        "8. План на следующую неделю — впиши кратко, одним сообщением.",
        reply_markup=kb.cancel_report_kb(),
    )


@router.callback_query(StateFilter(WeeklyReportStates.q1_prayer, WeeklyReportStates.editing), F.data.startswith("q1:"))
async def cb_q1(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    st = await state.get_state()
    if st == WeeklyReportStates.editing:
        fld = (await state.get_data()).get("editing_field")
        if fld != 1:
            await query.answer("Сначала нажми этот же пункт во втором сообщении.", show_alert=False)
            return
    v = query.data.split(":", 1)[1]
    await state.update_data(q1=v)
    await query.message.edit_reply_markup(reply_markup=None)
    await _after_q1(bot, query.message.chat.id, query.from_user, state)
    await query.answer()


@router.callback_query(StateFilter(WeeklyReportStates.q2_sermons, WeeklyReportStates.editing), F.data.startswith("q2:"))
async def cb_q2(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    if await state.get_state() == WeeklyReportStates.editing:
        fld = (await state.get_data()).get("editing_field")
        if fld != 2:
            await query.answer()
            return
    v = query.data.split(":", 1)[1]
    await state.update_data(q2=v)
    await query.message.edit_reply_markup(reply_markup=None)
    await _after_q2(bot, query.message.chat.id, query.from_user, state)
    await query.answer()


@router.callback_query(StateFilter(WeeklyReportStates.q3_fast, WeeklyReportStates.editing), F.data.startswith("q3:"))
async def cb_q3(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    if await state.get_state() == WeeklyReportStates.editing:
        fld = (await state.get_data()).get("editing_field")
        if fld != 3:
            await query.answer()
            return
    yes = query.data.endswith("yes")
    await state.update_data(q3=yes)
    await query.message.edit_reply_markup(reply_markup=None)
    await _after_q3(bot, query.message.chat.id, query.from_user, state)
    await query.answer()


@router.message(StateFilter(WeeklyReportStates.q1_prayer), F.text)
async def msg_q1_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    v = parse_free_hours_to_q1(message.text or "")
    if v is None:
        await message.answer(ERR_CHOOSE_NUMBER)
        return
    await state.update_data(q1=v)
    await _after_q1(bot, message.chat.id, message.from_user, state)


@router.message(StateFilter(WeeklyReportStates.q2_sermons), F.text)
async def msg_q2_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    v = parse_free_int_sermons_meet(message.text or "")
    if v is None:
        await message.answer(ERR_CHOOSE_NUMBER)
        return
    await state.update_data(q2=v)
    await _after_q2(bot, message.chat.id, message.from_user, state)


@router.message(StateFilter(WeeklyReportStates.q3_fast), F.text)
async def msg_q3_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    pv = parse_free_fast(message.text or "")
    if pv is None:
        await message.answer(ERR_YESNO_SHORT)
        return
    await state.update_data(q3=pv)
    await _after_q3(bot, message.chat.id, message.from_user, state)


@router.message(StateFilter(WeeklyReportStates.q6_meetings), F.text)
async def msg_q6_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    v = parse_free_int_sermons_meet(message.text or "")
    if v is None:
        await message.answer(ERR_CHOOSE_NUMBER)
        return
    await state.update_data(q6=v)
    await _after_q6(bot, message.chat.id, message.from_user, state)


@router.message(StateFilter(WeeklyReportStates.q7_plan_pct), F.text)
async def msg_q7_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    v = parse_free_plan_pct(message.text or "")
    if v is None:
        await message.answer(ERR_CHOOSE_NUMBER)
        return
    await state.update_data(q7=v)
    await _after_q7(bot, message.chat.id, message.from_user, state)


@router.callback_query(StateFilter(WeeklyReportStates.q6_meetings, WeeklyReportStates.editing), F.data.startswith("q6:"))
async def cb_q6(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    if await state.get_state() == WeeklyReportStates.editing:
        fld = (await state.get_data()).get("editing_field")
        if fld != 6:
            await query.answer()
            return
    v = query.data.split(":", 1)[1]
    await state.update_data(q6=v)
    await query.message.edit_reply_markup(reply_markup=None)
    await _after_q6(bot, query.message.chat.id, query.from_user, state)
    await query.answer()


@router.callback_query(StateFilter(WeeklyReportStates.q7_plan_pct, WeeklyReportStates.editing), F.data.startswith("q7:"))
async def cb_q7(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    if await state.get_state() == WeeklyReportStates.editing:
        fld = (await state.get_data()).get("editing_field")
        if fld != 7:
            await query.answer()
            return
    raw = query.data.split(":", 1)[1]
    v = f"{raw}%"
    await state.update_data(q7=v)
    await query.message.edit_reply_markup(reply_markup=None)
    await _after_q7(bot, query.message.chat.id, query.from_user, state)
    await query.answer()


@router.message(StateFilter(WeeklyReportStates.q4_help), F.text)
async def msg_q4(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    await state.update_data(q4=(message.text or "").strip())
    await state.set_state(WeeklyReportStates.q5_revelations)
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        rows = (
            await session.scalars(
                select(Revelation).where(Revelation.user_id == u.id).order_by(Revelation.created_at)
            )
        ).all()
        await session.commit()

    bullets = "\n".join(f"- {r.text.strip()}" for r in rows) if rows else "(пока пусто)"
    await message.answer(
        "5. Откровения за неделю.\n\nСейчас в заметках:\n"
        + bullets
        + "\n\nПришли текстом пока нужно (несколько сообщений можно) или нажми «Готово».",
        reply_markup=kb.with_cancel_kb(kb.q5_done_kb()),
    )


@router.message(StateFilter(WeeklyReportStates.q5_revelations), F.text)
async def msg_q5_add(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer('На этом шаге жми «Готово», «Отменить отчёт» или продолжай текстом.')
        return
    t = (message.text or "").strip()
    if not t:
        return
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        session.add(Revelation(user_id=u.id, text=t, created_at=now_msk()))
        await session.commit()
    await message.answer("Записал. Ещё текстом или «Готово».", reply_markup=kb.with_cancel_kb(kb.q5_done_kb()))


@router.callback_query(StateFilter(WeeklyReportStates.q5_revelations), F.data == "q5:done")
async def cb_q5_done(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    await state.set_state(WeeklyReportStates.q6_meetings)
    await query.message.answer(
        "6. Сколько братьев обхватил личными встречами?" + HELP_NUMERIC, reply_markup=kb.with_cancel_kb(kb.meetings_kb())
    )
    await query.answer()


@router.message(StateFilter(WeeklyReportStates.editing), F.text)
async def msg_editing_free(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer(MSG_FINISH_OR_CANCEL)
        return
    d = await state.get_data()
    n = d.get("editing_field")
    if n is None:
        return
    text = (message.text or "").strip()
    chat_id = message.chat.id
    fu = message.from_user

    if n == 1:
        v = parse_free_hours_to_q1(text)
        if v is None:
            await message.answer(ERR_CHOOSE_NUMBER)
            return
        await state.update_data(q1=v, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 2:
        v = parse_free_int_sermons_meet(text)
        if v is None:
            await message.answer(ERR_CHOOSE_NUMBER)
            return
        await state.update_data(q2=v, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 3:
        pv = parse_free_fast(text)
        if pv is None:
            await message.answer(ERR_YESNO_SHORT)
            return
        await state.update_data(q3=pv, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 4:
        await state.update_data(q4=text, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 5:
        async with get_session_factory()() as session:
            u = await get_or_create_user(session, fu)
            await replace_user_revelations_from_text(session, u.id, text)
            await session.commit()
        await state.update_data(editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 6:
        v = parse_free_int_sermons_meet(text)
        if v is None:
            await message.answer(ERR_CHOOSE_NUMBER)
            return
        await state.update_data(q6=v, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 7:
        v = parse_free_plan_pct(text)
        if v is None:
            await message.answer(ERR_CHOOSE_NUMBER)
            return
        await state.update_data(q7=v, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)
        return
    if n == 8:
        await state.update_data(q8=text, editing_field=None)
        await state.set_state(WeeklyReportStates.confirm)
        await send_report_preview(bot, chat_id, fu, state)


@router.callback_query(StateFilter(WeeklyReportStates.confirm), F.data.startswith("edit:"))
async def cb_confirm_edit(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    n = int(query.data.split(":", 1)[1])
    await query.answer()
    await state.update_data(editing_field=n)
    await state.set_state(WeeklyReportStates.editing)
    if n == 1:
        await query.message.answer(
            "✏️ Пункт 1 — часы молитвы." + HELP_NUMERIC,
            reply_markup=kb.with_cancel_kb(kb.prayer_hours_kb()),
        )
    elif n == 2:
        await query.message.answer(
            "✏️ Пункт 2 — проповеди." + HELP_NUMERIC,
            reply_markup=kb.with_cancel_kb(kb.sermons_kb()),
        )
    elif n == 3:
        await query.message.answer(
            "✏️ Пункт 3 — пост." + HELP_YESNO,
            reply_markup=kb.with_cancel_kb(kb.fast_kb()),
        )
    elif n == 4:
        await query.message.answer(
            "✏️ Пункт 4 — молитвенная нужда. Одним сообщением:",
            reply_markup=kb.cancel_report_kb(),
        )
    elif n == 5:
        async with get_session_factory()() as session:
            u = await get_or_create_user(session, query.from_user)
            rows = (
                await session.scalars(
                    select(Revelation).where(Revelation.user_id == u.id).order_by(Revelation.created_at)
                )
            ).all()
            await session.commit()
        cur = "\n".join(r.text.strip() for r in rows)
        snippet = ("Сейчас:\n" + cur[:800] + ("\n…" if len(cur) > 800 else "")) if cur else "Пока строк нет."
        await query.message.answer(
            "✏️ Пункт 5 — откровения. Пришли одним сообщением: каждая новая строка — отдельная заметка.\n\n" + snippet,
            reply_markup=kb.cancel_report_kb(),
        )
    elif n == 6:
        await query.message.answer(
            "✏️ Пункт 6 — встречи." + HELP_NUMERIC,
            reply_markup=kb.with_cancel_kb(kb.meetings_kb()),
        )
    elif n == 7:
        await query.message.answer(
            "✏️ Пункт 7 — % плана." + HELP_NUMERIC,
            reply_markup=kb.with_cancel_kb(kb.plan_pct_kb()),
        )
    elif n == 8:
        await query.message.answer(
            "✏️ Пункт 8 — план на неделю. Одним сообщением:",
            reply_markup=kb.cancel_report_kb(),
        )
    else:
        await query.message.answer("Неизвестный номер пункта.")


@router.message(StateFilter(WeeklyReportStates.q8_next_plan), F.text)
async def msg_q8(message: Message, state: FSMContext, bot: Bot):
    if _is_menu_press(message.text):
        await message.answer('Закончи ответ пункта 8 или нажми «Отменить отчёт».')
        return
    await state.update_data(q8=(message.text or "").strip())
    await state.set_state(WeeklyReportStates.confirm)
    await send_report_preview(bot, message.chat.id, message.from_user, state)


@router.callback_query(StateFilter(WeeklyReportStates.confirm), F.data == "cf:reset")
async def cb_confirm_reset(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user:
        await query.answer()
        return
    d = await state.get_data()
    if d.get("resubmit_report_id"):
        await query.answer(
            "Это уже сохранённый отчёт недели — меняй пункты через ✏️ или отправь обновление кнопкой «Отправить».",
            show_alert=True,
        )
        return
    ok, err = await can_start_report(bot, query.from_user)
    if not ok:
        await query.answer(err or "Нельзя начать заново.", show_alert=True)
        return
    await query.answer()
    await state.clear()
    await query.message.answer("Окей, заново составим отчёт.")
    await push_report_step_1(query.message.chat.id, state, bot)


@router.callback_query(StateFilter(WeeklyReportStates.confirm), F.data == "cf:send")
async def cb_confirm_send(query: CallbackQuery, state: FSMContext, bot: Bot):
    if not query.from_user or query.message is None:
        await query.answer()
        return
    if not await is_allowed_member(bot, query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return

    data = await state.get_data()
    required = ("q1", "q2", "q3", "q4", "q6", "q7", "q8")
    if not all(k in data for k in required):
        await query.answer("Чего-то не хватает в черновике.", show_alert=True)
        return

    submitted_at = now_msk()
    week_start = week_start_from_date(now_msk().date())
    on_time = submitted_on_time(submitted_at, week_start)
    resubmit_rid = data.get("resubmit_report_id")

    settings = get_settings()
    is_update = False

    async with get_session_factory()() as session:
        u = await get_or_create_user(session, query.from_user)
        existing = await session.scalar(
            select(Report).where(Report.user_id == u.id, Report.week_start == week_start)
        )

        if existing:
            if resubmit_rid and resubmit_rid == existing.id:
                is_update = True
            elif resubmit_rid:
                await session.commit()
                await query.answer("Перезапусти правку («Править отчёт недели» заново).", show_alert=True)
                await state.clear()
                return
            else:
                await session.commit()
                await query.answer("Эта неделя уже закрыта в базе.", show_alert=True)
                await state.clear()
                return
        elif resubmit_rid:
            await session.commit()
            await query.answer("Черновик устарел — открой «Править отчёт недели» заново.", show_alert=True)
            await state.clear()
            return

        rev_rows = (
            await session.scalars(
                select(Revelation).where(Revelation.user_id == u.id).order_by(Revelation.created_at)
            )
        ).all()
        q5_final = "\n".join(f"- {r.text.strip()}" for r in rev_rows) if rev_rows else "—"

        if is_update:
            await session.execute(
                delete(RevelationArchive).where(
                    RevelationArchive.user_id == u.id,
                    RevelationArchive.report_week_start == week_start,
                )
            )

        for r in rev_rows:
            session.add(
                RevelationArchive(
                    user_id=u.id,
                    text=r.text,
                    report_week_start=week_start,
                    created_at=r.created_at,
                )
            )

        if rev_rows:
            await session.execute(delete(Revelation).where(Revelation.user_id == u.id))

        if is_update:
            existing.q1_prayer_hours = data["q1"]
            existing.q2_sermons = data["q2"]
            existing.q3_fasted = bool(data["q3"])
            existing.q4_help = data["q4"]
            existing.q5_revelations = q5_final
            existing.q6_meetings = data["q6"]
            existing.q7_plan_pct = data["q7"]
            existing.q8_next_plan = data["q8"]
            existing.submitted_at = submitted_at
            existing.on_time = on_time
            new_streak = u.streak
        else:
            session.add(
                Report(
                    user_id=u.id,
                    week_start=week_start,
                    q1_prayer_hours=data["q1"],
                    q2_sermons=data["q2"],
                    q3_fasted=bool(data["q3"]),
                    q4_help=data["q4"],
                    q5_revelations=q5_final,
                    q6_meetings=data["q6"],
                    q7_plan_pct=data["q7"],
                    q8_next_plan=data["q8"],
                    submitted_at=submitted_at,
                    on_time=on_time,
                )
            )
            new_streak = streak_after_submit(u.streak, u.last_report_week_start, week_start, on_time)
            u.streak = new_streak
            u.last_report_week_start = week_start

        await session.commit()

    out_data = {**data, "q5": q5_final}

    tg_user_id_sent = query.from_user.id
    txt_html = build_report_message_html(u, out_data)

    await query.message.edit_reply_markup(reply_markup=None)
    await state.clear()

    if settings.group_chat_id == 0:
        await bot.send_message(tg_user_id_sent, txt_html, parse_mode=ParseMode.HTML)
        sent_note = (
            "Отчёт недели обновлён (режим без общего чата — копия только тебе)."
            if is_update
            else "Отчёт сохранён. В общий чат не отправлен (режим без GROUP_CHAT_ID)."
        )
    else:
        await bot.send_message(settings.group_chat_id, txt_html, parse_mode=ParseMode.HTML)
        sent_note = (
            "Обновлённый отчёт снова отправлен братьям в общий чат." if is_update else "Отчёт отправлен братьям в общий чат."
        )

    if is_update:
        streak_line = (
            f"Серия «вовремя» при правке этой недели не пересчитывается: сейчас {new_streak} нед. подряд."
        )
    elif on_time:
        streak_line = (
            f"Серия «вовремя»: {new_streak} нед. подряд. Господь благословит упорство."
        )
    else:
        streak_line = (
            "Отчёт принят — серию «вовремя» ты прервал на этой неделе или ещё набирал. "
            "На новой неделе можно заново набрать сильную серию."
        )

    await query.message.answer(f"{sent_note}\n\n{streak_line}", reply_markup=kb.main_menu_kb())
    await query.answer()

    try:
        ref = await groq_client.get_reflection(data["q4"], q5_final)
        if ref:
            await bot.send_message(
                tg_user_id_sent,
                f"<b>Напутствие</b>\n\n{html.escape(ref)}",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        log.exception("reflection Groq")


@router.message(F.text == kb.BTN_PRAYER)
async def btn_prayer_list(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    ws = week_start_from_date(now_msk().date())
    async with get_session_factory()() as session:
        reports = (
            await session.scalars(
                select(Report).where(Report.week_start == ws).options(selectinload(Report.user))
            )
        ).all()
        await session.commit()
    if not reports:
        await message.answer(
            "Пока ни у кого нет отправленного отчёта за этот недельный круг или данные временно недоступны.",
        )
        return
    lines: list[str] = [
        f"<b>Молитвенный лист</b>\nОтчётная неделя с понедельника <code>{ws.isoformat()}</code>.\n<i>Открытые строки нужд из отчётов.</i>",
    ]
    for r in sorted(reports, key=lambda x: x.user.report_hashtag().lower()):
        name_plain = html.escape(r.user.report_hashtag().strip("# ").strip())
        need = html.escape(r.q4_help.strip())
        lines.append("")
        lines.append("<b>" + name_plain + "</b>")
        lines.append("")
        lines.append(need + "\n")
    body = "\n".join(lines)[:3960]
    await message.answer(body, parse_mode=ParseMode.HTML)


@router.message(F.text == kb.BTN_ADD_REV)
async def btn_add_rev(message: Message, state: FSMContext, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await state.set_state(AddRevelationStates.waiting_text)
    await message.answer(
        "Откровение недели — одним сообщением. Прервать — кнопка «Отменить» ниже.",
        reply_markup=kb.add_rev_cancel_kb(),
    )


@router.callback_query(StateFilter(AddRevelationStates.waiting_text), F.data == "addrev:cancel")
async def cb_addrev_cancel(query: CallbackQuery, state: FSMContext):
    await state.clear()
    if query.message:
        await query.message.answer("Добавление откровения отменено.", reply_markup=kb.main_menu_kb())
    await query.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await message.answer(
        "Отмена только кнопкой: при отчёте — «❌ Отменить отчёт», "
        "при добавлении откровения — «❌ Отменить»."
    )


@router.message(StateFilter(AddRevelationStates.waiting_text), F.text)
async def add_rev_text(message: Message, state: FSMContext, bot: Bot):
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return
    t = message.text.strip()
    if not t:
        return
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        session.add(Revelation(user_id=u.id, text=t, created_at=now_msk()))
        await session.commit()
    await state.clear()
    await message.answer("Добавил в список «откровения недели», пока ты не отправишь отчёт.", reply_markup=kb.main_menu_kb())


@router.message(F.text == kb.BTN_MY_REV)
async def btn_my_rev(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        rows = (
            await session.scalars(
                select(Revelation).where(Revelation.user_id == u.id).order_by(Revelation.created_at)
            )
        ).all()
        await session.commit()

    if not rows:
        await message.answer("Пока записей нет — можно добавить кнопкой «Добавить откровение» или в самом заполнении отчёта.")
        return
    text = "\n\n".join(f"• {r.text.strip()}" for r in rows)
    await message.answer("Откровения недели (ещё без отправленного отчёта):\n\n" + text[:3900])


@router.message(F.text == kb.BTN_ARCHIVE)
async def btn_archive(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    async with get_session_factory()() as session:
        u = await get_or_create_user(session, message.from_user)
        rows = (
            await session.scalars(
                select(RevelationArchive)
                .where(RevelationArchive.user_id == u.id)
                .order_by(RevelationArchive.created_at.desc())
                .limit(30)
            )
        ).all()
        await session.commit()

    if not rows:
        await message.answer("Архив откровений будет после первых отправленных отчётов.")
        return
    parts = []
    for r in reversed(rows):
        parts.append(f"({r.report_week_start.isoformat()})\n{r.text.strip()}")
    body = "\n\n".join(parts)
    await message.answer("Архив (до 30 последних заметок из отчётов):\n\n" + body[:3900])


@router.message(F.text == kb.BTN_STATS)
async def btn_stats(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    if not await is_allowed_member(bot, message.from_user.id):
        await message.answer("Нет доступа.")
        return
    async with get_session_factory()() as session:
        u_db = await get_or_create_user(session, message.from_user)
        four_weeks_ago = now_msk().date() - timedelta(weeks=4)
        reports = (
            await session.scalars(
                select(Report)
                .where(Report.user_id == u_db.id, Report.week_start >= four_weeks_ago)
                .order_by(Report.week_start.desc())
            )
        ).all()
        await session.commit()

    if not reports:
        await message.answer("Подожди хотя бы пару сохранённых отчётов — тогда смогу свести простую сводку.")
        return

    n = len(reports)
    hours = [_parse_hours_for_sum(r.q1_prayer_hours) for r in reports]
    sermons = [_parse_hours_for_sum(r.q2_sermons) for r in reports]
    meetings = [_parse_hours_for_sum(r.q6_meetings) for r in reports]
    pct: list[float] = []
    for r in reports:
        p = r.q7_plan_pct.strip().rstrip("%").replace(",", ".")
        try:
            pct.append(float(p))
        except ValueError:
            pass

    def avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    on_time_n = sum(1 for r in reports if r.on_time)

    plain_block = (
        f"Отчётов учтено: {n}\n"
        f"Молитва в среднем: {avg(hours):.1f} ч за нед.\n"
        f"Проповедей в среднем: {avg(sermons):.1f}\n"
        f"Личные встречи в среднем: {avg(meetings):.1f}\n"
        + (f"% плана в среднем: {avg(pct):.0f}\n" if pct else "")
        + f'Вовремя сдано раз: {on_time_n} из {n}\nСерия «вовремя» сейчас: {u_db.streak}'
    )

    hdr = '<b>Статистика последних недель</b>\n\n'
    out = hdr + html.escape(plain_block)
    commentary = await groq_client.get_analytics_comment(plain_block, user_id=u_db.tg_user_id)
    if commentary:
        out += "\n\n" + html.escape(commentary.strip())
    await message.answer(out, parse_mode=ParseMode.HTML)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


async def job_sunday_group_reminder(bot: Bot) -> None:
    try:
        settings = get_settings()
        if settings.group_chat_id == 0:
            log.info("skip group reminder: GROUP_CHAT_ID=0")
            return
        await bot.send_message(
            settings.group_chat_id,
            "Братья, пора снова поделиться тихой отчётностью. Напиши боту в личку /start.",
        )
    except Exception:
        log.exception("job_sunday_group_reminder failed")


async def job_sunday_dm_reminder(bot: Bot) -> None:
    try:
        ws = week_start_from_date(now_msk().date())

        async with get_session_factory()() as session:
            users = (await session.scalars(select(User))).all()
            pending_ids: list[int] = []
            for u in users:
                has = await session.scalar(
                    select(Report.id).where(Report.user_id == u.id, Report.week_start == ws)
                )
                if not has:
                    pending_ids.append(u.tg_user_id)
            await session.commit()

        for tg_id in pending_ids:
            if not await is_allowed_member(bot, tg_id):
                continue
            try:
                await bot.send_message(
                    tg_id,
                    "Напоминание: ты ещё не закрыл отчёт недели — если отправишь до ночи понедельника по Москве ровно до 00:00, серию «вовремя» ты продолжаешь.",
                )
            except Exception as e:
                log.warning("DM failed %s: %s", tg_id, e)
    except Exception:
        log.exception("job_sunday_dm_reminder failed")


async def job_monday_digest(bot: Bot) -> None:
    try:
        settings = get_settings()
        if settings.group_chat_id == 0:
            log.info("skip monday digest: GROUP_CHAT_ID=0")
            return
        monday = now_msk().date()
        ws = digest_week_monday_on_digest_day(monday)

        async with get_session_factory()() as session:
            reports = (
                await session.scalars(
                    select(Report).where(Report.week_start == ws).options(selectinload(Report.user))
                )
            ).all()
            total_hours = sum(_parse_hours_for_sum(r.q1_prayer_hours) for r in reports)
            await session.commit()

        if not reports:
            await bot.send_message(settings.group_chat_id, f"{ws.isoformat()} — пока ни одного отчёта к обзору.")
            return

        q5_pool: list[str] = []
        q5_chunks: list[str] = []
        for r in reports:
            q5_chunks.append(r.q5_revelations.strip())
            for line in r.q5_revelations.replace("\r", "").split("\n"):
                line = line.strip().lstrip("-•").strip()
                if len(line) >= 20:
                    q5_pool.append(line)

        random.shuffle(q5_chunks)
        merged_q5 = "\n\n---\n\n".join(q5_chunks)[:3000]
        ai_line = await groq_client.get_digest_summary(merged_q5) if merged_q5.strip() else None

        part_names = sorted(
            {r.user.report_hashtag().strip("#").strip() for r in reports}, key=lambda s: s.lower()
        )

        intro = (
            f"<b>Дайджест недели</b> (с {ws.isoformat()})\n\n"
            f"Отчётов: {len(reports)} | суммарно молитва (как сами написали): {total_hours:.1f} ч\n\n"
            f"<b>Братья в отчёте:</b> {html.escape(', '.join(part_names))}\n"
        )

        tail = ""
        if ai_line:
            tail = "\n\n<b>Строка поддержки недели</b>\n<i>" + html.escape(ai_line) + "</i>"
        elif q5_pool:
            pick = random.choice(q5_pool)
            tail = (
                '\n\n<b>Случайный фрагмент откровения</b> «'
                + html.escape(pick[:480])
                + ("»…" if len(pick) > 480 else "»")
            )

        await bot.send_message(settings.group_chat_id, intro + tail, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("job_monday_digest failed")
