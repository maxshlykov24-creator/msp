"""
Базовый HTTP-клиент МойСклад с retry и rate-limit guard.
Лимит МойСклад: 45 req/s. Мы делаем не больше 5 req/s — запас x9.
"""

import time
import requests
from config import MS_TOKEN, MS_BASE, ATTR_IDS, DICT_IDS


_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {MS_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json;charset=utf-8",
})

_DELAY = 0.25  # секунды между запросами (4 req/s)


def _request(method: str, url: str, **kwargs) -> dict:
    """Выполнить запрос с одним retry при 429/5xx."""
    time.sleep(_DELAY)
    for attempt in range(3):
        resp = _session.request(method, url, **kwargs)
        if resp.status_code == 429:
            print(f"  [rate-limit] ждём 2с...")
            time.sleep(2)
            continue
        if resp.status_code >= 500:
            print(f"  [server-error {resp.status_code}] retry {attempt+1}/3...")
            time.sleep(2 ** attempt)
            continue
        if not resp.ok:
            raise RuntimeError(
                f"{method} {url} → {resp.status_code}\n{resp.text[:400]}"
            )
        return resp.json()
    raise RuntimeError(f"Превышено число попыток для {url}")


def get_all_products() -> list:
    """Загрузить все товары (все страницы)."""
    products = []
    url = f"{MS_BASE}/entity/product"
    params = {"limit": 100, "offset": 0}
    while True:
        data = _request("GET", url, params=params)
        rows = data.get("rows", [])
        products.extend(rows)
        meta = data.get("meta", {})
        if len(products) >= meta.get("size", 0):
            break
        params["offset"] += 100
    return products


def update_product(product_id: str, payload: dict) -> dict:
    """Обновить товар по ID."""
    url = f"{MS_BASE}/entity/product/{product_id}"
    return _request("PUT", url, json=payload)


def make_attr(attr_name: str, value) -> dict:
    """
    Сформировать элемент attributes для PUT-запроса.

    value может быть:
      - str / int / float — для string и long полей
      - dict с ключами 'dict_name' и 'value_id' — для customentity полей
    """
    attr_id = ATTR_IDS[attr_name]
    attr_meta = {
        "meta": {
            "href": f"{MS_BASE}/entity/product/metadata/attributes/{attr_id}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        }
    }

    if isinstance(value, dict) and "dict_name" in value:
        dict_name = value["dict_name"]
        value_id = value["value_id"]
        dict_id = DICT_IDS[dict_name]
        attr_meta["value"] = {
            "meta": {
                "href": f"{MS_BASE}/entity/customentity/{dict_id}/{value_id}",
                "metadataHref": (
                    f"{MS_BASE}/context/companysettings"
                    f"/metadata/customEntities/{dict_id}"
                ),
                "type": "customentity",
                "mediaType": "application/json",
            }
        }
    else:
        attr_meta["value"] = value

    return attr_meta
