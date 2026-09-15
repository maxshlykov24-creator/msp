"""Опрос чатов Авито и ответы тем же голосом, что в Telegram.

Webhook не ставим. Стартуем с курсора «сейчас»: старые непрочитанные не
поднимаем. Новые чаты берём сами. Если с клиентом уже была переписка,
на новое сообщение не отвечаем.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from bot import avito_match, human, nudge, store
from bot.avito import Avito, AvitoError
from bot.config import settings

log = logging.getLogger("avito")

PRIOR_GAP_SEC = 2 * 3600
ADOPT_SHOT_KEY = "adopted_20260914"
# Семь диалогов со скрина Авито 2026-09-14. Михаила «Цена реальная?» в API
# нет: его ищем по тексту, остальные — по id, чтобы не задеть соседние чаты.
SHOT_IDS = frozenset(
    {
        "u2i-MhZXyF5yTdAohb9FKLduFw",  # Salam, Tank 700
        "u2i-6X3pAWDl1Sb9Y91uUzrAsg",  # Владимир, G-класс
        "u2i-wI396zabNtnS2PHKL1WEbQ",  # Premium Auto, G-класс
        "u2i-nLS7kZ3H2aSrnZn8ofOqtQ",  # Максим Алаев, Jetour T2
        "u2i-jemXIwesLXR_B~XA911wYg",  # Максим, Bestune NAT
        "u2i-eu3XqDBTHT81k6IeZE423A",  # MSProduction, BMW X6
    }
)
UNSUPPORTED = (
    "сообщение не поддерживается",
    "пожалуйста, перейдите в авито мессенджер",
    "аккуратно напомнили",
    "сообщение удалено",
)
ADOPT_PROFILE_KEY = "adopted_u2u_20260914"
# Чат по профилю, не по объявлению: API без chat_types=u2u его не отдаёт.
MIKHAIL_U2U = "u2u-GgGsxybRa8lF_hT4SUeHzw"
ME_NAMES = frozenset({"диво моторс", "divo motors", "divo моторс"})


def _state_path() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "_avito.json"


def load_state() -> dict:
    path = _state_path()
    empty = {"cursor": {}, "allow": [], "legacy": [], "arm_next": False, "seen": []}
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
    data.setdefault("legacy", [])
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


def is_legacy(chat_id: str, state: dict | None = None) -> bool:
    state = state or load_state()
    return chat_id in set(state.get("legacy") or [])


def _put(state: dict, key: str, chat_id: str) -> None:
    rows = [x for x in (state.get(key) or []) if x != chat_id]
    rows.append(chat_id)
    state[key] = rows


def remember_allow(state: dict, chat_id: str) -> None:
    _put(state, "allow", chat_id)
    state["legacy"] = [x for x in (state.get("legacy") or []) if x != chat_id]


def remember_legacy(state: dict, chat_id: str) -> None:
    if chat_id in set(state.get("allow") or []) or chat_id in settings.avito_allowlist:
        return
    _put(state, "legacy", chat_id)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def is_noise(msg: dict) -> bool:
    """Системные пинги Авито и заглушка «сообщение не поддерживается»."""
    msg = msg or {}
    if (msg.get("type") or "").lower() == "system":
        return True
    blob = " ".join(
        [
            str(msg.get("type") or ""),
            content_text(msg),
            str((msg.get("content") or {}).get("type") or "")
            if isinstance(msg.get("content"), dict)
            else "",
        ]
    ).lower()
    if blob.startswith("[системное сообщение]") or "[системное сообщение]" in blob:
        return True
    return any(piece in blob for piece in UNSUPPORTED)


def had_prior_correspondence(msgs: list[dict], newest_in: int) -> bool:
    """Уже был диалог: исходящее продавца или входящее сильно раньше текущего."""
    for msg in msgs or []:
        if is_noise(msg):
            continue
        at = created_of(msg)
        direction = msg.get("direction") or ""
        if direction == "out":
            return True
        if direction == "in" and newest_in and at and (newest_in - at) > PRIOR_GAP_SEC:
            return True
    return False


def listing_of(chat: dict) -> dict:
    ctx = ((chat.get("context") or {}).get("value") or {})
    return {
        "item_id": ctx.get("id"),
        "title": ctx.get("title") or "",
        "price": ctx.get("price_string") or "",
        "url": ctx.get("url") or "",
    }


def content_text(msg: dict) -> str:
    content = (msg or {}).get("content") or {}
    if isinstance(content, dict):
        return (content.get("text") or "").strip()
    return str(content).strip() if content else ""


def item_card_text(item: dict | None) -> str:
    """Карточка чужого объявления в чате Авито: type=item, не type=link."""
    item = item or {}
    title = str(item.get("title") or "").strip()
    price = str(item.get("price_string") or item.get("price") or "").strip()
    url = str(item.get("item_url") or item.get("url") or "").strip()
    bits = [x for x in (title, price) if x]
    line = "Клиент прислал объявление"
    if bits:
        line += ": " + ", ".join(bits)
    if url:
        line += ". Ссылка: " + url
    return line


def message_text(msg: dict) -> str:
    if (msg.get("direction") or "") != "in":
        return ""
    if is_noise(msg):
        return ""
    content = msg.get("content") or {}
    text = content_text(msg)
    item = content.get("item") if isinstance(content, dict) else None
    card = item_card_text(item) if isinstance(item, dict) else ""
    if text and card:
        return text + "\n" + card
    if text:
        return text
    if card:
        return card
    kind = (msg.get("type") or "").lower()
    if isinstance(content, dict):
        kind = kind or str(content.get("type") or "").lower()
        if kind == "image" or content.get("image"):
            return "Клиент прислал фото"
        if kind == "link":
            return (content.get("url") or "Клиент прислал ссылку").strip()
        if kind == "item":
            return "Клиент прислал объявление"
    if kind == "image":
        return "Клиент прислал фото"
    if kind == "link":
        return "Клиент прислал ссылку"
    if kind == "item":
        return "Клиент прислал объявление"
    return ""


def outbound_text(msg: dict) -> str:
    if (msg.get("direction") or "") != "out":
        return ""
    if is_noise(msg):
        return ""
    return content_text(msg)


def chat_peer(chat: dict) -> str:
    me = settings.avito_user_id
    for user in chat.get("users") or []:
        uid = user.get("id")
        try:
            if me and uid is not None and int(uid) == int(me):
                continue
        except (TypeError, ValueError):
            pass
        name = (user.get("name") or "").strip()
        if _norm(name) in ME_NAMES:
            continue
        if name:
            return name
    return ""


def is_shot_chat(chat: dict) -> bool:
    cid = str(chat.get("id") or "")
    if cid in SHOT_IDS:
        return True
    peer = _norm(chat_peer(chat))
    last_txt = _norm(message_text(chat.get("last_message") or {}) or content_text(chat.get("last_message") or {}))
    return "михаил" in peer and "цена реальная" in last_txt


def _append_turn(turns: list[dict], role: str, content: str) -> None:
    text = (content or "").strip()
    if not text:
        return
    if turns and turns[-1].get("role") == role:
        turns[-1]["content"] = "%s\n%s" % (turns[-1].get("content") or "", text)
        return
    turns.append({"role": role, "content": text})


def history_from_messages(msgs: list[dict]) -> tuple[list[dict], list[str], int]:
    """История до последнего исходящего плюс хвост входящих, на которые ещё не отвечали."""
    ordered = sorted(msgs or [], key=created_of)
    turns: list[dict] = []
    pending: list[str] = []
    last_out_at = 0
    max_at = 0
    for msg in ordered:
        at = created_of(msg)
        max_at = max(max_at, at)
        incoming = message_text(msg)
        if incoming:
            pending.append(incoming)
            continue
        outgoing = outbound_text(msg)
        if not outgoing:
            continue
        if pending:
            _append_turn(turns, "user", "\n".join(pending))
            pending = []
        _append_turn(turns, "assistant", outgoing)
        last_out_at = at
    cursor = last_out_at if pending else max_at
    return turns, pending, cursor


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

    async def notify_owner(self, text: str) -> None:
        await self.tg.notify_owner(text)


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


async def list_chats(api: Avito, limit: int = 200) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    page = 50
    while len(out) < limit:
        batch = await api.chats(
            unread_only=False, limit=min(page, limit - len(out)), offset=offset
        )
        if not batch:
            break
        fresh = 0
        for chat in batch:
            cid = str(chat.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            out.append(chat)
            fresh += 1
        if not fresh:
            break
        if len(batch) < page:
            break
        offset += len(batch)
    return out


async def _adopt_one(api: Avito, state: dict, cursor: dict, chat: dict) -> None:
    cid = str(chat.get("id") or "")
    if not cid:
        return
    try:
        msgs = await api.messages(cid, limit=100)
    except AvitoError as exc:
        log.warning("авито adopt %s: %s", cid[:12], exc)
        return
    turns, pending_texts, cur = history_from_messages(msgs)
    chat_key = store_id(cid)
    await remember_listing(chat_key, chat, api)
    store.save_history(chat_key, turns)
    doc = store.load_doc(chat_key)
    meta = nudge.refresh(doc.get("nudge") or {}, turns)
    meta["waiting"] = False
    doc["nudge"] = meta
    store.save_doc(chat_key, doc)
    store.resume(chat_key)
    remember_allow(state, cid)
    cursor[cid] = cur
    title = listing_of(chat).get("title") or ""
    log.info(
        "авито беру %s (%s, %s), история %d, хвост %s",
        cid[:12],
        chat_peer(chat) or "?",
        title[:50],
        len(turns),
        (pending_texts[-1][:40] if pending_texts else "нет"),
    )


async def adopt_shot(api: Avito) -> None:
    """Разово взять диалоги со скрина: полная история, без повторного приветствия."""
    state = load_state()
    if state.get(ADOPT_SHOT_KEY):
        return
    try:
        chats = await list_chats(api, 200)
    except AvitoError as exc:
        log.warning("авито adopt: не прочитал чаты: %s", exc)
        return
    picked = [chat for chat in chats if is_shot_chat(chat)]
    if not picked:
        log.warning("авито adopt: чаты со скрина не нашёл")
        return
    found = {str(chat.get("id") or "") for chat in picked}
    missing = [cid for cid in SHOT_IDS if cid not in found]
    if missing:
        log.warning("авито adopt: нет в ленте %s", ",".join(cid[:16] for cid in missing))
    mikhail = next(
        (
            chat
            for chat in chats
            if "михаил" in _norm(chat_peer(chat))
            and "цена реальная" in _norm(
                message_text(chat.get("last_message") or {})
                or content_text(chat.get("last_message") or {})
            )
        ),
        None,
    )
    if mikhail is None:
        log.warning("авито adopt: Михаил «Цена реальная?» в API не найден")
    cursor = dict(state.get("cursor") or {})
    for chat in picked:
        await _adopt_one(api, state, cursor, chat)
    state["cursor"] = cursor
    state[ADOPT_SHOT_KEY] = True
    save_state(state)


async def prime_unseen(api: Avito, state: dict | None = None) -> None:
    """Новые типы чатов (u2u) не отвечать задним числом, только запомнить хвост."""
    state = state or load_state()
    cursor = dict(state.get("cursor") or {})
    try:
        chats = await list_chats(api, 250)
    except AvitoError as exc:
        log.warning("авито курсор u2u: %s", exc)
        return
    added = 0
    for chat in chats:
        cid = str(chat.get("id") or "")
        if not cid or cid in cursor:
            continue
        cursor[cid] = created_of(chat.get("last_message") or {})
        added += 1
    if added:
        state["cursor"] = cursor
        save_state(state)
        log.info("авито: в курсор добавил %d чатов по профилю, хвост не трогаю", added)


async def adopt_profile_chat(api: Avito) -> None:
    """Михаил «Цена реальная?» — чат по профилю, его не было в ленте u2i."""
    state = load_state()
    if not state.get(ADOPT_PROFILE_KEY):
        chat = None
        try:
            chat = await api.chat(MIKHAIL_U2U)
        except AvitoError as exc:
            log.warning("авито adopt профиль: %s", exc)
        if not (chat or {}).get("id"):
            try:
                chats = await list_chats(api, 80)
            except AvitoError:
                chats = []
            chat = next((c for c in chats if str(c.get("id") or "") == MIKHAIL_U2U), None)
        if chat and chat.get("id"):
            cursor = dict(state.get("cursor") or {})
            await _adopt_one(api, state, cursor, chat)
            state["cursor"] = cursor
        else:
            log.warning("авито adopt: профиль-чат Михаила снова не найден")
        state[ADOPT_PROFILE_KEY] = True
        save_state(state)
    await prime_unseen(api, state)


async def remember_listing(chat_key: str, chat: dict, api: Avito) -> None:
    doc = store.load_doc(chat_key)
    peer = chat_peer(chat)
    if (doc.get("avito") or {}).get("chat_id"):
        av = dict(doc.get("avito") or {})
        if peer and av.get("peer") != peer:
            av["peer"] = peer
            doc["avito"] = av
            store.save_doc(chat_key, doc)
        return
    listing = listing_of(chat)
    ctx_type = str((chat.get("context") or {}).get("type") or "")
    if ctx_type == "u2u" and not listing.get("title"):
        listing["title"] = "чат по профилю, объявление не привязано"
    cme_id = ""
    item_id = listing.get("item_id")
    if item_id in (0, "0", None, ""):
        item_id = None
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
        "peer": peer,
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
        if cid.startswith("a2u-"):
            last = chat.get("last_message") or {}
            last_at = created_of(last)
            seen = int(cursor.get(cid) or 0)
            if last_at > seen:
                cursor[cid] = last_at
                changed = True
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
        if (
            not allowed(cid, state)
            and is_legacy(cid, state)
            and not state.get("arm_next")
        ):
            cursor[cid] = max(seen, last_at)
            changed = True
            log.info("авито чат %s уже был, молчу", cid[:12])
            continue
        try:
            need = 50 if not allowed(cid, state) else 10
            msgs = await api.messages(cid, limit=need)
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
        if state.get("arm_next") and not allowed(cid, state):
            remember_allow(state, cid)
            state["arm_next"] = False
            save_state(state)
            log.info("авито: /next поймал чат %s", cid[:12])
        elif not allowed(cid, state):
            if is_legacy(cid, state) or had_prior_correspondence(msgs, incoming[-1][0]):
                remember_legacy(state, cid)
                save_state(state)
                log.info("авито чат %s уже был, молчу (%s)", cid[:12], title)
                continue
            remember_allow(state, cid)
            save_state(state)
            log.info("авито новый чат %s, беру (%s)", cid[:12], title)
        for text in texts:
            store.log_line(chat_key, "клиент", text)
        if store.hard_paused(chat_key):
            log.info("авито чат %s на паузе", cid[:12])
            from bot import crm

            await crm.client_wrote_again(chat_key, texts[-1])
            continue
        from bot import crm

        urgent = await crm.capture_if_urgent(chat_key, texts)
        if urgent in crm.PAUSE_REASONS:
            await crm.ack_callback(channel, chat_key, texts, urgent)
            store.pause(chat_key, "эскалация: %s" % urgent)
            log.info("авито чат %s сразу человеку (%s)", cid[:12], urgent)
            if store.hard_paused(chat_key):
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
        "Авито: %s\nновые чаты: беру сам\nlive-чаты: %s\nстарые с перепиской: %s\nnext: %s\nкурсор чатов: %s"
        % (
            "вкл" if settings.avito_enabled else "выкл",
            ", ".join(allow + extra) or "пока пусто",
            len(state.get("legacy") or []),
            "ждёт следующее входящее" if state.get("arm_next") else "нет",
            len(state.get("cursor") or {}),
        )
    )
