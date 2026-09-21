"""Догон Авто.ру за календарный день. Пишем только если история читается.

  PYTHONPATH=. LLM_PROXY=socks5://127.0.0.1:1080 python tools/catchup_autoru_day.py
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from bot import nudge, store
from bot.autoru import Autoru, AutoruError, is_offer_room
from bot.autoru_loop import (
    AutoruChannel,
    created_of,
    is_out,
    listing_from_room,
    load_state,
    message_text,
    remember_allow,
    remember_listing,
    save_state,
    store_id,
    we_sell,
)
from bot.config import settings
from bot.main import CHANNELS, _answer_locked
from bot.tg import Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("catchup_autoru")

MSK = timezone(timedelta(hours=3))
STAFF_HANDOFF = re.compile(
    r"уже разговаривал|уже общал|с назаром|с никитой|с евгением|с эльзаром",
    re.I,
)


def day_bounds(day: datetime) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=MSK)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _clean(text: str) -> str:
    return html.unescape(text or "").strip()


def history_from_msgs(msgs: list[dict], me: str) -> list[dict]:
    turns: list[dict] = []
    for msg in msgs or []:
        if is_out(msg, me):
            txt = _clean(str(((msg.get("payload") or {}).get("value") or "")))
            role = "assistant"
        else:
            txt = _clean(message_text(msg, me))
            role = "user"
        if not txt:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] = "%s\n%s" % (turns[-1]["content"], txt)
        else:
            turns.append({"role": role, "content": txt})
    return turns


def skip_reason(last_text: str, turns: list[dict], listing: dict) -> str:
    if not listing.get("title") and not listing.get("item_id"):
        return "нет карточки машины"
    if not turns:
        return "история пустая"
    if not any(t["role"] == "user" for t in turns):
        return "нет текста клиента"
    if turns[-1]["role"] != "user":
        return "последнее слово за салоном"
    last = turns[-1]["content"]
    if STAFF_HANDOFF.search(last):
        return "клиент уже с менеджером"
    if not nudge.needs_reply(last_text or last):
        return "ответ не требуется"
    return ""


async def list_day(api: Autoru, own: dict, start: int, end: int) -> list[dict]:
    out = []
    for page in range(1, 6):
        data = await api._cabinet(
            "cabinet/postDealerChats",
            {"filter": {}, "pagination": {"page": page, "page_size": 40}},
        )
        rows = data.get("chats") or []
        if not rows:
            break
        page_min = None
        for chat in rows:
            last = chat.get("last_message") or {}
            ts = created_of(last)
            page_min = ts if page_min is None else min(page_min, ts)
            if not (start <= ts < end):
                continue
            item = dict(chat)
            item["id"] = str(chat.get("chat_room_id") or "")
            if not item["id"] or not is_offer_room(item):
                continue
            if not we_sell(item, own, {"foreign": []}):
                continue
            out.append(item)
        if page_min is not None and page_min < start:
            break
    return out


async def catchup_day(day: datetime | None = None) -> dict:
    day = day or (datetime.now(MSK) - timedelta(days=1))
    start, end = day_bounds(day)
    api = Autoru()
    tg = Telegram(settings.telegram_token)
    CHANNELS["tg"] = tg
    CHANNELS["autoru"] = AutoruChannel(api, tg)
    report = {"sent": [], "skip": [], "day": day.strftime("%Y-%m-%d")}
    try:
        own = await api.own_offers(force=True)
        rooms = await list_day(api, own, start, end)
        state = load_state()
        for room in rooms:
            cid = room["id"]
            me = str(room.get("me") or "")
            last = room.get("last_message") or {}
            title = ((room.get("subject") or {}).get("title") or "объявление")[:60]
            last_text = _clean(message_text(last, me) or str(((last.get("payload") or {}).get("value") or "")))
            if is_out(last, me):
                report["skip"].append((title, "последнее слово за салоном", last_text[:80]))
                continue
            try:
                msgs = await api.messages(cid, count=50)
            except AutoruError as exc:
                report["skip"].append((title, "не прочитал сообщения: %s" % exc, last_text[:80]))
                continue
            turns = history_from_msgs(msgs, me)
            listing = listing_from_room(room, own)
            why = skip_reason(last_text, turns, listing)
            if why:
                report["skip"].append((title, why, last_text[:80]))
                continue
            chat_key = store_id(cid)
            store.save_history(chat_key, turns)
            await remember_listing(chat_key, room, own)
            remember_allow(state, cid)
            save_state(state)
            user_last = turns[-1]["content"]
            await _answer_locked(CHANNELS["autoru"], chat_key, [user_last])
            hist = store.load_history(chat_key)
            if hist and hist[-1].get("role") == "assistant":
                report["sent"].append((title, user_last[:80], hist[-1]["content"][:80]))
                cursor = dict(state.get("cursor") or {})
                cursor[cid] = max(int(cursor.get(cid) or 0), created_of(last), int(datetime.now(timezone.utc).timestamp()))
                state["cursor"] = cursor
                save_state(state)
                log.info("догон авто.ру: ответил %s (%s)", cid[:12], title)
            else:
                report["skip"].append((title, "модель не дала исходящее", user_last[:80]))
            await asyncio.sleep(1.2)
        return report
    finally:
        await tg.close()
        await api.close()


async def main() -> None:
    report = await catchup_day()
    print("день", report["day"])
    print("ответил", len(report["sent"]))
    for title, last, out in report["sent"]:
        print("SENT", title, "|", last, "→", out)
    print("пропуск", len(report["skip"]))
    for title, why, last in report["skip"]:
        print("SKIP", title, "|", why, "|", last)


if __name__ == "__main__":
    asyncio.run(main())
