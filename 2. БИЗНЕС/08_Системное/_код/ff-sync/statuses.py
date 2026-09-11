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

# Габаритный тип задания WB: 1 малогабаритный, 2 сверхгабаритный, 3 крупный.
# Значение ставит площадка полем cargoType, сами мы его не считаем. На точку
# сдачи он не влияет: 11.09 у всех 21000 заданий в базе cargoType=1, а в СЦ
# уехала именно малогабаритная поставка.
#
# Точку сдачи через API выбрать нельзя. Проверено живыми запросами 11.09:
# /api/v3/supplies/{id}/shipping-point, /api/v3/shipping-points и /spot дают
# 404, у deliver тела нет, а /api/v3/offices отдаёт 182 сортировочных центра
# и склада WB, пунктов выдачи там нет вовсе — ПВЗ Домодедовская 28 (50095011)
# не встречается ни в одной записи. Точку выбирает человек в ЛК на конкретной
# поставке, API это поле только читает.
#
# Поэтому адрес сдачи не угадываем, а читаем с карточки поставки:
#   shippingPointId заполнен          → точку ПВЗ выбрали, едем на ПВЗ;
#   точки нет, isPickupPointShipmentAllowed=false → поставка уйдёт в СЦ;
#   точки нет, флаг true              → ПВЗ разрешён, но точка ещё не выбрана.
# Живой пример расхождения 10.09, один кабинет и один габарит: WB-GI-276355488
# создана нашим кодом, shippingPointId пустой, флаг false, уехала в СЦ;
# WB-GI-276491672 создана в ЛК с выбором точки — shippingPointId=50095011,
# флаг true, приняли на Домодедовской 28.
#
# На задании флаг isPickupPointShipmentAllowed по-прежнему не читаем: 10.09 он
# приходил False на все 3929 свежих заказов, включая принятые на ПВЗ.
CARGO_MGT = "1"
PVZ = "ПВЗ Домодедовская, 28"
SC = "СЦ Кавказский бульвар, 57 стр. 1, Москва"
PVZ_SHIPPING_POINT = 50095011
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


PVZ_WAIT = "точка не выбрана"


def dropoff_kind(pickup_allowed="", shipping_point=""):
    """Куда поедет поставка по факту с карточки WB.

    `pvz` — точку выбрали, `sc` — поставка уйдёт в сортировочный центр,
    пусто — ПВЗ разрешён, но точки ещё нет: её выбирают в ЛК, и пока этого не
    сделали, писать адрес нельзя.
    """
    if str(shipping_point or "").strip():
        return "pvz"
    flag = str(pickup_allowed or "").strip()
    if flag == "0":
        return "sc"
    return ""


def to_pickup(cargo_type="", pickup_allowed="", shipping_point=""):
    """Едем ли на ПВЗ. Габарит в сигнатуре ради старых вызовов, на решение не влияет."""
    return dropoff_kind(pickup_allowed, shipping_point) == "pvz"


def dropoff(cargo_type="", pickup_allowed="", shipping_point=""):
    """Адрес сдачи для колонки «Куда везти». Точки нет — пусто, не выдумываем."""
    kind = dropoff_kind(pickup_allowed, shipping_point)
    if kind == "pvz":
        return PVZ
    if kind == "sc":
        return SC
    return ""


def cargo_label(cargo_type, pickup_allowed="", shipping_point=""):
    """«малогабаритный · ПВЗ». Габарита нет — пусто, врать не будем."""
    got = CARGO.get(str(cargo_type or "").strip())
    if not got:
        return ""
    kind = got[0]
    dest = {"pvz": "ПВЗ", "sc": "СЦ"}.get(dropoff_kind(pickup_allowed, shipping_point), PVZ_WAIT)
    return "%s · %s" % (kind, dest)


def dropoff_warning(state, pickup_allowed="", shipping_point=""):
    """Что сказать оператору, если точка сдачи не выбрана. Всё в порядке — пусто.

    Шаг необратимый: после передачи в доставку поставка закрыта и точку уже не
    поменять. 10.09 ПВЗ потеряли именно здесь.
    """
    if dropoff_kind(pickup_allowed, shipping_point) == "pvz":
        return ""
    if str(state or "") != "open":
        return "Поставка закрыта без точки ПВЗ — ушла в %s." % SC
    return (
        "Точка ПВЗ не выбрана: API её задать не может, только личный кабинет WB. "
        "Открой поставку в ЛК, выбери %s и нажми «Обновить с площадки». "
        "Передашь как есть — уйдёт в %s." % (PVZ, SC)
    )

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
