import os
import threading
import time

import requests
from requests.adapters import HTTPAdapter

MS_BASE = "https://api.moysklad.ru/api/remap/1.2"
WB_BASE = "https://marketplace-api.wildberries.ru"
WB_STATS = "https://statistics-api.wildberries.ru"
OZON_BASE = "https://api-seller.ozon.ru"

# Рукопожатие с WB с этой ноды проходит примерно в одном случае из четырёх:
# замер 18.09 — 5 попыток к statistics-api подряд не дошли, ICMP при этом без
# потерь. Уже открытое соединение работает: 20 запросов в одной сессии прошли
# без сбоя за 1,9 с. Поэтому соединение держим открытым, а короткий таймаут
# рукопожатия и повтор нужны, чтобы неудачный SYN стоил секунды, а не полминуты.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 60
TRIES = 6

_last = 0.0
_sessions = {}
_lock = threading.Lock()


def session():
    """Своя сессия на поток: keep-alive держит соединение, requests не потокобезопасен."""
    key = threading.get_ident()
    with _lock:
        found = _sessions.get(key)
        if found is None:
            found = requests.Session()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
            found.mount("https://", adapter)
            found.mount("http://", adapter)
            _sessions[key] = found
        return found


def drop_session():
    """Соединение оборвалось — сессию выкидываем, следующая попытка откроет новую."""
    key = threading.get_ident()
    with _lock:
        found = _sessions.pop(key, None)
    if found is not None:
        try:
            found.close()
        except Exception:
            pass


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
    """Запрос к внешнему API с повтором.

    Повторяем не только 429 и 5xx, но и обрыв соединения: раньше таймаут
    рукопожатия выбрасывал исключение из середины постраничной выборки, и проход
    заканчивался неполными данными. Выборка WB идёт от старых заказов к новым,
    поэтому терялись именно свежие: 18.09 у ИП Вахрушев в панели было 3 новых
    задания из 7 в кабинете.
    """
    headers = dict(headers or {})
    kw.setdefault("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
    last = None
    broke = None
    for attempt in range(1, TRIES + 1):
        pause()
        try:
            last = session().request(method, url, headers=headers, **kw)
            broke = None
        except requests.RequestException as exc:
            broke = exc
            drop_session()
            print("%s %s → %s (попытка %s)" % (method, url, type(exc).__name__, attempt))
            time.sleep(min(2 ** (attempt - 1), 8))
            continue
        if last.status_code == 429 or last.status_code >= 500:
            print("%s %s → %s (попытка %s)" % (method, url, last.status_code, attempt))
            time.sleep(min(2 ** (attempt - 1), 8))
            continue
        return last
    if broke is not None:
        raise broke
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
