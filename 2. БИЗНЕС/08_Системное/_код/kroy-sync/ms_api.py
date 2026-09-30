"""Чтение и точечная запись в МойСклад. На площадки этот модуль не ходит."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

import config

BASE = "https://api.moysklad.ru/api/remap/1.2"


class MsError(RuntimeError):
    pass


def _token():
    token = os.environ.get("MS_TOKEN", "").strip()
    if not token:
        raise MsError("нет MS_TOKEN")
    return token


def request(method, path, body=None):
    data = None
    headers = {
        "Authorization": "Bearer " + _token(),
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    last_error = None
    for _ in range(3):
        try:
            return _read(req)
        except urllib.error.URLError as exc:
            last_error = exc
    raise MsError("МойСклад не ответил: %s" % last_error) from last_error


def _read(req):
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            if not raw:
                return {}
            return json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise MsError("МойСклад %s %s" % (exc.code, detail)) from exc
    except urllib.error.URLError:
        raise


def rows(path):
    out = []
    offset = 0
    while True:
        sep = "&" if "?" in path else "?"
        data = request("GET", "%s%slimit=100&offset=%s" % (path, sep, offset))
        batch = data.get("rows") or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        offset += 100


def meta(kind, entity_id):
    return {
        "href": "%s/entity/%s/%s" % (BASE, kind, entity_id),
        "type": kind,
        "mediaType": "application/json",
    }


def find_folder(name):
    found = rows("/entity/productfolder?filter=" + urllib.parse.quote("name=" + name))
    for row in found:
        if row.get("name") == name:
            return row
    return None


def ensure_folder(name):
    found = find_folder(name)
    if found:
        return found
    return request("POST", "/entity/productfolder", {"name": name})


def find_product_by_article(article):
    found = rows("/entity/product?filter=" + urllib.parse.quote("article=" + article))
    for row in found:
        if row.get("article") == article:
            return row
    return None


def stock_on_store(store_id, assortment_id):
    """Свободный и полный остаток одной позиции. Нет строки — None, не ноль."""
    href = "%s/entity/store/%s" % (BASE, store_id)
    path = "/report/stock/all?filter=" + urllib.parse.quote("store=" + href)
    for row in rows(path):
        aid = ((row.get("meta") or {}).get("href") or "").rstrip("/").split("/")[-1].split("?")[0]
        if aid == assortment_id:
            return {
                "stock": row.get("stock"),
                "reserve": row.get("reserve"),
                "quantity": row.get("quantity"),
            }
    return None


def find_doc(kind, external_code):
    path = "/entity/%s?filter=%s" % (kind, urllib.parse.quote("externalCode=" + external_code))
    for row in rows(path):
        if row.get("externalCode") == external_code:
            return row
    return None


def delete_doc(kind, entity_id):
    request("DELETE", "/entity/%s/%s" % (kind, entity_id))
