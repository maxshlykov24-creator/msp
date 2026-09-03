"""All Telegram handlers: pairing, question flow, moderator controls."""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update

from app.config import get_settings
from app.database import get_session_factory
from app.keyboards import (
    EMPTY_KB,
    deck_keyboard,
    moderator_active_keyboard,
    moderator_waiting_keyboard,
    participant_question_keyboard,
    participant_ready_keyboard,
    preset_keyboard,
    role_keyboard,
)
from app.models import DeckLedger, GameSession, SessionQuestion
from app.questions import build_queue, get_question_by_number, get_questions, phase_name, tag_emoji

log = logging.getLogger(__name__)
router = Router()


# ─── FSM ──────────────────────────────────────────────────────────────────────

class CodeState(StatesGroup):
    waiting_for_code = State()


# ─── DB helpers ───────────────────────────────────────────────────────────────

def sf():
    return get_session_factory()


async def _session_by_mod(mod_chat_id: int) -> GameSession | None:
    async with sf()() as db:
        r = await db.execute(
            select(GameSession)
            .where(GameSession.moderator_chat_id == mod_chat_id)
            .where(GameSession.status != "finished")
            .order_by(GameSession.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _session_by_part(part_chat_id: int) -> GameSession | None:
    async with sf()() as db:
        r = await db.execute(
            select(GameSession)
            .where(GameSession.participant_chat_id == part_chat_id)
            .where(GameSession.status != "finished")
            .order_by(GameSession.created_at.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _session_by_code(code: str) -> GameSession | None:
    async with sf()() as db:
        r = await db.execute(
            select(GameSession)
            .where(GameSession.code == code)
            .where(GameSession.participant_chat_id.is_(None))
            .where(GameSession.status == "waiting_participant")
        )
        return r.scalar_one_or_none()


async def _reload_session(session_id: int) -> GameSession:
    async with sf()() as db:
        r = await db.execute(select(GameSession).where(GameSession.id == session_id))
        return r.scalar_one()


async def _used_numbers(deck_id: str) -> set[int]:
    async with sf()() as db:
        r = await db.execute(
            select(DeckLedger.question_number).where(DeckLedger.deck_id == deck_id)
        )
        return {row[0] for row in r.all()}


async def _used_count(deck_id: str) -> int:
    async with sf()() as db:
        r = await db.execute(
            select(func.count()).select_from(DeckLedger).where(DeckLedger.deck_id == deck_id)
        )
        return r.scalar_one()


async def _progress(session_id: int) -> tuple[int, int, int]:
    """(done_count, pending_count, phase)"""
    async with sf()() as db:
        done = (await db.execute(
            select(func.count()).select_from(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status.in_(["done", "skipped"]))
        )).scalar_one()
        pending = (await db.execute(
            select(func.count()).select_from(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status == "pending")
        )).scalar_one()
        phase = (await db.execute(
            select(GameSession.phase).where(GameSession.id == session_id)
        )).scalar_one_or_none() or 1
    return done, pending, phase


async def _has_closed_questions(session_id: int) -> bool:
    async with sf()() as db:
        r = await db.execute(
            select(func.count()).select_from(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status.in_(["done", "skipped"]))
        )
        return r.scalar_one() > 0


async def _last_closed(session_id: int) -> SessionQuestion | None:
    async with sf()() as db:
        r = await db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status.in_(["done", "skipped"]))
            .order_by(SessionQuestion.order_index.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _shown_question(session_id: int) -> SessionQuestion | None:
    async with sf()() as db:
        r = await db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status == "shown")
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _next_pending(session_id: int) -> SessionQuestion | None:
    async with sf()() as db:
        r = await db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session_id)
            .where(SessionQuestion.status == "pending")
            .order_by(SessionQuestion.order_index)
            .limit(1)
        )
        return r.scalar_one_or_none()


# ─── Message send / edit helpers ──────────────────────────────────────────────

async def _try_edit(
    bot: Bot,
    chat_id: int | None,
    msg_id: int | None,
    text: str,
    keyboard,
) -> int | None:
    """Edit existing message, or send new one. Returns message_id or None."""
    if chat_id is None:
        return None
    kb = keyboard if keyboard is not None else EMPTY_KB
    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return msg_id
        except Exception:
            pass
    try:
        msg = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        return msg.message_id
    except Exception as e:
        log.warning("send_message failed to %s: %s", chat_id, e)
        return None


async def _save_msg_ids(session_id: int, mod_id: int | None, part_id: int | None) -> None:
    values = {}
    if mod_id is not None:
        values["mod_msg_id"] = mod_id
    if part_id is not None:
        values["part_msg_id"] = part_id
    if not values:
        return
    async with sf()() as db:
        await db.execute(update(GameSession).where(GameSession.id == session_id).values(**values))
        await db.commit()


# ─── Screen builders ──────────────────────────────────────────────────────────

async def _render_moderator_active(bot: Bot, session: GameSession, sq: SessionQuestion) -> None:
    q = get_question_by_number(sq.question_number)
    if not q:
        return
    done, pending, _ = await _progress(session.id)
    has_undo = False  # no undo while question is active

    text = (
        f"📌 <b>Активный вопрос</b> · #{q.number} [{q.tag}]\n"
        f"<i>{q.category}</i>\n\n"
        f"<b>{q.text}</b>\n\n"
        f"───────────────\n"
        f"Сыграно: {done} · Осталось: {pending} · {phase_name(sq.phase_label)}"
    )
    new_id = await _try_edit(bot, session.moderator_chat_id, session.mod_msg_id, text,
                             moderator_active_keyboard(has_undo))
    if new_id and new_id != session.mod_msg_id:
        await _save_msg_ids(session.id, mod_id=new_id, part_id=None)


async def _render_moderator_waiting(bot: Bot, session: GameSession) -> None:
    done, pending, phase = await _progress(session.id)
    has_undo = await _has_closed_questions(session.id)

    text = (
        f"⏳ <b>Ожидаем следующий вопрос...</b>\n\n"
        f"Сыграно: {done} · Осталось: {pending} · {phase_name(phase)}"
    )
    new_id = await _try_edit(bot, session.moderator_chat_id, session.mod_msg_id, text,
                             moderator_waiting_keyboard(has_undo))
    if new_id and new_id != session.mod_msg_id:
        await _save_msg_ids(session.id, mod_id=new_id, part_id=None)


async def _render_participant_question(bot: Bot, session: GameSession, sq: SessionQuestion) -> None:
    q = get_question_by_number(sq.question_number)
    if not q:
        return
    emoji = tag_emoji(q.tag)
    text = (
        f"{emoji} <b>Вопрос #{q.number}</b>\n\n"
        f"<b>{q.text}</b>\n\n"
        f"⏳ <i>Ожидаем модератора...</i>"
    )
    new_id = await _try_edit(bot, session.participant_chat_id, session.part_msg_id, text,
                             participant_question_keyboard(not session.replace_used))
    if new_id and new_id != session.part_msg_id:
        await _save_msg_ids(session.id, mod_id=None, part_id=new_id)


async def _render_participant_ready(bot: Bot, session: GameSession) -> None:
    text = "✅ <b>Готово!</b>\n\nПередай телефон следующему и нажми кнопку."
    new_id = await _try_edit(bot, session.participant_chat_id, session.part_msg_id, text,
                             participant_ready_keyboard())
    if new_id and new_id != session.part_msg_id:
        await _save_msg_ids(session.id, mod_id=None, part_id=new_id)


async def _show_question_to_both(bot: Bot, session: GameSession, sq: SessionQuestion) -> None:
    await _render_participant_question(bot, session, sq)
    session = await _reload_session(session.id)  # refresh after part_msg_id save
    await _render_moderator_active(bot, session, sq)
    async with sf()() as db:
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(phase=sq.phase_label)
        )
        await db.commit()


# ─── /start and /reset ────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    chat_id = message.chat.id

    mod_sess = await _session_by_mod(chat_id)
    if mod_sess:
        msg = await message.answer("🔄 Восстанавливаю твою сессию модератора...")
        await _save_msg_ids(mod_sess.id, mod_id=msg.message_id, part_id=None)
        mod_sess = await _reload_session(mod_sess.id)
        if mod_sess.status == "active":
            await _render_moderator_waiting(message.bot, mod_sess)
        else:
            await message.bot.edit_message_text(
                chat_id=chat_id, message_id=msg.message_id,
                text=(
                    f"🎯 <b>Активная сессия</b>\n"
                    f"Код: <code>{mod_sess.code}</code>\n"
                    f"Статус: {mod_sess.status}\n\n"
                    f"Используй /reset чтобы завершить."
                ),
                parse_mode="HTML",
            )
        return

    part_sess = await _session_by_part(chat_id)
    if part_sess:
        msg = await message.answer("🔄 Восстанавливаю твою сессию экрана...")
        await _save_msg_ids(part_sess.id, mod_id=None, part_id=msg.message_id)
        part_sess = await _reload_session(part_sess.id)
        if part_sess.status == "active":
            if part_sess.current_question_number is not None:
                sq = await _shown_question(part_sess.id)
                if sq:
                    await _render_participant_question(message.bot, part_sess, sq)
                    return
            await _render_participant_ready(message.bot, part_sess)
        else:
            await message.bot.edit_message_text(
                chat_id=chat_id, message_id=msg.message_id,
                text="⏳ Ожидаем настройку от модератора...",
                parse_mode="HTML",
            )
        return

    msg = await message.answer(
        "👋 <b>Вопросы ДГ</b>\n\nВыберите роль:",
        reply_markup=role_keyboard(),
        parse_mode="HTML",
    )
    await state.update_data(start_msg_id=msg.message_id)


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    mod_sess = await _session_by_mod(chat_id)
    if mod_sess:
        async with sf()() as db:
            await db.execute(
                update(GameSession).where(GameSession.id == mod_sess.id).values(status="finished")
            )
            await db.commit()
        await message.answer("🔄 Сессия завершена. Нажми /start для новой.")
    else:
        await message.answer("Нет активной сессии. Нажми /start.")
    await state.clear()


async def _format_played_report() -> str:
    deck_id = get_settings().deck_id
    total = len(get_questions())

    async with sf()() as db:
        ledger_rows = (await db.execute(
            select(DeckLedger)
            .where(DeckLedger.deck_id == deck_id)
            .order_by(DeckLedger.used_at)
        )).scalars().all()

        in_progress = (await db.execute(
            select(SessionQuestion, GameSession)
            .join(GameSession, SessionQuestion.session_id == GameSession.id)
            .where(GameSession.status == "active")
            .where(SessionQuestion.status.in_(["done", "skipped", "shown"]))
            .order_by(SessionQuestion.order_index)
        )).all()

    lines = [
        "📊 <b>Сыгранные вопросы</b>",
        "",
        f"Колода: <b>{len(ledger_rows)}</b> из {total} записано навсегда",
        "<i>(после «Завершить встречу»)</i>",
    ]

    if ledger_rows:
        lines.append("")
        for row in ledger_rows:
            q = get_question_by_number(row.question_number)
            if not q:
                continue
            date = row.used_at.strftime("%d.%m.%Y") if row.used_at else "?"
            lines.append(f"• #{q.number} [{q.tag}] — {q.text[:80]}{'…' if len(q.text) > 80 else ''}")
            lines.append(f"  <i>{date}</i>")
    else:
        lines.append("")
        lines.append("Пока ни одной завершённой встречи — колода пуста.")

    if in_progress:
        lines.append("")
        lines.append("🔄 <b>Текущая встреча</b> (ещё не в колоде):")
        for sq, sess in in_progress:
            q = get_question_by_number(sq.question_number)
            if not q:
                continue
            mark = {"done": "✅", "skipped": "⏭", "shown": "👁"}.get(sq.status, "?")
            lines.append(f"{mark} #{q.number} [{q.tag}] — {q.text[:70]}{'…' if len(q.text) > 70 else ''}")

    return "\n".join(lines)


@router.message(Command("played"))
async def cmd_played(message: Message) -> None:
    text = await _format_played_report()
    # Telegram limit ~4096 chars — split if needed
    if len(text) <= 4000:
        await message.answer(text, parse_mode="HTML")
        return
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 4000:
            await message.answer(chunk, parse_mode="HTML")
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk.strip():
        await message.answer(chunk, parse_mode="HTML")


# ─── Role selection ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "role:moderator")
async def cb_role_moderator(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    chat_id = callback.from_user.id

    existing = await _session_by_mod(chat_id)
    if existing:
        await callback.message.edit_text(
            f"⚠️ У тебя уже есть активная сессия (код: <b>{existing.code}</b>).\n"
            f"Используй /reset чтобы сбросить.",
            parse_mode="HTML",
        )
        return

    # Generate unique 4-digit code
    code = "".join(random.choices(string.digits, k=4))
    for _ in range(20):
        if not await _session_by_code(code):
            break
        code = "".join(random.choices(string.digits, k=4))

    async with sf()() as db:
        session = GameSession(
            code=code,
            moderator_chat_id=chat_id,
            status="waiting_participant",
            mod_msg_id=callback.message.message_id,
        )
        db.add(session)
        await db.commit()

    await callback.message.edit_text(
        f"🎯 <b>Ты — модератор</b>\n\n"
        f"Код для «Экрана для стола»:\n\n"
        f"<code>{code}</code>\n\n"
        f"Покажи этот код на телефоне-экране.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "role:participant")
async def cb_role_participant(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(CodeState.waiting_for_code)
    await state.update_data(screen_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "📺 <b>Ты — экран для стола</b>\n\n"
        "Введи 4-значный код с телефона модератора:",
        parse_mode="HTML",
    )


# ─── Code entry (participant) ─────────────────────────────────────────────────

@router.message(CodeState.waiting_for_code)
async def msg_code_entry(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    data = await state.get_data()
    screen_msg_id: int | None = data.get("screen_msg_id")
    bot = message.bot
    chat_id = message.chat.id

    try:
        await message.delete()
    except Exception:
        pass

    async def _edit_screen(text: str) -> None:
        if screen_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=screen_msg_id,
                    text=text, parse_mode="HTML",
                )
                return
            except Exception:
                pass
        await bot.send_message(chat_id, text, parse_mode="HTML")

    if not code.isdigit() or len(code) != 4:
        await _edit_screen(
            "📺 <b>Ты — экран для стола</b>\n\n"
            "❌ Код — 4 цифры. Попробуй ещё раз:"
        )
        return

    session = await _session_by_code(code)
    if not session:
        await _edit_screen(
            f"📺 <b>Ты — экран для стола</b>\n\n"
            f"❌ Код <code>{code}</code> не найден или уже использован.\n"
            f"Проверь код у модератора и попробуй снова:"
        )
        return

    # Link participant
    async with sf()() as db:
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(
                participant_chat_id=chat_id,
                status="deck_choice",
                part_msg_id=screen_msg_id,
            )
        )
        await db.commit()

    await state.clear()

    # Participant screen: waiting
    await _edit_screen("✅ <b>Подключено!</b>\n\nОжидаем настройку встречи от модератора...")

    # Moderator: show deck choice
    deck_id = get_settings().deck_id
    used = await _used_count(deck_id)
    session = await _reload_session(session.id)

    mod_text = (
        f"✅ <b>Экран подключён!</b>\n\n"
        f"В колоде уже сыграно вопросов: <b>{used}</b>\n\n"
        f"Продолжить или начать сначала?"
    )
    new_mod_id = await _try_edit(bot, session.moderator_chat_id, session.mod_msg_id,
                                  mod_text, deck_keyboard(used))
    if new_mod_id and new_mod_id != session.mod_msg_id:
        await _save_msg_ids(session.id, mod_id=new_mod_id, part_id=None)


# ─── Deck and preset choice (moderator) ───────────────────────────────────────

@router.callback_query(F.data.startswith("deck:"))
async def cb_deck_choice(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_mod(chat_id)
    if not session or session.status != "deck_choice":
        return

    deck_continue = callback.data == "deck:continue"
    async with sf()() as db:
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(status="preset_choice", deck_continue=deck_continue)
        )
        await db.commit()

    await callback.message.edit_text(
        "🎮 <b>Формат встречи:</b>",
        reply_markup=preset_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("preset:"))
async def cb_preset(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_mod(chat_id)
    if not session or session.status != "preset_choice":
        return

    preset = "meeting1" if callback.data == "preset:meeting1" else "full"
    deck_id = get_settings().deck_id

    used: set[int] = set()
    if session.deck_continue:
        used = await _used_numbers(deck_id)

    queue = build_queue(preset, used)

    async with sf()() as db:
        for idx, (q_num, phase) in enumerate(queue):
            db.add(SessionQuestion(
                session_id=session.id,
                question_number=q_num,
                order_index=idx,
                phase_label=phase,
                status="pending",
            ))
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(preset=preset, status="active", phase=1)
        )
        await db.commit()

    session = await _reload_session(session.id)
    done, pending, _ = await _progress(session.id)
    preset_name = "Встреча 1" if preset == "meeting1" else "Полная колода"

    mod_text = (
        f"🚀 <b>Встреча начата! · {preset_name}</b>\n\n"
        f"Всего вопросов: {pending}\n\n"
        f"⏳ Ожидаю, когда экран запросит первый вопрос..."
    )
    new_mod_id = await _try_edit(callback.bot, session.moderator_chat_id, session.mod_msg_id,
                                  mod_text, moderator_waiting_keyboard(has_undo=False))

    part_text = (
        "🎯 <b>Встреча началась!</b>\n\n"
        "Нажми кнопку ниже, когда будет твоя очередь."
    )
    new_part_id = await _try_edit(callback.bot, session.participant_chat_id, session.part_msg_id,
                                   part_text, participant_ready_keyboard())

    await _save_msg_ids(session.id,
                        mod_id=new_mod_id if new_mod_id != session.mod_msg_id else None,
                        part_id=new_part_id if new_part_id != session.part_msg_id else None)


# ─── Question flow ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "question:get")
async def cb_get_question(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_part(chat_id)

    if not session or session.status != "active":
        await callback.answer("Сессия неактивна.", show_alert=True)
        return
    if session.current_question_number is not None:
        await callback.answer("Текущий вопрос ещё не закрыт модератором.", show_alert=True)
        return

    sq = await _next_pending(session.id)
    if sq is None:
        await callback.answer("Все вопросы сыграны! 🎉", show_alert=True)
        await _finish_session(callback.bot, session)
        return

    async with sf()() as db:
        await db.execute(
            update(SessionQuestion)
            .where(SessionQuestion.id == sq.id)
            .values(status="shown", shown_at=datetime.utcnow())
        )
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(current_question_number=sq.question_number, replace_used=False)
        )
        await db.commit()

    session = await _reload_session(session.id)
    await _show_question_to_both(callback.bot, session, sq)


@router.callback_query(F.data == "question:replace")
async def cb_replace_question(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_part(chat_id)

    if not session or session.status != "active":
        return
    if session.replace_used or session.current_question_number is None:
        await callback.answer("Замена уже использована или нет активного вопроса.", show_alert=True)
        return

    curr_sq = await _shown_question(session.id)
    if not curr_sq:
        return

    async with sf()() as db:
        # Put current question at the end
        max_idx = (await db.execute(
            select(func.max(SessionQuestion.order_index))
            .where(SessionQuestion.session_id == session.id)
        )).scalar_one() or 0

        await db.execute(
            update(SessionQuestion)
            .where(SessionQuestion.id == curr_sq.id)
            .values(status="pending", order_index=max_idx + 1, shown_at=None)
        )

        # Get next pending (old current is now at the end)
        next_sq = (await db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session.id)
            .where(SessionQuestion.status == "pending")
            .order_by(SessionQuestion.order_index)
            .limit(1)
        )).scalar_one_or_none()

        if next_sq is None:
            # Only one question in deck — restore it
            await db.execute(
                update(SessionQuestion)
                .where(SessionQuestion.id == curr_sq.id)
                .values(status="shown", shown_at=datetime.utcnow())
            )
            await db.execute(
                update(GameSession).where(GameSession.id == session.id).values(replace_used=True)
            )
            await db.commit()
            await callback.answer("Больше нет вопросов для замены.", show_alert=True)
            return

        await db.execute(
            update(SessionQuestion)
            .where(SessionQuestion.id == next_sq.id)
            .values(status="shown", shown_at=datetime.utcnow())
        )
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(current_question_number=next_sq.question_number, replace_used=True)
        )
        await db.commit()

    session = await _reload_session(session.id)
    await _show_question_to_both(callback.bot, session, next_sq)


# ─── Moderator controls ───────────────────────────────────────────────────────

@router.callback_query(F.data == "answer:done")
async def cb_answer_done(callback: CallbackQuery) -> None:
    await _close_question(callback, "done")


@router.callback_query(F.data == "answer:skip")
async def cb_answer_skip(callback: CallbackQuery) -> None:
    await _close_question(callback, "skipped")


async def _close_question(callback: CallbackQuery, new_status: str) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_mod(chat_id)

    if not session or session.status != "active":
        return
    if session.current_question_number is None:
        return

    sq = await _shown_question(session.id)
    if sq is None:
        return

    async with sf()() as db:
        await db.execute(
            update(SessionQuestion)
            .where(SessionQuestion.id == sq.id)
            .values(status=new_status, closed_at=datetime.utcnow())
        )
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(current_question_number=None)
        )
        await db.commit()

    session = await _reload_session(session.id)
    await _render_moderator_waiting(callback.bot, session)
    await _render_participant_ready(callback.bot, session)


@router.callback_query(F.data == "answer:undo")
async def cb_answer_undo(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_mod(chat_id)

    if not session or session.status != "active":
        return
    if session.current_question_number is not None:
        await callback.answer("Нельзя отменить — вопрос ещё активен.", show_alert=True)
        return

    sq = await _last_closed(session.id)
    if sq is None:
        await callback.answer("Нет действий для отмены.", show_alert=True)
        return

    async with sf()() as db:
        await db.execute(
            update(SessionQuestion)
            .where(SessionQuestion.id == sq.id)
            .values(status="shown", closed_at=None)
        )
        await db.execute(
            update(GameSession)
            .where(GameSession.id == session.id)
            .values(current_question_number=sq.question_number, replace_used=True)
        )
        await db.commit()

    session = await _reload_session(session.id)
    await _show_question_to_both(callback.bot, session, sq)


@router.callback_query(F.data == "session:finish")
async def cb_session_finish(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.from_user.id
    session = await _session_by_mod(chat_id)

    if not session or session.status == "finished":
        return

    await _finish_session(callback.bot, session)


async def _finish_session(bot: Bot, session: GameSession) -> None:
    deck_id = get_settings().deck_id

    async with sf()() as db:
        closed = (await db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session.id)
            .where(SessionQuestion.status.in_(["done", "skipped"]))
        )).scalars().all()

        done_count = sum(1 for q in closed if q.status == "done")
        skip_count = sum(1 for q in closed if q.status == "skipped")

        existing_in_ledger = {row[0] for row in (await db.execute(
            select(DeckLedger.question_number).where(DeckLedger.deck_id == deck_id)
        )).all()}

        now = datetime.utcnow()
        for sq in closed:
            if sq.question_number not in existing_in_ledger:
                db.add(DeckLedger(deck_id=deck_id, question_number=sq.question_number, used_at=now))

        await db.execute(
            update(GameSession).where(GameSession.id == session.id).values(status="finished")
        )
        await db.commit()

    done_nums = [sq.question_number for sq in closed if sq.status == "done"]
    skip_nums = [sq.question_number for sq in closed if sq.status == "skipped"]
    done_nums.sort()
    skip_nums.sort()

    nums_block = ""
    if done_nums:
        nums_block += f"\n\n✅ <b>Ответили</b> (#{', #'.join(map(str, done_nums))})"
    if skip_nums:
        nums_block += f"\n\n⏭ <b>Пропустили</b> (#{', #'.join(map(str, skip_nums))})"

    mod_text = (
        f"🏁 <b>Встреча завершена!</b>\n\n"
        f"✅ Ответили: {done_count}\n"
        f"⏭ Пропустили: {skip_count}\n"
        f"📊 Итого: {done_count + skip_count} вопросов"
        f"{nums_block}\n\n"
        f"Вопросы записаны в колоду — в следующий раз не повторятся.\n"
        f"Полный список: /played\n\n"
        f"Нажми /start для новой встречи."
    )
    await _try_edit(bot, session.moderator_chat_id, session.mod_msg_id, mod_text, None)

    if session.participant_chat_id:
        part_text = (
            "🏁 <b>Встреча завершена! Спасибо всем ✨</b>\n\n"
            "Нажми /start для новой встречи."
        )
        await _try_edit(bot, session.participant_chat_id, session.part_msg_id, part_text, None)


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp
