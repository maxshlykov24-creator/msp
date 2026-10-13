"""Действие «Собрать» на площадке.

Ozon: `/v4/posting/fbs/ship` переводит отправление из «Ожидает упаковки» в
«Ожидает отгрузки» и только после этого площадка отдаёт этикетку. В теле метода
передаются грузовые места (`packages`); **каждое место становится отдельным
отправлением**. Сергей собирает по одной единице товара на наклейку, поэтому по
умолчанию дробим многотоварные и многоштучные заказы на единицы.

Wildberries: отдельного «собрать» в API нет. Задание уходит в статус `confirm`
(«На сборке») в момент добавления в поставку — это делает `wb_supply`.
"""

from net import OZON_BASE, ozon_headers, req

OZON_SHIP = OZON_BASE + "/v4/posting/fbs/ship"


class PackError(Exception):
    pass


def ozon_products(cab, posting):
    """Состав отправления: product_id и количество. Без них `ship` не примет тело."""
    from shipments_pull import pull_ozon_get, ozon_composition

    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    detail = pull_ozon_get(heads, "fbs", posting) or {}
    if isinstance(detail, list):
        detail = detail[0] if detail else {}
    return [{'product_id': p['product_id'], 'quantity': p['qty']}
            for p in ozon_composition(detail.get('products'))]


def packages(products, split=True):
    """Грузовые места. При дроблении — по одной единице в место.

    Ozon сверяет сумму количеств с составом отправления: если не совпадёт,
    метод вернёт ошибку и статус не изменится.
    """
    if not split:
        return [{"products": list(products)}]
    out = []
    for prod in products:
        for _ in range(max(1, int(prod["quantity"]))):
            out.append({"products": [{"product_id": prod["product_id"], "quantity": 1}]})
    return out


def ship_ozon(cab, posting, split=True):
    """Собрать отправление. Возвращает (список номеров после сборки, заметки)."""
    notes = []
    products = ozon_products(cab, posting)
    if not products:
        return [], ["Ozon %s: не отдал состав отправления, собрать не смог." % posting]
    body = {
        "posting_number": posting,
        "packages": packages(products, split=split),
        "with": {"additional_data": False},
    }
    r = req("POST", OZON_SHIP, headers=ozon_headers(cab["client_id_ext"], cab["token"]), json=body, retry_safe=False)
    if r.status_code != 200:
        detail = (r.text or "")[:300]
        low = detail.lower()
        if "exemplar" in low or "mark" in low:
            # маркированный товар: сначала нужны коды маркировки на позицию
            notes.append("Ozon %s: площадка требует коды маркировки до сборки. %s" % (posting, detail))
        else:
            notes.append("Ozon %s: не собрал (%s) %s" % (posting, r.status_code, detail))
        return [], notes
    try:
        data = r.json()
    except ValueError:
        return [], ["Ozon %s: нераспознанный ответ на сборку." % posting]
    result = data.get("result")
    if isinstance(result, dict):
        result = result.get("posting_numbers") or result.get("result") or []
    numbers = [str(x) for x in (result or []) if x]
    if not numbers:
        from shipments_pull import pull_ozon_get
        detail = pull_ozon_get(ozon_headers(cab['client_id_ext'], cab['token']), 'fbs', posting, strict=True)
        if detail.get('status') not in ('awaiting_deliver', 'delivering', 'driver_pickup', 'delivered'):
            raise PackError('Ozon не подтвердил сборку. Обнови отправление перед повтором.')
        numbers = [posting]
    if len(numbers) > 1:
        notes.append("Ozon %s разделено на %s отправлений: %s" % (posting, len(numbers), ", ".join(numbers)))
    return numbers, notes


def refresh_ozon(client, cab, postings):
    """Перечитать отправления после сборки, чтобы статус и номера стали свежими."""
    if not postings:
        return 0
    from shipments_pull import handle_ozon

    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    return handle_ozon(client, cab, "fbs", [{"posting_number": p} for p in postings], heads)
