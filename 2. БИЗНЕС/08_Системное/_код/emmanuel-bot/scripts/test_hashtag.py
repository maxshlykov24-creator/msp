#!/usr/bin/env python3
"""Юнит-проверка: отчёт = хэштег."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hashtag import DIGEST_HASHTAG, extract_hashtag, looks_like_report, override_from_tag, tag_from_telegram
from app.assigned import assigned_hashtag, is_out_of_scope
from app.time_utils import last_sunday_on_or_before, late_report_bucket, week_range_label, MSK
from datetime import date, datetime


def format_public_digest(*, week_start: date, total: int, wrote: int) -> str:
    missing = max(total - wrote, 0)
    pct = 0 if total <= 0 else round(100 * wrote / total)
    head = f"{DIGEST_HASHTAG} <b>{wrote} из {total}</b>, {pct}% ({week_range_label(week_start)})"
    if total > 0 and wrote >= total:
        return f"{head}\n\nЗа неделю отчёт написали все )"
    return (
        f"{head}\n"
        f"\n"
        f"Не написали {missing}\n"
        f"\n"
        f"Кто ещё не написал, переходите в @emmrov_bot, он поможет собрать текст )"
    )


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print("ok", msg)


def main() -> int:
    check(extract_hashtag("#СапелкинСергей\n1. молитва") == "#СапелкинСергей", "plain tag")
    check(extract_hashtag("привет аминь") is None, "no tag")
    check(extract_hashtag("🔥 круто") is None, "emoji comment")
    check(extract_hashtag("1. Сколько времени\n2") is None, "report without tag")
    check(extract_hashtag("#рвыотчёт <b>14 из 34</b>") is None, "digest tag skipped")
    check(extract_hashtag("#рвыотчёт #ШлыковМаксим") == "#ШлыковМаксим", "digest then name")
    denis = (
        "1. Сколько времени потратил на личный духовный рост (часы молитвы)?  2\n"
        "2. В чем мы, как команда, можем тебе помочь?"
    )
    andrey = "1.Сколько времени потратили на личный духовный рост (часы молитвы)? 4  2.В чем мы как команда можем тебе помочь?"
    check(looks_like_report(denis), "denis template")
    check(looks_like_report(andrey), "andrey template")
    check(not looks_like_report("1. Сколько времени потратил на личный духовный рост (часы молитвы)? 4"), "one item not enough")
    check(not looks_like_report("1. Купил хлеб 2. Забрал детей"), "numbered chores not report")
    check(not looks_like_report("Мужчины, давайте дальше отчеты писать, понимаю, что много дел"), "nudge not report")
    check(not looks_like_report("🟢Уже завтра ночная мужская молитва! 20:00 - 22:00"), "event not report")
    check(tag_from_telegram(username="Andreynkl", first_name="Андрей", last_name=None, tg_user_id=1) == "#Andreynkl", "nick username")
    check(tag_from_telegram(username=None, first_name="Денис", last_name="Петрий", tg_user_id=2) == "#ПетрийДенис", "nick name")
    check(extract_hashtag("#Кирилл Спиридонов") == "#Кирилл", "tag then space")
    check(extract_hashtag("текст #ПоляковМаксим ещё") == "#ПоляковМаксим", "tag mid text")
    check(override_from_tag("#ШлыковМаксим") == "ШлыковМаксим", "override strip")
    check(override_from_tag("ШлыковМаксим") == "ШлыковМаксим", "override plain")
    check(assigned_hashtag(422813303) == "#Арам", "assigned aram")
    check(assigned_hashtag(924861280) == "#МаксимБ", "assigned maxim b")
    check(is_out_of_scope(291663131), "pastor out of scope")
    check(not is_out_of_scope(435207481), "max in scope")
    check(last_sunday_on_or_before(date(2026, 9, 21)) == date(2026, 9, 20), "last sunday mon")
    check(last_sunday_on_or_before(date(2026, 9, 20)) == date(2026, 9, 20), "last sunday sun")
    check(week_range_label(date(2026, 9, 14)) == "14-20 сентября", "week range same month")
    check(week_range_label(date(2026, 9, 28)) == "28 сентября - 4 октября", "week range cross month")
    t = format_public_digest(week_start=date(2026, 9, 14), total=35, wrote=18)
    check(t.startswith("#рвыотчёт <b>18 из 35</b>, 51% (14-20 сентября)"), "public head")
    check("Не написали 17" in t and "@emmrov_bot" in t, "public digest")
    check("?" not in t, "public no question")
    t_all = format_public_digest(week_start=date(2026, 9, 14), total=34, wrote=34)
    check(t_all.startswith("#рвыотчёт <b>34 из 34</b>, 100% (14-20 сентября)"), "public all")
    t16 = format_public_digest(week_start=date(2026, 9, 14), total=34, wrote=16)
    check(t16.startswith("#рвыотчёт <b>16 из 34</b>, 47% (14-20 сентября)"), "public 16 of 34")
    ws = date(2026, 9, 14)
    check(late_report_bucket(datetime(2026, 9, 20, 23, 59, tzinfo=MSK), ws) == "sun", "bucket sun")
    check(late_report_bucket(datetime(2026, 9, 21, 0, 0, tzinfo=MSK), ws) == "night", "bucket night 00")
    check(late_report_bucket(datetime(2026, 9, 21, 1, 59, tzinfo=MSK), ws) == "night", "bucket night 2")
    check(late_report_bucket(datetime(2026, 9, 21, 2, 0, tzinfo=MSK), ws) == "mon_am", "bucket mon 2")
    check(late_report_bucket(datetime(2026, 9, 21, 11, 59, tzinfo=MSK), ws) == "mon_am", "bucket mon am")
    check(late_report_bucket(datetime(2026, 9, 21, 12, 0, tzinfo=MSK), ws) == "mon_pm", "bucket mon noon")
    check(late_report_bucket(datetime(2026, 9, 21, 23, 59, tzinfo=MSK), ws) == "mon_pm", "bucket mon pm")
    check(late_report_bucket(datetime(2026, 9, 22, 0, 0, tzinfo=MSK), ws) == "day:2026-09-22", "bucket tuesday")
    check(late_report_bucket(datetime(2026, 9, 23, 15, 0, tzinfo=MSK), ws) == "day:2026-09-23", "bucket wednesday")
    from app.coverage import format_coverage_text
    from app.models import GroupMember

    def member(uid: int, name: str) -> GroupMember:
        return GroupMember(
            tg_user_id=uid,
            tg_first_name=name,
            status="member",
            is_bot=False,
            in_scope=True,
            updated_at=datetime(2026, 9, 21, tzinfo=MSK),
        )

    text = format_coverage_text(
        week_start=ws,
        members=[member(1, "А"), member(2, "Б"), member(3, "В"), member(4, "Я")],
        submitted_ids={1, 2, 3, 4},
        submitted_at={
            1: datetime(2026, 9, 20, 21, 0, tzinfo=MSK),
            4: datetime(2026, 9, 20, 10, 0, tzinfo=MSK),
            2: datetime(2026, 9, 21, 1, 0, tzinfo=MSK),
            3: datetime(2026, 9, 23, 10, 0, tzinfo=MSK),
        },
    )
    sun = text.split("Написали до 2:00")[0]
    check(sun.index("· Я") < sun.index("· А"), "coverage time order")
    check("Написали до вс 23:59" in text and "· А" in text, "coverage sun")
    check("Написали до 2:00" in text and "· Б" in text, "coverage night")
    check("Написали в пн до 12:00" in text and "· никого" in text, "coverage monday morning empty stays")
    check("Написали в пн после 12:00" not in text, "coverage monday afternoon hidden")
    check("Написали во вторник" not in text, "coverage empty tuesday hidden")
    check("Написали в среду" in text and "· В" in text, "coverage wednesday")
    from app.time_utils import group_digest_follows_reports

    check(not group_digest_follows_reports(datetime(2026, 9, 28, 8, 59, tzinfo=MSK)), "digest quiet before monday 9")
    check(group_digest_follows_reports(datetime(2026, 9, 28, 9, 0, tzinfo=MSK)), "digest monday morning")
    check(group_digest_follows_reports(datetime(2026, 9, 28, 20, 0, tzinfo=MSK)), "digest monday evening")
    check(group_digest_follows_reports(datetime(2026, 9, 30, 9, 0, tzinfo=MSK)), "digest wednesday")
    check(group_digest_follows_reports(datetime(2026, 10, 2, 23, 0, tzinfo=MSK)), "digest friday")
    check(not group_digest_follows_reports(datetime(2026, 10, 3, 12, 0, tzinfo=MSK)), "digest quiet saturday")
    check(not group_digest_follows_reports(datetime(2026, 9, 27, 18, 0, tzinfo=MSK)), "digest quiet sunday")
    print("all passed")
    return 0


async def check_report_flow():
    """Полный маршрут через Dispatcher, SQLite и фиктивный Telegram transport."""
    import asyncio
    import os
    import shutil
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Update, Message
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from sqlalchemy import select, func
    from app import database as db, bot as handlers
    from app.config import get_settings
    from app.models import User, Revelation, Report, RevelationArchive
    from app.states import WeeklyReportStates

    class FakeSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.sent = []
            self.fail_next = False
        async def close(self):
            pass
        async def stream_content(self, *args, **kwargs):
            yield b""
        async def make_request(self, bot, method, timeout=None):
            if method.__api_method__ == "sendMessage":
                if self.fail_next:
                    self.fail_next = False
                    from aiogram.exceptions import TelegramNetworkError
                    raise TelegramNetworkError(method=method, message="injected timeout")
                assert len(method.text) <= 4096
                self.sent.append(method.text)
                return Message(message_id=len(self.sent), date=datetime.now(MSK),
                    chat={"id": method.chat_id, "type": "private"}, text=method.text)
            return True

    with tempfile.TemporaryDirectory(prefix="emmanuel-flow-") as tmp:
        path = Path(tmp) / "audit.sqlite3"
        source = os.environ.get("EMMANUEL_AUDIT_DB_COPY")
        if source:
            shutil.copyfile(source, path)
        cfg = get_settings()
        cfg.database_url = "sqlite+aiosqlite:///" + str(path)
        cfg.group_chat_id = 0
        cfg.groq_api_key = ""
        db._engine = None
        db._session_factory = None
        await db.init_db()
        await db.init_db()  # повторная миграция безопасна
        handlers.is_allowed_member = AsyncMock(return_value=True)
        handlers.groq_client.get_reflection = AsyncMock(return_value=None)
        transport = FakeSession()
        bot = Bot("999999999:TEST_TOKEN", session=transport)
        dp = handlers.build_dispatcher()
        uid = 999999991
        fu = {"id": uid, "is_bot": False, "first_name": "Тест"}
        counter = 0
        async def feed(text=None, callback=None):
            nonlocal counter
            counter += 1
            msg = {"message_id": counter, "date": int(datetime.now(MSK).timestamp()),
                   "chat": {"id": uid, "type": "private"}, "from": fu, "text": text or "step"}
            body = {"update_id": counter}
            if callback:
                body["callback_query"] = {"id": str(counter), "from": fu,
                    "chat_instance": "test", "data": callback, "message": msg}
            else:
                body["message"] = msg
            await dp.feed_update(bot, Update.model_validate(body))
        state = FSMContext(dp.storage, StorageKey(bot.id, uid, uid))
        async with db.get_session_factory()() as session:
            before = {t.__tablename__: await session.scalar(select(func.count()).select_from(t))
                      for t in [User, Report, Revelation, RevelationArchive]}
            u = User(tg_user_id=uid, tg_first_name="Тест", report_hashtag_override="Тест",
                     created_at=handlers.now_msk(), streak=0)
            session.add(u)
            await session.flush()
            user_id = u.id
            ws = handlers.week_start_from_date(handlers.now_msk().date())
            session.add(Revelation(user_id=user_id, text="OLD_DO_NOT_INCLUDE",
                created_at=datetime.combine(ws, datetime.min.time()) - __import__("datetime").timedelta(days=7)))
            session.add(Revelation(user_id=user_id, text="CURRENT_INCLUDE",
                created_at=datetime.combine(ws, datetime.min.time())))
            await session.commit()
        await feed(callback="rep_menu:write")
        for callback in ["q1:2", "q1b:5", "q2:3", "q6:2", "q3:yes"]:
            await feed(callback=callback)
        check(await state.get_state() == WeeklyReportStates.q4_help.state, "all numeric steps reach prayer need")
        await feed(text=handlers.kb.BTN_REPORT)
        check(await state.get_state() == WeeklyReportStates.q4_help.state, "menu resumes active report without reset")
        await feed(callback="rep_menu:write")
        check(await state.get_state() == WeeklyReportStates.q4_help.state, "old menu cannot overwrite active draft")
        transport.fail_next = True
        await feed(text="Нужда < & >")
        check(await state.get_state() == WeeklyReportStates.q5_revelations.state, "timeout preserves next step")
        from app.database import SQLiteFSMStorage
        resumed = FSMContext(SQLiteFSMStorage(), state.key)
        check((await resumed.get_data())["q4"] == "Нужда < & >", "new storage instance restores draft")
        await feed(text="/resume")
        check("CURRENT_INCLUDE" in transport.sent[-1] and "OLD_DO_NOT_INCLUDE" not in transport.sent[-1], "only report week revelations")
        await feed(text="NEW_INCLUDE < & >")
        await feed(callback="q5:done")
        check("NEW_INCLUDE &lt; &amp; &gt;" in transport.sent[-2], "new revelation in escaped preview")
        for number, value in [(1,"3 часа"),(2,"7"),(3,"4"),(4,"3"),(5,"нет"),(6,"Исправленная нужда"),(7,"REPLACED_REVELATION")]:
            await feed(callback=f"edit:{number}")
            await feed(text=value)
            check(await state.get_state() == WeeklyReportStates.confirm.state, f"edit field {number} returns preview")
        await feed(callback="cf:send")
        async with db.get_session_factory()() as session:
            rep = await session.scalar(select(Report).where(Report.user_id == user_id))
            check(rep.q5_revelations == "REPLACED_REVELATION", "submitted revelation matches preview")
            saved_time, saved_ontime = rep.submitted_at, rep.on_time
            check(await session.scalar(select(func.count()).select_from(Revelation).where(Revelation.user_id == user_id)) == 1, "old notes retained, only selected notes consumed")
        await feed(callback="rep_menu:edit")
        await feed(callback="edit:7")
        await feed(text="CANCELLED_EDIT")
        await feed(callback="rep:cancel")
        async with db.get_session_factory()() as session:
            rep = await session.scalar(select(Report).where(Report.user_id == user_id))
            check(rep.q5_revelations == "REPLACED_REVELATION", "cancel does not modify saved report")
        await feed(callback="rep_menu:edit")
        await feed(callback="edit:7")
        await feed(text="UPDATED_REVELATION")
        await feed(callback="cf:send")
        async with db.get_session_factory()() as session:
            rep = await session.scalar(select(Report).where(Report.user_id == user_id))
            check(rep.q5_revelations == "UPDATED_REVELATION", "update saves new revelation")
            check(rep.submitted_at == saved_time and rep.on_time == saved_ontime, "update preserves original submission deadline")
            for t in [User, Report, Revelation, RevelationArchive]:
                field = t.id if t is User else t.user_id
                original_count = await session.scalar(select(func.count()).select_from(t).where(field != user_id))
                check(original_count == before[t.__tablename__], f"original {t.__tablename__} rows preserved")
        await feed(callback="q5:done")
        await handlers.send_report_body(bot, uid, "<b>Текст</b>\n" + "&lt;&amp;" * 2000)
        check(all(len(t) <= 4096 for t in transport.sent), "long messages fit Telegram limit")
        for value in ["-1", "nan", "inf", "-2+", "169"]:
            check(handlers.parse_free_hours_to_q1(value) is None, f"invalid hours rejected: {value}")
        check(handlers.parse_free_hours_to_q1("1,5 часа") == "1.5", "Russian hours accepted")
        await bot.session.close()
        await dp.storage.close()
        await db.get_engine().dispose()
    print("report flow passed; no real Telegram messages sent")


if __name__ == "__main__":
    import asyncio
    main()
    asyncio.run(check_report_flow())
