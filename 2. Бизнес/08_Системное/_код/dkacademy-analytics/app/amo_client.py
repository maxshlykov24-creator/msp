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

log = logging.getLogger("amo")


class RateLimiter:
    """Простой лимитер: не больше max_rps запросов в секунду (amo лимит ~7 rps)."""

    def __init__(self, max_rps: float = 5.0) -> None:
        self._min_interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class AmoError(Exception):
    pass


class AmoRetryable(AmoError):
    """Ошибки, при которых имеет смысл повторить (429, 5xx, сетевые)."""


class AmoClient:
    def __init__(self) -> None:
        self.base = settings.amo_base_url.rstrip("/")
        self.token = settings.amo_access_token
        self._limiter = RateLimiter(max_rps=5.0)
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

    @retry(
        retry=retry_if_exception_type((AmoRetryable, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._limiter.wait()
        try:
            resp = self._client.get(path, params=params)
        except httpx.TransportError as exc:  # сеть — повторяем
            log.warning("amo transport error on %s: %s", path, exc)
            raise

        if resp.status_code == 204:
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("amo %s -> %s, retry", path, resp.status_code)
            raise AmoRetryable(f"{resp.status_code} on {path}")
        if resp.status_code == 401:
            raise AmoError("amo 401: токен недействителен или истёк")
        if resp.status_code >= 400:
            raise AmoError(f"amo {resp.status_code} on {path}: {resp.text[:200]}")
        return resp.json()

    # ── высокоуровневые методы ──

    def account(self) -> dict[str, Any] | None:
        return self._get("/api/v4/account")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self._get(path, params)

    def paginate(
        self,
        path: str,
        embedded_key: str,
        params: dict[str, Any] | None = None,
        limit: int = 250,
        max_pages: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Итерирует элементы коллекции amo постранично через _links.next."""
        params = dict(params or {})
        params.setdefault("limit", limit)
        page = 1
        while page <= max_pages:
            params["page"] = page
            data = self._get(path, params)
            if not data:
                return
            items = data.get("_embedded", {}).get(embedded_key, [])
            if not items:
                return
            yield from items
            if not data.get("_links", {}).get("next"):
                return
            page += 1
