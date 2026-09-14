"""Алерты менеджерам: отдельный бот, стиль как у LicenseBridge.

Клиенту сюда ничего не уходит. HTML, эмодзи, коротко.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from bot.config import settings
from bot.nudge import MSK, in_working_hours, now_msk

log = logging.getLogger("alerts")

PING_MINUTES = (0, 5, 10, 15, 30, 60)
WAIT_CALL = "call"
WAIT_CHAT = "chat"

HEAD = {
    (WAIT_CALL, 0): "📞 <b>Ждёт звонка</b> | DIVO",
    (WAIT_CALL, 5): "⏰ <b>5 мин</b> | всё ещё ждёт звонка",
    (WAIT_CALL, 10): "🔥 <b>10 мин</b> | не взяли трубку",
    (WAIT_CALL, 15): "🚨 <b>15 мин</b> | клиент остывает",
    (WAIT_CALL, 30): "🚨 <b>30 мин</b> | до сих пор не взяли",
    (WAIT_CALL, 60): "🚨 <b>1 час</b> | диалог висит",
    (WAIT_CHAT, 0): "✍️ <b>Ждёт ответ в чате</b> | DIVO",
    (WAIT_CHAT, 5): "⏰ <b>5 мин</b> | чат без ответа",
    (WAIT_CHAT, 10): "🔥 <b>10 мин</b> | менеджер молчит",
    (WAIT_CHAT, 15): "🚨 <b>15 мин</b> | лояльность падает",
    (WAIT_CHAT, 30): "🚨 <b>30 мин</b> | до сих пор молчим",
    (WAIT_CHAT, 60): "🚨 <b>1 час</b> | диалог висит",
}

REASON_LINE = {
    "phone": "оставил номер",
    "call": "просит позвонить",
    "stuck": "агент в тупике",
    "complaint": "жалоба / конфликт",
    "handoff": "нужен живой менеджер",
    "llm": "бот не смог ответить",
}

THREAD_LIMIT = 6
LINE_LIMIT = 160
BRIEF_LIMIT = 360
SKIP_BRIEF_START = (
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "здравствуйте",
    "привет",
    "слушаю вас",
    "очень приятно",
    "как могу к вам",
)
SKIP_BRIEF_HAS = (
    "микрон",
    "толщиномер",
    "толщин",
    "как бьёт",
    "как бьется",
    "видеообзор",
    "живой менеджер",
    "для точных",
    "контактный телефон",
    "напишите телефон",
    "по какому телефону",
)
CLIENT_TOPICS = (
    (("в кредит", "кредит", "рассрочк"), "хочет в кредит"),
    (("лизинг",), "хочет в лизинг"),
    (("обмен", "trade", "свою машин", "мой авто"), "интересует обмен"),
    (("дтп", "битая", "битый", "битые", "окрас", "крашен"), "про ДТП и кузов"),
    (("посмотреть", "приехать", "осмотр", "когда можно", "во сколько"), "хочет на осмотр"),
    (("торг", "скидк", "цену", "стоимость", "почём", "почем"), "вопрос цены"),
    (("такси", "каршеринг"), "спрашивал про такси"),
    (("автотек", "отчет", "отчёт"), "просил автотеку"),
    (("ндс", "юрлиц", "на компанию", "по счёту", "по счету"), "покупка на юрлицо"),
)


def _esc(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def pretty_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        d = digits
        return "+7 %s %s-%s-%s" % (d[1:4], d[4:7], d[7:9], d[9:11])
    return phone or ""


def clamp_work(dt: datetime) -> datetime:
    local = dt.astimezone(MSK)
    start = settings.nudge_hour_from
    end = settings.nudge_hour_to
    if local.hour >= end:
        local = (local + timedelta(days=1)).replace(
            hour=start, minute=0, second=0, microsecond=0
        )
    elif local.hour < start:
        local = local.replace(hour=start, minute=0, second=0, microsecond=0)
    return local


def ping_at(started: datetime, minutes: int) -> datetime:
    local = started.astimezone(MSK)
    if not in_working_hours(local):
        return clamp_work(local) + timedelta(minutes=minutes)
    due = local + timedelta(minutes=minutes)
    if in_working_hours(due):
        return due
    return clamp_work(due) + timedelta(minutes=minutes)


def next_ping(started: datetime, done: list[int], now: datetime | None = None) -> int | None:
    moment = (now or now_msk()).astimezone(MSK)
    if not in_working_hours(moment):
        return None
    sent = set(int(x) for x in (done or []))
    for minutes in PING_MINUTES:
        if minutes in sent:
            continue
        if moment >= ping_at(started, minutes):
            return minutes
    return None


def _first_sentence(text: str, limit: int = 110) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    for sep in ".!?":
        pos = clean.find(sep)
        if 8 <= pos <= limit:
            take = pos + 1 if sep == "?" else pos
            clean = clean[:take].strip()
            break
    if len(clean) > limit:
        clean = clean[: limit - 1].rstrip(" ,;") + "…"
    return clean


def _skip_bit(text: str) -> bool:
    low = " ".join(str(text or "").split()).lower().rstrip(".!?")
    if len(low) < 4:
        return True
    if any(low.startswith(p) for p in SKIP_BRIEF_START):
        return True
    return any(p in low for p in SKIP_BRIEF_HAS)


def _chunks(text: str) -> list[str]:
    raw = " ".join(str(text or "").split())
    if not raw:
        return []
    parts: list[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        if ch in ".!?":
            bit = buf.strip()
            if bit:
                parts.append(bit)
            buf = ""
    tail = buf.strip()
    if tail:
        parts.append(tail)
    return parts or [raw]


def _useful_bit(text: str) -> str:
    for part in _chunks(text):
        if _skip_bit(part):
            continue
        bit = _first_sentence(part)
        if bit:
            return bit.rstrip(".!?")
    return ""


def _client_blob(history: list[dict] | None) -> str:
    parts = []
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if text:
            parts.append(text.lower().replace("ё", "е"))
    return " ".join(parts)


def _topic_line(history: list[dict] | None) -> str:
    blob = _client_blob(history)
    if not blob:
        return ""
    found: list[str] = []
    for keys, label in CLIENT_TOPICS:
        if any(key in blob for key in keys):
            found.append(label)
    if not found:
        return ""
    if len(found) == 1:
        return found[0]
    return "%s, %s" % (found[0], found[1])


def _cap(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    return clean[0].upper() + clean[1:]


def brief_from_history(history: list[dict] | None, reason: str = "") -> str:
    """Выжимка для менеджера: интерес клиента и что уже закрыли. Не переписка."""
    facts: list[str] = []
    for msg in history or []:
        if msg.get("role") != "assistant":
            continue
        bit = _useful_bit(str(msg.get("content") or ""))
        if bit and bit not in facts:
            facts.append(bit)
    parts: list[str] = []
    topic = _topic_line(history)
    if topic:
        parts.append(_cap(topic))
    if facts:
        parts.append(". ".join(facts[-3:]))
    body = ". ".join(p.rstrip(".") for p in parts if p)
    if body and not body.endswith((".", "!", "?")):
        body += "."
    if len(body) > BRIEF_LIMIT:
        body = body[: BRIEF_LIMIT - 1].rstrip(" ,;") + "…"
    return body


def brief_from_thread(thread: list | None, reason: str = "") -> str:
    fake: list[dict] = []
    for item in thread or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        who = str(item[0])
        role = "assistant" if who in {"Никита", "assistant"} else "user"
        fake.append({"role": role, "content": str(item[1] or "")})
    return brief_from_history(fake, reason)


def compact_thread(history: list[dict] | None) -> list[list[str]]:
    picked: list[list[str]] = []
    for msg in history or []:
        role = msg.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if not text:
            continue
        if len(text) > LINE_LIMIT:
            text = text[: LINE_LIMIT - 3] + "..."
        who = "Клиент" if role == "user" else "Никита"
        picked.append([who, text])
    return picked[-THREAD_LIMIT:]


def format_alert(snap: dict, ping: int = 0) -> str:
    wait = snap.get("wait") or (WAIT_CALL if snap.get("phone") else WAIT_CHAT)
    head = HEAD.get((wait, ping)) or HEAD[(WAIT_CHAT, 0)]
    lines = [head, ""]
    name = snap.get("name") or "без имени"
    car = snap.get("car") or "машина не названа"
    lines.append("▪️ <b>Авто:</b> %s" % _esc(car))
    lines.append("▪️ <b>Клиент:</b> %s" % _esc(name))
    if snap.get("phone"):
        lines.append("▪️ <b>Телефон:</b> %s" % _esc(pretty_phone(snap["phone"])))
    lines.append("▪️ <b>Канал:</b> %s" % _esc(snap.get("channel") or "чат"))
    why = REASON_LINE.get(snap.get("reason") or "", "")
    if why:
        lines.append("▪️ <b>Повод:</b> %s" % _esc(why))
    if snap.get("lead_url"):
        lines.append("▪️ <b>Сделка:</b> %s" % _esc(snap["lead_url"]))
    brief = (snap.get("brief") or "").strip()
    if not brief:
        brief = brief_from_thread(snap.get("thread"), snap.get("reason") or "")
    if brief:
        lines.append("▪️ <b>Контекст:</b> %s" % _esc(" ".join(brief.split())))
    return "\n".join(lines)


def take_keyboard(token: str, wait: str = WAIT_CALL) -> dict:
    label = "📞 Звоню" if wait == WAIT_CALL else "✍️ Беру"
    return {"inline_keyboard": [[{"text": label, "callback_data": "take:%s" % token}]]}


def format_done(snap: dict, line: str) -> str:
    lines = format_alert(snap, 0).splitlines()
    if lines:
        lines[0] = "✅ <b>Связались</b> | DIVO"
    if len(lines) >= 2 and lines[1] == "":
        lines.insert(2, line)
    else:
        lines.insert(1, line)
    return "\n".join(lines)


def format_taken(snap: dict, who: str, when: str) -> str:
    return format_done(
        snap,
        "Взял %s в %s. Напоминать не буду." % (_esc(who), _esc(when)),
    )


class AlertBot:
    def __init__(self) -> None:
        self.token = settings.alert_bot_token
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))
        self.offset = 0
        self.chats = set(settings.alert_chat_ids)
        self.on_take = None
        self._load()

    def _path(self) -> Path:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        return settings.state_dir / "_alerts.json"

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in data.get("chats") or []:
            try:
                self.chats.add(int(item))
            except (TypeError, ValueError):
                continue
        try:
            self.offset = int(data.get("offset") or 0)
        except (TypeError, ValueError):
            self.offset = 0

    def _save(self) -> None:
        self._path().write_text(
            json.dumps(
                {"chats": sorted(self.chats), "offset": self.offset},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def ready(self) -> bool:
        return bool(self.token) and bool(self._targets())

    def _targets(self) -> set[int]:
        """Если в .env задан ALERT_CHAT_ID, пишем только туда."""
        if settings.alert_chat_ids:
            return set(settings.alert_chat_ids)
        return set(self.chats)

    async def close(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, payload: dict | None = None) -> dict:
        if not self.token:
            return {}
        url = "https://api.telegram.org/bot%s/%s" % (self.token, method)
        resp = await self.client.post(url, json=payload or {})
        data = resp.json()
        if not data.get("ok"):
            params = data.get("parameters") or {}
            migrate = params.get("migrate_to_chat_id")
            if migrate:
                raise RuntimeError("migrated:%s:%s" % (migrate, data.get("description")))
            raise RuntimeError("%s: %s" % (method, data.get("description")))
        return data.get("result")

    async def listen(self, timeout: int = 0) -> None:
        """Запоминает чаты и ловит кнопку Звоню / Беру."""
        if not self.token:
            return
        try:
            result = await self._call(
                "getUpdates",
                {
                    "offset": self.offset,
                    "timeout": int(timeout),
                    "allowed_updates": ["message", "my_chat_member", "callback_query"],
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("alert getUpdates: %s", exc)
            return
        rows = result if isinstance(result, list) else []
        changed = False
        for upd in rows:
            self.offset = max(self.offset, int(upd.get("update_id") or 0) + 1)
            changed = True
            cb = upd.get("callback_query") or {}
            if cb:
                if self.on_take:
                    try:
                        await self.on_take(cb)
                    except Exception:
                        log.exception("кнопка алерта")
                continue
            msg = upd.get("message") or {}
            member = upd.get("my_chat_member") or {}
            chat = msg.get("chat") or member.get("chat") or {}
            chat_id = chat.get("id")
            if not chat_id:
                continue
            if int(chat_id) not in self.chats:
                self.chats.add(int(chat_id))
                log.info("алерты: новый чат %s (%s)", chat_id, chat.get("title") or chat.get("type"))
                try:
                    await self._call(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": "✅ DIVO: алерты подключены. Сюда придут номер, звонок и эскалация.",
                            "disable_web_page_preview": True,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("приветствие алерта не ушло: %s", exc)
        if changed:
            self._save()

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        if not callback_id:
            return
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:180]
        try:
            await self._call("answerCallbackQuery", payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("answerCallbackQuery: %s", exc)

    async def send_to(
        self, chat_id: int, text: str, markup: dict | None = None
    ) -> int | None:
        if not self.token:
            log.warning("алерт без токена: %s", text.replace("\n", " ")[:200])
            return None
        for attempt in range(2):
            try:
                payload: dict = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if markup:
                    payload["reply_markup"] = markup
                result = await self._call("sendMessage", payload)
                if isinstance(result, dict) and result.get("message_id"):
                    return int(result["message_id"])
                return None
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if msg.startswith("migrated:"):
                    try:
                        chat_id = int(msg.split(":")[1])
                    except (IndexError, ValueError):
                        log.warning("алерт не ушёл в %s: %s", chat_id, exc)
                        return None
                    log.warning("группа стала супергруппой, пишу в %s", chat_id)
                    continue
                wait = 0
                if "retry after" in msg.lower():
                    try:
                        wait = int(msg.rsplit(" ", 1)[-1])
                    except ValueError:
                        wait = 5
                if attempt == 0 and wait:
                    log.warning("алерт лимит, жду %s сек", wait)
                    await asyncio.sleep(wait + 1)
                    continue
                log.warning("алерт не ушёл в %s: %s", chat_id, exc)
                return None
        return None

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        markup: dict | None = None,
    ) -> bool:
        payload: dict = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": markup or {"inline_keyboard": []},
        }
        try:
            await self._call("editMessageText", payload)
            return True
        except Exception as exc:  # noqa: BLE001
            if "message is not modified" in str(exc).lower():
                return True
            log.warning("edit %s/%s: %s", chat_id, message_id, exc)
            return False

    async def delete(self, chat_id: int, message_id: int) -> bool:
        try:
            await self._call(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": int(message_id)},
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("delete %s/%s: %s", chat_id, message_id, exc)
            return False

    async def send(self, text: str, markup: dict | None = None) -> tuple[int, int] | None:
        if not self._targets():
            log.warning("алерт без чата менеджеров: %s", text.replace("\n", " ")[:200])
            return None
        last = None
        for chat_id in list(self._targets()):
            mid = await self.send_to(chat_id, text, markup)
            if mid:
                last = (int(chat_id), int(mid))
        return last
