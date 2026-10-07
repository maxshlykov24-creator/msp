"""Чистые правила кроя и остатка. Сеть здесь не ходим."""

import hashlib
import json

ROLL_FOLDER = "Рулоны"
YUJI_FOLDER = "YUJI"
SIZES = ("S", "M", "L")


def fact_payload(date, product, sizes, rolls):
    payload = {
        "d": date,
        "p": product.strip(),
        "s": {k: int(sizes.get(k) or 0) for k in SIZES},
        "r": int(rolls),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def fact_key(date, product, sizes, rolls):
    raw = fact_payload(date, product, sizes, rolls)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def roll_article(color):
    digest = hashlib.sha1(color.strip().lower().encode("utf-8")).hexdigest()[:8]
    return "ROL-" + digest


def color_of(product_name):
    if " — " not in product_name:
        return ""
    return product_name.split(" — ", 1)[1].strip()


def parse_sizes(raw):
    sizes = {}
    for key in SIZES:
        value = raw.get(key) or 0
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            value = 0 if value == "" else value
        number = int(value)
        if number < 0:
            raise ValueError("количество %s меньше нуля" % key)
        sizes[key] = number
    return sizes


def check_color(product_name, color):
    expected = color_of(product_name)
    if not expected:
        return "в имени изделия нет цвета"
    if color.strip() != expected:
        return "цвет «%s» не совпадает с изделием «%s»" % (color.strip(), expected)
    return ""


def plan_consumption(lines, norms):
    """Метры рулона по цвету на строки приёмки.

    lines: изделие, размер, штуки. norms: изделие -> размер -> метры на штуку.
    Пустая норма не ноль: такую строку возвращаем в missing, списание по ней не считаем.
    """
    missing = []
    by_color = {}
    details = []
    for product, size, qty in lines:
        qty = float(qty or 0)
        if qty <= 0:
            continue
        product = (product or "").strip()
        size = (size or "").strip()
        color = color_of(product)
        per = (norms.get(product) or {}).get(size)
        if not color or size not in SIZES or per is None:
            missing.append("%s, %s" % (product or "без изделия", size or "без размера"))
            continue
        meters = round(qty * float(per), 3)
        by_color[color] = round(by_color.get(color, 0.0) + meters, 3)
        details.append({
            "product": product,
            "size": size,
            "qty": qty,
            "per": float(per),
            "meters": meters,
            "color": color,
        })
    return {"by_color": by_color, "missing": missing, "details": details}


def plan_cut(*, roll_stock, rolls, loss_exists, enter_exists):
    """Что делать с фактом. Пустой ответ склада не считаем нулём."""
    if loss_exists and enter_exists:
        return "уже проведено"
    if rolls < 1:
        return "рулоны не указаны"
    if roll_stock is None:
        return "остаток рулона не прочитан"
    if float(roll_stock) < float(rolls):
        return "рулонов не хватает"
    return "провести"


def available_qty(stock, reserve, quantity):
    """На площадку уходит свободный остаток, уже за вычетом резерва.

    В отчёте Моего Склада этого кабинета stock — на складе, quantity — свободно.
    quantity может быть отрицательным, на площадку тогда уходит 0.
    Нет числа — не шлём, это не ноль.
    """
    if quantity is None and stock is None:
        return None
    if quantity is None:
        quantity = float(stock) - float(reserve or 0)
    number = float(quantity)
    if number < 0:
        number = 0
    return int(number)


def available_after_ship(on_hand, reserve, shipped):
    """Отгрузка снимает штуки и резерв. Свободный остаток второй раз не падает."""
    return (float(on_hand) - float(shipped)) - (float(reserve) - float(shipped))


def plan_customer_order(*, enabled, moment, cutover, already):
    if not enabled:
        return "выключено"
    if not cutover:
        return "нет даты включения"
    if str(moment)[:10] < cutover:
        return "до даты включения"
    if already:
        return "заказ уже есть"
    return "создать резерв"


def plan_shipment(*, enabled, demand_exists):
    if not enabled:
        return "выключено"
    if demand_exists:
        return "отгрузка уже есть"
    return "списать резерв"


def plan_fbo(*, enabled, supply_id, already):
    if not enabled:
        return "выключено"
    if not supply_id:
        return "нет номера поставки"
    if already:
        return "поставка уже списана"
    return "списать поставку"


def plan_cancel(*, enabled, demand_exists):
    if not enabled:
        return "выключено"
    if demand_exists:
        return "вернуть отгрузку"
    return "снять резерв"


def plan_disable_native(*, enabled, confirm):
    if not enabled or not confirm:
        return "не гасим: режим выключен"
    return "гасить штатный обмен"


def plan_ozon(*, enabled, api_key):
    if not api_key:
        return "нет ключа Ozon"
    if not enabled:
        return "выключено"
    return "включить Ozon"


def push_lines(rows, wb_by_barcode, warehouses):
    """Строки остатка Моего Склада в тело пуша на один склад FBS.

    rows: dict variant_id, barcode, folder, stock, reserve, quantity.
    wb_by_barcode: штрихкод -> список карточек WB. Больше одной — спор, не шлём.
    Пустой rows не превращаем в обнуление чужих карточек.
    """
    if len(warehouses) != 1:
        return [], "складов FBS %s, пуш остановлен" % len(warehouses)
    out = []
    skipped = []
    for row in rows:
        if row.get("folder") == ROLL_FOLDER or str(row.get("article") or "").startswith("ROL-"):
            skipped.append("рулон")
            continue
        code = str(row.get("barcode") or "").strip()
        if not code:
            skipped.append("нет штрихкода")
            continue
        cards = wb_by_barcode.get(code) or []
        if len(cards) != 1:
            skipped.append("спор или нет пары")
            continue
        qty = available_qty(row.get("stock"), row.get("reserve"), row.get("quantity"))
        if qty is None:
            skipped.append("нет числа")
            continue
        out.append({"chrtId": cards[0]["chrtId"], "amount": qty, "barcode": code})
    return out, "ok"
