"""CM.Expert OAuth2 + GET /dealers/dms/cars с retry 401/429/5xx."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("cme")


class CmeError(Exception):
    pass


class CmeAuthError(CmeError):
    pass


class CmeRetryable(CmeError):
    pass


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "cars", "result", "stockCars", "StockCars"):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    embedded = payload.get("_embedded")
    if isinstance(embedded, dict):
        for val in embedded.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return [x for x in val if isinstance(x, dict)]
    return []


class CmeClient:
    def __init__(self) -> None:
        if not settings.cme_client_id or not settings.cme_client_secret:
            raise CmeAuthError(
                "Нет CME_CLIENT_ID / CME_CLIENT_SECRET. "
                "Логин в ЛК не подходит — письмо на help@cm.expert (см. README)."
            )
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.Client(
            base_url=settings.cme_base_url.rstrip("/"),
            timeout=httpx.Timeout(45.0),
            headers={"User-Agent": settings.cme_user_agent, "Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def _auth(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_exp:
            return self._token
        resp = self._http.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.cme_client_id,
                "client_secret": settings.cme_client_secret,
            },
            headers={"User-Agent": settings.cme_user_agent},
        )
        if resp.status_code >= 400:
            raise CmeAuthError(f"oauth {resp.status_code}: {resp.text[:400]}")
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise CmeAuthError(f"oauth без access_token: {body}")
        ttl = int(body.get("expires_in") or 3600)
        self._token = str(token)
        self._token_exp = now + max(ttl - 60, 30)
        return self._token

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        reauthed = False
        for attempt in range(1, 6):
            token = self._auth(force=reauthed)
            try:
                resp = self._http.request(
                    method,
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "User-Agent": settings.cme_user_agent},
                )
            except httpx.TransportError as exc:
                last_exc = CmeRetryable(str(exc))
                time.sleep(min(2 ** attempt, 30))
                continue

            if resp.status_code == 401 and not reauthed:
                log.warning("cme 401 на %s — обновляю токен", path)
                reauthed = True
                self._token = None
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                ra = resp.headers.get("Retry-After") or resp.headers.get("X-Rate-Limit-Reset")
                if ra:
                    try:
                        wait = max(wait, int(float(ra)))
                    except ValueError:
                        pass
                log.warning("cme %s %s → %s, sleep %ss", method, path, resp.status_code, wait)
                time.sleep(min(wait, 60))
                last_exc = CmeRetryable(f"{resp.status_code} {resp.text[:200]}")
                continue
            if resp.status_code >= 400:
                raise CmeError(f"{method} {path} → {resp.status_code}: {resp.text[:500]}")
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as exc:
                raise CmeError(f"не JSON от {path}: {resp.text[:200]}") from exc
        raise last_exc or CmeError(f"{method} {path} исчерпаны попытки")

    def dealers(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/v1/dealers/")
        return _extract_items(payload) if not isinstance(payload, list) else [
            x for x in payload if isinstance(x, dict)
        ]

    def cars_page(self, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], Any]:
        payload = self._request("GET", "/api/v1/dealers/dms/cars", params=params or {})
        return _extract_items(payload), payload

    def iter_cars(self, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while page <= 100:
            params: dict[str, Any] = {"page": page, "perPage": per_page}
            if extra:
                params.update(extra)
            items, payload = self.cars_page(params)
            if not items and page == 1 and extra:
                # часть инсталляций не принимает фильтры в query — берём всё и режем у себя
                items, payload = self.cars_page({"page": page, "perPage": per_page})
                extra = None
            if not items:
                break
            collected.extend(items)
            total = None
            if isinstance(payload, dict):
                total = payload.get("total") or payload.get("count")
            if total is not None:
                try:
                    if len(collected) >= int(total):
                        break
                except (TypeError, ValueError):
                    pass
            if len(items) < per_page:
                break
            page += 1
        if page > 100:
            raise CmeError("pagination safety: больше 100 страниц")
        log.info("cme cars fetched: %s", len(collected))
        return collected
