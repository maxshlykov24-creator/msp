"""Telegram-адаптер на aiogram 3: меню, приём голоса/текста, подтверждение, правки."""
from __future__ import annotations

import logging
import sqlite3
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import db, operations, ui
from ..config import Config
from ..pipeline import DraftLine, parse_text, parse_text_with_fallback
from ..text import normalize
from ..text.matcher import Matcher

log = logging.getLogger(__name__)

_MIME_EXT = {
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "video/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/aac": "aac",
}


def _ext_from_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), "mp3")


class Flow(StatesGroup):
    awaiting_input = State()
    reviewing = State()
    awaiting_money = State()


def _kb(spec: ui.Keyboard) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=data) for (label, data) in row]
        for row in spec
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class TelegramBot:
    def __init__(self, config: Config, conn: sqlite3.Connection, matcher: Matcher, nexara, groq=None):
        self.config = config
        self.conn = conn
        self.matcher = matcher
        self.nexara = nexara
        self.groq = groq
        self.bot = Bot(token=config.telegram_token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self._register()

    def reload_matcher(self) -> None:
        self.matcher = Matcher(db.aliases_for_matching(self.conn), self.config.match_threshold)

    # --- доступ ----------------------------------------------------------
    def _allowed(self, user_id: int) -> bool:
        return db.is_allowed(self.conn, user_id)

    def _register(self) -> None:
        dp = self.dp

        @dp.message(Command("start"))
        async def on_start(msg: Message, state: FSMContext):
            ok = db.try_register_user(
                self.conn, msg.from_user.id, msg.from_user.username or msg.from_user.full_name,
                self.config.max_whitelist,
            )
            if not ok:
                await msg.answer("Бот уже закреплён за другими пользователями. Доступ закрыт.")
                return
            await state.clear()
            text, kb = ui.main_menu()
            await msg.answer(
                f"Здравствуйте, {msg.from_user.first_name}! Это бот учёта комбикорма.\n\n{text}",
                reply_markup=_kb(kb),
            )

        @dp.message(Command("menu"))
        async def on_menu(msg: Message, state: FSMContext):
            if not self._allowed(msg.from_user.id):
                return
            await state.clear()
            text, kb = ui.main_menu()
            await msg.answer(text, reply_markup=_kb(kb))

        @dp.callback_query(F.data.startswith("op:"))
        async def on_op(cb: CallbackQuery, state: FSMContext):
            if not self._allowed(cb.from_user.id):
                await cb.answer("Доступ закрыт", show_alert=True)
                return
            op = cb.data.split(":", 1)[1]
            if op == "help":
                await cb.message.answer(ui.HELP_TEXT, parse_mode="HTML")
                await cb.answer()
                return
            if op == "stock":
                await self._show_stock(cb.message)
                await cb.answer()
                return
            if op == "money":
                await state.set_state(Flow.awaiting_money)
                await cb.message.answer(ui.op_prompt("money"), parse_mode="HTML")
                await cb.answer()
                return
            await state.set_state(Flow.awaiting_input)
            await state.update_data(op=op)
            await cb.message.answer(ui.op_prompt(op), parse_mode="HTML")
            await cb.answer()

        @dp.message(Flow.awaiting_money)
        async def on_money(msg: Message, state: FSMContext):
            if not self._allowed(msg.from_user.id):
                return
            text = await self._extract_text(msg)
            if not text:
                await msg.answer("Пришлите отчёт текстом или голосом.")
                return
            m = normalize.parse_money_report(text)
            from datetime import date as _d
            result = operations.commit_money(
                self.conn, day=_d.today().isoformat(),
                cash=m["cash"], transfer=m["transfer"], expense=m["expense"],
                closing_cash=m["closing"],
            )
            await state.clear()
            await msg.answer(ui.render_money(result), parse_mode="HTML")
            await self._send_menu(msg)

        @dp.message(Flow.awaiting_input, F.voice | F.audio | F.document | F.text)
        async def on_input(msg: Message, state: FSMContext):
            if not self._allowed(msg.from_user.id):
                return
            data = await state.get_data()
            op = data.get("op")
            text = await self._extract_text(msg)
            if not text:
                await msg.answer(
                    "Не удалось распознать голос/аудио. Попробуйте ещё раз или напишите текстом.\n"
                    "Если это голосовое сообщение Telegram (значок микрофона) — обычно распознаётся лучше, "
                    "чем прикреплённый файл."
                )
                return
            expect_sum = op == "sales"
            drafts = await parse_text_with_fallback(
                self.matcher,
                text,
                expect_sum=expect_sum,
                groq=self.groq if self.config.llm_fallback_enabled else None,
                product_lookup=lambda pid: db.product_by_id(self.conn, pid),
            )
            total_spoken = self._extract_total(text) if op == "sales" else None
            await state.update_data(
                op=op, drafts=drafts, total_spoken=total_spoken, raw_text=text,
            )
            await state.set_state(Flow.reviewing)
            await self._render_review(msg, op, drafts, total_spoken)

        @dp.callback_query(Flow.reviewing, F.data.startswith("pick:"))
        async def on_pick(cb: CallbackQuery, state: FSMContext):
            _, idx_s, pid_s = cb.data.split(":")
            idx, pid = int(idx_s), int(pid_s)
            data = await state.get_data()
            drafts: list[DraftLine] = data["drafts"]
            prod = db.product_by_id(self.conn, pid)
            for d in drafts:
                if d.index == idx:
                    d.product_id = pid
                    d.name = prod["canonical_name"]
                    d.bag_size_kg = prod["bag_size_kg"]
                    d.price_bag = prod["price_bag"]
                    d.status = "ok" if d.qty is not None else "ambiguous"
                    # само-обучение: запоминаем сказанное как алиас позиции
                    learned = " ".join(sorted(normalize.tokenize_name(d.raw)))
                    if learned:
                        db.add_alias(self.conn, pid, learned)
                    if d.status == "ambiguous":
                        await cb.answer(f"Позиция {idx}: укажите количество текстом «{idx} = ... 3 мешка»", show_alert=True)
                    break
            self.reload_matcher()
            await state.update_data(drafts=drafts)
            await self._edit_review(cb, data["op"], drafts, data.get("total_spoken"))
            await cb.answer()

        @dp.callback_query(Flow.reviewing, F.data.startswith("remove:"))
        async def on_remove(cb: CallbackQuery, state: FSMContext):
            idx = int(cb.data.split(":")[1])
            data = await state.get_data()
            drafts = [d for d in data["drafts"] if d.index != idx]
            await state.update_data(drafts=drafts)
            await self._edit_review(cb, data["op"], drafts, data.get("total_spoken"))
            await cb.answer("Убрано")

        @dp.message(Flow.reviewing, F.text)
        async def on_text_edit(msg: Message, state: FSMContext):
            """Правка строки: «2 = раменский пк-1 3 мешка 1200» или «убрать 4»."""
            data = await state.get_data()
            op = data["op"]
            drafts: list[DraftLine] = data["drafts"]
            t = msg.text.strip().lower()
            import re
            mrem = re.match(r"(?:убрать|удалить)\s+(\d+)", t)
            medit = re.match(r"(\d+)\s*=\s*(.+)", t)
            if mrem:
                idx = int(mrem.group(1))
                drafts = [d for d in drafts if d.index != idx]
            elif medit:
                idx = int(medit.group(1))
                new_drafts = await parse_text_with_fallback(
                    self.matcher,
                    medit.group(2),
                    expect_sum=(op == "sales"),
                    groq=self.groq if self.config.llm_fallback_enabled else None,
                    product_lookup=lambda pid: db.product_by_id(self.conn, pid),
                )
                if new_drafts:
                    nd = new_drafts[0]
                    nd.index = idx
                    drafts = [nd if d.index == idx else d for d in drafts]
                    if not any(d.index == idx for d in drafts):
                        drafts.append(nd)
            else:
                await msg.answer("Правка: «2 = раменский пк-1 3 мешка 1200» или «убрать 4».")
                return
            drafts.sort(key=lambda d: d.index)
            await state.update_data(drafts=drafts)
            await self._render_review(msg, op, drafts, data.get("total_spoken"))

        @dp.callback_query(Flow.reviewing, F.data == "confirm:cancel")
        async def on_cancel(cb: CallbackQuery, state: FSMContext):
            await state.clear()
            await cb.message.answer("Отменено.")
            await self._send_menu(cb.message)
            await cb.answer()

        @dp.callback_query(Flow.reviewing, F.data == "confirm:ok")
        async def on_confirm(cb: CallbackQuery, state: FSMContext):
            data = await state.get_data()
            op = data["op"]
            drafts: list[DraftLine] = [d for d in data["drafts"] if d.ok]
            raw = data.get("raw_text")
            if not drafts:
                await cb.answer("Нет готовых позиций", show_alert=True)
                return
            items = [
                operations.LineItem(
                    product_id=d.product_id, name=d.name or "", qty=d.qty or 0,
                    unit=d.unit, bag_size_kg=d.bag_size_kg, price=d.price_bag,
                    line_sum=d.line_sum, reason=d.reason,
                )
                for d in drafts
            ]
            await state.clear()
            result_text = self._commit(op, items, data.get("total_spoken"), raw)
            await cb.message.answer(result_text, parse_mode="HTML")
            await self._send_menu(cb.message)
            await cb.answer("Готово")

        @dp.message(F.voice | F.audio | F.document)
        async def on_stray_media(msg: Message, state: FSMContext):
            """Голос/файл пришёл вне ожидаемого шага: не выбрана операция в меню
            (или состояние сброшено перезапуском бота). Раньше молча игнорировалось."""
            if not self._allowed(msg.from_user.id):
                return
            log.info("Голос/файл вне сценария от user_id=%s", msg.from_user.id)
            _, kb = ui.main_menu()
            await msg.answer(
                "Сначала выберите действие кнопкой ниже (например, «Продажи за день»), "
                "потом присылайте голосовое сообщение.",
                reply_markup=_kb(kb),
            )

    # --- helpers ---------------------------------------------------------

    def _commit(self, op: str, items, total_spoken, raw) -> str:
        if op == "sales":
            r = operations.commit_sales(self.conn, items, total_spoken, raw)
            return (
                f"✅ Продажи записаны. Выручка: <b>{r['revenue']:g} ₽</b>"
                + (f" (скидка {r['discount']:g} ₽)" if r["discount"] else "")
            )
        if op == "receipt":
            n = operations.commit_receipt(self.conn, items, raw)
            return f"✅ Приёмка записана: {n} позиц."
        if op == "writeoff":
            n = operations.commit_writeoff(self.conn, items, raw)
            return f"✅ Списание записано: {n} позиц."
        if op == "inventory":
            report = operations.commit_inventory(self.conn, items, raw)
            return ui.render_inventory_report(report)
        return "Готово."

    def _extract_total(self, text: str):
        parsed = normalize.parse_line(normalize.normalize(text), expect_sum=True)
        # грубая эвристика: если во всём тексте есть 'итого'
        if any(w in text.lower() for w in ("итого", "всего", "сумма")):
            return parsed.line_sum
        return None

    async def _extract_text(self, msg: Message) -> str:
        if msg.text:
            return msg.text
        file_id = None
        fname, ctype = "voice.ogg", "audio/ogg"
        if msg.voice:
            file_id = msg.voice.file_id
            fname, ctype = "voice.ogg", "audio/ogg"
        elif msg.audio:
            file_id = msg.audio.file_id
            ctype = msg.audio.mime_type or ""
            fname = msg.audio.file_name or f"audio.{_ext_from_mime(ctype)}"
        elif msg.document:
            doc_mime = msg.document.mime_type or ""
            if not doc_mime.startswith("audio/") and not doc_mime.startswith("video/"):
                log.info("Документ без аудио mime_type: %s (%s)", msg.document.file_name, doc_mime)
                return ""
            file_id = msg.document.file_id
            ctype = doc_mime
            fname = msg.document.file_name or f"audio.{_ext_from_mime(ctype)}"
        if not file_id:
            return ""
        audio = await self.download_voice(file_id)
        log.info("Аудио получено: filename=%r content_type=%r size=%d байт", fname, ctype, len(audio))
        prompt = self._stt_prompt()
        try:
            text = await self.nexara.transcribe(audio, fname, ctype or "application/octet-stream", prompt)
        except Exception as e:  # noqa: BLE001
            log.exception("Nexara error")
            await msg.answer(f"Ошибка распознавания: {e}")
            return ""
        log.info("Nexara расшифровка (%d символов): %r", len(text), text[:300])
        return text

    def _stt_prompt(self) -> str:
        rows = db.active_products(self.conn)
        names = {(r["brand"] or "") for r in rows}
        codes = {(r["code"] or "") for r in rows}
        vocab = sorted(w for w in (names | codes) if w)
        return "Комбикорм, зерно. Термины: " + ", ".join(vocab[:60]) if vocab else ""

    async def download_voice(self, file_ref: str) -> bytes:
        buf = BytesIO()
        await self.bot.download(file_ref, destination=buf)
        return buf.getvalue()

    async def _render_review(self, msg: Message, op, drafts, total_spoken):
        text, kb = ui.render_draft(op, drafts, total_spoken=total_spoken)
        await msg.answer(text, reply_markup=_kb(kb), parse_mode="HTML")

    async def _edit_review(self, cb: CallbackQuery, op, drafts, total_spoken):
        text, kb = ui.render_draft(op, drafts, total_spoken=total_spoken)
        try:
            await cb.message.edit_text(text, reply_markup=_kb(kb), parse_mode="HTML")
        except Exception:  # noqa: BLE001
            await cb.message.answer(text, reply_markup=_kb(kb), parse_mode="HTML")

    async def _show_stock(self, msg: Message):
        rows = db.all_stock(self.conn)
        await msg.answer(ui.render_stock(rows), parse_mode="HTML")

    async def _send_menu(self, msg: Message):
        text, kb = ui.main_menu()
        await msg.answer(text, reply_markup=_kb(kb))

    async def run(self) -> None:
        log.info("Запуск Telegram long polling")
        await self.dp.start_polling(self.bot)
