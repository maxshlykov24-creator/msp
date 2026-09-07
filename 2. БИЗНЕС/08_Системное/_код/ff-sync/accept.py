"""Фактическая приёмка на склад: с неё стартует счётчик денег.

Заведение карточки и заказ поставщика — ещё не приёмка. Товар считается
принятым, когда кладовщик провёл в МойСклад «Приёмку» по этому заказу и
остаток сел на склад. До этого партия ничего не должна: в lots.accepted_at
пусто, хранение и сборка по ней равны нулю.
"""

from datetime import datetime, timedelta, timezone

from db import init_db, lots_awaiting_accept, mark_lot_accepted, run_lock
from net import MS_BASE, ms_headers, req

MSK = timezone(timedelta(hours=3))
POS_LIMIT = 1000


def product_id_of(position):
    href = (((position.get("assortment") or {}).get("meta") or {}).get("href") or "")
    return href.rstrip("/").rsplit("/", 1)[-1].split("?")[0] if href else ""


def supply_positions(supply_id):
    """Позиции приёмки постранично: у большой поставки их больше сотни."""
    out = []
    offset = 0
    while True:
        r = req(
            "GET",
            MS_BASE + "/entity/supply/%s/positions" % supply_id,
            headers=ms_headers(),
            params={"limit": POS_LIMIT, "offset": offset},
        )
        if r.status_code != 200:
            return out, "позиции приёмки %s: %s" % (supply_id, r.status_code)
        data = r.json() or {}
        rows = data.get("rows") or []
        out.extend(rows)
        offset += len(rows)
        if len(rows) < POS_LIMIT or offset >= int((data.get("meta") or {}).get("size") or 0):
            return out, ""


def supplies_of_order(order_id):
    """Приёмки, привязанные к заказу поставщика."""
    r = req("GET", MS_BASE + "/entity/purchaseorder/%s" % order_id, headers=ms_headers())
    if r.status_code != 200:
        return [], "заказ поставщика %s: %s" % (order_id, r.status_code)
    ids = []
    for item in (r.json() or {}).get("supplies") or []:
        href = ((item or {}).get("meta") or {}).get("href") or ""
        sid = href.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if sid:
            ids.append(sid)
    return ids, ""


def accepted_map(order_id):
    """{ms_product_id: дата приёмки} по одному заказу поставщика.

    Берём самую раннюю проведённую приёмку: клиент платит с первых суток,
    когда товар физически лёг на склад, а не с последней долитой партии.
    """
    ids, err = supplies_of_order(order_id)
    if err:
        return {}, err
    found = {}
    for sid in ids:
        r = req("GET", MS_BASE + "/entity/supply/%s" % sid, headers=ms_headers())
        if r.status_code != 200:
            continue
        doc = r.json() or {}
        if not doc.get("applicable"):
            # черновик приёмки склад не двигает
            continue
        if doc.get("deleted"):
            # приёмка лежит в корзине: остаток она уже не держит
            continue
        when = str(doc.get("moment") or "").replace(" ", "T")
        if not when:
            continue
        rows, perr = supply_positions(sid)
        if perr:
            return found, perr
        for pos in rows:
            pid = product_id_of(pos)
            if not pid:
                continue
            if pid not in found or when < found[pid]:
                found[pid] = when
    return found, ""


def run(blocking=True):
    init_db()
    with run_lock("accept", blocking=blocking):
        return _run()


def _run():
    lots = lots_awaiting_accept()
    if not lots:
        return {"checked": 0, "accepted": [], "errors": []}
    cache = {}
    accepted = []
    errors = []
    for lot in lots:
        order_id = lot["ms_supply_id"]
        if order_id not in cache:
            cache[order_id], err = accepted_map(order_id)
            if err:
                errors.append(err)
        when = cache[order_id].get(lot["ms_product_id"])
        if not when:
            continue
        mark_lot_accepted(lot["id"], when)
        accepted.append({"id": lot["id"], "article": lot["article"] or "", "at": when[:16]})
        print("приёмка партии %s: %s" % (lot["id"], when[:16]))
    return {"checked": len(lots), "accepted": accepted, "errors": errors}


def stamp():
    return datetime.now(MSK).isoformat(timespec="seconds")


if __name__ == "__main__":
    print(run())
