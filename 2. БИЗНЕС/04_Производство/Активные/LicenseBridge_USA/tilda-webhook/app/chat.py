"""Чтение и классификация чата контакта (чаты синкаются в Kommo через Wazzup).

Классы:
- "none"  — переписки нет → безопасный soft-merge.
- "light" — несколько входящих текстовых сообщений, без ответов менеджера и без
            вложений, содержимое читается через API → можно скопировать в примечание
            и сделать soft-merge (при наличии номера для ответа в канале).
- "rich"  — вложения / диалог с ответами менеджера / много сообщений / не читается
            через API → только ручная native-merge (тег), не удалять.

Принцип: при любой неуверенности деградируем в "rich" (не удаляем вслепую).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.kommo.client import KommoClient

# Типы note, которые Kommo/Wazzup использует для сообщений мессенджеров.
_MESSAGE_NOTE_TYPES = {"amomessage", "chat_message", "message_cashier"}
_ATTACHMENT_NOTE_TYPES = {"attachment", "call_in", "call_out"}


@dataclass
class ChatInfo:
    has_chat: bool = False
    readable: bool = True
    message_count: int = 0
    has_attachments: bool = False
    has_manager_reply: bool = False
    messages: list[str] = field(default_factory=list)

    def classify(self) -> str:
        if not self.has_chat:
            return "none"
        if not self.readable or self.has_attachments or self.has_manager_reply:
            return "rich"
        if self.message_count > settings.light_chat_max_messages:
            return "rich"
        return "light"


def note_text(note: dict[str, Any]) -> str:
    params = note.get("params") or {}
    for key in ("text", "message", "comment"):
        v = params.get(key)
        if v:
            return str(v)
    return ""


def _is_incoming(note: dict[str, Any]) -> bool:
    """True если сообщение от клиента (входящее). Wazzup кладёт направление в
    params.in/out или в note_type. При неоднозначности считаем исходящим (безопаснее
    — трактуется как ответ менеджера → rich)."""
    params = note.get("params") or {}
    if "in" in params:
        return bool(params.get("in"))
    nt = note.get("note_type", "")
    if "in" in nt:
        return True
    if "out" in nt:
        return False
    return False


def read_contact_chat(client: KommoClient, contact_id: int) -> ChatInfo:
    """Best-effort чтение переписки контакта. API Kommo по amojo-чатам ограничен:
    если сообщения не читаются надёжно — readable=False → "rich"."""
    info = ChatInfo()
    try:
        notes = client.get_notes("contacts", contact_id)
    except Exception:
        # не смогли прочитать — считаем, что чат может быть и он нечитаем
        info.has_chat = True
        info.readable = False
        return info

    for n in notes:
        nt = n.get("note_type", "")
        if nt in _ATTACHMENT_NOTE_TYPES:
            info.has_chat = True
            info.has_attachments = True
            continue
        if nt not in _MESSAGE_NOTE_TYPES:
            continue
        info.has_chat = True
        text = note_text(n)
        if not text:
            # сообщение есть, но текст не извлёкся → нечитаемо
            info.readable = False
            continue
        if _is_incoming(n):
            info.message_count += 1
            info.messages.append(text)
        else:
            info.has_manager_reply = True

    # talks (открытые беседы) — сигнал живого диалога
    try:
        data = client.get_contact(contact_id, with_="leads")
        talks = ((data or {}).get("_embedded", {}) or {}).get("talks", []) or []
        if talks:
            info.has_chat = True
    except Exception:
        pass

    return info


def can_reply_in_channel(contact: dict[str, Any]) -> bool:
    """Ответ в том же канале сохраняется, если у выжившего есть телефон
    (WhatsApp/Telegram привязаны к номеру). Instagram-ник без номера — нельзя."""
    from app.identity import contact_phones

    return bool(contact_phones(contact))
