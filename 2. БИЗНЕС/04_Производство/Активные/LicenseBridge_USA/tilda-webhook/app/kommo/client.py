"""Kommo API v4 client: rate limit, retry, типизированные ошибки.

Все мутации проходят через этот клиент. В shadow-режиме мутации не вызываются
(решение принимает вызывающий код по settings.shadow_mode / feature-flag).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

log = logging.getLogger("kommo")


class RateLimiter:
    """Не больше max_rps запросов в секунду (лимит Kommo ~7 rps)."""

    def __init__(self, max_rps: float) -> None:
        self._min_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class KommoError(Exception):
    """Неретраибельная ошибка Kommo (4xx кроме 429)."""


class KommoRetryable(KommoError):
    """Ретраибельная ошибка (429/5xx/сеть)."""


class KommoClient:
    def __init__(self, base: str | None = None, token: str | None = None) -> None:
        self.base = (base or settings.kommo_base).rstrip("/")
        self.token = token or settings.kommo_token
        self._limiter = RateLimiter(settings.kommo_max_rps)
        self._client = httpx.Client(
            base_url=self.base,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KommoClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── низкоуровневый запрос ──
    @retry(
        retry=retry_if_exception_type((KommoRetryable, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any] | None:
        self._limiter.wait()
        try:
            resp = self._client.request(method, path, params=params, json=json)
        except httpx.TransportError as exc:
            log.warning("kommo transport error %s %s: %s", method, path, exc)
            raise
        if resp.status_code in (200, 201):
            body = resp.text
            return resp.json() if body else None
        if resp.status_code in (202, 204):
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("kommo %s %s -> %s, retry", method, path, resp.status_code)
            raise KommoRetryable(f"{resp.status_code} on {path}")
        if resp.status_code in (401, 403):
            raise KommoError(f"kommo {resp.status_code}: токен/доступ ({path})")
        raise KommoError(f"kommo {resp.status_code} on {path}: {resp.text[:300]}")

    # ── generic ──
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Any) -> dict[str, Any] | None:
        return self._request("POST", path, json=json)

    def patch(self, path: str, json: Any) -> dict[str, Any] | None:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> dict[str, Any] | None:
        return self._request("DELETE", path)

    def paginate(
        self,
        path: str,
        embedded_key: str,
        params: dict[str, Any] | None = None,
        limit: int = 250,
        max_pages: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", limit)
        page = 1
        while page <= max_pages:
            params["page"] = page
            data = self.get(path, params)
            if not data:
                return
            items = data.get("_embedded", {}).get(embedded_key, [])
            if not items:
                return
            yield from items
            if not data.get("_links", {}).get("next"):
                return
            page += 1

    # ── account / users ──
    def account(self) -> dict[str, Any] | None:
        return self.get("/account")

    def users(self) -> list[dict[str, Any]]:
        return list(self.paginate("/users", "users"))

    def pipelines(self) -> list[dict[str, Any]]:
        data = self.get("/leads/pipelines")
        if not data:
            return []
        return data.get("_embedded", {}).get("pipelines", [])

    def custom_fields(self, entity: str) -> list[dict[str, Any]]:
        path = f"/{entity}/custom_fields"
        return list(self.paginate(path, "custom_fields"))

    # ── contacts ──
    def get_contact(self, contact_id: int, with_: str | None = "leads") -> dict[str, Any] | None:
        params = {"with": with_} if with_ else None
        return self.get(f"/contacts/{contact_id}", params)

    def search_contacts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self.get("/contacts", {"query": query, "limit": limit, "with": "leads"})
        if not data:
            return []
        return data.get("_embedded", {}).get("contacts", [])

    def create_contact(self, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
        return self.post("/contacts", payload)

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self.patch(f"/contacts/{contact_id}", payload)

    # Удаления контактов/сделок в API v4 нет: DELETE отдаёт 405 (политика Kommo,
    # проверено 2026-07-24). Дубли помечаются тегом — см. actions.mark_duplicate.

    # ── leads ──
    def get_lead(self, lead_id: int, with_: str = "contacts") -> dict[str, Any] | None:
        return self.get(f"/leads/{lead_id}", {"with": with_})

    def create_lead(self, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
        return self.post("/leads", payload)

    def update_lead(self, lead_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self.patch(f"/leads/{lead_id}", payload)

    def link_lead_contact(self, lead_id: int, contact_id: int, main: bool = False) -> None:
        # признак главного контакта называется is_main; main_contact API отвергает
        # (400 FieldNotExpected, проверено на боевом аккаунте 2026-07-24)
        payload = [{"to_entity_id": contact_id, "to_entity_type": "contacts",
                    "metadata": {"is_main": main}}]
        self.post(f"/leads/{lead_id}/link", payload)

    def unlink_lead_contact(self, lead_id: int, contact_id: int) -> None:
        payload = [{"to_entity_id": contact_id, "to_entity_type": "contacts"}]
        self.post(f"/leads/{lead_id}/unlink", payload)

    # ── notes / tasks / tags ──
    def get_notes(self, entity: str, entity_id: int) -> list[dict[str, Any]]:
        return list(self.paginate(f"/{entity}/{entity_id}/notes", "notes"))

    def add_note(self, entity: str, entity_id: int, text: str,
                 note_type: str = "common") -> dict[str, Any] | None:
        payload = [{"entity_id": entity_id, "note_type": note_type,
                    "params": {"text": text}}]
        return self.post(f"/{entity}/notes", payload)

    def add_note_raw(self, entity: str, note: dict[str, Any]) -> dict[str, Any] | None:
        return self.post(f"/{entity}/notes", [note])

    def create_task(self, entity: str, entity_id: int, text: str,
                    responsible_user_id: int, complete_till: int) -> dict[str, Any] | None:
        payload = [{"text": text, "complete_till": complete_till,
                    "entity_id": entity_id, "entity_type": entity,
                    "responsible_user_id": responsible_user_id}]
        return self.post("/tasks", payload)

    def set_tags(self, entity: str, entity_id: int, tags: list[str]) -> None:
        """Полностью заменяет теги (merge с существующими делает вызывающий код)."""
        payload = {"_embedded": {"tags": [{"name": t} for t in tags]}}
        self.patch(f"/{entity}/{entity_id}", payload)

    # ── chats / talks (best-effort, API ограничен) ──
    def get_lead_talks(self, lead_id: int) -> list[dict[str, Any]]:
        data = self.get_lead(lead_id, with_="contacts")
        return ((data or {}).get("_embedded", {}) or {}).get("talks", []) or []
