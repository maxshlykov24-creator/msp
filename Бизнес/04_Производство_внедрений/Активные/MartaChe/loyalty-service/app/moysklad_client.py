from __future__ import annotations

import base64
import time
from typing import Any, Optional

import requests
from requests.exceptions import ConnectTimeout, ReadTimeout

from app.config import get_settings


def basic_auth_header(token: str) -> str:
    """По доке Remap 1.2: ``Basic`` = base64(``логин:пароль``) — для логина+пароля пользователя."""
    return "Basic " + base64.standard_b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _raise_for_status(r: requests.Response) -> None:
    if r.ok:
        return
    try:
        payload = r.json()
    except Exception:
        payload = r.text
    raise RuntimeError(f"MoySklad API {r.status_code}: {payload}")


class MoySkladClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base = s.api_base.rstrip("/")
        self.token = s.ms_token
        # API-токен из кабинета МойСклад: ``Bearer`` (с Basic в наших тестах часто 1056).
        # gzip: для api.moysklad.ru ожидается сжатие (см. документацию Remap 1.2)
        self._headers_base = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;charset=utf-8",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
        }

    def headers(self, disable_webhook: bool = False) -> dict[str, str]:
        h = dict(self._headers_base)
        if disable_webhook:
            h["X-Lognex-WebHook-Disable"] = "1"
        return h

    def get(self, path: str, params: Optional[dict] = None, timeout: int = 120) -> dict | list:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(8):
            try:
                r = requests.get(url, headers=self.headers(), params=params, timeout=timeout)
            except (ReadTimeout, ConnectTimeout):
                time.sleep(min(2**attempt, 30))
                if attempt == 7:
                    raise
                continue
            if r.status_code == 429:
                time.sleep(2 ** min(attempt + 1, 5))
                continue
            _raise_for_status(r)
            return r.json()
        raise RuntimeError("GET failed")

    def post(self, path: str, body: dict, disable_webhook: bool = True, timeout: int = 120) -> dict:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(8):
            try:
                r = requests.post(url, headers=self.headers(disable_webhook=disable_webhook), json=body, timeout=timeout)
            except (ReadTimeout, ConnectTimeout):
                time.sleep(min(2**attempt, 30))
                if attempt == 7:
                    raise
                continue
            if r.status_code == 429:
                time.sleep(2 ** min(attempt + 1, 5))
                continue
            _raise_for_status(r)
            return r.json()
        raise RuntimeError("POST failed")

    def put(self, path: str, body: dict, disable_webhook: bool = True, timeout: int = 120) -> dict:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(8):
            try:
                r = requests.put(url, headers=self.headers(disable_webhook=disable_webhook), json=body, timeout=timeout)
            except (ReadTimeout, ConnectTimeout):
                time.sleep(min(2**attempt, 30))
                if attempt == 7:
                    raise
                continue
            if r.status_code == 429:
                time.sleep(2 ** min(attempt + 1, 5))
                continue
            if r.status_code == 412:
                try:
                    err = r.json().get("errors", [{}])[0].get("error", r.text)
                except Exception:
                    err = r.text
                raise RuntimeError(f"412: {err}")
            _raise_for_status(r)
            return r.json()
        raise RuntimeError("PUT failed")

    def delete(self, path: str, disable_webhook: bool = True, timeout: int = 120) -> None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(8):
            try:
                r = requests.delete(url, headers=self.headers(disable_webhook=disable_webhook), timeout=timeout)
            except (ReadTimeout, ConnectTimeout):
                time.sleep(min(2**attempt, 30))
                if attempt == 7:
                    raise
                continue
            if r.status_code == 429:
                time.sleep(2 ** min(attempt + 1, 5))
                continue
            if r.status_code == 404:
                return
            _raise_for_status(r)
            return
        raise RuntimeError("DELETE failed")

    def fetch_bonus_program_meta(self) -> dict[str, Any]:
        data = self.get("/entity/bonusprogram", params={"limit": 1})
        rows = data.get("rows") or []
        if not rows:
            raise RuntimeError("В аккаунте нет бонусной программы. Создайте её в МойСклад.")
        return rows[0]["meta"]

    def fetch_customerorder(self, order_id: str) -> dict[str, Any]:
        return self.get(
            f"/entity/customerorder/{order_id}",
            params={
                "expand": "state,agent,positions,positions.assortment,positions.assortment.productFolder",
            },
        )

    def fetch_salesreturns_for_order(self, order_meta_href: str) -> list[dict[str, Any]]:
        """Фильтр по customerOrder в МС задаётся href-ом заказа."""
        flt = f"customerOrder={order_meta_href}"
        data = self.get("/entity/salesreturn", params={"filter": flt, "limit": 100})
        return list(data.get("rows") or [])

    def fetch_salesreturn(self, salesreturn_id: str) -> dict[str, Any]:
        return self.get(
            f"/entity/salesreturn/{salesreturn_id}",
            params={"expand": "positions,positions.assortment,positions.assortment.productFolder"},
        )

    def search_counterparties(self, search: str, limit: int = 100) -> list[dict[str, Any]]:
        data = self.get("/entity/counterparty", params={"search": search, "limit": limit})
        return list(data.get("rows") or [])

    def fetch_counterparty(self, counterparty_id: str) -> dict[str, Any]:
        return self.get(f"/entity/counterparty/{counterparty_id}")
