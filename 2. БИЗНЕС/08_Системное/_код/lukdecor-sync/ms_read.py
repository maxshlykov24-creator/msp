"""Изолированный GET-клиент. Основа: ff-sync/net.py (keep-alive, timeout, throttle).

Исходные модули Березы не импортируются: там есть операции записи.
Секрет поступает только из окружения, URL и тело ошибки не журналируются.
"""
import os
import time
from urllib.parse import urlparse

import requests

MS_BASE = "https://api.moysklad.ru/api/remap/1.2"
READY = "Подтвержден/Можно комплектовать"


class ReadError(ValueError):
    pass


class MoySklad:
    def __init__(self):
        self.session = requests.Session()
        self.last = 0
        self.states = {}

    def load_states(self):
        self.states = {s['meta']['href']: s['name'] for s in self.get('/entity/customerorder/metadata').get('states', [])}
        if READY not in self.states.values():
            raise ReadError("Не найден разрешённый статус МойСклад")

    def get(self, path, **params):
        token = os.environ.get("MOYSKLAD_TOKEN", "")
        login, password = os.environ.get("MOYSKLAD_LOGIN"), os.environ.get("MOYSKLAD_PASSWORD")
        if not token and not (login and password):
            raise ReadError("Нет MOYSKLAD_TOKEN. Живые данные не загружены.")
        url = path if path.startswith("https://") else MS_BASE + path
        if not url.startswith(MS_BASE + "/") or urlparse(url).netloc != "api.moysklad.ru":
            raise ReadError("Запрещён адрес вне API МойСклад")
        for attempt in range(3):
            time.sleep(max(0, .25 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                headers = {"Accept-Encoding": "gzip"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                r = self.session.get(url, params=params, headers=headers,
                                     auth=None if token else (login, password),
                                     timeout=(5, 30), allow_redirects=False)
            except requests.RequestException:
                if attempt == 2:
                    raise ReadError("МойСклад не отвечает. Операция остановлена.") from None
                time.sleep(attempt + 1)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(attempt + 1)
                continue
            if r.status_code != 200:
                raise ReadError(f"МойСклад: HTTP {r.status_code}")
            return r.json()
        raise ReadError("МойСклад не отвечает")

    def rows(self, path, **params):
        offset = 0
        while True:
            batch = self.get(path, limit=100, offset=offset, **params)
            yield from batch.get("rows", [])
            offset += len(batch.get("rows", []))
            if not batch.get("rows") or offset >= batch.get("meta", {}).get("size", offset):
                break

    @staticmethod
    def attributes(order):
        result = {}
        for a in order.get("attributes", []):
            value = a.get("value", "")
            result[a["name"]] = value.get("name", "") if isinstance(value, dict) else str(value)
        return result

    def status(self, order_id):
        if not self.states:
            self.load_states()
        o = self.get("/entity/customerorder/" + order_id)
        return self.states.get(o.get('state', {}).get('meta', {}).get('href'), '')

    def verify_label(self, order_id, qr):
        order = self.get('/entity/customerorder/' + order_id)
        return (self.states.get(order.get('state', {}).get('meta', {}).get('href')) == READY
                and self.attributes(order).get('ШККОД128') == qr)

    def collect(self, count=20):
        # Ограниченная выборка. Никаких контрагентов, адресов и телефонов в снимке.
        stores = {s["meta"]["href"]: s["name"] for s in self.rows("/entity/store")}
        projects = {p['meta']['href']: p['name'] for p in self.rows('/entity/project')}
        self.load_states()
        ready_href = next(k for k,v in self.states.items() if v == READY)
        orders = self.get("/entity/customerorder", limit=100, order="moment,desc", filter="state=" + ready_href)
        result = []
        skipped = 0
        for order in orders.get("rows", []):
            a = self.attributes(order)
            store = stores.get(order.get("store", {}).get("meta", {}).get("href"), "")
            if projects.get(order.get('project', {}).get('meta', {}).get('href')) != 'WB FBS':
                continue
            if self.states.get(order.get('state', {}).get('meta', {}).get('href')) != READY or store not in ("Склад ФБС", "Склад Екатеринбург"):
                continue
            label = a.get("Ссылка на этикетку", "")
            if not label:
                continue
            positions = list(self.rows(f'/entity/customerorder/{order["id"]}/positions', expand="assortment"))
            if len(positions) != 1 or positions[0].get("quantity") != 1:
                skipped += 1
                continue
            product = positions[0]["assortment"]
            codes = [str(v) for b in product.get("barcodes", []) for v in b.values() if v]
            if not codes:
                skipped += 1
                continue
            result.append({
                "id": order["id"], "number": order["name"], "store": store,
                "moment": order.get('moment', ''),
                "product_id": product["id"], "product": product["name"],
                "article": product.get("article", ""), "barcodes": codes,
                "quantity": 1, "label_url": label, "status": READY,
                "cell": "", "cell_verified": False, "marketplace": "wb",
                "supply": None, "deadline": None,
            })
            if len(result) >= count:
                break
        return {"orders": result, "scanned_orders": len(orders.get("rows", [])), "skipped": skipped}


def fetch_label(url):
    # Ссылку принимает только сервер из заказа, пользовательский URL сюда не попадает.
    hosts = {h.strip() for h in os.environ.get("TOPSELLER_LABEL_HOSTS", "").split(",") if h.strip()}
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in hosts or p.username or p.password or p.port not in (None, 443):
        raise ReadError("Хост этикетки не подтверждён. Настрой TOPSELLER_LABEL_HOSTS после проверки ссылки.")
    try:
        with requests.get(url, timeout=(5, 20), allow_redirects=False, stream=True) as r:
            if r.status_code != 200:
                raise ReadError(f"Сервис этикеток: HTTP {r.status_code}. Запасной путь: TopSeller.")
            body = bytearray()
            for chunk in r.iter_content(65536):
                body.extend(chunk)
                if len(body) > 5_000_000:
                    raise ReadError("Этикетка превышает допустимый размер")
    except requests.RequestException:
        raise ReadError("Сервис этикеток недоступен. Запасной путь: TopSeller.") from None
    if not body.startswith(b"%PDF"):
        raise ReadError("Сервис этикеток вернул не PDF")
    return bytes(body)


def decode_sticker(pdf):
    import fitz
    import cv2
    import numpy as np
    try:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            if len(doc) != 1:
                raise ReadError("В ссылке несколько страниц. Требуется одна этикетка задания.")
            page = doc[0]
            mm = [page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72]
            pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB)
            data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            code, _, _ = cv2.QRCodeDetector().detectAndDecode(data)
            if not code:
                raise ReadError("QR оригинального стикера не прочитан. Контрольный скан заблокирован.")
            return code, mm
    except ReadError:
        raise
    except Exception:
        raise ReadError("Не удалось прочитать PDF этикетки") from None
