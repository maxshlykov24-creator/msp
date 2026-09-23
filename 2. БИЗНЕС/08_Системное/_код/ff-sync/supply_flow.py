"""Поставки WB и сборка Ozon: связка площадки с нашей базой.

Тонкие обёртки над API живут в `wb_supply` и `pack`, здесь — порядок действий и
запись результата к себе. Правило одно: сначала площадка, потом наша база. Если
WB не принял задание в поставку, у нас не должно остаться отметки, что принял.
"""

import time
from datetime import datetime, timedelta, timezone

import pack
import wb_supply
from db import (
    catalog_card,
    delete_shipments_by_ext,
    delete_wb_boxes,
    get_cabinet,
    get_cabinet_by_client_mp,
    get_client_by_id,
    get_setting,
    get_wb_point,
    list_wb_points,
    save_wb_points,
    set_setting,
    set_wb_supply_point,
    wb_points_count,
    get_shipments_by_ext,
    get_shipments_by_ids,
    get_wb_box,
    get_wb_supply,
    insert_wb_boxes,
    insert_wb_supply,
    find_wb_supplies,
    list_cabinets,
    list_supply_shipments,
    list_wb_boxes,
    list_wb_supplies,
    mark_wb_supply_delivered,
    set_shipment_platform,
    set_shipment_supply,
    set_supply_shipments_dropoff,
    set_wb_supply_dropoff,
    set_wb_supply_state,
    set_work_state,
)

MSK = timezone(timedelta(hours=3))


def now_iso():
    return datetime.now(MSK).isoformat(timespec="seconds")


def _wb_stamp(raw):
    """Метка WB (UTC) в наш ISO с Москвой. Не разобрали — сейчас."""
    text = str(raw or "").strip()
    if not text:
        return now_iso()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone(MSK).isoformat(timespec="seconds")
    except ValueError:
        return now_iso()


def _cab_of_supply(supply):
    cab = get_cabinet(supply["cabinet_id"])
    if not cab or not cab["token"]:
        raise ValueError("у кабинета WB нет токена, поставка недоступна")
    return cab


def _supply(supply_id):
    row = get_wb_supply(supply_id)
    if not row:
        raise ValueError("поставка не найдена")
    return row


def _col(row, name, default=""):
    if row is None:
        return default
    return row[name] if name in row.keys() else default


def point_address(point_id):
    """Адрес точки сдачи из справочника. Незнакомая точка — пусто, не выдумываем."""
    row = get_wb_point(point_id)
    if not row:
        return ""
    return str(row["address"] or row["name"] or "")


def _apply_dropoff(supply, pickup_allowed, cargo="", shipping_point=""):
    """Записать адрес сдачи на поставку и её задания."""
    import statuses

    cargo = str(cargo or _col(supply, "cargo_type") or "")
    flag = str(pickup_allowed or "")
    point = str(shipping_point or "")
    office = statuses.dropoff(cargo, flag, point, point_address(point))
    set_wb_supply_dropoff(supply["id"], cargo, flag, point)
    # адрес пишем и пустым: точку в ЛК могли снять, и старая надпись соврёт
    set_supply_shipments_dropoff(supply["cabinet_id"], supply["ext_id"], office, flag)
    return office


def _dropoff_from_card(info):
    """Габарит, флаг ПВЗ и выбранную точку — с карточки поставки WB."""
    import statuses

    cargo = str(info.get("cargoType") or "")
    flag = statuses.pickup_flag(info.get("isPickupPointShipmentAllowed"))
    point = str(info.get("shippingPointId") or "")
    return cargo, flag, point


# --- точка сдачи --------------------------------------------------------
#
# Точку ставит наша кнопка, а не ЛК: PATCH шлёт
# /api/marketplace/v3/fbs/supplies/shipping-method, список точек приходит из
# /api/marketplace/v3/fbs/shipping-points. Менять можно до сканирования
# поставки в пункте отгрузки, после WB отвечает 409.
CITY_KEY = "wb:dropoff:city"
POINT_KEY = "wb:dropoff:point"


def _client_point_key(client_id):
    return "%s:client:%s" % (POINT_KEY, int(client_id))


def dropoff_default(client_id=None):
    """Точка сдачи по умолчанию: своя у контрагента, иначе общая, иначе наш ПВЗ.

    Склад фулфилмента везёт в одно место, поэтому выбор не должен повторяться на
    каждой поставке: точка ставится сама при создании, а окно поставки нужно
    только чтобы её сменить.
    """
    import statuses

    point = ""
    if client_id:
        point = str(get_setting(_client_point_key(client_id)) or "")
    point = point or str(get_setting(POINT_KEY) or "") or str(statuses.PVZ_SHIPPING_POINT)
    city = str(get_setting(CITY_KEY) or "") or "Москва"
    try:
        point = int(point)
    except (TypeError, ValueError):
        point = int(statuses.PVZ_SHIPPING_POINT)
    return {"point_id": point, "city": city, "address": point_address(point)}


def set_dropoff_default(point_id, client_id=None, city=""):
    """Запомнить точку по умолчанию: общую или для одного контрагента."""
    point = int(point_id or 0)
    if not point:
        raise ValueError("не выбрана точка сдачи")
    if client_id:
        set_setting(_client_point_key(client_id), str(point))
    else:
        set_setting(POINT_KEY, str(point))
    if city:
        set_setting(CITY_KEY, str(city))
    return dropoff_default(client_id)


def points(client_id=None, city="", cargo_type="1", query="", refresh=False):
    """Пункты отгрузки для выбора в интерфейсе: из справочника, при нужде с площадки.

    Справочник кэшируем: по Москве и малогабариту WB отдаёт 5565 точек, а лимит
    у группы ручек поставок 300 запросов в минуту, где каждый 4XX списывается как
    десять. Поэтому за площадкой идём только когда справочник пуст или просят
    обновить.
    """
    city = str(city or "").strip() or dropoff_default(client_id)["city"]
    cargo = str(cargo_type or "1")
    notes = []
    have = list_wb_points(city=city, cargo_type=cargo, query=query)
    if refresh or (not have and not query):
        cab = get_cabinet_by_client_mp(int(client_id), "wb") if client_id else None
        if not cab:
            cab = next(
                (c for c in list_cabinets() if c["marketplace"] == "wb" and c["token"] and c["active"]),
                None,
            )
        if not cab:
            notes.append("Справочник точек не обновить: нет активного кабинета WB с токеном.")
        else:
            try:
                fresh = wb_supply.shipping_points(cab, city, cargo)
            except wb_supply.SupplyError as exc:
                notes.append("WB не отдал точки: %s" % exc)
            else:
                save_wb_points(fresh, now_iso())
                have = list_wb_points(city=city, cargo_type=cargo, query=query)
    return {
        "city": city,
        "cargo_type": cargo,
        "default": dropoff_default(client_id),
        "total": wb_points_count(),
        "points": [
            {
                "id": r["id"],
                "address": r["address"] or r["name"] or "",
                "city": r["city"] or "",
                "kind": r["office_type"] or "",
                "fulfillment": bool(r["fulfillment"]),
            }
            for r in have
        ],
        "notes": notes,
    }


def _point_reason(err, supply):
    """Отказ площадки по точке — словами склада. Незнакомый отдаём как есть.

    Живые отказы 18.09 на старых поставках: `NotFound` у поставки, которой на
    площадке уже нет, и `IncorrectRequestBody` у пустой поставки от 14.11.2025.
    """
    text = str(err or "")
    if "NotFound" in text:
        return "такой поставки на площадке нет. Нажми «Обновить с площадки»."
    if "IncorrectRequestBody" in text and not list_supply_shipments(
        supply["cabinet_id"], supply["ext_id"]
    ):
        return "в поставке нет заданий. Точка встанет сама, когда первое задание уйдёт в неё."
    if "scanned" in text.lower():
        return "поставку уже отсканировали в пункте отгрузки, точку не поменять."
    return text


def set_dropoff(supply_id, point_id, date="", ship_type=wb_supply.SELF_SHIPPING, remember=False):
    """Выбрать точку сдачи поставки. Сначала площадка, потом наша база.

    Дату WB требует вместе с точкой: без `shippingDt` метод не принимает запрос.
    Пустую подставляем сегодняшнюю — смена сдаётся в день сборки.
    """
    import statuses

    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка уже передана в доставку, точку сдачи не поменять")
    point = int(point_id or 0)
    if not point:
        raise ValueError("не выбрана точка сдачи")
    day = str(date or "") or datetime.now(MSK).strftime("%Y-%m-%d")
    cab = _cab_of_supply(supply)
    res = wb_supply.set_shipping_method(
        cab,
        [{"supply_ext": supply["ext_id"], "point_id": point, "date": day, "ship_type": ship_type}],
    )
    err = res.get(supply["ext_id"], "")
    if err:
        raise ValueError("WB не принял точку сдачи: %s" % _point_reason(err, supply))
    set_wb_supply_point(supply["id"], point, day)
    if remember:
        set_dropoff_default(point, supply["client_id"])
    notes = []
    # карточка отдаёт и флаг ПВЗ, и подтверждение точки: пишем её ответ, а не своё.
    # Не дочитали — точка всё равно наша: площадка ответила success на этот запрос
    try:
        flag, cargo, got = _sync_dropoff(_supply(supply_id))
    except (ValueError, wb_supply.SupplyError) as exc:
        flag, cargo, got = _col(supply, "pickup_allowed"), _col(supply, "cargo_type"), str(point)
        notes.append("Точку поставил, но карточку не перечитал: %s" % exc)
    if str(got or "") != str(point):
        raise ValueError(
            "WB принял запрос, но в карточке точка %s. Обнови поставку и попробуй снова."
            % (got or "пустая")
        )
    return {
        "point_id": point,
        "date": day,
        "address": point_address(point),
        "office": statuses.dropoff(cargo, flag, got, point_address(point)),
        "notes": notes,
    }


def point_fits(point_id, cargo_type):
    """Принимает ли точка этот габарит. Точки нет в справочнике — не спорим, пробуем.

    ПВЗ берут только малогабарит: по Москве `cargoType=2` и `3` отдают лишь
    сортировочные центры. Ставить крупногабаритной поставке наш ПВЗ значит
    отправить склад не туда.
    """
    cargo = str(cargo_type or "").strip()
    if not cargo:
        return True
    row = get_wb_point(point_id)
    if not row:
        return True
    kinds = [x for x in str(row["cargo_types"] or "").split(",") if x]
    return not kinds or cargo in kinds


def ensure_dropoff(supply_id, author=""):
    """Поставить точку по умолчанию, если её ещё нет. Тихая, ошибку только заметкой.

    Зовётся при создании поставки и при первом задании в ней: оператор не должен
    выбирать точку вручную, когда склад всю смену везёт в одно место.
    """
    supply = _supply(supply_id)
    if str(_col(supply, "shipping_point") or ""):
        return {"point_id": "", "notes": []}
    default = dropoff_default(supply["client_id"])
    cargo = str(_col(supply, "cargo_type") or "")
    if not point_fits(default["point_id"], cargo):
        return {
            "point_id": "",
            "notes": [
                "Точку по умолчанию не поставил: она берёт только малогабарит, а поставка %s. "
                "Выбери пункт кнопкой «Куда везти»." % (statuses_cargo(cargo) or "другого габарита")
            ],
        }
    try:
        res = set_dropoff(supply["id"], default["point_id"])
    except (ValueError, wb_supply.SupplyError) as exc:
        # поставка уже создана, и смену из-за точки рвать нельзя: скажем заметкой
        return {"point_id": "", "notes": ["Точку сдачи не поставил: %s" % exc]}
    return {"point_id": res["point_id"], "notes": res.get("notes") or []}


def statuses_cargo(cargo_type):
    import statuses

    return statuses.cargo_kind(cargo_type)


def _sync_dropoff(supply):
    """Перечитать точку сдачи с карточки WB и записать к себе.

    Точку ставим мы, но подтверждает её только карточка: пока `shippingPointId`
    пустой, поставка уйдёт в СЦ, и оператор должен видеть это, а не надпись
    «ПВЗ», выведенную из габарита.
    """
    cab = _cab_of_supply(supply)
    info = wb_supply.info(cab, supply["ext_id"]) or {}
    cargo, flag, point = _dropoff_from_card(info)
    cargo = cargo or str(_col(supply, "cargo_type") or "")
    _apply_dropoff(supply, flag, cargo, point)
    return flag, cargo, point


# --- Ozon: собрать ------------------------------------------------------


def ship_ozon(ship_ids, split=True):
    """Собрать выбранные отправления Ozon. WB так не собирается — только поставкой."""
    rows = get_shipments_by_ids(ship_ids)
    if not rows:
        raise ValueError("отправления не найдены")
    notes = []
    done = 0
    replaced = {}
    stay = []
    cabs = {}
    for row in rows:
        if row["marketplace"] != "ozon" or row["kind"] != "fbs":
            notes.append("%s: собирается не здесь — WB уходит в сборку вместе с поставкой." % row["ext_id"])
            continue
        cab_id = row["cabinet_id"]
        if cab_id not in cabs:
            cabs[cab_id] = get_cabinet(cab_id)
        cab = cabs[cab_id]
        if not cab or not cab["token"]:
            notes.append("%s: у кабинета нет токена." % row["ext_id"])
            continue
        try:
            numbers, more = pack.ship_ozon(cab, row["ext_id"], split=split)
        except Exception as exc:
            notes.append("%s: %s" % (row["ext_id"], exc))
            continue
        notes.extend(more)
        if not numbers:
            continue
        done += 1
        replaced.setdefault(cab_id, {"client": row["client_id"], "new": [], "gone": []})
        replaced[cab_id]["new"].extend(numbers)
        if row["ext_id"] not in numbers:
            replaced[cab_id]["gone"].append(row["ext_id"])
        else:
            stay.append(row["id"])
    for cab_id, bag in replaced.items():
        client = get_client_by_id(bag["client"])
        cab = cabs[cab_id]
        if client and cab:
            pack.refresh_ozon(client, cab, bag["new"])
        if bag["gone"]:
            # номера, которых после дробления на площадке больше нет
            delete_shipments_by_ext(cab_id, "fbs", bag["gone"])
    if stay:
        # площадка увела отправление в «ожидают отгрузки». Отметка «на сборке»
        # старше её по нашему правилу чтения, поэтому снимаем: дальше статус
        # ведёт Ozon, иначе заказ залипнет на вкладке «На сборке».
        set_work_state(stay, "")
    return {"shipped": done, "notes": notes}


def assemble(ship_ids, split=True, boxes=0):
    """Кнопка «Собрано»: Ozon собираем на площадке, WB отмечаем у себя.

    Своего «собрать» у WB в API нет — задание уходит в сборку вместе с поставкой,
    поэтому для него это складская отметка «ожидают отгрузки», не звонок наружу.
    Зато здесь заводятся грузоместа: сколько коробов вышло, знает только
    сборщик, и говорит он это в момент, когда закрыл последнюю коробку. Что
    внутри короба, площадке не передаётся — состав заводит ПВЗ при приёмке.
    """
    rows = get_shipments_by_ids(ship_ids)
    if not rows:
        raise ValueError("отправления не найдены")
    ozon = [r["id"] for r in rows if r["marketplace"] == "ozon" and r["kind"] == "fbs"]
    rest = [r["id"] for r in rows if r["id"] not in set(ozon)]
    notes = []
    shipped = 0
    if ozon:
        res = ship_ozon(ozon, split=split)
        shipped = res["shipped"]
        notes.extend(res["notes"])
    made = []
    if int(boxes or 0) > 0:
        made, more = _boxes_for(rows, int(boxes))
        notes.extend(more)
    marked = set_work_state(rest, "ready") if rest else 0
    return {"shipped": shipped, "marked": marked, "boxes": made, "notes": notes}


def _boxes_for(rows, amount):
    """Грузоместа на поставку из выборки. Поставка должна быть одна."""
    exts = {r["supply_ext"] for r in rows if (r["supply_ext"] or "")}
    if not exts:
        return [], ["Коробов не завёл: у выбранных заданий нет поставки WB."]
    if len(exts) > 1:
        return [], [
            "Коробов не завёл: в выборке %s поставки, а число коробов одно. Собери поставки по очереди."
            % len(exts)
        ]
    found = find_wb_supplies(exts)
    if not found:
        return [], ["Коробов не завёл: поставка %s заведена не у нас." % exts.pop()]
    try:
        return make_boxes(found[0]["id"], amount)["boxes"], []
    except (ValueError, wb_supply.SupplyError) as exc:
        # отказ по коробам не должен ронять «Собрано»: смена уже собрана, и
        # отметка нужна складу независимо от того, завелось грузоместо или нет
        return [], ["Коробов не завёл: %s" % exc]


def _sync_cabinet(cab_id, author):
    """Открытые поставки кабинета с площадки. Обрыв связи смену не рвёт."""
    try:
        sync_open(cabinet_id=cab_id, author=author)
    except (ValueError, wb_supply.SupplyError):
        pass


def _open_matches(client_id, cab_id, cargo):
    """Открытые поставки этого кабинета и этого габарита. WB чужой габарит не примет."""
    cargo = str(cargo or "")
    found = []
    for row in list_wb_supplies(client_id=client_id, state="open", limit=500):
        if int(row["cabinet_id"]) != int(cab_id):
            continue
        if str(row["cargo_type"] or "") != cargo:
            continue
        found.append(row)
    return found


def _choice_key(client_id, cab_id, cargo):
    return "%s:%s:%s" % (int(client_id), int(cab_id), str(cargo or ""))


def _lookup_choice(choices, key):
    """«new» или id поставки. Ключа нет — склад ещё не выбрал."""
    if not choices or key not in choices:
        return None
    raw = choices[key]
    if str(raw).strip().lower() == "new":
        return "new"
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("не понял, в какую поставку класть задания")


def _choice_public(row):
    """Строка выбора: номер, сколько уже лежит, куда везти. По ней склад узнаёт кривую поставку."""
    import statuses

    cargo = str(row["cargo_type"] or "")
    flag = str(_col(row, "pickup_allowed") or "")
    point = str(_col(row, "shipping_point") or "")
    office = statuses.dropoff(cargo, flag, point, point_address(point)) or "точка не выбрана"
    return {
        "id": int(row["id"]),
        "ext_id": row["ext_id"],
        "name": row["name"] or "",
        "orders": int(row["orders"] or 0),
        "office": office,
    }


def _create_supply(client_id, cab_id, cargo, author):
    """Новая поставка под габарит. Точку сдачи ставим сразу, склад везёт в одно место."""
    import statuses

    cab = get_cabinet(cab_id)
    if not cab or not cab["token"]:
        raise ValueError("у кабинета WB нет токена, поставку не открыть")
    mark = statuses.CARGO.get(str(cargo or ""), ("", ""))[0]
    name = "%s%s" % (
        datetime.now(MSK).strftime("Смена %d.%m %H:%M"),
        " · " + mark if mark else "",
    )
    ext = wb_supply.create(cab, name)
    sid = insert_wb_supply(int(client_id), cab_id, ext, name, now_iso(), author, cargo_type=cargo)
    ensure_dropoff(sid, author)
    return {"id": sid, "ext_id": ext}


def take(ship_ids, author="", choices=None, preview=False):
    """Кнопка «Взять в сборку».

    Задания WB сразу уходят в поставку: своего «взять» у WB нет, задание
    попадает в сборку в момент добавления в поставку.

    Группируем по кабинету и габаритному типу: поставка WB держит только
    один `cargoType`. Точку сдачи в ключ не берём.

    Если открытой поставки этого кабинета и габарита нет, новую создаём без
    вопроса: так же делает личный кабинет WB. Если есть, молча в неё не
    кладём. 22.09 новые заказы уехали в поставку с двумя проблемными товарами,
    а вынуть задание WB не даёт. Склад выбирает: добавить в открытую или
    создать новую. `choices` — словарь «клиент:кабинет:габарит» → «new» или id.
    Пока выбора нет, наружу уходит `need_choice` и ничего не пишем.
    `preview` только смотрит открытые поставки, поставку не создаёт.

    Перед поиском открытые поставки кабинета сверяются с площадкой. 10.09 без
    этого задание легло в нашу прежнюю поставку, ехавшую в СЦ, хотя в ЛК уже
    была другая, с ПВЗ.

    Ozon-заданиям поставка не нужна, для них это складская отметка. Если по WB
    ещё нужно выбрать поставку, Ozon тоже не трогаем: отмена не должна
    наполовину провести смену.
    """
    import statuses

    rows = get_shipments_by_ids(ship_ids)
    if not rows:
        raise ValueError("отправления не найдены")
    notes = []
    groups = {}
    rest = []
    for row in rows:
        if row["marketplace"] == "wb" and row["kind"] == "fbs":
            cargo = str(_col(row, "cargo_type") or "")
            groups.setdefault((row["client_id"], row["cabinet_id"], cargo), []).append(row)
        else:
            rest.append(row["id"])
    synced = set()
    pending = []
    actions = []
    for (client_id, cab_id, cargo), group in groups.items():
        ready = [r for r in group if (r["supply_ext"] or "")]
        fresh = [r for r in group if not (r["supply_ext"] or "")]
        opens = []
        if fresh:
            if cab_id not in synced:
                _sync_cabinet(cab_id, author)
                synced.add(cab_id)
            opens = _open_matches(client_id, cab_id, cargo)
        key = _choice_key(client_id, cab_id, cargo)
        choice = _lookup_choice(choices, key) if fresh and opens else ("new" if fresh else None)
        if fresh and opens and (preview or choice is None):
            pending.append(
                {
                    "key": key,
                    "client": _col(group[0], "client_name"),
                    "cargo": statuses.cargo_kind(cargo) or "габарит не указан",
                    "count": len(fresh),
                    "supplies": [_choice_public(s) for s in opens],
                }
            )
        actions.append(
            {
                "ready": ready,
                "fresh": fresh,
                "opens": opens,
                "choice": choice,
                "client_id": client_id,
                "cab_id": cab_id,
                "cargo": cargo,
            }
        )
    if preview or pending:
        return {"need_choice": bool(pending), "groups": pending, "supplies": [], "marked": 0, "notes": []}
    supplies = []
    for act in actions:
        ready = act["ready"]
        if ready:
            notes.append(
                "Уже в поставке, повторно не кладу: %s." % ", ".join(r["ext_id"] for r in ready)
            )
            set_work_state([r["id"] for r in ready], "assembling")
        fresh = act["fresh"]
        if not fresh:
            continue
        choice = act["choice"]
        if choice == "new" or not act["opens"]:
            try:
                supply = _create_supply(act["client_id"], act["cab_id"], act["cargo"], author)
            except ValueError as exc:
                notes.append("%s заданий без поставки: %s" % (len(fresh), exc))
                continue
        else:
            hit = next((r for r in act["opens"] if int(r["id"]) == int(choice)), None)
            if not hit:
                raise ValueError("эта поставка уже закрыта или другого габарита")
            supply = {"id": hit["id"], "ext_id": hit["ext_id"]}
        res = add_orders(supply["id"], [r["id"] for r in fresh])
        notes.extend(res["notes"])
        if res["added"]:
            supplies.append({"id": supply["id"], "ext_id": supply["ext_id"], "orders": res["added"]})
    marked = set_work_state(rest, "assembling") if rest else 0
    return {"need_choice": False, "groups": [], "supplies": supplies, "marked": marked, "notes": notes}


# --- WB: поставка -------------------------------------------------------


def create_supply(client_id, name, author):
    cab = get_cabinet_by_client_mp(int(client_id), "wb")
    if not cab or not cab["token"]:
        raise ValueError("у контрагента нет активного кабинета WB")
    ext = wb_supply.create(cab, name or "Поставка %s" % datetime.now(MSK).strftime("%d.%m %H:%M"))
    sid = insert_wb_supply(int(client_id), cab["id"], ext, name or "", now_iso(), author)
    notes = ensure_dropoff(sid, author)["notes"]
    return {"id": sid, "ext_id": ext, "notes": notes}


_synced_at = {}


def sync_open(cabinet_id=None, client_id=None, author="площадка", min_gap=0):
    """Свести открытые поставки кабинета с площадкой.

    Поставку могут создать руками в ЛК — например чтобы выбрать точку ПВЗ,
    которую через API не задать. Такая поставка нам не видна, и задания уходят
    не туда: 10.09 четвёртое задание легло в прежнюю поставку, ехавшую в СЦ.

    Что делаем: новые открытые поставки площадки заводим у себя вместе с
    составом, у знакомых обновляем точку сдачи, а закрытые на площадке
    отмечаем закрытыми и у нас. Наружу ничего не пишем, только читаем.

    `min_gap` в секундах бережёт лимит WB: таблица сборки перерисовывается
    часто, а у группы ручек поставок 300 запросов в минуту, и каждый 4XX
    списывается как десять.
    """
    if min_gap:
        key = (int(cabinet_id or 0), int(client_id or 0))
        was = _synced_at.get(key) or 0
        now = time.monotonic()
        if now - was < float(min_gap):
            return {"found": 0, "notes": [], "skipped": True}
        _synced_at[key] = now
    cabs = []
    if cabinet_id:
        cab = get_cabinet(int(cabinet_id))
        if cab:
            cabs = [cab]
    else:
        cabs = [
            c
            for c in list_cabinets()
            if c["marketplace"] == "wb" and c["token"] and c["active"]
            and (not client_id or c["client_id"] == int(client_id))
        ]
    found = 0
    notes = []
    for cab in cabs:
        if cab["marketplace"] != "wb" or not cab["token"]:
            continue
        try:
            rows = wb_supply.list_supplies(cab, only_open=True)
        except wb_supply.SupplyError as exc:
            notes.append("%s: %s" % (cab["name"], exc))
            continue
        live = {str(x.get("id") or "") for x in rows if x.get("id")}
        for item in rows:
            ext = str(item.get("id") or "")
            if not ext:
                continue
            mine = [r for r in find_wb_supplies([ext]) if r["cabinet_id"] == cab["id"]]
            cargo, flag, point = _dropoff_from_card(item)
            if mine:
                _apply_dropoff(mine[0], flag, cargo or str(mine[0]["cargo_type"] or ""), point)
                continue
            try:
                refresh_from_wb(cab["id"], ext, author)
            except (ValueError, wb_supply.SupplyError) as exc:
                notes.append("%s: %s" % (ext, exc))
                continue
            found += 1
        # у себя открыта, а в списке её нет: скорее всего сдали через ЛК. Перед
        # тем как закрыть, спрашиваем карточку — список мог оборваться на
        # пагинации, и закрыть живую поставку хуже, чем не заметить сданную
        for row in list_wb_supplies(client_id=cab["client_id"], state="open"):
            if row["cabinet_id"] != cab["id"] or row["ext_id"] in live:
                continue
            card = wb_supply.info(cab, row["ext_id"]) or {}
            if not card.get("done"):
                continue
            mark_wb_supply_delivered(row["id"], _wb_stamp(card.get("closedAt")))
            notes.append("%s: на площадке уже закрыта, отметил сданной." % row["ext_id"])
    return {"found": found, "notes": notes}


def refresh_from_wb(cabinet_id, ext_id, author="площадка"):
    """Подтянуть поставку с площадки: состав, короба, адрес сдачи, статусы.

    Нужно, когда поставку пересобрали в ЛК: у нас остался старый номер, а
    задания уже лежат в новом. Площадку не пишем, только читаем.
    """
    import statuses
    from shipments_pull import wb_statuses

    cab = get_cabinet(int(cabinet_id))
    if not cab or not cab["token"]:
        raise ValueError("у кабинета WB нет токена")
    ext_id = str(ext_id or "").strip()
    if not ext_id:
        raise ValueError("нет номера поставки")
    info = wb_supply.info(cab, ext_id)
    if not info or not info.get("id"):
        raise ValueError("WB не нашёл поставку %s" % ext_id)
    cargo, flag, point = _dropoff_from_card(info)
    name = str(info.get("name") or ext_id)
    created = _wb_stamp(info.get("createdAt"))
    found = [r for r in find_wb_supplies([ext_id]) if r["cabinet_id"] == cab["id"]]
    if found:
        sid = found[0]["id"]
        set_wb_supply_dropoff(sid, cargo, flag, point)
    else:
        sid = insert_wb_supply(
            cab["client_id"], cab["id"], ext_id, name, created, author, cargo, flag, point
        )
    orders = [str(x) for x in wb_supply.order_ids(cab, ext_id)]
    ships = get_shipments_by_ext(cab["id"], "fbs", orders)
    have = {str(r["ext_id"]): r for r in ships}
    missing = [x for x in orders if x not in have]
    box_ids = wb_supply.list_boxes(cab, ext_id)
    if box_ids:
        insert_wb_boxes(sid, box_ids, now_iso())
    if ships:
        trbx = box_ids[0] if len(box_ids) == 1 else None
        set_shipment_supply([r["id"] for r in ships], ext_id, trbx_ext=trbx)
    supply = get_wb_supply(sid)
    office = _apply_dropoff(supply, flag, cargo, point)
    statuses_map = wb_statuses(cab["token"], orders) if orders else {}
    done = bool(info.get("done"))
    if done:
        mark_wb_supply_delivered(sid, _wb_stamp(info.get("closedAt")))
    for row in ships:
        st = statuses_map.get(str(row["ext_id"])) or {}
        group = statuses.wb_group(st.get("supplier"), st.get("wb"), row["work_state"] or "")
        text = statuses.wb_text(st.get("supplier"), st.get("wb")) or row["status"]
        if group == statuses.CANCELLED:
            work = statuses.CANCELLED
        elif done or group == statuses.SHIPPED:
            work = statuses.SHIPPED
        elif group == statuses.ASSEMBLING:
            work = statuses.ASSEMBLING
        else:
            work = statuses.SHIPPED if done else statuses.ASSEMBLING
        set_shipment_platform(row["id"], text, group, work)
    return {
        "id": sid,
        "ext_id": ext_id,
        "name": name,
        "state": "delivered" if done else "open",
        "cargo": cargo,
        "pickup": statuses.to_pickup(cargo, flag, point),
        "office": office,
        "shipping_point": point,
        "warn": statuses.dropoff_warning("delivered" if done else "open", flag, point),
        "orders": orders,
        "missing": missing,
        "boxes": box_ids,
    }


def add_orders(supply_id, ship_ids):
    """Добавить собранные задания WB в поставку. Здесь же они уходят в «На сборке»."""
    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка уже передана в доставку, добавить в неё нельзя")
    cab = _cab_of_supply(supply)
    rows = get_shipments_by_ids(ship_ids)
    ok = []
    notes = []
    for row in rows:
        if row["marketplace"] != "wb" or row["kind"] != "fbs":
            notes.append("%s: в поставку WB идут только задания WB FBS." % row["ext_id"])
            continue
        if row["cabinet_id"] != supply["cabinet_id"]:
            notes.append("%s: другой кабинет, в эту поставку не пойдёт." % row["ext_id"])
            continue
        if (row["supply_ext"] or "") and row["supply_ext"] != supply["ext_id"]:
            notes.append("%s: уже лежит в поставке %s — WB перенесёт его сюда." % (row["ext_id"], row["supply_ext"]))
        ok.append(row)
    if not ok:
        raise ValueError("; ".join(notes) or "нечего добавлять")
    done, more = wb_supply.add_orders(cab, supply["ext_id"], [r["ext_id"] for r in ok])
    notes.extend(more)
    if done:
        # у себя помечаем только если площадка приняла: иначе соврём сборщику
        ids = [r["id"] for r in ok]
        set_shipment_supply(ids, supply["ext_id"])
        set_work_state(ids, "assembling")
        # после первого задания поставка получает cargoType; адрес сдачи — от него
        _sync_dropoff(_supply(supply_id))
        # поставку могли создать кнопкой «Создать», где габарита ещё нет: точку
        # ставим или проверяем здесь, когда площадка габарит уже назвала
        fresh = _supply(supply_id)
        point = str(_col(fresh, "shipping_point") or "")
        if not point:
            notes.extend(ensure_dropoff(supply_id)["notes"])
        elif not point_fits(point, _col(fresh, "cargo_type")):
            notes.append(
                "Точка %s берёт не этот габарит. Смени её кнопкой «Куда везти», иначе поставку не примут."
                % point
            )
    return {"added": done, "notes": notes}


def make_boxes(supply_id, amount):
    """Завести грузоместа. Предел у WB — половина заданий, округление вниз.

    По габариту заранее не отказываем: короба зависят не от него, а от того,
    выбрана ли в ЛК точка ПВЗ. Спрашиваем площадку и читаем её отказ.
    """
    import statuses

    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка передана в доставку, грузоместа не меняются")
    cargo = str(supply["cargo_type"] or "")
    amount = int(amount or 0)
    if amount < 1:
        raise ValueError("сколько грузомест создать?")
    orders = len(list_supply_shipments(supply["cabinet_id"], supply["ext_id"]))
    have = len(list_wb_boxes(supply["id"]))
    # предел считаем сами, чтобы не ловить 4XX: у WB он списывается как десять запросов
    limit = wb_supply.box_limit(orders)
    # поставка из одного задания по правилу половины коробов не получает вовсе.
    # На живом кабинете этот случай не проверен: заданий в статусе `new` у GripOn
    # 10.09 не было. Один короб на одно задание не блокируем, а спрашиваем
    # площадку — она ответит, и отказ уйдёт понятной заметкой, а не отменит смену.
    ask_wb = limit == 0 and orders >= 1 and have == 0 and amount == 1
    if not ask_wb and have + amount > limit:
        raise ValueError(
            "WB разрешает коробов не больше половины заданий: заданий %s, значит максимум %s, уже создано %s. Добавь в поставку ещё задания или удали лишний короб."
            % (orders, limit, have)
        )
    cab = _cab_of_supply(supply)
    try:
        ext_ids = wb_supply.add_boxes(cab, supply["ext_id"], amount)
    except wb_supply.SupplyError as exc:
        # 409 приходит на двух разных поводах: превышен предел коробов либо
        # поставка сдаётся не на ПВЗ. Второе — единственный честный признак,
        # что точку не выбрали, поэтому перечитываем карточку и пишем правду.
        if "pickup point" in str(exc).lower():
            flag, cargo_now, point = _sync_dropoff(supply)
            raise ValueError(
                "WB не даёт короба: поставка сдаётся не на ПВЗ. %s"
                % statuses.dropoff_warning("open", flag, point)
            )
        if "FailedToAddSupplyTrbx" in str(exc):
            raise ValueError(
                "WB отказал в коробе: заданий в поставке %s, коробов уже %s, предел площадки %s."
                % (orders, have, limit)
            )
        raise ValueError(str(exc))
    insert_wb_boxes(supply["id"], ext_ids, now_iso())
    return {"boxes": ext_ids}


def fill_box(box_id, ship_ids):
    """Раскладка заданий по коробкам. Отметка наша, площадку не трогаем.

    Метода привязки задания к грузоместу в API WB нет — сверено по спеке. Так
    что это учёт для склада: кто в какой коробке лежит, чтобы после печати QR
    коробку собрали тем же составом. Задание должно уже быть в поставке.
    """
    box = get_wb_box(box_id)
    if not box:
        raise ValueError("грузоместо не найдено")
    supply = _supply(box["supply_id"])
    if supply["state"] != "open":
        raise ValueError("поставка передана в доставку, состав грузомест не меняется")
    rows = get_shipments_by_ids(ship_ids)
    ok = []
    notes = []
    for row in rows:
        if (row["supply_ext"] or "") != supply["ext_id"]:
            notes.append("%s: сначала добавь задание в поставку %s." % (row["ext_id"], supply["ext_id"]))
            continue
        if row["trbx_ext"] or "":
            notes.append("%s: уже в коробе %s." % (row["ext_id"], row["trbx_ext"]))
            continue
        ok.append(row)
    if not ok:
        raise ValueError("; ".join(notes) or "нечего укладывать")
    set_shipment_supply([r["id"] for r in ok], supply["ext_id"], trbx_ext=box["ext_id"])
    return {"packed": len(ok), "box": box["ext_id"], "notes": notes}


def empty_box(box_id, ship_ids):
    """Вынуть задания из коробки: остаются в поставке, но без грузоместа."""
    box = get_wb_box(box_id)
    if not box:
        raise ValueError("грузоместо не найдено")
    supply = _supply(box["supply_id"])
    rows = [r for r in get_shipments_by_ids(ship_ids) if (r["trbx_ext"] or "") == box["ext_id"]]
    if not rows:
        raise ValueError("в этом грузоместе выбранных заданий нет")
    set_shipment_supply([r["id"] for r in rows], supply["ext_id"], trbx_ext="")
    return {"taken": len(rows)}


def drop_box(box_id):
    box = get_wb_box(box_id)
    if not box:
        raise ValueError("грузоместо не найдено")
    supply = _supply(box["supply_id"])
    if supply["state"] != "open":
        raise ValueError("поставка передана в доставку, грузоместа не удаляются")
    cab = _cab_of_supply(supply)
    wb_supply.drop_boxes(cab, supply["ext_id"], [box["ext_id"]])
    delete_wb_boxes(supply["id"], [box["ext_id"]])
    return {"ok": True}


def deliver(supply_id, confirm=False, force=False):
    """Передать поставку в доставку.

    Шаг необратимый: WB закрывает поставку и переводит все задания в «В доставке».
    Без `confirm` ничего не делаем. Раскладка товара по коробам площадке не
    нужна: достаточно числа грузомест и их QR. Параметр `force` оставлен
    для старых вызовов и ничего не меняет.

    Перед отправкой перечитываем точку сдачи с площадки: после закрытия её уже
    не поменять, а выбирают её в ЛК, мимо нас.
    """
    import statuses

    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка уже передана в доставку")
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    if not rows:
        raise ValueError("в поставке нет заданий")
    if not confirm:
        raise ValueError("нужно подтверждение: шаг необратимый")
    flag, _cargo, point = _sync_dropoff(supply)
    pickup = statuses.to_pickup("", flag, point)
    loose = [r for r in rows if not (r["trbx_ext"] or "")] if pickup else []
    cab = _cab_of_supply(supply)
    wb_supply.deliver(cab, supply["ext_id"])
    set_wb_supply_state(supply["id"], "ready", now_iso())
    set_work_state([r["id"] for r in rows], "ready")
    return {
        "ok": True,
        "orders": len(rows),
        "loose": len(loose),
        "office": statuses.dropoff("", flag, point, point_address(point)),
        "warn": statuses.dropoff_warning("ready", flag, point),
    }


def preflight(supply_id):
    """Что покажем в окне подтверждения перед передачей в доставку.

    Точку сдачи перечитываем с площадки: её могли выбрать в ЛК минуту назад, а
    после передачи поставка закрыта и поправить уже нечего.
    """
    import statuses

    supply = _supply(supply_id)
    try:
        flag, cargo, point = _sync_dropoff(supply)
    except (ValueError, wb_supply.SupplyError):
        flag = _col(supply, "pickup_allowed")
        cargo = _col(supply, "cargo_type")
        point = _col(supply, "shipping_point")
    if not point and supply["state"] == "open":
        # последний рубеж перед необратимым шагом: 10.09 ПВЗ потеряли именно тут,
        # когда поставка ушла в СЦ без точки
        if ensure_dropoff(supply_id)["point_id"]:
            flag, cargo, point = _sync_dropoff(_supply(supply_id))
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    boxes = list_wb_boxes(supply["id"])
    pickup = statuses.to_pickup(cargo, flag, point)
    loose = [r for r in rows if not (r["trbx_ext"] or "")] if pickup else []
    empty = [b for b in boxes if not b["orders"]]
    return {
        "ext_id": supply["ext_id"],
        "client": supply["client_name"],
        "orders": len(rows),
        "boxes": len(boxes),
        "loose": len(loose),
        "empty_boxes": [b["ext_id"] for b in empty],
        "state": supply["state"],
        "office": statuses.dropoff(cargo, flag, point, point_address(point)),
        "pickup": pickup,
        "warn": statuses.dropoff_warning(supply["state"], flag, point),
    }


# --- печать -------------------------------------------------------------


def boxes_pdf(supply_id, box_ids=None):
    """QR грузомест одним PDF."""
    import labels

    supply = _supply(supply_id)
    cab = _cab_of_supply(supply)
    boxes = list_wb_boxes(supply["id"])
    if box_ids:
        want = {int(x) for x in box_ids}
        boxes = [b for b in boxes if b["id"] in want]
    stickers, notes = wb_supply.box_stickers(cab, supply["ext_id"], [b["ext_id"] for b in boxes])
    items = [
        {
            "png": s["png"],
            "caption": "%s · %s" % (supply["ext_id"], s["barcode"] or s["ext_id"]),
        }
        for s in stickers
    ]
    pdf, pages = labels.build_pngs(items)
    return pdf, notes, pages


def _label_payload(rows):
    """Поля этикетки: бренд и цвет только из каталога, в задании их нет."""
    out = []
    for s in rows:
        cab = get_cabinet(s["cabinet_id"])
        card = catalog_card(s["client_id"], barcode=s["barcode"] or "", article=s["article"] or "")
        out.append(
            {
                "cabinet_id": s["cabinet_id"],
                "cabinet": cab,
                "marketplace": s["marketplace"],
                "ext_id": s["ext_id"],
                "article": s["article"] or "",
                "barcode": (s["barcode"] or "") or card.get("barcode") or "",
                "name": s["name"] or card.get("name") or "",
                "client": s["client_name"] if "client_name" in s.keys() else "",
                "brand": card.get("brand") or "",
                "color": card.get("color") or "",
                "size": card.get("size") or "",
                "box": (s["trbx_ext"] or ""),
                "id": s["id"],
            }
        )
    return out


def _supply_rows(supply, ship_ids):
    members = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    if not ship_ids:
        return members
    want = {int(x) for x in ship_ids}
    have = {r["id"] for r in members}
    if want - have:
        raise ValueError("в этой поставке таких заданий нет")
    return [r for r in members if r["id"] in want]


def print_labels(supply_id, ship_ids, mode, box_ids=None):
    """Печать из окна поставки: заказ, товар, короб и их связки."""
    import labels

    supply = _supply(supply_id)
    rows = _supply_rows(supply, ship_ids)
    mode = str(mode or labels.MODE_POSTING)
    boxes = list_wb_boxes(supply["id"])
    if box_ids:
        want = {int(x) for x in box_ids}
        pick_boxes = [b["id"] for b in boxes if b["id"] in want]
    else:
        pick_boxes = [b["id"] for b in boxes]

    if mode == labels.MODE_BOX:
        if not pick_boxes:
            raise ValueError("в поставке нет коробов. Сначала добавь грузоместо.")
        return boxes_pdf(supply_id, pick_boxes)
    if not rows:
        raise ValueError("нечего печатать")
    if mode == labels.MODE_PRODUCT:
        return labels.build(_label_payload(rows), mode=labels.MODE_PRODUCT)
    if mode == labels.MODE_POSTING:
        return labels.build(_label_payload(rows), mode=labels.MODE_POSTING)
    if mode == labels.MODE_BOTH:
        return labels.build(_label_payload(rows), mode=labels.MODE_BOTH)
    if mode not in (labels.MODE_POSTING_BOX, labels.MODE_POSTING_PRODUCT_BOX):
        raise ValueError("неизвестный режим печати")

    inner = labels.MODE_BOTH if mode == labels.MODE_POSTING_PRODUCT_BOX else labels.MODE_POSTING
    pdf, notes, pages = labels.build(_label_payload(rows), mode=inner)
    if not pick_boxes:
        return pdf, notes + ["коробов нет, напечатал только заказы"], pages
    bpdf, bnotes, bpages = boxes_pdf(supply_id, pick_boxes)
    merged, mpages = labels.merge_pdfs([pdf, bpdf])
    return merged, notes + bnotes, mpages


def print_assembly(ship_ids, mode):
    """Печать из списка заказов: те же режимы, что в окне поставки."""
    import labels

    ships = get_shipments_by_ids(ship_ids)
    if not ships:
        raise ValueError("отправления не найдены")
    mode = str(mode or labels.MODE_POSTING)
    if mode in labels.MODES:
        return labels.build(_label_payload(ships), mode=mode)

    by_ext = {}
    other = []
    for s in ships:
        ext = (s["supply_ext"] or "") if "supply_ext" in s.keys() else ""
        if s["marketplace"] == "wb" and ext:
            by_ext.setdefault(ext, []).append(s)
        else:
            other.append(s)
    found = {s["ext_id"]: s for s in find_wb_supplies(by_ext)}
    blobs = []
    notes = []

    if mode == labels.MODE_BOX:
        if not by_ext:
            raise ValueError("короб печатается из поставки WB. В выборке её нет.")
        for ext, group in by_ext.items():
            sup = found.get(ext)
            if not sup:
                notes.append("%s: поставка не найдена" % ext)
                continue
            try:
                pdf, more, _ = print_labels(sup["id"], [r["id"] for r in group], labels.MODE_BOX)
            except ValueError as exc:
                notes.append("%s: %s" % (ext, exc))
                continue
            blobs.append(pdf)
            notes.extend(more)
        if other:
            notes.append("короб только у WB, без поставки: %s" % len(other))
        if not blobs:
            raise ValueError("; ".join(notes) or "нечего печатать")
        pdf, pages = labels.merge_pdfs(blobs)
        return pdf, notes, pages

    if mode not in (labels.MODE_POSTING_BOX, labels.MODE_POSTING_PRODUCT_BOX):
        raise ValueError("неизвестный режим печати")

    inner = labels.MODE_BOTH if mode == labels.MODE_POSTING_PRODUCT_BOX else labels.MODE_POSTING
    for ext, group in by_ext.items():
        sup = found.get(ext)
        if not sup:
            other.extend(group)
            continue
        pdf, more, _ = print_labels(sup["id"], [r["id"] for r in group], mode)
        blobs.append(pdf)
        notes.extend(more)
    if other:
        pdf, more, _ = labels.build(_label_payload(other), mode=inner)
        blobs.append(pdf)
        notes.extend(more)
        notes.append("короба только у поставок WB")
    if not blobs:
        raise ValueError("нечего печатать")
    pdf, pages = labels.merge_pdfs(blobs)
    return pdf, notes, pages


def supply_pdf(supply_id):
    """QR поставки. WB отдаёт его только после передачи в доставку."""
    import labels

    supply = _supply(supply_id)
    cab = _cab_of_supply(supply)
    got, notes = wb_supply.supply_qr(cab, supply["ext_id"])
    if not got:
        raise ValueError("; ".join(notes) or "WB не отдал QR поставки")
    pdf, pages = labels.build_pngs(
        [{"png": got["png"], "caption": "%s · %s" % (supply["ext_id"], supply["client_name"])}]
    )
    return pdf, notes, pages
