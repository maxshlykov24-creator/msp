"""Переписка мессенджеров: единственный способ узнать о ней через API Kommo.

Почему отдельный модуль, а не `chat.py`. Сообщения WhatsApp и SMS в этом аккаунте
**не попадают в примечания**: `amomessage` в базе ноль, в сделке лежат только
звонки и common-заметки. `GET /leads/{id}?with=talks` на сделке отдаёт пусто, а
`GET /events?filter[entity_id]=<сделка>` чат-события не возвращает вовсе
(проверено 19.08.2026 на сделке 29892373: 21 сообщение клиента, а по карточке —
ни одного чат-события).

Отдаёт их только общий поток `GET /events` с фильтром по типу. Поэтому считать
переписку можно лишь окном по всему аккаунту, а затем группировать по сделке.
Из этого следует и цена: окно кешируется в процессе, иначе каждый дедуп тянул бы
тысячи событий.

Из-за этого же наш прежний детектор `_lead_has_chat` (notes + talks) всегда
отвечал «переписки нет», и подсказка «на дубле была переписка» в живую карточку
не попадала ни разу.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

log = logging.getLogger("chat_events")

INCOMING = "incoming_chat_message"
OUTGOING = "outgoing_chat_message"

# origin из события → как называть канал человеку
CHANNELS = {
    "com.wazzup.whatsapp": "WhatsApp",
    "com.wazzup24-1": "WhatsApp (второй канал)",
    "com.wazzup.instagram": "Instagram",
    "com.ringcentral.sms": "SMS",
    "tech.widget.e-chat": "e-chat",
    "instagram_business": "Instagram",
    "facebook": "Facebook",
    "com.apple.bm": "Apple Messages",
}


def channel_name(origin: str) -> str:
    return CHANNELS.get(origin, origin or "неизвестный канал")


def lead_url(lead_id: int) -> str:
    """Ссылка на карточку для человека: из API-базы делаем адрес интерфейса."""
    base = settings.kommo_base.split("/api/")[0]
    return f"{base}/leads/detail/{lead_id}"


@dataclass
class ChatStats:
    """Переписка по одной сделке за окно."""

    lead_id: int
    incoming: int = 0
    outgoing: int = 0
    outgoing_human: int = 0       # ответ человека, а не бота (created_by > 0)
    last_in: int = 0              # unix ts последнего сообщения клиента
    last_human_out: int = 0       # unix ts последнего ответа человека
    origins: set[str] = field(default_factory=set)

    @property
    def unanswered(self) -> bool:
        """Клиент написал, и человек после этого не ответил.

        Ответ бота ответом не считается: формулировка та же, что в июльском
        аудите (63,6% чатов без ответа менеджера), иначе цифры несравнимы."""
        return bool(self.last_in) and self.last_human_out < self.last_in

    def channels(self) -> str:
        return ", ".join(sorted({channel_name(o) for o in self.origins})) or "—"

    def describe(self) -> str:
        """Строка для примечания в карточке."""
        parts = [f"клиент {self.incoming}", f"исходящих {self.outgoing}"]
        if self.outgoing_human != self.outgoing:
            parts.append(f"из них ботом {self.outgoing - self.outgoing_human}")
        when = ""
        if self.last_in:
            local = datetime.fromtimestamp(self.last_in, tz=ZoneInfo(settings.tz))
            when = f", последнее от клиента {local:%d.%m %H:%M}"
        return f"{', '.join(parts)}{when}. Канал: {self.channels()}"


def _origin(event: dict[str, Any]) -> str:
    values = event.get("value_after") or []
    if values and isinstance(values[0], dict):
        return str((values[0].get("message") or {}).get("origin") or "")
    return ""


def fetch(client: Any, since_ts: int, until_ts: int | None = None) -> dict[int, ChatStats]:
    """Переписка по всем сделкам за окно. Ключ — id сделки."""
    out: dict[int, ChatStats] = {}
    for event_type in (INCOMING, OUTGOING):
        params: dict[str, Any] = {"filter[type]": event_type,
                                 "filter[created_at][from]": since_ts,
                                 "limit": 250}
        if until_ts:
            params["filter[created_at][to]"] = until_ts
        for event in client.paginate("/events", "events", params=params, max_pages=60):
            if event.get("entity_type") != "lead":
                continue           # чат на карточке контакта считаем отдельно
            lead_id = int(event["entity_id"])
            st = out.setdefault(lead_id, ChatStats(lead_id=lead_id))
            created = int(event.get("created_at") or 0)
            author = int(event.get("created_by") or 0)
            origin = _origin(event)
            if origin:
                st.origins.add(origin)
            if event_type == INCOMING:
                st.incoming += 1
                st.last_in = max(st.last_in, created)
            else:
                st.outgoing += 1
                if author > 0:
                    st.outgoing_human += 1
                    st.last_human_out = max(st.last_human_out, created)
    return out


# Окно кешируется: за дедуп-проход к нему обращаются десятки раз, а каждый проход
# по /events — это десятки страниц.
_cache: dict[int, tuple[float, dict[int, ChatStats]]] = {}


def recent(client: Any, days: int | None = None, ttl: float = 300.0) -> dict[int, ChatStats]:
    days = days or settings.chat_digest_days
    hit = _cache.get(days)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    since = int(time.time()) - days * 86400
    stats = fetch(client, since)
    _cache[days] = (time.monotonic(), stats)
    log.info("chat window %s дней: сделок с перепиской %s", days, len(stats))
    return stats


def for_lead(client: Any, lead_id: int, days: int | None = None) -> ChatStats | None:
    return recent(client, days).get(int(lead_id))


def utcnow_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())
