"""Минимальный клиент МойСклад для обновления цен: чтение вариаций каталога и
батч-обновление salePrices. Логика запроса/ретраев зеркалит
scripts/supply_pipeline/ms_api.py — не тащим кросс-пакетный импорт ради одного
скрипта, дублируем маленький нужный кусок.
"""
from __future__ import annotations

import gzip
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from config import MOYSKLAD_ENV, MS_API

_TRANSIENT = (urllib.error.URLError, TimeoutError, OSError, socket.timeout, ssl.SSLError)


def load_token(path: Path = MOYSKLAD_ENV) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Нет {path}. Создай файл со строкой MOYSKLAD_TOKEN=...")
    token = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("MOYSKLAD_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        raise RuntimeError("MOYSKLAD_TOKEN пуст")
    return token


class MoySklad:
    def __init__(self, token: Optional[str] = None):
        self.token = token or load_token()
        self._price_type_meta: Optional[Dict[str, Any]] = None
        self._currency_meta: Optional[Dict[str, Any]] = None

    def _req(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Any = None,
        timeout: int = 90,
        retries: int = 5,
    ) -> Any:
        url = f"{MS_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        last_err: Optional[BaseException] = None
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    text = raw.decode("utf-8")
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    last_err = e
                    print(f"  MS retry {attempt}/{retries} HTTP {e.code}", flush=True)
                    time.sleep(min(2**attempt, 30))
                    continue
                err_raw = e.read()
                try:
                    if err_raw[:2] == b"\x1f\x8b":
                        err_raw = gzip.decompress(err_raw)
                    err = err_raw.decode("utf-8", errors="replace")
                except Exception:
                    err = repr(err_raw[:200])
                raise RuntimeError(f"MS {method} {path} → {e.code}: {err[:800]}") from e
            except _TRANSIENT as e:
                last_err = e
                if attempt < retries:
                    print(f"  MS retry {attempt}/{retries}: {e}", flush=True)
                    time.sleep(min(2**attempt, 30))
                    continue
                raise
        raise RuntimeError(f"MS {method} {path} failed after retries: {last_err}")

    def get(self, path: str, timeout: int = 90, retries: int = 5, **params) -> Any:
        return self._req("GET", path, params=params or None, timeout=timeout, retries=retries)

    def post(self, path: str, body: Any, timeout: int = 90, retries: int = 5) -> Any:
        return self._req("POST", path, body=body, timeout=timeout, retries=retries)

    def iter_variants(self, page: int = 1000) -> Iterator[Dict[str, Any]]:
        """Все модификации каталога с раскрытым родителем — для матчинга по
        имени вида (`product.name`) и текущими `salePrices`."""
        offset = 0
        while True:
            data = self.get(
                "/entity/variant",
                limit=page,
                offset=offset,
                expand="product",
            )
            rows = data.get("rows", [])
            if not rows:
                break
            for r in rows:
                yield r
            offset += len(rows)
            if offset >= data.get("meta", {}).get("size", offset):
                break
            time.sleep(0.03)

    def default_price_and_currency_meta(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Мета первого типа цены и рублёвой валюты — только для тех редких
        модификаций, у которых `salePrices` вообще пуст (обычно не встречается,
        раз позиция уже продаётся)."""
        if self._price_type_meta is not None and self._currency_meta is not None:
            return self._price_type_meta, self._currency_meta
        settings = self.get("/context/companysettings")
        price_types = (settings.get("priceTypes") or [{}])[0]
        self._price_type_meta = price_types.get("meta") or {}
        currency = settings.get("currency") or {}
        self._currency_meta = currency.get("meta") or {}
        return self._price_type_meta, self._currency_meta

    def batch_update_sale_prices(
        self, items: List[Dict[str, Any]], chunk_size: int = 50
    ) -> Tuple[int, List[str]]:
        """items: [{id, salePrices}] → POST /entity/variant (массив), как
        `batch_update_variants` в supply_pipeline, но верифицируем по цене."""
        ok = 0
        errors: List[str] = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            body = []
            for it in chunk:
                body.append(
                    {
                        "meta": {
                            "href": f"{MS_API}/entity/variant/{it['id']}",
                            "metadataHref": f"{MS_API}/entity/variant/metadata",
                            "type": "variant",
                            "mediaType": "application/json",
                        },
                        "salePrices": it["salePrices"],
                    }
                )
            expected = [it["salePrices"][0]["value"] for it in chunk]
            try:
                resp = self.post("/entity/variant", body, timeout=180)
                if isinstance(resp, list):
                    for row, want, it in zip(resp, expected, chunk):
                        got = ((row.get("salePrices") or [{}])[0]).get("value")
                        if got == want:
                            ok += 1
                        else:
                            errors.append(f"{it['id']}: price want={want!r} got={got!r}")
                else:
                    errors.append(f"chunk@{i}: unexpected resp type {type(resp)}")
            except Exception as e:
                recovered = 0
                for it, want in zip(chunk, expected):
                    try:
                        row = self.get(f"/entity/variant/{it['id']}")
                        got = ((row.get("salePrices") or [{}])[0]).get("value")
                        if got == want:
                            recovered += 1
                        else:
                            errors.append(f"{it['id']}: after error price={got!r} want={want!r}")
                    except Exception as e2:
                        errors.append(f"{it['id']}: {e} / verify {e2}")
                ok += recovered
                if recovered != len(chunk):
                    errors.append(f"chunk@{i}: {e} (recovered {recovered}/{len(chunk)})")
            time.sleep(0.05)
        return ok, errors
