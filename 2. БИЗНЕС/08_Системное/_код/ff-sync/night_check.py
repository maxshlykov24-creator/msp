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
    record_sync,
    lock_name,
    prune_old_shipments,
)
from net import OZON_BASE, ozon_headers
from orders_pull import ozon_list, since_days
from shipments_pull import pull_ozon_get, wb_statuses

# Площадка ушла вперёд — складская отметка больше не главная
DONE = (statuses_mod.SHIPPED, statuses_mod.PICKUP, statuses_mod.DELIVERED, statuses_mod.CANCELLED)
WATCH = ("new", "assembling", "ready", "shipped", "pickup")
# Потолок на одиночные запросы: те, кого не было в списке площадки, спрашиваем
# поштучно, и упереться в это на всю ночь не хочется
OZON_CAP = 300


def _changed(row, status, group):
    return (row["status"] or "") != (status or "") or (row["status_group"] or "") != (group or "")


def check_wb(cab, live=False):
    rows = list_open_shipments(cab["id"], kind="fbs", groups=WATCH)
    if not rows:
        return 0, 0
    ids = [str(r["ext_id"]) for r in rows]
    found = wb_statuses(cab["token"], ids, strict=True)
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
        set_shipment_status(row["id"], status, group, clear_work=group in DONE and not (
            row["work_state"] == "ready" and info.get("wb") == "waiting" and info.get("supplier") == "complete"))
        fixed += 1
    return len(rows), fixed


def check_ozon(cab, live=False):
    """Статусы Ozon одним списком, а не по отправлению за запрос.

    В работе и в пути у площадок больше четырёх тысяч отправлений. Ручка `get`
    отдаёт одно за запрос, а `list` — сотню, и окно совпадает с тем, что мы
    вообще храним. Одиночный `get` оставлен для тех, кого в списке не оказалось.
    """
    rows = list_open_shipments(cab["id"], kind="fbs", groups=WATCH)
    if not rows:
        return 0, 0
    headers = ozon_headers(cab["client_id_ext"], cab["token"])
    listed = ozon_list(OZON_BASE + "/v3/posting/fbs/list", headers, since_days(SHIP_KEEP_DAYS), strict=True)
    found = {}
    for post in listed:
        if isinstance(post, dict) and post.get("posting_number"):
            found[str(post["posting_number"])] = post
    fixed = 0
    late = 0
    incomplete = False
    for row in rows:
        post = found.get(str(row["ext_id"]))
        if post is None:
            if late >= OZON_CAP:
                incomplete = True
                continue
            late += 1
            try:
                post = pull_ozon_get(headers, "fbs", str(row["ext_id"]), strict=True) or {}
            except Exception as exc:
                incomplete = True
                print("ozon %s не ответил: %s" % (row["ext_id"], exc))
                continue
        raw = (post or {}).get("status") or ""
        if not raw or raw not in statuses_mod.OZON:
            incomplete = True
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
    if incomplete:
        raise RuntimeError("Ozon: часть рабочих отправлений не удалось сверить")
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


def _run(client_id=None, live=False):
    seen, fixed, notes = 0, 0, []
    for cab in list_cabinets():
        if not cab["active"] or not cab["token"] or (client_id and cab["client_id"] != int(client_id)):
            continue
        if cab["marketplace"] not in ("wb", "ozon"):
            continue
        try:
            with run_lock(name=lock_name(cab["client_id"]), blocking=False):
                check = check_wb if cab["marketplace"] == "wb" else check_ozon
                n, k = check(cab, live=live)
                record_sync(cab["id"], "statuses", count=n)
        except BlockingIOError:
            continue
        except Exception as exc:
            err = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            record_sync(cab["id"], "statuses", error=err)
            notes.append("Кабинет %s: %s" % (cab["id"], err))
            continue
        seen += n
        fixed += k
    prune_old_shipments()
    if not live:
        check_supplies()
    print("сверка: проверено %s, поправлено %s, ошибок %s" % (seen, fixed, len(notes)), flush=True)
    return {"seen": seen, "fixed": fixed, "notes": notes}


def run_live(client_id=None):
    init_db()
    with run_lock(name="ff-live-statuses", blocking=False):
        return _run(client_id=client_id, live=True)


if __name__ == "__main__":
    print(run())
