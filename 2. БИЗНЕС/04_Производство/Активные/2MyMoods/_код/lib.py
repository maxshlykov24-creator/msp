"""Общий клиент amo и МойСклад для скриптов 2MY. Секреты только из .env."""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "06_Доступы" / ".env"
CODE = Path(__file__).resolve().parent
MS_BASE = "https://api.moysklad.ru/api/remap/1.2"

PIPELINE_SALES_OLD = 10193806
PIPELINE_MKT_OLD = 10567466
PIPELINE_SALES_NEW = 11318374
PIPELINE_MKT_NEW = 11318378

ST = {
    "unsorted": 88727566,
    "new": 88727570,
    "in_work": 88727574,
    "waitlist": 88727578,
    "visit": 88727582,
    "pay_wait": 88727586,
    "paid": 88727590,
    "prod": 88727594,
    "pack": 88727598,
    "sent": 88727602,
    "won": 142,
    "lost": 143,
    "mkt_talk": 88727610,
    "mkt_spam": 88727614,
    "mkt_no": 88727618,
    "mkt_work": 88727622,
}

FIELD_TRACK = 928963
FIELD_TRACK_MS = 929039
FIELD_CDEK = 929209
FIELD_PAY_LINK = 929103
FIELD_PAY_STATUS = 929105
FIELD_CANCEL = 928971
FIELD_MS_ORDER_ID = 929049
FIELD_MS_ORDER_NUM = 929057
FIELD_MS_ORDER_URL = 929081

USER_OKSANA = 8329672
USER_POLINA = 7953124
USER_MAXIM = 9490530

STORE_MSK = "65475d80-c447-11eb-0a80-08be002efbd3"
MS_STATE_CONFIRMED = "7b7e229f-dc0a-11ef-0a80-06a30022a4fa"
MS_STATE_SHOWROOM = "b452b1cc-1553-11f0-0a80-0b560012bbdd"
MS_STATE_DONE = "655b226e-c447-11eb-0a80-08be002efc0f"
MS_CHANNEL_SITE = "07ff5606-dc11-11ef-0a80-03cd002298d2"
MS_CHANNEL_SHOWROOM = "ee56d9bb-7eea-11ee-0a80-0b35000db75e"
MS_ATTR_TRACK = "c6feb69e-de2a-11ef-0a80-0d360015ef35"
MS_ATTR_LI_STATUS = "c6feb8e4-de2a-11ef-0a80-0d360015ef36"
MS_ATTR_AMO_LINK = "9db98fd9-a8fb-11f0-0a80-0cf6001fe7a7"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def href_id(meta: dict | None) -> str:
    if not meta:
        return ""
    href = (meta.get("href") or "").rstrip("/")
    return href.rsplit("/", 1)[-1]


class Amo:
    def __init__(self, env: dict[str, str] | None = None):
        env = env or load_env()
        self.base = (env.get("AMOCRM_BASE_URL") or "").rstrip("/")
        self.token = (env.get("AMOCRM_LONG_LIVED_TOKEN") or os.environ.get("AMOCRM_TOKEN") or "").strip()
        if not self.base or not self.token:
            raise SystemExit("Нет amo URL или токена")
        proxy = (os.environ.get("HTTPS_PROXY") or env.get("AMOCRM_HTTPS_PROXY") or "").strip()
        if proxy:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        else:
            self.opener = urllib.request.build_opener()

    def req(self, method: str, path: str, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        r = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener.open(r, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"error": raw.decode(errors="replace")[:400]}

    def cf(self, entity: dict, field_id: int) -> str:
        for grp in entity.get("custom_fields_values") or []:
            if grp.get("field_id") != field_id:
                continue
            for v in grp.get("values") or []:
                val = str(v.get("value") or "").strip()
                if val:
                    return val
        return ""

    def iter_leads(self, params: str, pages: int = 20) -> list[dict]:
        items: list[dict] = []
        page = 1
        sep = "&" if "?" in params else "?"
        while page <= pages:
            st, b = self.req("GET", f"/api/v4/leads{params}{sep}limit=250&page={page}")
            if st == 204 or not (200 <= st < 300):
                break
            chunk = (b.get("_embedded") or {}).get("leads") or []
            items.extend(chunk)
            if len(chunk) < 250:
                break
            page += 1
        return items


class MS:
    def __init__(self, env: dict[str, str] | None = None):
        env = env or load_env()
        self.token = (env.get("MOYSKLAD_TOKEN") or "").strip()
        if not self.token:
            raise SystemExit("Нет MOYSKLAD_TOKEN")
        self._last = 0.0

    def req(self, path: str, params: dict | None = None):
        wait = 0.22 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = path if path.startswith("http") else MS_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        r = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;charset=utf-8",
            "Accept-Encoding": "gzip",
            "User-Agent": "MSProduct-2MY/1.0 (max.shlykov24@gmail.com)",
        })
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                self._last = time.time()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            self._last = time.time()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"error": raw.decode(errors="replace")[:400]}

    def rows(self, path: str, params: dict | None = None, pages: int = 200) -> list[dict]:
        params = dict(params or {})
        params.setdefault("limit", 100)
        offset = 0
        out: list[dict] = []
        for _ in range(pages):
            params["offset"] = offset
            st, b = self.req(path, params)
            if not (200 <= st < 300):
                break
            chunk = b.get("rows") or []
            out.extend(chunk)
            size = (b.get("meta") or {}).get("size") or 0
            offset += len(chunk)
            if not chunk or offset >= size:
                break
        return out
