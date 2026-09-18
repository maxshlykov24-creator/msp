"""Ночная сверка статусов: что ждёт отгрузки и что уже уехало.

Днём выгрузка тянет окно целиком и статусы приходят вместе с заданиями. Но у
двух групп статус живёт дальше нашего последнего прохода: «Ожидают отгрузки» и
«Отгружены». Заказ мог уехать, доставиться или отмениться, а у нас держится
складская отметка — и он остаётся на вкладке, где его уже нет на площадке.

Сверяем раз в сутки, ночью: заказы FBS обеих площадок и поставки WB. Наружу
ничего не пишем, только читаем и правим свою базу.
"""

import statuses as statuses_mod
from db import (
    SHIP_KEEP_DAYS,
    init_db,
    list_cabinets,
    list_open_shipments,
    run_lock,
    set_shipment_status,
)
from net import OZON_BASE, ozon_headers
from orders_pull import ozon_list, since_days
from shipments_pull import pull_ozon_get, wb_statuses

# Площадка ушла вперёд — складская отметка больше не главная
DONE = (statuses_mod.SHIPPED, statuses_mod.DELIVERED, statuses_mod.CANCELLED)
# Потолок на одиночные запросы: те, кого не было в списке площадки, спрашиваем
# поштучно, и упереться в это на всю ночь не хочется
OZON_CAP = 300


def _changed(row, status, group):
    return (row["status"] or "") != (status or "") or (row["status_group"] or "") != (group or "")


def check_wb(cab):
    rows = list_open_shipments(cab["id"], kind="fbs")
    if not rows:
        return 0, 0
    ids = [str(r["ext_id"]) for r in rows]
    found = wb_statuses(cab["token"], ids)
    if not found:
        return len(rows), 0
    fixed = 0
    for row in rows:
        info = found.get(str(row["ext_id"]))
        if not info:
            continue
        status = statuses_mod.wb_text(info.get("supplier"), info.get("wb"))
        group = statuses_mod.wb_group(info.get("supplier"), info.get("wb"))
        stale = _changed(row, status, group) or (group in DONE and (row["work_state"] or ""))
        if not stale:
            continue
        set_shipment_status(row["id"], status, group, clear_work=group in DONE)
        fixed += 1
    return len(rows), fixed


def check_ozon(cab):
    """Статусы Ozon одним списком, а не по отправлению за запрос.

    В работе и в пути у площадок больше четырёх тысяч отправлений. Ручка `get`
    отдаёт одно за запрос, а `list` — сотню, и окно совпадает с тем, что мы
    вообще храним. Одиночный `get` оставлен для тех, кого в списке не оказалось.
    """
    rows = list_open_shipments(cab["id"], kind="fbs")
    if not rows:
        return 0, 0
    headers = ozon_headers(cab["client_id_ext"], cab["token"])
    try:
        listed = ozon_list(OZON_BASE + "/v3/posting/fbs/list", headers, since_days(SHIP_KEEP_DAYS))
    except Exception as exc:
        print("ozon список не ответил: %s" % exc)
        listed = []
    found = {}
    for post in listed:
        if isinstance(post, dict) and post.get("posting_number"):
            found[str(post["posting_number"])] = post
    fixed = 0
    late = 0
    for row in rows:
        post = found.get(str(row["ext_id"]))
        if post is None:
            if late >= OZON_CAP:
                continue
            late += 1
            try:
                post = pull_ozon_get(headers, "fbs", str(row["ext_id"])) or {}
            except Exception as exc:
                print("ozon %s не ответил: %s" % (row["ext_id"], exc))
                continue
        raw = (post or {}).get("status") or ""
        if not raw:
            continue
        status = statuses_mod.ru(raw)
        group = statuses_mod.ozon_group(raw)
        track = post.get("tracking_number") or row["track"] or ""
        stale = (
            _changed(row, status, group)
            or track != (row["track"] or "")
            or (group in DONE and (row["work_state"] or ""))
        )
        if not stale:
            continue
        set_shipment_status(row["id"], status, group, clear_work=group in DONE, track=track)
        fixed += 1
    return len(rows), fixed


def check_supplies():
    """Поставки WB: состав, точка сдачи, закрытые на площадке."""
    import supply_flow

    try:
        return supply_flow.sync_open()
    except Exception as exc:
        print("поставки WB: %s" % exc)
        return {"found": 0, "notes": [str(exc)]}


def run(blocking=True):
    init_db()
    with run_lock(blocking=blocking):
        return _run()


def _run():
    seen = 0
    fixed = 0
    notes = []
    for cab in list_cabinets():
        if not cab["active"] or not cab["token"]:
            continue
        try:
            if cab["marketplace"] == "wb":
                n, k = check_wb(cab)
            elif cab["marketplace"] == "ozon":
                n, k = check_ozon(cab)
            else:
                continue
        except Exception as exc:
            notes.append("кабинет %s: %s" % (cab["id"], exc))
            continue
        seen += n
        fixed += k
        if k:
            notes.append("кабинет %s %s: поправил %s из %s" % (cab["id"], cab["marketplace"], k, n))
    check_supplies()
    print("ночная сверка: проверено %s, поправлено %s" % (seen, fixed))
    return {"seen": seen, "fixed": fixed, "notes": notes}


if __name__ == "__main__":
    print(run())
