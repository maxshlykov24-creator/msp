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

import re
import json
import logging

log = logging.getLogger(__name__)

from db import get_shipments_by_ids, get_cabinet, list_shipment_marks, replace_shipment_marks, run_lock, shipment_items
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


# Что рисует ЙЦУКЕН, и какую латинскую клавишу сканер на самом деле жал.
# Знаки вроде «.» и «"» тоже из этой раскладки: точка на слэше, кавычка на Shift+2.
_RU_LAYOUT = "йцукенгшщзхъфывапролджэячсмитьбю.ёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,Ё\"№;:?/"
_EN_LAYOUT = "qwertyuiop[]asdfghjkl;'zxcvbnm,./`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~@#$^&|"
_RU_TO_EN = dict(zip(_RU_LAYOUT, _EN_LAYOUT))


def layout_to_en(text):
    """Вернуть латиницу, если строку набрали при русской раскладке.

    Без кириллицы не трогаем: английский скан и так верный, а «.» в нём это точка,
    не слэш. GS и цифры в карте нет.
    """
    if not re.search(r"[А-Яа-яЁё№]", text):
        return text
    return "".join(_RU_TO_EN.get(ch, ch) for ch in text)


def clean_code(raw):
    """Код как отдал сканер. Режем переводы строк, краевые пробелы и чужую раскладку."""
    text = str(raw or "").replace("\r", "").replace("\n", "").strip()
    text = layout_to_en(text)
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


def normalize_wb_code(code):
    """Восстановить GS только в однозначном полном формате 01/21/91/92.

    HID-сканер может передать GS как Ctrl+], который обычный input теряет.
    Длины полей здесь фиксированы: GTIN 14, серия 13, ключ 4, подпись 44.
    Не ищем 91/92 внутри серии и не достраиваем отсутствующий криптохвост.
    """
    code = clean_code(code)
    if code.startswith("]d2"):
        code = code[3:]
    match = re.fullmatch(r"(01\d{14}21[^\x1d]{13})\x1d?(91[^\x1d]{4})\x1d?(92[^\x1d]{44})", code)
    return "\x1d".join(match.groups()) if match else code


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


def _ozon_product_info(row, data):
    items = shipment_items(row)
    by_id = {str(i.get('product_id')): i for i in items if i.get('product_id')}
    products = data.get('products') or []
    out = []
    for prod in products:
        if not isinstance(prod, dict) or not prod.get('product_id'):
            raise KizError('Ozon не отдал полный состав экземпляров')
        item = by_id.get(str(prod.get('product_id')))
        if item is None and len(products) == 1 and len(items) == 1 and not items[0].get('product_id'):
            item = items[0]
        if item is None:
            raise KizError('Состав отправления изменился. Обнови отправления и открой КиЗ заново.')
        if int(prod.get('quantity') or 0) != int(item.get('qty') or 0):
            raise KizError('Количество товара изменилось. Обнови отправления и открой КиЗ заново.')
        out.append((prod, item))
    return out


def _ozon_plan(row, cab):
    data = _ozon_create(cab, row["ext_id"])
    need = 0
    have = 0
    products = []
    remote_marks = []
    for prod, item in _ozon_product_info(row, data):
        if not isinstance(prod, dict):
            continue
        qty = int(float(prod.get("quantity") or 0))
        required = bool(prod.get('is_mandatory_mark_needed')) or bool(item.get('need_kiz'))
        if len(shipment_items(row)) == 1:
            required = required or product_marked(row)
        marked = required or bool(prod.get("is_mandatory_mark_possible"))
        filled = 0
        for ex in prod.get("exemplars") or []:
            if any((m or {}).get("mark") for m in (ex.get("marks") or [])):
                filled += 1
            for mark in ex.get('marks') or []:
                if mark.get('mark') and mark.get('mark_type') == 'mandatory_mark':
                    remote_marks.append({'code': mark['mark'], 'gtin': gtin_of(mark['mark']), 'article': item.get('article') or ''})
        if required:
            need += qty
        have += filled
        products.append(
            {
                "product_id": prod.get("product_id"),
                "quantity": qty,
                "marked": marked,
                "required": required,
                "article": item.get('article') or '',
                "name": item.get('name') or '',
                "gtin": item.get('gtin') or '',
                "filled": filled,
            }
        )
    note = ""
    if row["status_group"] and row["status_group"] != "new":
        note = "Ozon принимает коды только до сборки: отправление уже ушло дальше «ожидает упаковки»."
    return {"need": need, "have": have, "products": products, "note": note, '_remote_marks': remote_marks}


def _check_ozon_validation(checked, codes, articles):
    """Ошибки бывают у товара, экземпляра и марки; HTTP 200 не означает успех."""
    bad, verified = [], set()
    for prod in checked.get('products') or []:
        article = articles.get(prod.get('product_id'), str(prod.get('product_id') or 'товар'))
        if prod.get('valid') is False or prod.get('error'):
            bad.append('%s: %s' % (article, prod.get('error') or 'товар не прошёл проверку'))
        for number, ex in enumerate(prod.get('exemplars') or [], 1):
            if ex.get('valid') is False or ex.get('errors'):
                bad.append('%s, экземпляр %s: %s' % (article, number,
                    ', '.join(map(str, ex.get('errors') or [])) or 'экземпляр не прошёл проверку'))
            for mark in ex.get('marks') or []:
                if mark.get('valid') is False or mark.get('errors'):
                    bad.append('%s, код %s: %s' % (article, number,
                        ', '.join(map(str, mark.get('errors') or [])) or 'код не принят'))
                elif mark.get('valid') is True:
                    verified.add(mark.get('mark'))
    if bad:
        raise KizError('Ozon не принял коды: ' + '; '.join(bad))
    if verified != set(codes):
        raise KizError('Ozon не подтвердил проверку всех кодов, передача остановлена')


def _ozon_submit(row, cab, codes, assignments=None):
    """Экземпляры Ozon: сначала проверка кодов, потом set, потом ждём ship_available."""
    if row['status_group'] != 'new':
        raise KizError('Ozon принимает КиЗ до сборки. Обнови статус отправления.')
    heads = ozon_headers(cab["client_id_ext"], cab["token"])
    data = _ozon_create(cab, row["ext_id"])
    notes = []
    info = _ozon_product_info(row, data)
    pools, articles, gtins, required = {}, {}, {}, {}
    for prod, item in info:
        pid = int(prod['product_id'])
        must = bool(prod.get('is_mandatory_mark_needed')) or bool(item.get('need_kiz'))
        if len(info) == 1:
            must = must or product_marked(row)
        if not (must or prod.get('is_mandatory_mark_possible')):
            continue
        pools[pid] = [ex for ex in prod.get('exemplars') or [] if isinstance(ex, dict) and ex.get('exemplar_id')]
        articles[pid] = item.get('article') or str(pid)
        gtins[pid] = str(item.get('gtin') or '').zfill(14) if item.get('gtin') else ''
        required[pid] = int(prod.get('quantity') or 0) if must else 0
    if not pools:
        raise KizError("Ozon не создал экземпляры под это отправление, коды передавать некуда")
    explicit = {}
    for assignment in assignments or []:
        code = normalize_wb_code(assignment.get('code'))
        try:
            pid = int(assignment.get('product_id'))
        except (TypeError, ValueError):
            raise KizError('Не выбран артикул кода маркировки')
        if code in explicit and explicit[code] != pid:
            raise KizError('Один код назначен разным артикулам')
        explicit[code] = pid
    slots, counts, saved = [], {}, []
    for code in codes:
        gtin = gtin_of(code)
        matched = [pid for pid, value in gtins.items() if value and value == gtin]
        pid = explicit.get(code)
        if pid is None:
            pid = matched[0] if len(matched) == 1 else next(iter(pools)) if len(pools) == 1 else None
        if pid not in pools:
            raise KizError('Для каждого кода выбери его артикул. Порядок сканирования не определяет товар.')
        if gtins[pid] and gtin != gtins[pid]:
            raise KizError('Код не относится к выбранному артикулу %s' % articles[pid])
        index = counts.get(pid, 0)
        if index >= len(pools[pid]):
            raise KizError('Кодов больше, чем экземпляров артикула %s' % articles[pid])
        counts[pid] = index + 1
        slots.append((pid, pools[pid][index]))
        saved.append({'code': code, 'gtin': gtin, 'article': articles[pid]})
    for pid, qty in required.items():
        if counts.get(pid, 0) < qty:
            raise KizError('Артикул %s: нужно %s кодов, внесено %s' % (articles[pid], qty, counts.get(pid, 0)))

    validation = {}
    for (pid, ex), code in zip(slots, codes):
        validation.setdefault(pid, {'product_id': pid, 'exemplars': []})['exemplars'].append(
            {'exemplar_id': ex['exemplar_id'], 'marks': [{'mark': code, 'mark_type': 'mandatory_mark'}]})
    r = req(
        "POST",
        OZON_VALIDATE,
        headers=heads,
        json={
            "posting_number": row["ext_id"],
            "products": list(validation.values()),
        },
    )
    if r.status_code == 200:
        try:
            checked = r.json() or {}
        except ValueError:
            checked = {}
        _check_ozon_validation(checked, codes, articles)
    else:
        _fail(r, 'Ozon не проверил коды, передача остановлена')

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
        retry_safe=False,
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
        raise KizError('Ozon отклонил маркировку: сборка недоступна. Проверь коды в кабинете.')
    elif state == "ship_available":
        notes.append("Ozon проверил коды: сборка доступна.")
    else:
        raise KizError('Коды отправлены, но Ozon ещё не подтвердил результат. Проверь отправление перед повтором.')
    return {"sent": len(codes), "state": state, "notes": notes, '_saved': saved}


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


def product_marked(row):
    """Карточка в нашем каталоге помечена как маркируемая.

    Это сильнее ответа площадки. WB часто пишет optional по заказу, у которого
    в карточке needKiz, и без нашей проверки такой товар уезжает без кода.
    """
    from db import find_cache

    keys = row.keys() if hasattr(row, "keys") else ()
    client_id = row["client_id"] if "client_id" in keys else None
    if not client_id:
        return False
    barcode = (row["barcode"] or "") if "barcode" in keys else ""
    article = (row["article"] or "") if "article" in keys else ""
    hits = find_cache(client_id, barcode=barcode or None, article=article or None)
    for hit in hits:
        if "need_kiz" in hit.keys() and hit["need_kiz"]:
            return True
    return False


def missing_mark_note(rows):
    """Текст отказа, если маркированный товар идёт дальше без кодов. Пусто, если можно."""
    missing = []
    for row in rows:
        if dict(row).get('marketplace') == 'ozon' and dict(row).get('items_json') not in (None, '', '[]'):
            marks = list_shipment_marks([row['id']])
            for item in shipment_items(row):
                if not item.get('need_kiz'):
                    continue
                have = sum(m['article'] == item['article'] for m in marks)
                if have < item['qty']:
                    missing.append('%s: %s из %s' % (item['article'], have, item['qty']))
            continue
        keys = row.keys() if hasattr(row, "keys") else ()
        if "kind" in keys and str(row["kind"] or "") != "fbs":
            continue
        if not product_marked(row):
            continue
        qty = max(1, int(float(row["qty"] or 1)))
        have = int(row["marks_count"] or 0) if "marks_count" in keys else 0
        if have >= qty:
            continue
        label = (row["article"] or "") if "article" in keys else ""
        if not label and "ext_id" in keys:
            label = str(row["ext_id"])
        missing.append("%s: %s из %s" % (label or "заказ", have, qty))
    if not missing:
        return ""
    shown = ", ".join(missing[:6])
    if len(missing) > 6:
        shown += " и ещё %s" % (len(missing) - 6)
    return "Маркированный товар без КиЗ не пропускаем. " + shown


def _wb_plan(row, cab):
    order = _wb_meta(cab, row["ext_id"])
    item = _wb_sgtin(order)
    marked = product_marked(row)
    if not item:
        # слота sgtin нет: WB код не примет. Маркированную карточку всё равно не прячем.
        if marked:
            qty = max(1, int(float(row["qty"] or 1)))
            return {
                "need": qty,
                "have": 0,
                "products": [],
                "note": "Товар с маркировкой, но WB не принимает код по этому заданию.",
            }
        return {"need": 0, "have": 0, "products": [], "note": "WB не запрашивает коды по этому заданию."}
    decision = str(item.get("decision") or "")
    value = item.get("value")
    have = len(value) if isinstance(value, list) else (1 if value else 0)
    qty = max(1, int(float(row["qty"] or 1)))
    need = qty if decision in WB_WANT else 0
    note = WB_DECISION_RU.get(decision, decision)
    if decision in WB_DONE:
        note = "WB уже принял коды: %s." % note
    elif decision == "pending":
        note = "WB проверяет ранее отправленные коды."
    elif decision == "optional" and marked:
        # площадка отгрузку без кода пропустит, склад так делать не должен
        need = qty
        note = "Товар с маркировкой: код обязателен у нас, даже если WB его не требует."
    elif decision == "optional":
        note = "WB не требует код по этому заданию, но примет его."
    else:
        note = "WB: %s." % note
    return {"need": need, "have": have, "products": [], "note": note, "decision": decision}


def _wb_submit(row, cab, codes):
    order = _wb_meta(cab, row["ext_id"])
    item = _wb_sgtin(order)
    if not item:
        raise KizError("WB не принимает коды по этому заданию: в метаданных нет sgtin")
    decision = str(item.get("decision") or "")
    if decision in WB_DONE or decision == "pending":
        raise KizError("WB уже принял код или проверяет его. Повторная отправка затрёт существующий код")
    for code in codes:
        # У длинного КиЗ серийный номер переменной длины. Без живых GS перед
        # группами 91 и 92 WB получает строку, но затем отвергает её формат.
        if not re.fullmatch(r"01\d{14}21[^\x1d]+\x1d91[^\x1d]+\x1d92[^\x1d]+", code):
            raise KizError("Не удалось распознать полный КиЗ. Отсканируй заново: нужны серийный номер и криптохвост целиком")
    r = req("PUT", WB_SGTIN % row["ext_id"], headers=wb_headers(cab["token"]), json={"sgtins": codes})
    if r.status_code not in (200, 204):
        _fail(r, "WB не принял коды маркировки")
    notes = []
    item = _wb_sgtin(_wb_meta(cab, row["ext_id"])) or {}
    decision = str(item.get("decision") or "")
    if decision in WB_WANT and decision != "required":
        raise KizError("WB не подтвердил код: %s. Проверь маркировку; задание остаётся без принятого КиЗ." % WB_DECISION_RU.get(decision, decision))
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
    remote_marks = out.pop('_remote_marks', [])
    if remote_marks:
        known = {r['code']: {'code': r['code'], 'gtin': r['gtin'], 'article': r['article']} for r in list_shipment_marks([row['id']])}
        known.update({r['code']: r for r in remote_marks})
        replace_shipment_marks(row['id'], list(known.values()))
    saved = [r["code"] for r in list_shipment_marks([row["id"]])]
    assignments = []
    if row['marketplace'] == 'ozon':
        for mark in list_shipment_marks([row['id']]):
            matches = [p for p in out['products'] if p['article'] == mark['article']]
            if len(matches) == 1:
                assignments.append({'code': mark['code'], 'product_id': matches[0]['product_id']})
    out.update(
        {
            "id": row["id"],
            "ext_id": row["ext_id"],
            "marketplace": row["marketplace"],
            "article": row["article"] or "",
            "name": row["name"] or "",
            "qty": int(float(row["qty"] or 1)),
            "marks": int(row["marks_count"] or 0),
            # уже просканированное показываем снова: повторная отправка затирает
            # прежний набор целиком, и пустой список стёр бы принятые коды
            "codes": saved,
            "assignments": assignments,
        }
    )
    return out


def submit(ship_id, raw_codes, assignments=None):
    try:
        with run_lock(name="kiz-%s" % int(ship_id), blocking=False):
            return _submit(ship_id, raw_codes, assignments)
    except BlockingIOError:
        raise KizError("Код этого задания уже отправляется. Дождись результата.")
    except KizError as exc:
        # В журнале только причина и ID. Полный КиЗ и криптохвост не записываем.
        reason = str(exc)
        for raw in raw_codes or []:
            for code in (str(raw or ''), clean_code(raw), normalize_wb_code(raw)):
                if code:
                    reason = reason.replace(json.dumps(code, ensure_ascii=False)[1:-1], '[КиЗ]')
                    reason = reason.replace(json.dumps(code)[1:-1], '[КиЗ]')
                    reason = reason.replace(code, '[КиЗ]')
                    reason = reason.replace(code[:20], '[КиЗ]')
        reason = re.sub(r'01\d{14}21[^\s,;]*', '[КиЗ]', reason)
        log.warning('kiz_rejected shipment_id=%s reason=%s', ship_id, reason[:600])
        raise


def _submit(ship_id, raw_codes, assignments=None):
    """Передать коды на площадку и записать их себе — но только после её «да»."""
    codes = clean_codes([normalize_wb_code(code) for code in raw_codes or []])
    if not codes:
        raise KizError("нет кодов для передачи")
    row = _row(ship_id)
    cab = _cab(row)
    if row["kind"] != "fbs":
        raise KizError("коды маркировки вводим только по FBS")
    if row["marketplace"] == "ozon":
        res = _ozon_submit(row, cab, codes, assignments)
    elif row["marketplace"] == "wb":
        codes = clean_codes([normalize_wb_code(code) for code in codes])
        res = _wb_submit(row, cab, codes)
    else:
        raise KizError("неизвестная площадка %s" % row["marketplace"])
    replace_shipment_marks(
        row["id"],
        res.pop('_saved', None) or [{"code": code, "gtin": gtin_of(code), "article": row["article"] or ""} for code in codes],
    )
    return res
