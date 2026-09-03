"""Минимальный клиент amoCRM для DIVO. Только чтение.

Токен берётся из .env проекта или из DIVO_Motors/.env (долгосрочный токен).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path(__file__).resolve().parents[5]
DIVO_ENV = VAULT / "2. БИЗНЕС" / "04_Производство" / "Активные" / "DIVO_Motors" / ".env"
LOCAL_ENV = Path(__file__).resolve().parents[1] / ".env"

NIKITA_USER_ID = 13334858


def _load_env() -> None:
    for path in (LOCAL_ENV, DIVO_ENV):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

DOMAIN = os.environ.get("AMOCRM_BASE_DOMAIN") or os.environ.get("AMOCRM_SUBDOMAIN", "")
if DOMAIN and "." not in DOMAIN:
    DOMAIN = f"{DOMAIN}.amocrm.ru"
TOKEN = os.environ.get("AMOCRM_LONG_LIVED_TOKEN") or os.environ.get("AMO_ACCESS_TOKEN", "")


class AmoError(RuntimeError):
    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"amo {code}: {detail}")
        self.code = code
        self.detail = detail


def request(path: str, params: dict | None = None) -> tuple[int, dict]:
    """GET к amo. Возвращает (http_code, data). 204 и 4xx не бросают исключение."""
    if not DOMAIN or not TOKEN:
        raise AmoError(0, "нет AMOCRM_BASE_DOMAIN или AMOCRM_LONG_LIVED_TOKEN")
    url = f"https://{DOMAIN}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "User-Agent": "divo-ai-manager/1.0",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return resp.status, {}
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"detail": body[:400]}
            return exc.code, data
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise AmoError(0, str(exc.reason)) from exc
    raise AmoError(0, "не достучались")


def get(path: str, params: dict | None = None) -> dict:
    code, data = request(path, params)
    if code >= 400:
        raise AmoError(code, str(data.get("detail") or data.get("title") or data)[:300])
    return data


def items(data: dict, key: str) -> list[dict]:
    return (data.get("_embedded") or {}).get(key) or []


def paged(path: str, params: dict, key: str, max_pages: int = 50):
    """Итератор по страницам списочного метода amo."""
    params = dict(params)
    page = 1
    while page <= max_pages:
        params["page"] = page
        code, data = request(path, params)
        if code == 204 or not data:
            return
        if code >= 400:
            raise AmoError(code, str(data.get("detail") or data)[:300])
        rows = items(data, key)
        if not rows:
            return
        yield from rows
        if not (data.get("_links") or {}).get("next"):
            return
        page += 1
        time.sleep(0.2)
