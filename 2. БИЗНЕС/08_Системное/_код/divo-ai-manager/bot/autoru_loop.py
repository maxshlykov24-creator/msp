"""Опрос чатов Авто.ру. Те же правила, что у Авито: новые берём, к старым молчим.

Отвечаем только в чатах по нашим объявлениям. Диалоги, где DIVO сам пишет
как покупатель на чужую карточку, не трогаем.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from bot import avito_match, human, store
from bot.autoru import Autoru, AutoruError, is_offer_room, listing_from_room, source_id
from bot.config import settings

log = logging.getLogger("autoru")

PRIOR_GAP_SEC = 2 * 3600
PREFIX = "ar:"


def _state_path() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "_autoru.json"


def load_state() -> dict:
    path = _state_path()
    empty = {
        "cursor": {},
        "allow": [],
        "legacy": [],
        "foreign": [],
        "arm_next": False,
        "seen": [],
    }
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    for key, default in empty.items():
        data.setdefault(key, default)
    return data


def save_state(data: dict) -> None:
    _state_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def store_id(chat_id: str) -> str:
    return PREFIX + str(chat_id)


def raw_id(chat_key: str) -> str:
    text = str(chat_key)
    return text[len(PREFIX) :] if text.startswith(PREFIX) else text


def allowed(chat_id: str, state: dict | None = None) -> bool:
    state = state or load_state()
    if chat_id in settings.autoru_allowlist:
        return True
    return chat_id in set(state.get("allow") or [])


def is_legacy(chat_id: str, state: dict | None = None) -> bool:
    state = state or load_state()
    return chat_id in set(state.get("legacy") or [])


def _put(state: dict, key: str, chat_id: str) -> None:
    rows = [x for x in (state.get(key) or []) if x != chat_id]
    rows.append(chat_id)
    state[key] = rows[-500:]


def remember_allow(state: dict, chat_id: str) -> None:
    _put(state, "allow", chat_id)
    state["legacy"] = [x for x in (state.get("legacy") or []) if x != chat_id]


def remember_legacy(state: dict, chat_id: str) -> None:
    if chat_id in set(state.get("allow") or []) or chat_id in settings.autoru_allowlist:
        return
    _put(state, "legacy", chat_id)


def remember_foreign(state: dict, offer_id: str) -> None:
    if not offer_id:
        return
    _put(state, "foreign", offer_id)


def created_of(msg: dict) -> int:
    raw = msg.get("created")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    text = str(raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return 0


def is_out(msg: dict, me: str = "") -> bool:
    if msg.get("me") is True:
        return True
    author = msg.get("author") or ""
    return bool(me) and author == me


def _usable(msg: dict) -> bool:
    payload = msg.get("payload") or {}
    ctype = str(payload.get("content_type") or "").upper()
    props = msg.get("properties") or {}
    kind = str(props.get("type") or "").upper()
    if kind.startswith("ADV") or kind in {"NOTIFICATION", "TECH"}:
        return False
    if ctype in {"TEXT_HTML"}:
        return False
    return True


def had_prior_correspondence(msgs: list[dict], newest_in: int, me: str = "") -> bool:
    """Уже был диалог: исходящее салона или входящее сильно раньше текущего."""
    for msg in msgs or []:
        if not _usable(msg):
            continue
        at = created_of(msg)
        if is_out(msg, me):
            return True
        if (not is_out(msg, me)) and newest_in and at and (newest_in - at) > PRIOR_GAP_SEC:
            return True
    return False


def message_text(msg: dict, me: str = "") -> str:
    if is_out(msg, me):
        return ""
    if not _usable(msg):
        return ""
    payload = msg.get("payload") or {}
    ctype = str(payload.get("content_type") or "").upper()
    if "IMAGE" in ctype or "PHOTO" in ctype:
        return "Клиент прислал фото"
    if ctype and ctype not in {"TEXT_PLAIN", "TEXT"}:
        return ""
    return str(payload.get("value") or "").strip()


def we_sell(room: dict, own: dict[str, dict], state: dict) -> bool:
    oid = source_id(room)
    if not oid or not is_offer_room(room):
        return False
    if oid in own:
        return True
    if oid in set(state.get("foreign") or []):
        return False
    return False


class AutoruChannel:
    def __init__(self, api: Autoru, tg) -> None:
        self.api = api
        self.tg = tg

    async def send(self, chat_id: int | str, text: str) -> None:
        data = await self.api.send_text(raw_id(str(chat_id)), human.for_chat(text))
        from bot import crm

        msg = data.get("message") if isinstance(data, dict) else {}
        if not isinstance(msg, dict):
            msg = data if isinstance(data, dict) else {}
        crm.remember_out(
            chat_id,
            {"id": msg.get("id"), "created": created_of(msg) or int(time.time())},
        )

    async def typing(self, chat_id: int | str) -> None:
        return

    async def notify_admin(self, text: str) -> None:
        await self.tg.notify_admin(text)

    async def notify_owner(self, text: str) -> None:
        await self.tg.notify_owner(text)


async def _classify_new_offer(api: Autoru, oid: str, own: dict, state: dict) -> dict:
    if oid in own or oid in set(state.get("foreign") or []):
        return own
    fresh = await api.own_offers(force=True)
    if oid not in fresh:
        remember_foreign(state, oid)
        save_state(state)
    return fresh


async def prime_cursor(api: Autoru) -> None:
    """Запомнить хвост, ничего не отвечая."""
    state = load_state()
    if state.get("cursor"):
        return
    own = await api.own_offers(force=True)
    rooms = await api.rooms()
    cursor = {}
    for room in rooms:
        cid = str(room.get("id") or "")
        if not cid:
            continue
        last = room.get("last_message") or {}
        cursor[cid] = created_of(last)
        oid = source_id(room)
        if oid and oid not in own:
            remember_foreign(state, oid)
    state["cursor"] = cursor
    save_state(state)
    log.info(
        "авто.ру: курсор на %d чатах, своих объявлений %d, чужих диалогов %d",
        len(cursor),
        len(own),
        len(state.get("foreign") or []),
    )


async def remember_listing(chat_key: str, room: dict, catalog: dict[str, dict]) -> None:
    doc = store.load_doc(chat_key)
    if (doc.get("autoru") or {}).get("title"):
        return
    listing = listing_from_room(room, catalog)
    title = listing.get("title") or ""
    price = listing.get("price") or ""
    url = listing.get("url") or ""
    doc["autoru"] = {
        "chat_id": raw_id(chat_key),
        "title": title,
        "price": price,
        "url": url,
        "item_id": listing.get("item_id") or "",
        "focus": avito_match.focus_block(
            title,
            price,
            url,
            "",
            channel="Авто.ру",
        ),
    }
    store.save_doc(chat_key, doc)


async def poll_once(api: Autoru, channel: AutoruChannel, schedule, pending: dict) -> None:
    state = load_state()
    cursor: dict = dict(state.get("cursor") or {})
    try:
        own = await api.own_offers()
        rooms = await api.rooms()
    except AutoruError as exc:
        log.warning("авто.ру чаты: %s", exc)
        return
    changed = False
    for room in rooms:
        cid = str(room.get("id") or "")
        if not cid or not is_offer_room(room):
            continue
        oid = source_id(room)
        if oid and oid not in own and oid not in set(state.get("foreign") or []):
            own = await _classify_new_offer(api, oid, own, state)
        if not we_sell(room, own, state):
            last = room.get("last_message") or {}
            last_at = created_of(last)
            seen = int(cursor.get(cid) or 0)
            if last_at > seen:
                cursor[cid] = last_at
                changed = True
            continue
        last = room.get("last_message") or {}
        last_at = created_of(last)
        seen = int(cursor.get(cid) or 0)
        if last_at <= seen:
            continue
        me = str(room.get("me") or "")
        if is_out(last, me):
            if store.is_paused(store_id(cid)):
                from bot import crm

                crm.on_foreign_out(
                    store_id(cid),
                    {"id": last.get("id"), "created": last_at},
                )
            cursor[cid] = max(seen, last_at)
            changed = True
            continue
        if (
            not allowed(cid, state)
            and is_legacy(cid, state)
            and not state.get("arm_next")
        ):
            cursor[cid] = max(seen, last_at)
            changed = True
            log.info("авто.ру чат %s уже был, молчу", cid[:12])
            continue
        try:
            need = 50 if not allowed(cid, state) else 10
            msgs = await api.messages(cid, count=need)
        except AutoruError as exc:
            log.warning("авто.ру сообщения %s: %s", cid, exc)
            continue
        incoming = []
        max_at = seen
        for msg in msgs:
            at = created_of(msg)
            max_at = max(max_at, at)
            if at <= seen:
                continue
            text = message_text(msg, me)
            if text:
                incoming.append((at, text))
        incoming.sort()
        cursor[cid] = max_at
        changed = True
        if not incoming:
            continue
        texts = [t for _, t in incoming]
        listing = listing_from_room(room, own)
        title = listing.get("title") or "объявление"
        chat_key = store_id(cid)
        await remember_listing(chat_key, room, own)
        if state.get("arm_next") and not allowed(cid, state):
            remember_allow(state, cid)
            state["arm_next"] = False
            save_state(state)
            log.info("авто.ру: /next поймал чат %s", cid[:12])
        elif not allowed(cid, state):
            if is_legacy(cid, state) or had_prior_correspondence(
                msgs, incoming[-1][0], me
            ):
                remember_legacy(state, cid)
                save_state(state)
                log.info("авто.ру чат %s уже был, молчу (%s)", cid[:12], title)
                continue
            remember_allow(state, cid)
            save_state(state)
            log.info("авто.ру новый чат %s, беру (%s)", cid[:12], title)
        for text in texts:
            store.log_line(chat_key, "клиент", text)
        if store.is_paused(chat_key):
            log.info("авто.ру чат %s на паузе", cid[:12])
            from bot import crm

            await crm.client_wrote_again(chat_key, texts[-1])
            continue
        pending.setdefault(chat_key, []).extend(texts)
        schedule(channel, chat_key)
        log.info("авто.ру входящее %s (%s): %s", cid[:12], title, texts[-1][:80])
    if changed:
        state["cursor"] = cursor
        save_state(state)


def status_text() -> str:
    state = load_state()
    allow = list(state.get("allow") or [])
    extra = list(settings.autoru_allowlist)
    expire = (settings.autoru_session_expire or "")[:10]
    return (
        "Авто.ру: %s\nновые чаты по нашим объявлениям: беру сам\n"
        "live-чаты: %s\nстарые с перепиской: %s\nчужие объявления: %s\n"
        "next: %s\nкурсор чатов: %s\nсессия до: %s"
        % (
            "вкл" if settings.autoru_enabled else "выкл",
            ", ".join(allow + extra) or "пока пусто",
            len(state.get("legacy") or []),
            len(state.get("foreign") or []),
            "ждёт следующее входящее" if state.get("arm_next") else "нет",
            len(state.get("cursor") or {}),
            expire or "не задана",
        )
    )
