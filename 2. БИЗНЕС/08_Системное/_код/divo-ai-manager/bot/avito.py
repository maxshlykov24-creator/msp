"""Клиент Messenger API Авито. Только чтение и отправка текста.

Webhook не регистрируем: на аккаунте уже висит amo, второй URL его не дублирует
надёжно, а замена погасит виджет. Новые входящие забираем опросом.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from bot.config import settings

log = logging.getLogger("avito")
BASE = "https://api.avito.ru"


class AvitoError(RuntimeError):
    pass


class Avito:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._token = ""
        self._token_at = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    async def _auth(self) -> str:
        if self._token and time.time() - self._token_at < 80000:
            return self._token
        resp = await self.client.post(
            "%s/token/" % BASE,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.avito_client_id,
                "client_secret": settings.avito_client_secret,
            },
        )
        data = resp.json()
        token = data.get("access_token")
        if resp.status_code != 200 or not token:
            raise AvitoError("token %s %s" % (resp.status_code, data))
        self._token = token
        self._token_at = time.time()
        return token

    async def _call(self, method: str, path: str, **kwargs) -> Any:
        token = await self._auth()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = "Bearer %s" % token
        resp = await self.client.request(method, BASE + path, headers=headers, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:300]}
        if resp.status_code >= 400:
            raise AvitoError("%s %s %s" % (method, path, data))
        return data

    @property
    def user_id(self) -> int:
        return settings.avito_user_id

    async def chats(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        params = {"limit": limit, "offset": 0}
        if unread_only:
            params["unread_only"] = "true"
        data = await self._call(
            "GET",
            "/messenger/v2/accounts/%s/chats" % self.user_id,
            params=params,
        )
        return list(data.get("chats") or [])

    async def messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        data = await self._call(
            "GET",
            "/messenger/v3/accounts/%s/chats/%s/messages/" % (self.user_id, chat_id),
            params={"limit": limit},
        )
        return list(data.get("messages") or [])

    async def item(self, item_id: int | str) -> dict:
        return await self._call(
            "GET",
            "/core/v1/accounts/%s/items/%s" % (self.user_id, item_id),
        )

    async def send_text(self, chat_id: str, text: str) -> dict:
        body = text.strip()
        if len(body) > 1000:
            body = body[:997] + "..."
        return await self._call(
            "POST",
            "/messenger/v1/accounts/%s/chats/%s/messages" % (self.user_id, chat_id),
            json={"type": "text", "message": {"text": body}},
        )
