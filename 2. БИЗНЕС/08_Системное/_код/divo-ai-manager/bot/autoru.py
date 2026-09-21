"""Клиент чатов Авто.ру. Только чтение и отправка текста.

Webhook не регистрируем: на кабинете уже висит виджет amo, второй URL
его снимет. Сток и apiauto-сессия видят объявления. Входящие салона
живёт в кабинете: postDealerChats / chatMessages / chatPostMessage.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from bot.config import settings

log = logging.getLogger("autoru")
BASE = "https://apiauto.ru/1.0"
CABINET = "https://cabinet.auto.ru"
CABINET_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
OFFER_TTL = 180
ROOM_OFFER = "ROOM_TYPE_OFFER"
DEALER_PAGES = 2
DEALER_PAGE_SIZE = 40


class AutoruError(RuntimeError):
    pass


def _price_str(raw: Any) -> str:
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return "%s ₽" % f"{n:,}".replace(",", " ")


def listing_from_offer(offer: dict) -> dict:
    ci = offer.get("car_info") or {}
    mark = (ci.get("mark_info") or {}).get("name") or ci.get("mark") or ""
    model = (ci.get("model_info") or {}).get("name") or ci.get("model") or ""
    docs = offer.get("documents") or {}
    year = docs.get("year") or ""
    price_info = offer.get("price_info") or {}
    price = price_info.get("price") or price_info.get("rur_price") or 0
    url = offer.get("url") or offer.get("mobile_url") or ""
    title = " ".join(str(x) for x in (mark, model, year) if x).strip()
    return {
        "item_id": str(offer.get("id") or ""),
        "title": title,
        "price": _price_str(price),
        "url": url,
    }


def source_id(room: dict) -> str:
    offer = ((room.get("subject") or {}).get("offer") or {})
    src = offer.get("source") or {}
    return str(src.get("id") or "")


def listing_from_room(room: dict, catalog: dict[str, dict] | None = None) -> dict:
    oid = source_id(room)
    if oid and catalog and catalog.get(oid):
        return dict(catalog[oid])
    offer = ((room.get("subject") or {}).get("offer") or {})
    val = offer.get("value") if isinstance(offer.get("value"), dict) else {}
    listing = listing_from_offer(val) if val else {}
    if not listing.get("item_id"):
        listing["item_id"] = oid
    if not listing.get("title"):
        subj = room.get("subject") or {}
        title = subj.get("title_v2") or subj.get("title") or ""
        if isinstance(title, dict):
            title = title.get("title") or ""
        listing["title"] = "" if str(title) == "Чат" else str(title or "")
    return listing


def is_offer_room(room: dict) -> bool:
    return (room.get("room_type") or "") == ROOM_OFFER


class Autoru:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._own: dict[str, dict] = {}
        self._own_at = 0.0
        self._csrf = ""
        self._jar_ready = False

    async def close(self) -> None:
        await self.client.aclose()

    def _headers(self) -> dict[str, str]:
        sid = settings.autoru_session_id
        return {
            "x-authorization": settings.autoru_vertis_key,
            "x-session-id": sid,
            "x-session": sid,
            "Accept": "application/json",
        }

    def _use_cabinet(self) -> bool:
        return bool(settings.autoru_cabinet_cookie)

    def _load_cabinet_jar(self) -> None:
        if self._jar_ready:
            return
        raw = settings.autoru_cabinet_cookie
        for part in raw.split(";"):
            item = part.strip()
            if "=" not in item:
                continue
            name, value = item.split("=", 1)
            name = name.strip()
            if not name:
                continue
            self.client.cookies.set(name, value, domain=".auto.ru", path="/")
        self._jar_ready = True

    def _cabinet_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": CABINET,
            "Referer": CABINET + "/chats/",
            "User-Agent": CABINET_UA,
            "x-requested-with": "XMLHttpRequest",
        }
        if self._csrf:
            headers["x-csrf-token"] = self._csrf
        return headers

    async def _refresh_csrf(self) -> None:
        self._load_cabinet_jar()
        resp = await self.client.get(
            CABINET + "/chats/",
            headers={"User-Agent": CABINET_UA, "Accept": "text/html"},
        )
        token = self.client.cookies.get("_csrf_token") or ""
        if token:
            self._csrf = token
        if resp.status_code >= 400:
            raise AutoruError("GET /chats/ %s" % resp.status_code)

    async def _cabinet(self, resource: str, payload: dict) -> Any:
        self._load_cabinet_jar()
        if not self._csrf:
            await self._refresh_csrf()
        url = "%s/-/ajax/%s/" % (CABINET, resource)
        resp = await self.client.post(
            url, headers=self._cabinet_headers(), json=payload
        )
        if resp.status_code in {401, 403}:
            await self._refresh_csrf()
            resp = await self.client.post(
                url, headers=self._cabinet_headers(), json=payload
            )
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:300]}
        if resp.status_code >= 400:
            raise AutoruError("POST %s %s %s" % (resource, resp.status_code, data))
        if isinstance(data, dict) and data.get("status") == "ERROR":
            raise AutoruError("POST %s %s" % (resource, data))
        return data

    async def _call(self, method: str, path: str, **kwargs) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._headers())
        resp = await self.client.request(method, BASE + path, headers=headers, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:300]}
        if resp.status_code >= 400:
            raise AutoruError("%s %s %s" % (method, path, data))
        if isinstance(data, dict) and data.get("status") == "ERROR":
            raise AutoruError("%s %s %s" % (method, path, data))
        return data

    async def rooms(self) -> list[dict]:
        if self._use_cabinet():
            return await self._dealer_rooms()
        data = await self._call("GET", "/chat/room/light")
        return list(data.get("rooms") or [])

    async def _dealer_rooms(self) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(1, DEALER_PAGES + 1):
            data = await self._cabinet(
                "cabinet/postDealerChats",
                {
                    "filter": {},
                    "pagination": {"page": page, "page_size": DEALER_PAGE_SIZE},
                },
            )
            rows = list(data.get("chats") or [])
            for chat in rows:
                if not isinstance(chat, dict):
                    continue
                rid = str(chat.get("chat_room_id") or chat.get("id") or "")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                item = dict(chat)
                item["id"] = rid
                out.append(item)
            pag = data.get("pagination") or {}
            total_pages = int(pag.get("total_page_count") or page)
            if page >= total_pages or len(rows) < DEALER_PAGE_SIZE:
                break
        log.info("авто.ру кабинет: %d чатов на хвосте", len(out))
        return out

    async def room(self, room_id: str) -> dict:
        if self._use_cabinet():
            for item in await self._dealer_rooms():
                if str(item.get("id") or "") == str(room_id):
                    return item
            return {}
        data = await self._call("GET", "/chat/room/by-id", params={"id": room_id})
        rooms = data.get("rooms") or []
        return rooms[0] if rooms else {}

    async def messages(self, room_id: str, count: int = 50) -> list[dict]:
        if self._use_cabinet():
            data = await self._cabinet(
                "chat/chatMessages",
                {"room_id": room_id, "count": count, "asc": True},
            )
            return list(data.get("messages") or [])
        data = await self._call(
            "GET",
            "/chat/message",
            params={"room_id": room_id, "count": count, "asc": "true"},
        )
        return list(data.get("messages") or [])

    async def send_text(self, room_id: str, text: str) -> dict:
        body = text.strip()
        if len(body) > 1000:
            body = body[:997] + "..."
        if self._use_cabinet():
            data = await self._cabinet(
                "chat/chatPostMessage",
                {"room_id": room_id, "text": body},
            )
            if isinstance(data, dict) and "message" not in data and data.get("id"):
                return {"message": data}
            return data if isinstance(data, dict) else {}
        return await self._call(
            "POST",
            "/chat/message",
            json={
                "room_id": room_id,
                "payload": {"content_type": "TEXT_PLAIN", "value": body},
            },
        )

    async def own_offers(self, force: bool = False) -> dict[str, dict]:
        now = time.time()
        if self._own and not force and now - self._own_at < OFFER_TTL:
            return self._own
        out: dict[str, dict] = {}
        page = 1
        while page <= 10:
            data = await self._call(
                "GET",
                "/user/offers/cars",
                params={"status": "ACTIVE", "page_size": 50, "page": page},
            )
            for offer in data.get("offers") or []:
                item = listing_from_offer(offer)
                if item.get("item_id"):
                    out[str(item["item_id"])] = item
            pag = data.get("pagination") or {}
            total_pages = int(pag.get("total_page_count") or 1)
            if page >= total_pages:
                break
            page += 1
        self._own = out
        self._own_at = now
        log.info("авто.ру объявлений в продаже: %d", len(out))
        return out
