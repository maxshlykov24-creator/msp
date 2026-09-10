"""Поставки WB и сборка Ozon: связка площадки с нашей базой.

Тонкие обёртки над API живут в `wb_supply` и `pack`, здесь — порядок действий и
запись результата к себе. Правило одно: сначала площадка, потом наша база. Если
WB не принял задание в поставку, у нас не должно остаться отметки, что принял.
"""

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
    get_shipments_by_ext,
    get_shipments_by_ids,
    get_wb_box,
    get_wb_supply,
    insert_wb_boxes,
    insert_wb_supply,
    find_wb_supplies,
    list_supply_shipments,
    list_wb_boxes,
    list_wb_supplies,
    mark_wb_supply_delivered,
    set_shipment_platform,
    set_shipment_supply,
    set_supply_shipments_dropoff,
    set_wb_supply_dropoff,
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


def _apply_dropoff(supply, pickup_allowed, cargo=""):
    """Записать адрес сдачи на поставку и её задания."""
    import statuses

    cargo = str(cargo or _col(supply, "cargo_type") or "")
    flag = str(pickup_allowed or "")
    office = statuses.dropoff(cargo, flag)
    set_wb_supply_dropoff(supply["id"], cargo, flag)
    if office:
        set_supply_shipments_dropoff(supply["cabinet_id"], supply["ext_id"], office, flag)
    return office


def _flag_for_cargo(cargo):
    """ПВЗ для малогабарита, СЦ только для габарита 2 и 3. Флаг WB не читаем."""
    import statuses

    return "1" if statuses.to_pickup(cargo) else "0"


def _sync_dropoff(supply):
    """Сверить габарит с карточкой WB. Флаг ПВЗ с карточки не берём: он врёт."""
    cab = _cab_of_supply(supply)
    info = wb_supply.info(cab, supply["ext_id"]) or {}
    cargo = str(info.get("cargoType") or _col(supply, "cargo_type") or "")
    flag = _flag_for_cargo(cargo)
    _apply_dropoff(supply, flag, cargo)
    return flag, cargo


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


def take(ship_ids, author=""):
    """Кнопка «Взять в сборку».

    Задания WB сразу уходят в поставку: своего «взять» у WB нет, задание
    попадает в сборку в момент добавления в поставку. Поставку заводим сами,
    отдельной кнопки больше не нужно.

    Группируем по кабинету и габаритному типу: поставка WB держит только
    один `cargoType`. Малогабарит всегда на ПВЗ, крупный — отдельно в СЦ.
    Ozon-заданиям поставка не нужна, для них это по-прежнему складская отметка.

    Шаг у WB необратимый: задание уходит из `new` в `confirm`, а метода вынуть
    его из поставки в API нет. Поэтому «Вернуть в новые» снимет нашу отметку,
    но статус на площадке останется — об этом предупреждаем оператора.
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
            flag = "1" if statuses.to_pickup(cargo, _col(row, "pickup_allowed")) else "0"
            groups.setdefault((row["client_id"], row["cabinet_id"], cargo, flag), []).append(row)
        else:
            rest.append(row["id"])
    supplies = []
    for (client_id, cab_id, cargo, flag), group in groups.items():
        ready = [r for r in group if (r["supply_ext"] or "")]
        fresh = [r for r in group if not (r["supply_ext"] or "")]
        if ready:
            notes.append(
                "Уже в поставке, повторно не кладу: %s." % ", ".join(r["ext_id"] for r in ready)
            )
            set_work_state([r["id"] for r in ready], "assembling")
        if not fresh:
            continue
        try:
            supply = _open_supply(client_id, cab_id, cargo, author, pickup_allowed=flag)
        except ValueError as exc:
            notes.append("%s заданий без поставки: %s" % (len(fresh), exc))
            continue
        res = add_orders(supply["id"], [r["id"] for r in fresh])
        notes.extend(res["notes"])
        if res["added"]:
            supplies.append({"id": supply["id"], "ext_id": supply["ext_id"], "orders": res["added"]})
    marked = set_work_state(rest, "assembling") if rest else 0
    return {"supplies": supplies, "marked": marked, "notes": notes}


def _open_supply(client_id, cab_id, cargo, author, pickup_allowed=""):
    """Открытая поставка кабинета под этот габарит и точку сдачи, иначе новая.

    Заводить по поставке на каждое нажатие нельзя: смена уходит одной поставкой,
    а Сергей отбирает задания несколькими заходами. ПВЗ и СЦ не смешиваем:
    площадка короба принимает только на пункт выдачи.
    """
    import statuses

    want_pickup = statuses.to_pickup(cargo, pickup_allowed)
    for row in list_wb_supplies(client_id=client_id, state="open"):
        if row["cabinet_id"] != cab_id:
            continue
        if str(row["cargo_type"] or "") != cargo:
            continue
        have = statuses.to_pickup(row["cargo_type"] or "", _col(row, "pickup_allowed"))
        if have != want_pickup:
            continue
        return {"id": row["id"], "ext_id": row["ext_id"]}
    cab = get_cabinet(cab_id)
    if not cab or not cab["token"]:
        raise ValueError("у кабинета WB нет токена, поставку не открыть")
    mark = statuses.CARGO.get(cargo, ("", ""))[0]
    dest = "ПВЗ" if want_pickup else "СЦ" if cargo else ""
    bits = [x for x in (mark, dest) if x]
    name = "%s%s" % (datetime.now(MSK).strftime("Смена %d.%m %H:%M"), " · " + " · ".join(bits) if bits else "")
    ext = wb_supply.create(cab, name)
    flag = "1" if want_pickup else "0" if cargo else ""
    sid = insert_wb_supply(int(client_id), cab_id, ext, name, now_iso(), author, cargo_type=cargo, pickup_allowed=flag)
    return {"id": sid, "ext_id": ext}


# --- WB: поставка -------------------------------------------------------


def create_supply(client_id, name, author):
    cab = get_cabinet_by_client_mp(int(client_id), "wb")
    if not cab or not cab["token"]:
        raise ValueError("у контрагента нет активного кабинета WB")
    ext = wb_supply.create(cab, name or "Поставка %s" % datetime.now(MSK).strftime("%d.%m %H:%M"))
    sid = insert_wb_supply(int(client_id), cab["id"], ext, name or "", now_iso(), author)
    return {"id": sid, "ext_id": ext}


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
    cargo = str(info.get("cargoType") or "")
    flag = _flag_for_cargo(cargo)
    name = str(info.get("name") or ext_id)
    created = _wb_stamp(info.get("createdAt"))
    found = [r for r in find_wb_supplies([ext_id]) if r["cabinet_id"] == cab["id"]]
    if found:
        sid = found[0]["id"]
        set_wb_supply_dropoff(sid, cargo, flag)
    else:
        sid = insert_wb_supply(
            cab["client_id"], cab["id"], ext_id, name, created, author, cargo, flag
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
    office = _apply_dropoff(supply, flag, cargo)
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
        "pickup": statuses.to_pickup(cargo, flag),
        "office": office,
        "shipping_point": info.get("shippingPointId"),
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
    return {"added": done, "notes": notes}


def make_boxes(supply_id, amount):
    """Завести грузоместа. Предел у WB — половина заданий, округление вниз."""
    import statuses

    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка передана в доставку, грузоместа не меняются")
    cargo = str(supply["cargo_type"] or "")
    if cargo and not statuses.to_pickup(cargo, _col(supply, "pickup_allowed")):
        raise ValueError(
            "грузоместа заводятся только для поставок на ПВЗ. Этот товар едет на %s, там короба не нужны."
            % statuses.SC
        )
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
            "WB разрешает коробов не больше половины заданий: заданий %s, значит максимум %s, уже создано %s. Добавь в поставку ещё задания или уложи товар в имеющиеся короба."
            % (orders, limit, have)
        )
    cab = _cab_of_supply(supply)
    try:
        ext_ids = wb_supply.add_boxes(cab, supply["ext_id"], amount)
    except wb_supply.SupplyError as exc:
        # 409 бывает и на пределе коробов, и когда WB пишет про pickup point.
        # На МГТ это не повод переписывать адрес на СЦ: тот же товар 10.09
        # приняли на Домодедовской 28. СЦ только если габарит 2 или 3.
        info = wb_supply.info(cab, supply["ext_id"]) or {}
        cargo_now = str(info.get("cargoType") or cargo or "")
        if cargo_now and cargo_now != statuses.CARGO_MGT:
            _apply_dropoff(supply, "0", cargo_now)
            raise ValueError(
                "грузоместа заводятся только для поставок на ПВЗ. Этот товар едет на %s, там короба не нужны."
                % statuses.SC
            )
        if "FailedToAddSupplyTrbx" in str(exc) or "pickup point" in str(exc).lower():
            raise ValueError(
                "WB отказал в коробе: заданий в поставке %s, коробов уже %s, предел площадки %s. Уложи товар в имеющиеся короба."
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
    Поэтому без `confirm` ничего не делаем, а на задания без грузоместа
    предупреждаем отдельно — их придётся сдавать врассыпную.
    """
    import statuses

    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка уже передана в доставку")
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    if not rows:
        raise ValueError("в поставке нет заданий")
    pickup = statuses.to_pickup(_col(supply, "cargo_type"), _col(supply, "pickup_allowed"))
    loose = [r for r in rows if not (r["trbx_ext"] or "")] if pickup else []
    if not confirm:
        raise ValueError("нужно подтверждение: шаг необратимый")
    if loose and not force:
        raise ValueError(
            "заданий без грузоместа: %s из %s. Разложи по коробкам или подтверди отправку врассыпную."
            % (len(loose), len(rows))
        )
    cab = _cab_of_supply(supply)
    wb_supply.deliver(cab, supply["ext_id"])
    mark_wb_supply_delivered(supply["id"], now_iso())
    return {"ok": True, "orders": len(rows), "loose": len(loose)}


def preflight(supply_id):
    """Что покажем в окне подтверждения перед передачей в доставку."""
    import statuses

    supply = _supply(supply_id)
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    boxes = list_wb_boxes(supply["id"])
    cargo = _col(supply, "cargo_type")
    flag = _col(supply, "pickup_allowed")
    pickup = statuses.to_pickup(cargo, flag)
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
        "office": statuses.dropoff(cargo, flag),
        "pickup": pickup,
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
    counts = {b["ext_id"]: b["orders"] for b in boxes}
    items = [
        {
            "png": s["png"],
            "caption": "%s · %s · %s шт" % (supply["ext_id"], s["barcode"] or s["ext_id"], counts.get(s["ext_id"], 0)),
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


def print_labels(supply_id, ship_ids, mode):
    """Печать из окна поставки: товар, отправление, отправление + грузоместо, короб."""
    import labels

    supply = _supply(supply_id)
    rows = _supply_rows(supply, ship_ids)
    if not rows:
        raise ValueError("нечего печатать")
    mode = str(mode or labels.MODE_POSTING)
    boxes = list_wb_boxes(supply["id"])
    by_ext = {b["ext_id"]: b for b in boxes}

    def box_ids_of(group):
        seen = []
        for r in group:
            ext = r["trbx_ext"] or ""
            if ext and ext in by_ext and by_ext[ext]["id"] not in seen:
                seen.append(by_ext[ext]["id"])
        return seen

    if mode == labels.MODE_BOX:
        ids = box_ids_of(rows) or ([b["id"] for b in boxes] if not ship_ids else [])
        if not ids:
            raise ValueError("у выбранных заданий нет грузоместа. Сначала уложи их в короб или печатай отправление.")
        return boxes_pdf(supply_id, ids)
    if mode == labels.MODE_PRODUCT:
        return labels.build(_label_payload(rows), mode=labels.MODE_PRODUCT)
    if mode == labels.MODE_POSTING:
        return labels.build(_label_payload(rows), mode=labels.MODE_POSTING)
    if mode != labels.MODE_POSTING_BOX:
        raise ValueError("неизвестный режим печати")

    need = [b["ext_id"] for b in boxes if b["id"] in box_ids_of(rows)]
    box_pngs = {}
    extra_notes = []
    if need:
        cab = _cab_of_supply(supply)
        stickers, more = wb_supply.box_stickers(cab, supply["ext_id"], need)
        extra_notes.extend(more)
        counts = {b["ext_id"]: b["orders"] for b in boxes}
        for s in stickers:
            box_pngs[s["ext_id"]] = {
                "png": s["png"],
                "caption": "%s · %s · %s шт" % (supply["ext_id"], s["barcode"] or s["ext_id"], counts.get(s["ext_id"], 0)),
            }
    pdf, notes, pages = labels.build_posting_boxes(_label_payload(rows), box_pngs)
    return pdf, notes + extra_notes, pages


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
