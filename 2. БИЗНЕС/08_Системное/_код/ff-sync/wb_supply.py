"""Поставки FBS Wildberries: сборка, грузоместа, QR, передача в доставку.

Порядок, который Сергей делает руками в ЛК:

1. создать поставку — `POST /api/v3/supplies`;
2. добавить собранные задания — `PATCH /api/marketplace/v3/supplies/{id}/orders`,
   до 100 за раз. В этот момент задание уходит в статус `confirm` («На сборке»);
3. завести грузоместа — `POST /api/v3/supplies/{id}/trbx`, и разложить по ним
   задания — `PATCH /api/v3/supplies/{id}/trbx`;
4. напечатать QR грузомест — `POST /api/v3/supplies/{id}/trbx/stickers`;
5. передать поставку в доставку — `PATCH /api/v3/supplies/{id}/deliver`. Шаг
   необратимый: все задания уходят в «В доставке», добавить в поставку больше
   ничего нельзя. Поэтому спрашиваем подтверждение;
6. QR самой поставки — `GET /api/v3/supplies/{id}/barcode`, доступен **только
   после** передачи в доставку.

Лимит на всю группу ручек: 300 запросов в минуту, и любой ответ 4XX списывается
как десять запросов. Поэтому батчим и не крутим повторы на ошибках.
"""

import base64

from net import WB_BASE, req, wb_headers

ORDERS_CHUNK = 100   # предел батча добавления заданий в поставку
TRBX_CHUNK = 1000    # предел создания грузомест за запрос


class SupplyError(Exception):
    pass


def _ids(values):
    out = []
    for raw in values:
        digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
        if digits:
            out.append(int(digits))
    return out


def _fail(r, what):
    detail = (r.text or "")[:300] if r is not None else ""
    code = r.status_code if r is not None else "нет ответа"
    return SupplyError("WB: %s (%s) %s" % (what, code, detail))


def create(cab, name):
    r = req("POST", WB_BASE + "/api/v3/supplies", headers=wb_headers(cab["token"]), json={"name": name})
    if r.status_code not in (200, 201):
        raise _fail(r, "не создал поставку")
    try:
        data = r.json() or {}
    except ValueError:
        raise SupplyError("WB: нераспознанный ответ на создание поставки")
    ext = str(data.get("id") or data.get("supplyId") or "")
    if not ext:
        raise SupplyError("WB: не вернул номер поставки")
    return ext


def add_orders(cab, supply_ext, order_ids):
    """Добавить задания в поставку. Возвращает (сколько ушло, заметки).

    Батчевый метод живёт на префиксе `/api/marketplace/v3`. Если кабинет его не
    знает, откатываемся на поштучный `/api/v3/.../orders/{orderId}` — так же,
    как это делал ЛК до появления батча.
    """
    ids = _ids(order_ids)
    if not ids:
        return 0, []
    heads = wb_headers(cab["token"])
    done = 0
    notes = []
    for i in range(0, len(ids), ORDERS_CHUNK):
        chunk = ids[i : i + ORDERS_CHUNK]
        r = req(
            "PATCH",
            "%s/api/marketplace/v3/supplies/%s/orders" % (WB_BASE, supply_ext),
            headers=heads,
            json={"orders": chunk},
        )
        if r.status_code in (200, 204):
            done += len(chunk)
            continue
        if r.status_code in (404, 405):
            for oid in chunk:
                one = req(
                    "PATCH",
                    "%s/api/v3/supplies/%s/orders/%s" % (WB_BASE, supply_ext, oid),
                    headers=heads,
                )
                if one.status_code in (200, 204):
                    done += 1
                else:
                    notes.append("WB: задание %s не ушло в поставку (%s) %s" % (oid, one.status_code, (one.text or "")[:160]))
            continue
        notes.append("WB: батч из %s заданий не ушёл в поставку (%s) %s" % (len(chunk), r.status_code, (r.text or "")[:200]))
    return done, notes


def add_boxes(cab, supply_ext, amount):
    """Создать грузоместа. Возвращает список идентификаторов WB-TRBX-…"""
    amount = int(amount or 0)
    if amount < 1:
        raise SupplyError("сколько грузомест создать?")
    out = []
    left = amount
    while left > 0:
        step = min(left, TRBX_CHUNK)
        r = req(
            "POST",
            "%s/api/v3/supplies/%s/trbx" % (WB_BASE, supply_ext),
            headers=wb_headers(cab["token"]),
            json={"amount": step},
        )
        if r.status_code not in (200, 201):
            raise _fail(r, "не создал грузоместа")
        try:
            data = r.json() or {}
        except ValueError:
            raise SupplyError("WB: нераспознанный ответ на создание грузомест")
        got = [str(x) for x in (data.get("trbxIds") or []) if x]
        if not got:
            raise SupplyError("WB: не вернул номера грузомест")
        out.extend(got)
        left -= step
    return out


def put_in_box(cab, supply_ext, trbx_ext, order_ids):
    """Уложить задания в грузоместо."""
    ids = _ids(order_ids)
    if not ids:
        return 0
    r = req(
        "PATCH",
        "%s/api/v3/supplies/%s/trbx" % (WB_BASE, supply_ext),
        headers=wb_headers(cab["token"]),
        json={"trbxId": trbx_ext, "orderIds": ids},
    )
    if r.status_code not in (200, 204):
        raise _fail(r, "не уложил задания в грузоместо %s" % trbx_ext)
    return len(ids)


def take_from_box(cab, supply_ext, trbx_ext, order_id):
    """Изъять задание из грузоместа."""
    ids = _ids([order_id])
    if not ids:
        return False
    r = req(
        "DELETE",
        "%s/api/v3/supplies/%s/trbx/%s/orders/%s" % (WB_BASE, supply_ext, trbx_ext, ids[0]),
        headers=wb_headers(cab["token"]),
    )
    if r.status_code not in (200, 204):
        raise _fail(r, "не изъял задание %s из грузоместа" % ids[0])
    return True


def drop_boxes(cab, supply_ext, trbx_ids):
    """Удалить грузоместа. Доступно только пока поставка на сборке."""
    ids = [str(x) for x in trbx_ids if x]
    if not ids:
        return 0
    r = req(
        "DELETE",
        "%s/api/v3/supplies/%s/trbx" % (WB_BASE, supply_ext),
        headers=wb_headers(cab["token"]),
        json={"trbxIds": ids},
    )
    if r.status_code not in (200, 204):
        raise _fail(r, "не удалил грузоместа")
    return len(ids)


def box_stickers(cab, supply_ext, trbx_ids, kind="png"):
    """QR грузомест: [{ext_id, barcode, png}]. Пустое грузоместо стикер не получит."""
    ids = [str(x) for x in trbx_ids if x]
    if not ids:
        return [], ["Нет грузомест: сначала создай хотя бы одно."]
    r = req(
        "POST",
        "%s/api/v3/supplies/%s/trbx/stickers" % (WB_BASE, supply_ext),
        headers=wb_headers(cab["token"]),
        params={"type": kind},
        json={"trbxIds": ids},
    )
    if r.status_code != 200:
        return [], ["WB: не отдал QR грузомест (%s) %s" % (r.status_code, (r.text or "")[:200])]
    try:
        data = r.json() or {}
    except ValueError:
        return [], ["WB: нераспознанный ответ на QR грузомест"]
    out = []
    for i, item in enumerate(data.get("stickers") or []):
        if not isinstance(item, dict):
            continue
        try:
            png = base64.b64decode(item.get("file") or "")
        except Exception:
            continue
        if not png:
            continue
        out.append(
            {
                # WB отдаёт стикеры в порядке запроса, но идентификатор в ответе
                # называется по-разному в версиях — берём из запроса по позиции
                "ext_id": str(item.get("trbxId") or (ids[i] if i < len(ids) else "")),
                "barcode": str(item.get("barcode") or ""),
                "png": png,
            }
        )
    notes = []
    if len(out) < len(ids):
        notes.append("WB отдал QR на %s грузомест из %s: пустые коробки стикер не получают." % (len(out), len(ids)))
    return out, notes


def supply_qr(cab, supply_ext, kind="png"):
    """QR поставки. Доступен только после передачи в доставку."""
    r = req(
        "GET",
        "%s/api/v3/supplies/%s/barcode" % (WB_BASE, supply_ext),
        headers=wb_headers(cab["token"]),
        params={"type": kind},
    )
    if r.status_code != 200:
        return None, ["WB: не отдал QR поставки (%s) %s. Он появляется после передачи в доставку." % (r.status_code, (r.text or "")[:200])]
    try:
        data = r.json() or {}
        png = base64.b64decode(data.get("file") or "")
    except Exception:
        return None, ["WB: нераспознанный ответ на QR поставки"]
    if not png:
        return None, ["WB: пустой QR поставки"]
    return {"barcode": str(data.get("barcode") or supply_ext), "png": png}, []


def deliver(cab, supply_ext):
    """Передать поставку в доставку. Необратимо."""
    r = req(
        "PATCH",
        "%s/api/v3/supplies/%s/deliver" % (WB_BASE, supply_ext),
        headers=wb_headers(cab["token"]),
    )
    if r.status_code not in (200, 204):
        raise _fail(r, "не передал поставку в доставку")
    return True


def info(cab, supply_ext):
    """Карточка поставки у площадки: пригодится, чтобы сверить состояние."""
    r = req("GET", "%s/api/v3/supplies/%s" % (WB_BASE, supply_ext), headers=wb_headers(cab["token"]))
    if r.status_code != 200:
        return {}
    try:
        return r.json() or {}
    except ValueError:
        return {}
