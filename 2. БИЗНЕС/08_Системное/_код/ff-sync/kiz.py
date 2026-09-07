"""Коды маркировки: сколько их нужно площадке и передача того, что просканировал склад.

Момент сканирования диктуют сами площадки, и он у них разный:

* **Ozon** принимает экземпляры только пока отправление в `awaiting_packaging`,
  то есть **до** «Собрано». Цепочка: `v6 create-or-get` (сколько кодов нужно и
  какие `exemplar_id` заводить) → `v5 validate` → `v6 set` → `v5 status` до
  `ship_available`, и только потом `ship`.
* **Wildberries** наоборот: задание должно быть уже в поставке (статус
  `confirm`), тогда работает `PUT /api/v3/orders/{id}/meta/sgtin`.

Обе точки — это вкладка «На сборке», поэтому кнопка КиЗ живёт там.

Разделитель групп GS в коде передаём живым символом `\\x1d`: в JSON он уедет
как `\\u001d`, ровно как просит WB. Криптохвост оставляем как отдал сканер —
обрезать его нельзя, проверку кода делает площадка.
"""

from db import get_shipments_by_ids, get_cabinet, replace_shipment_marks
from net import OZON_BASE, WB_BASE, ozon_headers, req, wb_headers
from shipments_pull import gtin_of

OZON_CREATE = OZON_BASE + "/v6/fbs/posting/product/exemplar/create-or-get"
OZON_VALIDATE = OZON_BASE + "/v5/fbs/posting/product/exemplar/validate"
OZON_SET = OZON_BASE + "/v6/fbs/posting/product/exemplar/set"
OZON_STATUS = OZON_BASE + "/v5/fbs/posting/product/exemplar/status"
WB_META = WB_BASE + "/api/marketplace/v3/orders/meta"
WB_SGTIN = WB_BASE + "/api/v3/orders/%s/meta/sgtin"

# decision у WB: маркировка уже принята площадкой. sgtinIntroduced в описании
# метода не значится, но живые задания отдают именно его — код введён в оборот
WB_DONE = ("filled", "deadlineExceeded", "sgtinIntroduced")
# decision у WB: код нужен либо не прошёл проверку
WB_WANT = (
    "required",
    "sgtinInvalidFormat",
    "sgtinNotFound",
    "sgtinEmitted",
    "sgtinApplied",
    "sgtinWrittenOff",
    "sgtinRetired",
    "sgtinDisaggregated",
    "sgtinAppliedNotPaid",
)
WB_DECISION_RU = {
    "filled": "принято площадкой",
    "deadlineExceeded": "принято, срок проверки истёк",
    "sgtinIntroduced": "код введён в оборот",
    "optional": "не обязательно",
    "pending": "на проверке у площадки",
    "required": "нужен код",
    "sgtinInvalidFormat": "неверный формат кода",
    "sgtinNotFound": "кода нет в Честном знаке",
    "sgtinEmitted": "код только эмитирован",
    "sgtinApplied": "не пройден ввод в оборот",
    "sgtinWrittenOff": "код списан",
    "sgtinRetired": "код выбыл",
    "sgtinDisaggregated": "код расформирован",
    "sgtinAppliedNotPaid": "код не оплачен",
}


class KizError(Exception):
    pass


def clean_code(raw):
    """Код как отдал сканер. Режем только переводы строк и краевые пробелы."""
    text = str(raw or "").replace("\r", "").replace("\n", "").strip()
    # часть сканеров и ручной ввод отдают GS экранированной последовательностью
    for esc in ("\\u001d", "\\u001D", "\\x1d", "\\x1D"):
        text = text.replace(esc, "\x1d")
    return text


def clean_codes(raw_list):
    out = []
    for raw in raw_list or []:
        code = clean_code(raw)
        if code and code not in out:
            out.append(code)
    return out


def _row(ship_id):
    rows = get_shipments_by_ids([ship_id])
    if not rows:
        raise KizError("отправление не найдено")
    return rows[0]


def _cab(row):
    cab = get_cabinet(row["cabinet_id"])
    if not cab or not cab["token"]:
        raise KizError("у кабинета нет токена")
    return cab


def _fail(r, what):
    raise KizError("%s: площадка ответила %s. %s" % (what, r.status_code, (r.text or "")[:300]))


# --- Ozon ---------------------------------------------------------------


def _ozon_create(cab, posting):
    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    r = req("POST", OZON_CREATE, headers=heads, json={"posting_number": posting})
    if r.status_code != 200:
        _fail(r, "Ozon не отдал список экземпляров")
    try:
        return r.json() or {}
    except ValueError:
        raise KizError("Ozon вернул нераспознанный ответ по экземплярам")


def _ozon_plan(row, cab):
    data = _ozon_create(cab, row["ext_id"])
    need = 0
    have = 0
    products = []
    for prod in data.get("products") or []:
        if not isinstance(prod, dict):
            continue
        qty = int(float(prod.get("quantity") or 0))
        marked = bool(prod.get("is_mandatory_mark_needed")) or bool(prod.get("is_mandatory_mark_possible"))
        filled = 0
        for ex in prod.get("exemplars") or []:
            if any((m or {}).get("mark") for m in (ex.get("marks") or [])):
                filled += 1
        if bool(prod.get("is_mandatory_mark_needed")):
            need += qty
        have += filled
        products.append(
            {
                "product_id": prod.get("product_id"),
                "quantity": qty,
                "marked": marked,
                "required": bool(prod.get("is_mandatory_mark_needed")),
                "filled": filled,
            }
        )
    note = ""
    if row["status_group"] and row["status_group"] != "new":
        note = "Ozon принимает коды только до сборки: отправление уже ушло дальше «ожидает упаковки»."
    return {"need": need, "have": have, "products": products, "note": note}


def _ozon_submit(row, cab, codes):
    """Экземпляры Ozon: сначала проверка кодов, потом set, потом ждём ship_available."""
    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    data = _ozon_create(cab, row["ext_id"])
    notes = []
    slots = []
    for prod in data.get("products") or []:
        if not isinstance(prod, dict) or not prod.get("product_id"):
            continue
        exemplars = [ex for ex in (prod.get("exemplars") or []) if isinstance(ex, dict)]
        for ex in exemplars:
            slots.append((int(prod["product_id"]), ex))
    if not slots:
        raise KizError("Ozon не создал экземпляры под это отправление, коды передавать некуда")
    if len(codes) > len(slots):
        raise KizError(
            "кодов больше, чем экземпляров у площадки: кодов %s, экземпляров %s" % (len(codes), len(slots))
        )
    if len(codes) < len(slots):
        notes.append("Ozon ждёт %s кодов, передаю %s — остальные останутся пустыми." % (len(slots), len(codes)))

    r = req(
        "POST",
        OZON_VALIDATE,
        headers=heads,
        json={
            "posting_number": row["ext_id"],
            "products": [
                {
                    "product_id": pid,
                    "exemplars": [{"marks": [{"mark": code, "mark_type": "mandatory_mark"}]}],
                }
                for (pid, _ex), code in zip(slots, codes)
            ],
        },
    )
    if r.status_code == 200:
        try:
            checked = r.json() or {}
        except ValueError:
            checked = {}
        bad = []
        for prod in checked.get("products") or []:
            for ex in prod.get("exemplars") or []:
                for mark in ex.get("marks") or []:
                    if mark.get("valid") is False:
                        bad.append("%s: %s" % ((mark.get("mark") or "")[:20], ", ".join(mark.get("errors") or []) or "не принят"))
        if bad:
            raise KizError("Ozon не принял коды: " + "; ".join(bad))
    else:
        notes.append("Ozon не проверил коды заранее (%s), отправляю как есть." % r.status_code)

    products = {}
    for (pid, ex), code in zip(slots, codes):
        item = products.setdefault(pid, {"product_id": pid, "exemplars": []})
        body = {
            "exemplar_id": ex.get("exemplar_id"),
            "marks": [{"mark": code, "mark_type": "mandatory_mark"}],
            "is_gtd_absent": True if ex.get("gtd") in (None, "") else bool(ex.get("is_gtd_absent")),
            "is_rnpt_absent": True if ex.get("rnpt") in (None, "") else bool(ex.get("is_rnpt_absent")),
        }
        if ex.get("gtd"):
            body["gtd"] = ex["gtd"]
        if ex.get("rnpt"):
            body["rnpt"] = ex["rnpt"]
        item["exemplars"].append(body)

    r = req(
        "POST",
        OZON_SET,
        headers=heads,
        json={"posting_number": row["ext_id"], "products": list(products.values())},
    )
    if r.status_code != 200:
        _fail(r, "Ozon не принял коды маркировки")

    # set асинхронный: 200 значит «задачу приняли», результат смотрим в status
    state = ""
    r = req("POST", OZON_STATUS, headers=heads, json={"posting_number": row["ext_id"]})
    if r.status_code == 200:
        try:
            state = str((r.json() or {}).get("status") or "")
        except ValueError:
            state = ""
    if state == "validation_in_process":
        notes.append("Ozon ещё проверяет коды. Жми «Собрано», когда проверка пройдёт.")
    elif state == "ship_not_available":
        notes.append("Ozon пока не разрешает сборку по этим кодам — проверь их в кабинете.")
    elif state == "ship_available":
        notes.append("Ozon проверил коды: сборка доступна.")
    return {"sent": len(codes), "state": state, "notes": notes}


# --- Wildberries --------------------------------------------------------


def _wb_meta(cab, ext_id):
    r = req("POST", WB_META, headers=wb_headers(cab["token"]), json={"orders": [int(ext_id)]})
    if r.status_code != 200:
        _fail(r, "WB не отдал метаданные задания")
    try:
        data = r.json() or {}
    except ValueError:
        raise KizError("WB вернул нераспознанный ответ по метаданным")
    for order in data.get("orders") or []:
        if str(order.get("id")) == str(ext_id):
            return order
    return {}


def _wb_sgtin(order):
    for item in order.get("metaDetails") or []:
        if (item or {}).get("key") == "sgtin":
            return item
    return None


def _wb_plan(row, cab):
    order = _wb_meta(cab, row["ext_id"])
    item = _wb_sgtin(order)
    if not item:
        # объект метаданных не вернулся — значит такой маркировки у задания быть не может
        return {"need": 0, "have": 0, "products": [], "note": "WB не запрашивает коды по этому заданию."}
    decision = str(item.get("decision") or "")
    value = item.get("value")
    have = len(value) if isinstance(value, list) else (1 if value else 0)
    qty = int(float(row["qty"] or 1))
    need = qty if decision in WB_WANT else 0
    note = WB_DECISION_RU.get(decision, decision)
    if decision in WB_DONE:
        note = "WB уже принял коды: %s." % note
    elif decision == "pending":
        note = "WB проверяет ранее отправленные коды."
    elif decision == "optional":
        # WB примет код, но без него поставку в доставку всё равно отдаст:
        # не подсвечиваем как долг, иначе склад побежит искать несуществующий КиЗ
        note = "WB не требует код по этому заданию, но примет его."
    else:
        note = "WB: %s." % note
    return {"need": need, "have": have, "products": [], "note": note, "decision": decision}


def _wb_submit(row, cab, codes):
    order = _wb_meta(cab, row["ext_id"])
    if not _wb_sgtin(order):
        raise KizError("WB не принимает коды по этому заданию: в метаданных нет sgtin")
    r = req("PUT", WB_SGTIN % row["ext_id"], headers=wb_headers(cab["token"]), json={"sgtins": codes})
    if r.status_code not in (200, 204):
        _fail(r, "WB не принял коды маркировки")
    notes = []
    item = _wb_sgtin(_wb_meta(cab, row["ext_id"])) or {}
    decision = str(item.get("decision") or "")
    if decision:
        notes.append("WB: %s." % WB_DECISION_RU.get(decision, decision))
    return {"sent": len(codes), "state": decision, "notes": notes}


# --- наружу -------------------------------------------------------------


def plan(ship_id):
    """Сколько кодов ждёт площадка по этому отправлению и что уже принято."""
    row = _row(ship_id)
    cab = _cab(row)
    if row["kind"] != "fbs":
        raise KizError("коды маркировки вводим только по FBS")
    if row["marketplace"] == "ozon":
        out = _ozon_plan(row, cab)
    elif row["marketplace"] == "wb":
        out = _wb_plan(row, cab)
    else:
        raise KizError("неизвестная площадка %s" % row["marketplace"])
    out.update(
        {
            "id": row["id"],
            "ext_id": row["ext_id"],
            "marketplace": row["marketplace"],
            "article": row["article"] or "",
            "name": row["name"] or "",
            "qty": int(float(row["qty"] or 1)),
            "marks": int(row["marks_count"] or 0),
        }
    )
    return out


def submit(ship_id, raw_codes):
    """Передать коды на площадку и записать их себе — но только после её «да»."""
    codes = clean_codes(raw_codes)
    if not codes:
        raise KizError("нет кодов для передачи")
    row = _row(ship_id)
    cab = _cab(row)
    if row["kind"] != "fbs":
        raise KizError("коды маркировки вводим только по FBS")
    if row["marketplace"] == "ozon":
        res = _ozon_submit(row, cab, codes)
    elif row["marketplace"] == "wb":
        res = _wb_submit(row, cab, codes)
    else:
        raise KizError("неизвестная площадка %s" % row["marketplace"])
    replace_shipment_marks(
        row["id"],
        [{"code": code, "gtin": gtin_of(code), "article": row["article"] or ""} for code in codes],
    )
    return res
