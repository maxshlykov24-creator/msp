import os
import time

import requests

MS_BASE = "https://api.moysklad.ru/api/remap/1.2"
WB_BASE = "https://marketplace-api.wildberries.ru"
WB_STATS = "https://statistics-api.wildberries.ru"
OZON_BASE = "https://api-seller.ozon.ru"

_last = 0.0


def env(name, default=None):
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise SystemExit("нет переменной %s" % name)
    return val


def env_opt(name, default=""):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def pause():
    global _last
    wait = 0.25 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()


def req(method, url, headers=None, **kw):
    headers = dict(headers or {})
    last = None
    for attempt in range(1, 4):
        pause()
        last = requests.request(method, url, headers=headers, timeout=30, **kw)
        print("%s %s → %s (попытка %s)" % (method, url, last.status_code, attempt))
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(1)
            continue
        return last
    return last


def ms_headers():
    return {
        "Authorization": "Bearer %s" % env("MS_TOKEN"),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }


def wb_headers(token):
    return {"Authorization": token}


def ozon_headers(client_id, api_key):
    return {
        "Client-Id": str(client_id),
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
