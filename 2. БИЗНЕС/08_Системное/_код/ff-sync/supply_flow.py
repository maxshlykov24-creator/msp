"""Поставки WB и сборка Ozon: связка площадки с нашей базой.

Тонкие обёртки над API живут в `wb_supply` и `pack`, здесь — порядок действий и
запись результата к себе. Правило одно: сначала площадка, потом наша база. Если
WB не принял задание в поставку, у нас не должно остаться отметки, что принял.
"""

from datetime import datetime, timedelta, timezone

import pack
import wb_supply
from db import (
    delete_shipments_by_ext,
    delete_wb_boxes,
    get_cabinet,
    get_cabinet_by_client_mp,
    get_client_by_id,
    get_shipments_by_ids,
    get_wb_box,
    get_wb_supply,
    insert_wb_boxes,
    insert_wb_supply,
    list_supply_shipments,
    list_wb_boxes,
    list_wb_supplies,
    mark_wb_supply_delivered,
    set_shipment_supply,
    set_work_state,
)

MSK = timezone(timedelta(hours=3))


def now_iso():
    return datetime.now(MSK).isoformat(timespec="seconds")


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


def assemble(ship_ids, split=True):
    """Кнопка «Собрано»: Ozon собираем на площадке, WB отмечаем у себя.

    Своего «собрать» у WB в API нет — задание уходит в сборку вместе с поставкой,
    поэтому для него это складская отметка «ожидают отгрузки», не звонок наружу.
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
    marked = set_work_state(rest, "ready") if rest else 0
    return {"shipped": shipped, "marked": marked, "notes": notes}


def take(ship_ids, author=""):
    """Кнопка «Взять в сборку».

    Задания WB сразу уходят в поставку: своего «взять» у WB нет, задание
    попадает в сборку в момент добавления в поставку. Поставку заводим сами,
    отдельной кнопки больше не нужно.

    Группируем по кабинету и габаритному типу: поставка WB держит только один
    `cargoType`, смешанную площадка не примет. Ozon-заданиям поставка не нужна,
    для них это по-прежнему складская отметка.

    Шаг у WB необратимый: задание уходит из `new` в `confirm`, а метода вынуть
    его из поставки в API нет. Поэтому «Вернуть в новые» снимет нашу отметку,
    но статус на площадке останется — об этом предупреждаем оператора.
    """
    rows = get_shipments_by_ids(ship_ids)
    if not rows:
        raise ValueError("отправления не найдены")
    notes = []
    groups = {}
    rest = []
    for row in rows:
        if row["marketplace"] == "wb" and row["kind"] == "fbs":
            cargo = str((row["cargo_type"] if "cargo_type" in row.keys() else "") or "")
            groups.setdefault((row["client_id"], row["cabinet_id"], cargo), []).append(row)
        else:
            rest.append(row["id"])
    supplies = []
    for (client_id, cab_id, cargo), group in groups.items():
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
            supply = _open_supply(client_id, cab_id, cargo, author)
        except ValueError as exc:
            notes.append("%s заданий без поставки: %s" % (len(fresh), exc))
            continue
        res = add_orders(supply["id"], [r["id"] for r in fresh])
        notes.extend(res["notes"])
        if res["added"]:
            supplies.append({"id": supply["id"], "ext_id": supply["ext_id"], "orders": res["added"]})
    marked = set_work_state(rest, "assembling") if rest else 0
    return {"supplies": supplies, "marked": marked, "notes": notes}


def _open_supply(client_id, cab_id, cargo, author):
    """Открытая поставка кабинета под этот габаритный тип, иначе новая.

    Заводить по поставке на каждое нажатие нельзя: смена уходит одной поставкой,
    а Сергей отбирает задания несколькими заходами.
    """
    import statuses

    for row in list_wb_supplies(client_id=client_id, state="open"):
        if row["cabinet_id"] != cab_id:
            continue
        if str(row["cargo_type"] or "") != cargo:
            continue
        return {"id": row["id"], "ext_id": row["ext_id"]}
    cab = get_cabinet(cab_id)
    if not cab or not cab["token"]:
        raise ValueError("у кабинета WB нет токена, поставку не открыть")
    mark = statuses.CARGO.get(cargo, ("", ""))[0]
    name = "%s%s" % (datetime.now(MSK).strftime("Смена %d.%m %H:%M"), " · " + mark if mark else "")
    ext = wb_supply.create(cab, name)
    sid = insert_wb_supply(int(client_id), cab_id, ext, name, now_iso(), author, cargo_type=cargo)
    return {"id": sid, "ext_id": ext}


# --- WB: поставка -------------------------------------------------------


def create_supply(client_id, name, author):
    cab = get_cabinet_by_client_mp(int(client_id), "wb")
    if not cab or not cab["token"]:
        raise ValueError("у контрагента нет активного кабинета WB")
    ext = wb_supply.create(cab, name or "Поставка %s" % datetime.now(MSK).strftime("%d.%m %H:%M"))
    sid = insert_wb_supply(int(client_id), cab["id"], ext, name or "", now_iso(), author)
    return {"id": sid, "ext_id": ext}


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
    return {"added": done, "notes": notes}


def make_boxes(supply_id, amount):
    """Завести грузоместа. WB разрешает не больше половины числа заданий."""
    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка передана в доставку, грузоместа не меняются")
    amount = int(amount or 0)
    if amount < 1:
        raise ValueError("сколько грузомест создать?")
    orders = len(list_supply_shipments(supply["cabinet_id"], supply["ext_id"]))
    have = len(list_wb_boxes(supply["id"]))
    # предел считаем сами, чтобы не ловить 4XX: у WB он списывается как десять запросов
    limit = max(1, int(orders * wb_supply.TRBX_SHARE))
    if have + amount > limit:
        raise ValueError(
            "WB разрешает не больше половины числа заданий: заданий %s, значит грузомест максимум %s, уже создано %s."
            % (orders, limit, have)
        )
    cab = _cab_of_supply(supply)
    ext_ids = wb_supply.add_boxes(cab, supply["ext_id"], amount)
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
    supply = _supply(supply_id)
    if supply["state"] != "open":
        raise ValueError("поставка уже передана в доставку")
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    if not rows:
        raise ValueError("в поставке нет заданий")
    loose = [r for r in rows if not (r["trbx_ext"] or "")]
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
    supply = _supply(supply_id)
    rows = list_supply_shipments(supply["cabinet_id"], supply["ext_id"])
    boxes = list_wb_boxes(supply["id"])
    loose = [r for r in rows if not (r["trbx_ext"] or "")]
    empty = [b for b in boxes if not b["orders"]]
    return {
        "ext_id": supply["ext_id"],
        "client": supply["client_name"],
        "orders": len(rows),
        "boxes": len(boxes),
        "loose": len(loose),
        "empty_boxes": [b["ext_id"] for b in empty],
        "state": supply["state"],
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
