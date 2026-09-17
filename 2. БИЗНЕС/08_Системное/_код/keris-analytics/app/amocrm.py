"""Read-only клиент amoCRM API v4. Мутаций нет и быть не должно."""
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

log = logging.getLogger("amocrm")


class AmoError(Exception):
    """Неретраибельная ошибка (4xx кроме 429)."""


class AmoRetryable(AmoError):
    """429 / 5xx / сеть."""


class _RateLimiter:
    def __init__(self, max_rps: float) -> None:
        self._min_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class AmoClient:
    def __init__(self, base: str | None = None, token: str | None = None) -> None:
        self.base = (base or settings.amocrm_base).rstrip("/")
        self._limiter = _RateLimiter(settings.amocrm_max_rps)
        self._client = httpx.Client(
            base_url=self.base,
            headers={"Authorization": f"Bearer {token or settings.amocrm_token}"},
            timeout=httpx.Timeout(60.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AmoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(AmoRetryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._limiter.wait()
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise AmoRetryable(f"сеть: {e}") from e
        if r.status_code == 204:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            raise AmoRetryable(f"{r.status_code} на {path}")
        if r.status_code >= 400:
            raise AmoError(f"{r.status_code} на {path}: {r.text[:300]}")
        return r.json()

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, page_size: int = 250
    ) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            p = dict(params or {})
            p["limit"] = page_size
            p["page"] = page
            data = self.get(path, p)
            if not data:
                return
            embedded = data.get("_embedded") or {}
            key = next(iter(embedded), None)
            items = embedded.get(key) or [] if key else []
            yield from items
            if len(items) < page_size:
                return
            page += 1
            if page > 500:
                log.warning("paginate: обрыв на 500 странице %s", path)
                return

    def account(self) -> dict[str, Any]:
        return self.get("/api/v4/account") or {}

    def pipelines(self) -> list[dict[str, Any]]:
        data = self.get("/api/v4/leads/pipelines") or {}
        return data.get("_embedded", {}).get("pipelines", [])

    def leads(self, pipeline_id: int) -> list[dict[str, Any]]:
        return list(
            self.paginate(
                "/api/v4/leads",
                {"filter[pipeline_id]": pipeline_id, "with": "custom_fields"},
            )
        )
