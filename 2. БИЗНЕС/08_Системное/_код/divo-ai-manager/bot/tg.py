"""Тонкий клиент Telegram Bot API: long polling, без внешних фреймворков."""
from __future__ import annotations

import logging

import httpx

from bot import human
from bot.config import settings

log = logging.getLogger("tg")


class Telegram:
    def __init__(self, token: str) -> None:
        self.base = "https://api.telegram.org/bot%s" % token
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, **payload) -> dict:
        resp = await self.client.post("%s/%s" % (self.base, method), json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError("%s: %s" % (method, data.get("description")))
        return data.get("result")

    async def me(self) -> dict:
        return await self.call("getMe")

    async def updates(self, offset: int, timeout: int = 50) -> list[dict]:
        try:
            return await self.call(
                "getUpdates",
                offset=offset,
                timeout=timeout,
                allowed_updates=["message"],
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("getUpdates: %s", exc)
            return []

    async def send(self, chat_id: int | str, text: str) -> None:
        await self.call("sendMessage", chat_id=chat_id, text=human.for_chat(text))

    async def send_document(self, chat_id: int | str, path, caption: str = "") -> None:
        """Любой файл как документ — не проверяет формат, годится для заглушек."""
        with open(path, "rb") as fh:
            resp = await self.client.post(
                "%s/sendDocument" % self.base,
                data={"chat_id": str(chat_id), "caption": caption},
                files={"document": (path.name, fh)},
            )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError("sendDocument: %s" % data.get("description"))

    async def typing(self, chat_id: int | str) -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action="typing")
        except (httpx.HTTPError, RuntimeError):
            pass  # индикатор набора не критичен

    async def notify_admin(self, text: str) -> None:
        from bot import crm
        from bot.alerts import _esc

        if crm.bot and crm.bot.ready:
            try:
                await crm.bot.send(_esc(text))
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("алерт через группу не ушёл: %s", exc)
        if not settings.admin_chat_id:
            return
        try:
            await self.send(settings.admin_chat_id, text)
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("админу не ушло: %s", exc)

    async def notify_owner(self, text: str) -> None:
        """Служебное тебе в личку, не в группу менеджеров."""
        from bot import crm
        from bot.alerts import _esc

        raw = (settings.admin_chat_id or "").strip()
        if not raw:
            log.warning("нет TELEGRAM_ADMIN_CHAT_ID, служебный алерт некуда: %s", text[:120])
            return
        try:
            chat_id = int(raw)
        except ValueError:
            log.warning("TELEGRAM_ADMIN_CHAT_ID не число: %s", raw)
            return
        if crm.bot and crm.bot.token:
            try:
                await crm.bot.send_to(chat_id, _esc(text))
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("личка через alert-бота не ушла: %s", exc)
        try:
            await self.send(chat_id, text)
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("личка через основного бота не ушла: %s", exc)
