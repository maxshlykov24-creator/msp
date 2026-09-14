"""Опрос чатов Авито и ответы тем же голосом, что в Telegram.

Webhook не ставим. Стартуем с курсора «сейчас»: старые непрочитанные не
поднимаем, иначе бот набросится на весь хвост. Ответ — только если чат
включён (/avito on или /avito next).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bot import avito_match, human, store
from bot.avito import Avito, AvitoError
from bot.config import settings

log = logging.getLogger("avito")


def _state_path() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "_avito.json"


def load_state() -> dict:
    path = _state_path()
    empty = {"cursor": {}, "allow": [], "arm_next": False, "seen": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("cursor", {})
    data.setdefault("allow", [])
    data.setdefault("arm_next", False)
    data.setdefault("seen", [])
    return data


def save_state(data: dict) -> None:
    _state_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def store_id(chat_id: str) -> str:
    return "av:%s" % chat_id


def raw_id(chat_key: str) -> str:
    text = str(chat_key)
    return text[3:] if text.startswith("av:") else text


def allowed(chat_id: str, state: dict | None = None) -> bool:
    state = state or load_state()
    if chat_id in settings.avito_allowlist:
        return True
    return chat_id in set(state.get("allow") or [])


def listing_of(chat: dict) -> dict:
    ctx = ((chat.get("context") or {}).get("value") or {})
    return {
        "item_id": ctx.get("id"),
        "title": ctx.get("title") or "",
        "price": ctx.get("price_string") or "",
        "url": ctx.get("url") or "",
    }


def message_text(msg: dict) -> str:
    if (msg.get("direction") or "") != "in":
        return ""
    content = msg.get("content") or {}
    if isinstance(content, dict):
        text = (content.get("text") or "").strip()
        if text:
            return text
        if content.get("type") == "image" or content.get("image"):
            return "Клиент прислал фото"
        if content.get("type") == "link":
            return (content.get("text") or content.get("url") or "Клиент прислал ссылку").strip()
    if (msg.get("type") or "") == "system":
        return ""
    return ""


def created_of(msg: dict) -> int:
    try:
        return int(msg.get("created") or 0)
    except (TypeError, ValueError):
        return 0


class AvitoChannel:
    def __init__(self, api: Avito, tg) -> None:
        self.api = api
        self.tg = tg

    async def send(self, chat_id: int | str, text: str) -> None:
        data = await self.api.send_text(raw_id(str(chat_id)), human.for_chat(text))
        from bot import crm

        crm.remember_out(chat_id, data)

    async def typing(self, chat_id: int | str) -> None:
        return

    async def notify_admin(self, text: str) -> None:
        await self.tg.notify_admin(text)


async def prime_cursor(api: Avito) -> None:
    """Запомнить хвост, ничего не отвечая."""
    state = load_state()
    if state.get("cursor"):
        return
    chats = await api.chats(unread_only=False, limit=100)
    cursor = {}
    for chat in chats:
        cid = str(chat.get("id") or "")
        last = chat.get("last_message") or {}
        cursor[cid] = created_of(last)
    state["cursor"] = cursor
    save_state(state)
    log.info("авито: курсор на %d чатах, хвост не трогаю", len(cursor))


async def remember_listing(chat_key: str, chat: dict, api: Avito) -> None:
    doc = store.load_doc(chat_key)
    if doc.get("avito", {}).get("title"):
        return
    listing = listing_of(chat)
    cme_id = ""
    item_id = listing.get("item_id")
    if item_id:
        try:
            item = await api.item(item_id)
            cme_id = str(item.get("autoload_item_id") or "")
        except AvitoError as exc:
            log.warning("авито item %s: %s", item_id, exc)
    doc["avito"] = {
        "chat_id": raw_id(chat_key),
        "title": listing.get("title") or "",
        "price": listing.get("price") or "",
        "url": listing.get("url") or "",
        "item_id": item_id,
        "cme_id": cme_id,
        "focus": avito_match.focus_block(
            listing.get("title") or "",
            listing.get("price") or "",
            listing.get("url") or "",
            cme_id,
        ),
    }
    store.save_doc(chat_key, doc)


async def poll_once(api: Avito, channel: AvitoChannel, schedule, pending: dict) -> None:
    state = load_state()
    cursor: dict = dict(state.get("cursor") or {})
    try:
        chats = await api.chats(unread_only=False, limit=40)
    except AvitoError as exc:
        log.warning("авито чаты: %s", exc)
        return
    changed = False
    for chat in chats:
        cid = str(chat.get("id") or "")
        if not cid:
            continue
        last = chat.get("last_message") or {}
        last_at = created_of(last)
        seen = int(cursor.get(cid) or 0)
        if last_at <= seen:
            continue
        if (last.get("direction") or "") != "in":
            if store.is_paused(store_id(cid)):
                from bot import crm

                crm.on_foreign_out(store_id(cid), last)
            cursor[cid] = max(seen, last_at)
            changed = True
            continue
        try:
            msgs = await api.messages(cid, limit=10)
        except AvitoError as exc:
            log.warning("авито сообщения %s: %s", cid, exc)
            continue
        incoming = []
        max_at = seen
        for msg in msgs:
            at = created_of(msg)
            max_at = max(max_at, at)
            if at <= seen:
                continue
            text = message_text(msg)
            if text:
                incoming.append((at, text))
        incoming.sort()
        cursor[cid] = max_at
        changed = True
        if not incoming:
            continue
        texts = [t for _, t in incoming]
        listing = listing_of(chat)
        title = listing.get("title") or "объявление"
        chat_key = store_id(cid)
        await remember_listing(chat_key, chat, api)
        if not allowed(cid, state) and not state.get("arm_next"):
            log.info("авито новый чат %s (%s) — жду /avito on", cid[:12], title)
            await channel.notify_admin(
                "Авито, новый чат.\n%s\n%s\nЧтобы бот ответил, напиши:\n/avito on %s\nили /avito next и пусть клиент напишет ещё раз"
                % (title, " ⏎ ".join(texts)[:400], cid)
            )
            continue
        if state.get("arm_next") and not allowed(cid, state):
            allow = list(state.get("allow") or [])
            if cid not in allow:
                allow.append(cid)
            state["allow"] = allow
            state["arm_next"] = False
            save_state(state)
            log.info("авито: /next поймал чат %s", cid[:12])
            await channel.notify_admin("Авито: бот взял чат %s\n%s" % (cid, title))
        for text in texts:
            store.log_line(chat_key, "клиент", text)
        if store.is_paused(chat_key):
            log.info("авито чат %s на паузе", cid[:12])
            from bot import crm

            await crm.client_wrote_again(chat_key, texts[-1])
            continue
        pending.setdefault(chat_key, []).extend(texts)
        schedule(channel, chat_key)
        log.info("авито входящее %s (%s): %s", cid[:12], title, texts[-1][:80])
    if changed:
        state["cursor"] = cursor
        save_state(state)


def status_text() -> str:
    state = load_state()
    allow = list(state.get("allow") or [])
    extra = list(settings.avito_allowlist)
    return (
        "Авито: %s\nlive-чаты: %s\nnext: %s\nкурсор чатов: %s"
        % (
            "вкл" if settings.avito_enabled else "выкл",
            ", ".join(allow + extra) or "нет, бот молчит пока не /avito on",
            "ждёт следующее входящее" if state.get("arm_next") else "нет",
            len(state.get("cursor") or {}),
        )
    )
