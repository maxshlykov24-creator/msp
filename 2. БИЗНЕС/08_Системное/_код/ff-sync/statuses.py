"""Статусы отправлений WB и Ozon, сведённые к шести вкладкам раздела «Сборка».

Площадки называют одно и то же по-разному, поэтому оператор видит наши шесть
групп, а исходный статус площадки остаётся в колонке «Статус» текстом — чтобы
при разборе спорной ситуации было видно, что именно ответил маркетплейс.
"""

NEW = "new"
ASSEMBLING = "assembling"
READY = "ready"
SHIPPED = "shipped"
DELIVERED = "delivered"
CANCELLED = "cancelled"

GROUPS = (
    (NEW, "Новые"),
    (ASSEMBLING, "На сборке"),
    (READY, "Ожидают отгрузки"),
    (SHIPPED, "Отгружены"),
    (DELIVERED, "Доставлены"),
    (CANCELLED, "Отменены"),
)
GROUP_CODES = tuple(code for code, _ in GROUPS)
GROUP_LABELS = dict(GROUPS)

# Габаритный тип задания WB. Он определяет точку сдачи и то, нужны ли короба:
# малогабаритный товар везут на ПВЗ и раскладывают по грузоместам, крупный и
# сверхгабаритный сдают в сортировочный центр, где короба не заводятся.
#
# Точку сдачи через API WB выбрать нельзя: destinationOfficeId только читается.
# Склад Берёзы всегда один, поэтому адрес пишем сами. Поле offices в задании —
# это кластер покупателя (Москва_Север), а не куда везти коробку.
CARGO_MGT = "1"
PVZ = "ПВЗ Домодедовская, 28"
SC = "СЦ Кавказский бульвар, 57 стр. 1, Москва"
CARGO = {
    CARGO_MGT: ("малогабаритный", "ПВЗ"),
    "2": ("сверхгабаритный", "СЦ"),
    "3": ("крупногабаритный", "СЦ"),
}


def pickup_flag(raw):
    """Ответ WB `isPickupPointShipmentAllowed` → '1' / '0' / ''."""
    if raw is None:
        return ""
    if raw is True:
        return "1"
    if raw is False:
        return "0"
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "да"):
        return "1"
    if text in ("0", "false", "no", "нет"):
        return "0"
    return ""


def to_pickup(cargo_type, pickup_allowed=""):
    """Сдаём на ПВЗ: малогабарит (или тип ещё неизвестен) и площадка не запретила пункт выдачи."""
    cargo = str(cargo_type or "").strip()
    if cargo and cargo != CARGO_MGT:
        return False
    return pickup_flag(pickup_allowed) != "0"


def dropoff(cargo_type, pickup_allowed=""):
    """Адрес сдачи для колонки «Куда везти». Нет габарита — пусто, не выдумываем."""
    if not str(cargo_type or "").strip():
        return ""
    return PVZ if to_pickup(cargo_type, pickup_allowed) else SC


def cargo_label(cargo_type, pickup_allowed=""):
    """«малогабаритный · ПВЗ». Неизвестный тип — пусто, врать не будем."""
    cargo = str(cargo_type or "").strip()
    got = CARGO.get(cargo)
    if not got:
        return ""
    kind, dest = got
    if cargo == CARGO_MGT and not to_pickup(cargo, pickup_allowed):
        dest = "СЦ"
    return "%s · %s" % (kind, dest)

# Ozon FBS: статус отправления → наша группа.
# «На сборке» у Ozon нет: из awaiting_packaging заказ уходит сразу в
# awaiting_deliver, поэтому эту группу для Ozon заполняет локальная отметка
# work_state, которую ставит наш оператор.
OZON = {
    "awaiting_registration": NEW,
    "acceptance_in_progress": NEW,
    "awaiting_approve": NEW,
    "awaiting_packaging": NEW,
    "awaiting_deliver": READY,
    "delivering": SHIPPED,
    "driver_pickup": SHIPPED,
    "sent_by_seller": SHIPPED,
    # спор по доставке: товар уже уехал, поэтому держим его в «Отгружены»,
    # а текст статуса подскажет оператору, что там арбитраж
    "arbitration": SHIPPED,
    "client_arbitration": SHIPPED,
    "delivered": DELIVERED,
    "cancelled": CANCELLED,
    "canceled": CANCELLED,
    "not_accepted": CANCELLED,
}

# Wildberries: supplierStatus — то, что делаем мы.
WB_SUPPLIER = {
    "new": NEW,
    "confirm": ASSEMBLING,
    "complete": SHIPPED,
    "cancel": CANCELLED,
    "cancel_missed_call": CANCELLED,
}

# Wildberries: wbStatus — то, что делает площадка. Уточняет группу.
WB_STATUS = {
    "sold": DELIVERED,
    "ready_for_pickup": DELIVERED,
    "canceled": CANCELLED,
    "canceled_by_client": CANCELLED,
    "declined_by_client": CANCELLED,
    "canceled_by_missed_call": CANCELLED,
    "defect": CANCELLED,
}

RU = {
    "awaiting_registration": "ожидает регистрации",
    "acceptance_in_progress": "идёт приёмка",
    "awaiting_approve": "ожидает подтверждения",
    "awaiting_packaging": "ожидает сборки",
    "awaiting_deliver": "ожидает отгрузки",
    "delivering": "доставляется",
    "driver_pickup": "у водителя",
    "sent_by_seller": "отправлен продавцом",
    "arbitration": "арбитраж",
    "client_arbitration": "арбитраж с клиентом",
    "delivered": "доставлен",
    "cancelled": "отменён",
    "canceled": "отменён",
    "not_accepted": "не принят",
    "new": "новое",
    "confirm": "на сборке",
    "complete": "передано в доставку",
    "cancel": "отменено продавцом",
    "cancel_missed_call": "отмена, недозвон",
    "waiting": "в работе",
    "sorted": "отсортировано",
    "sold": "получено покупателем",
    "ready_for_pickup": "прибыло на ПВЗ",
    "canceled_by_client": "отменено покупателем",
    "declined_by_client": "отказ покупателя",
    "canceled_by_missed_call": "отмена, недозвон",
    "defect": "брак",
    "accepted_by_carrier": "принято перевозчиком",
    "sent_to_carrier": "передано перевозчику",
}


def ru(raw):
    key = str(raw or "").strip().lower()
    return RU.get(key, str(raw or "").strip())


# русская подпись → ключ площадки, для разбора уже сохранённого текста статуса
_BY_RU = {}
for _key, _label in RU.items():
    _BY_RU.setdefault(_label.lower(), _key)


def group_from_text(text):
    """Группа по сохранённому тексту статуса.

    Нужна для старых записей, у которых группа ещё не проставлена: текст мог
    остаться и английским ключом площадки, и русской подписью, и парой
    «поставщик / площадка». Что не разобрали — считаем историей и кладём в
    «Отгружены»: показать старый заказ в «Новых» хуже, чем в отгруженных,
    потому что сборщик пойдёт собирать то, что давно уехало.
    """
    parts = [p.strip().lower() for p in str(text or "").replace("·", "/").split("/") if p.strip()]
    found = []
    for part in parts:
        key = part if part in OZON or part in WB_SUPPLIER or part in WB_STATUS else _BY_RU.get(part, part)
        for table in (WB_STATUS, WB_SUPPLIER, OZON):
            if key in table:
                found.append(table[key])
                break
    if not found:
        return SHIPPED
    if CANCELLED in found:
        return CANCELLED
    if DELIVERED in found:
        return DELIVERED
    # у пары «поставщик / площадка» приоритет за тем, что дальше по маршруту
    order = [NEW, ASSEMBLING, READY, SHIPPED, DELIVERED, CANCELLED]
    return max(found, key=order.index)


def ozon_group(status, work_state=""):
    if work_state == ASSEMBLING and OZON.get(str(status or "").strip().lower()) == NEW:
        return ASSEMBLING
    return OZON.get(str(status or "").strip().lower(), NEW)


def wb_group(supplier, wb_status, work_state=""):
    sup = str(supplier or "").strip().lower()
    wbs = str(wb_status or "").strip().lower()
    # отмена с любой стороны важнее всего остального
    if WB_SUPPLIER.get(sup) == CANCELLED or WB_STATUS.get(wbs) == CANCELLED:
        return CANCELLED
    if WB_STATUS.get(wbs) == DELIVERED:
        return DELIVERED
    if sup in WB_SUPPLIER:
        return WB_SUPPLIER[sup]
    if work_state:
        return work_state
    return NEW


def wb_text(supplier, wb_status):
    sup = ru(supplier)
    wbs = ru(wb_status)
    if sup and wbs and sup != wbs:
        return "%s / %s" % (sup, wbs)
    return sup or wbs or "новое"
