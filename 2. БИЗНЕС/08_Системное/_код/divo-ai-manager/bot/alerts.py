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

PING_MINUTES = (0, 5, 10, 15)
WAIT_CALL = "call"
WAIT_CHAT = "chat"

HEAD = {
    (WAIT_CALL, 0): "📞 <b>Ждёт звонка</b> | DIVO",
    (WAIT_CALL, 5): "⏰ <b>5 мин</b> | всё ещё ждёт звонка",
    (WAIT_CALL, 10): "🔥 <b>10 мин</b> | не взяли трубку",
    (WAIT_CALL, 15): "🚨 <b>15 мин</b> | клиент остывает",
    (WAIT_CHAT, 0): "✍️ <b>Ждёт ответ в чате</b> | DIVO",
    (WAIT_CHAT, 5): "⏰ <b>5 мин</b> | чат без ответа",
    (WAIT_CHAT, 10): "🔥 <b>10 мин</b> | менеджер молчит",
    (WAIT_CHAT, 15): "🚨 <b>15 мин</b> | лояльность падает",
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
CUE = {
    WAIT_CALL: "Звони как Никита. Клиент уже общался в чате, не начинай с нуля.",
    WAIT_CHAT: "Пиши как Никита. Клиент уже в диалоге, не представляйся.",
}


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


def compact_thread(history: list[dict] | None) -> list[list[str]]:
    """Последние реплики, чтобы менеджер вошёл в тот же разговор."""
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
    cue = CUE.get(wait) or CUE[WAIT_CHAT]
    lines.append("")
    lines.append("👉 %s" % cue)
    thread = snap.get("thread")
    if not thread and snap.get("dialog"):
        thread = []
        for raw in str(snap["dialog"]).splitlines()[-THREAD_LIMIT:]:
            if ": " in raw:
                who, text = raw.split(": ", 1)
                thread.append([who, text])
    if thread:
        lines.append("")
        lines.append("<b>Диалог:</b>")
        for item in thread:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                who, text = item[0], item[1]
            else:
                continue
            lines.append("%s: <i>%s</i>" % (_esc(who), _esc(text)))
    else:
        ask = (snap.get("ask") or "").strip()
        if ask:
            short = ask if len(ask) <= LINE_LIMIT else ask[: LINE_LIMIT - 3] + "..."
            lines.append("")
            lines.append("▪️ <b>Запрос:</b> %s" % _esc(short))
    return "\n".join(lines)


class AlertBot:
    def __init__(self) -> None:
        self.token = settings.alert_bot_token
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))
        self.offset = 0
        self.chats = set(settings.alert_chat_ids)
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
            raise RuntimeError("%s: %s" % (method, data.get("description")))
        return data.get("result")

    async def listen(self) -> None:
        """Запоминает чаты, куда написали боту или куда его добавили."""
        if not self.token:
            return
        try:
            result = await self._call(
                "getUpdates",
                {
                    "offset": self.offset,
                    "timeout": 0,
                    "allowed_updates": ["message", "my_chat_member"],
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

    async def send_to(self, chat_id: int, text: str) -> bool:
        if not self.token:
            log.warning("алерт без токена: %s", text.replace("\n", " ")[:200])
            return False
        for attempt in range(2):
            try:
                await self._call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                return True
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
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
                return False
        return False

    async def send(self, text: str) -> bool:
        if not self._targets():
            log.warning("алерт без чата менеджеров: %s", text.replace("\n", " ")[:200])
            return False
        ok = True
        for chat_id in list(self._targets()):
            if not await self.send_to(chat_id, text):
                ok = False
        return ok
