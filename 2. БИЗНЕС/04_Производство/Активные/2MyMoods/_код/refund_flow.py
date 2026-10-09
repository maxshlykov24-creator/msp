#!/usr/bin/env python3
"""Возврат денег 2MY по заказу покупателя.

Без отгрузки: статус только что стал «Отменён» и есть входящие платежи.
Исходящий на сумму каждого входящего, контрагент заказа, без привязки к заказу.
Причина берётся из допполя «Причина отмены». Пустая причина не останавливает платёж.

С отгрузкой: возврат покупателя проведён, а у связанного заказа статус «Возврат».
Исходящий на всю сумму возврата и привязан к этому возврату. Причина из возврата
копируется в заказ, затем статус заказа становится «Отменён».

Если у заказа другой статус, возврат частичный или обмен: ничего не делаем.
Старые отмены исходящими задним числом не закрываем. Иначе повторно уйдут деньги
там, где возврат уже сделали вручную и без нашей пометки в комментарии.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import lib

STATE_CANCELLED = "655b234b-c447-11eb-0a80-08be002efc11"
STATE_RETURN = "806f1421-0a41-11ef-0a80-06ea001dc554"
ATTR_ORDER_REASON = "b9461617-c3cb-11f1-0a80-18f80010a4d1"
ATTR_RETURN_REASON = "2a823092-c3cc-11f1-0a80-1f6400119180"
NO_HOOK = {"X-Lognex-WebHook-Disable": "true"}
MARKER = "Возврат денег по заказу"
CANCEL_WINDOW = timedelta(days=7)
TZ = ZoneInfo("Europe/Moscow")
_WORK = threading.Lock()


def should_drop_cancelled_sale(state_id: str, shipped: bool, is_buy: bool) -> bool:
    """Отменён без проведённой отгрузки не является продажей. С отгрузкой продажа остаётся."""
    return bool(is_buy) and not shipped and state_id == STATE_CANCELLED


def authorized(path: str) -> bool:
    token = os.environ.get("REFUND_HOOK_TOKEN", "").strip()
    if not token:
        return False
    parts = [unquote(part) for part in path.split("?")[0].split("/") if part]
    if len(parts) < 2 or parts[0] != "2my-refund":
        return False
    got = parts[1]
    if len(got) != len(token):
        return False
    return hmac.compare_digest(got, token)


def reason_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return ""


def reason_href(value) -> str:
    if not isinstance(value, dict):
        return ""
    return str((value.get("meta") or {}).get("href") or "")


def comment_for(number: str, reason: str, incoming_id: str = "", return_name: str = "") -> str:
    text = reason.strip() if reason and reason.strip() else "не указана"
    head = f"{MARKER} {number}."
    if return_name:
        head += f" Возврат {return_name}."
    if incoming_id:
        head += f" Входящий {incoming_id}."
    return f"{head} Причина отмены: {text}."


def kopecks(value) -> int:
    return int(round(float(value or 0)))


def portion(payment: dict, target_ids: set[str]) -> int:
    """Сколько этого платежа отнесено на заказ или его счета. Не на другие заказы."""
    ops = payment.get("operations") or []
    matched = [op for op in ops if lib.href_id(op.get("meta")) in target_ids]
    if not matched:
        return 0
    linked = sum(kopecks(op.get("linkedSum")) for op in matched)
    if linked > 0:
        return linked
    if len(ops) == 1:
        return kopecks(payment.get("sum"))
    return 0


def _meta(ref: dict | None) -> dict | None:
    meta = (ref or {}).get("meta") if isinstance(ref, dict) else None
    if isinstance(meta, dict) and meta.get("href"):
        return meta
    return None


def _state_id(row: dict) -> str:
    return lib.href_id((row.get("state") or {}).get("meta"))


def _attr(row: dict, attr_id: str):
    for item in row.get("attributes") or []:
        if lib.href_id(item.get("meta")) == attr_id or item.get("id") == attr_id:
            return item.get("value")
    return None


def _req(ms, path: str, params: dict | None = None):
    status, body = 0, {}
    for attempt in range(6):
        status, body = ms.req(path, params)
        if status != 429:
            return status, body
        time.sleep(min(20, 2 * (attempt + 1)))
    return status, body


def _rows(ms, path: str, params: dict | None = None) -> list[dict]:
    params = dict(params or {})
    params.setdefault("limit", 100)
    out: list[dict] = []
    offset = 0
    while offset < 2000:
        params["offset"] = offset
        status, body = _req(ms, path, params)
        if status != 200:
            raise RuntimeError(f"чтение МойСклад HTTP {status}")
        chunk = body.get("rows") or []
        out.extend(chunk)
        size = (body.get("meta") or {}).get("size") or 0
        offset += len(chunk)
        if not chunk or offset >= size:
            return out
    return out


def _get(ms, path: str) -> dict:
    status, body = _req(ms, path)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"чтение МойСклад HTTP {status}")
    return body


def _send(ms, method: str, path: str, body: dict) -> dict:
    status, saved = ms.send(method, path, body, NO_HOOK)
    if status not in (200, 201):
        raise RuntimeError(f"запись МойСклад HTTP {status}")
    return saved if isinstance(saved, dict) else {}


def _covered(rows: list[dict], marker: str) -> int:
    total = 0
    for row in rows:
        if row.get("applicable") is False:
            continue
        if marker not in str(row.get("description") or ""):
            continue
        total += kopecks(row.get("sum"))
    return total


def _moment(value) -> datetime | None:
    text = str(value or "").strip().replace("T", " ")[:19]
    if len(text) < 19:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _recently_cancelled(ms, order_id: str) -> bool:
    """Создаём исходящий только если статус стал «Отменён» в последнюю неделю.

    Правка старого отменённого заказа платёж не порождает. Иначе одно
    сохранение карточки закрыло бы деньгами все исторические отмены.
    """
    rows = _rows(ms, f"/entity/customerorder/{order_id}/audit", {"limit": 50})
    for event in rows:
        diff = (event.get("diff") or {}).get("state") if isinstance(event.get("diff"), dict) else None
        if not isinstance(diff, dict):
            continue
        new_value = diff.get("newValue") if isinstance(diff.get("newValue"), dict) else {}
        if lib.href_id(new_value.get("meta")) != STATE_CANCELLED:
            return False
        moment = _moment(event.get("moment"))
        return bool(moment and datetime.now(TZ) - moment <= CANCEL_WINDOW)
    return False


def _has_shipment(ms, order_id: str) -> bool:
    href = f"{lib.MS_BASE}/entity/customerorder/{order_id}"
    for row in _rows(ms, "/entity/demand", {"filter": f"customerOrder={href}"}):
        if row.get("applicable") is True and kopecks(row.get("sum")) > 0:
            return True
    return False


def _invoices(ms, order_id: str) -> list[dict]:
    href = f"{lib.MS_BASE}/entity/customerorder/{order_id}"
    rows = _rows(ms, "/entity/invoiceout", {"filter": f"customerOrder={href}"})
    full = []
    for row in rows:
        if isinstance(row.get("payments"), list):
            full.append(row)
        elif row.get("id"):
            full.append(_get(ms, f"/entity/invoiceout/{row['id']}"))
    return full


def _remember(found: dict[str, dict], row: dict, kind: str, linked: int) -> None:
    if not row.get("id") or linked <= 0 or kind not in ("paymentin", "cashin"):
        return
    prev = found.get(row["id"])
    if prev is None:
        row["_kind"] = kind
        row["_linked"] = linked
        found[row["id"]] = row
        return
    prev["_linked"] = max(int(prev.get("_linked") or 0), linked)
    if "organization" not in prev and "organization" in row:
        prev.update(row)
        prev["_kind"] = kind
        prev["_linked"] = max(int(prev.get("_linked") or 0), linked)


def _incomings(ms, order: dict, invoices: list[dict]) -> list[dict]:
    """Входящие этого заказа: платежи счетов и платежи контрагента, привязанные к заказу."""
    found: dict[str, dict] = {}
    targets = {order["id"]} | {row["id"] for row in invoices if row.get("id")}
    for invoice in invoices:
        for pay in invoice.get("payments") or []:
            kind = ((pay.get("meta") or {}).get("type")) or ""
            _remember(found, dict(pay), kind, kopecks(pay.get("linkedSum")))
    agent = _meta(order.get("agent"))
    if agent:
        for entity in ("paymentin", "cashin"):
            for row in _rows(ms, f"/entity/{entity}", {"filter": f"agent={agent['href']}"}):
                if row.get("applicable") is not True:
                    continue
                _remember(found, row, entity, portion(row, targets))
    hydrated = []
    for row in found.values():
        if row.get("applicable") is False:
            continue
        if "organization" not in row and row.get("id"):
            full = _get(ms, f"/entity/{row['_kind']}/{row['id']}")
            if full.get("applicable") is not True:
                continue
            full["_kind"] = row["_kind"]
            full["_linked"] = row.get("_linked") or 0
            row = full
        if row.get("applicable") is False:
            continue
        hydrated.append(row)
    return hydrated


def _outgoing_kind(incoming_kind: str) -> str:
    return "cashout" if incoming_kind == "cashin" else "paymentout"


def _existing_outgoing(ms, marker: str) -> list[dict]:
    rows = []
    for entity in ("paymentout", "cashout"):
        rows.extend(_rows(ms, f"/entity/{entity}", {"filter": f"description~={marker}"}))
    return rows


def _linked_outgoing(ms, agent: dict | None, return_id: str) -> list[dict]:
    if not agent or not return_id:
        return []
    rows = []
    for entity in ("paymentout", "cashout"):
        for row in _rows(ms, f"/entity/{entity}", {"filter": f"agent={agent['href']}"}):
            if row.get("applicable") is False:
                continue
            linked_ids = {lib.href_id((op.get("meta") or {})) for op in row.get("operations") or []}
            if return_id in linked_ids:
                rows.append(row)
    return rows


def _order_agent(order: dict, incoming: dict | None) -> dict | None:
    return _meta(order.get("agent")) or (_meta((incoming or {}).get("agent")))


def _put_order(ms, order_id: str, body: dict) -> None:
    if not body:
        return
    _send(ms, "PUT", f"/entity/customerorder/{order_id}", body)


def _reason_attribute(value) -> dict | None:
    href = reason_href(value)
    if not href:
        return None
    return {
        "meta": {
            "href": f"{lib.MS_BASE}/entity/customerorder/metadata/attributes/{ATTR_ORDER_REASON}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": {"meta": (value.get("meta") if isinstance(value, dict) else {})},
    }


def _status_body(reason_value=None) -> dict:
    body = {
        "state": {
            "meta": {
                "href": f"{lib.MS_BASE}/entity/customerorder/metadata/states/{STATE_CANCELLED}",
                "type": "state",
                "mediaType": "application/json",
            }
        }
    }
    attribute = _reason_attribute(reason_value)
    if attribute:
        body["attributes"] = [attribute]
    return body


def _create_outgoing(ms, kind: str, organization: dict, agent: dict, account: dict | None,
                     rate, amount: int, description: str, operation: dict | None = None) -> None:
    body = {
        "organization": {"meta": organization},
        "agent": {"meta": agent},
        "sum": amount,
        "description": description,
        "applicable": True,
    }
    if account:
        body["organizationAccount"] = {"meta": account}
    if isinstance(rate, dict) and rate:
        body["rate"] = rate
    if operation:
        body["operations"] = [operation]
    _send(ms, "POST", f"/entity/{kind}", body)


def _refresh_comment(ms, rows: list[dict], description: str) -> None:
    for row in rows:
        if row.get("applicable") is False:
            continue
        current = str(row.get("description") or "")
        if not current.startswith(MARKER) or current == description:
            continue
        kind = (row.get("meta") or {}).get("type") or ""
        if kind not in ("paymentout", "cashout") or not row.get("id"):
            continue
        _send(ms, "PUT", f"/entity/{kind}/{row['id']}", {"description": description})


def handle_cancel(ms, order: dict, state_changed: bool) -> str:
    """Исходящие по отмене без отгрузки. На чужом обновлении заказ не трогаем."""
    if _state_id(order) != STATE_CANCELLED:
        return "не отмена"
    if _has_shipment(ms, order["id"]):
        return "есть отгрузка"
    number = str(order.get("name") or order["id"])
    reason = reason_text(_attr(order, ATTR_ORDER_REASON))
    invoices = _invoices(ms, order["id"])
    incomings = _incomings(ms, order, invoices)
    if not incomings:
        return "нет входящих"
    created = 0
    for incoming in incomings:
        needed = int(incoming.get("_linked") or 0)
        if needed <= 0:
            continue
        marker = f"Входящий {incoming['id']}"
        existing = _existing_outgoing(ms, marker)
        already = _covered(existing, marker)
        description = comment_for(number, reason, incoming_id=incoming["id"])
        if already + 1 < needed:
            if not state_changed:
                continue
            agent = _order_agent(order, incoming)
            organization = _meta(incoming.get("organization"))
            if not agent or not organization:
                raise RuntimeError("во входящем нет организации или контрагента")
            _create_outgoing(
                ms,
                _outgoing_kind(incoming.get("_kind") or ""),
                organization,
                agent,
                _meta(incoming.get("organizationAccount")),
                incoming.get("rate"),
                needed - already,
                description,
            )
            created += 1
            print(f"исходящий по отмене {number}: {(needed - already) / 100:.2f}", flush=True)
        elif reason:
            _refresh_comment(ms, existing, description)
    if created:
        return f"создано {created}"
    return "уже закрыто"


def handle_return(ms, ret: dict) -> str:
    """Полный возврат: исходящий к документу возврата, причина в заказ, статус «Отменён»."""
    if ret.get("applicable") is not True or kopecks(ret.get("sum")) <= 0:
        return "возврат не проведён"
    demand_id = lib.href_id((ret.get("demand") or {}).get("meta"))
    if not demand_id:
        return "нет отгрузки у возврата"
    demand = _get(ms, f"/entity/demand/{demand_id}")
    order_id = lib.href_id((demand.get("customerOrder") or {}).get("meta"))
    if not order_id:
        return "нет заказа у отгрузки"
    order = _get(ms, f"/entity/customerorder/{order_id}")
    state = _state_id(order)
    if state not in (STATE_RETURN, STATE_CANCELLED):
        return "не полный возврат"
    number = str(order.get("name") or order_id)
    return_name = str(ret.get("name") or ret["id"])
    reason_value = _attr(ret, ATTR_RETURN_REASON)
    reason = reason_text(reason_value)
    return_href = (ret.get("meta") or {}).get("href") or f"{lib.MS_BASE}/entity/salesreturn/{ret['id']}"
    linked = _linked_outgoing(ms, _meta(order.get("agent")), ret["id"])
    ours = [row for row in linked if str(row.get("description") or "").startswith(MARKER)]
    # Уже «Отменён» без нашего исходящего: это не сигнал полного возврата.
    if state == STATE_CANCELLED and not ours:
        return "не полный возврат"
    if state == STATE_RETURN and not linked:
        invoices = _invoices(ms, order_id)
        incomings = _incomings(ms, order, invoices)
        incoming = max(incomings, key=lambda row: int(row.get("_linked") or 0), default=None)
        organization = _meta((incoming or {}).get("organization")) or _meta(ret.get("organization")) or _meta(demand.get("organization"))
        agent = _order_agent(order, incoming)
        account = _meta((incoming or {}).get("organizationAccount"))
        rate = (incoming or {}).get("rate")
        kind = _outgoing_kind((incoming or {}).get("_kind") or "paymentin")
        if not organization or not agent:
            raise RuntimeError("нет организации или контрагента для исходящего")
        operation = {
            "meta": ret.get("meta") or {
                "href": return_href,
                "type": "salesreturn",
                "mediaType": "application/json",
            },
            "linkedSum": kopecks(ret.get("sum")),
        }
        _create_outgoing(
            ms, kind, organization, agent, account, rate, kopecks(ret.get("sum")),
            comment_for(number, reason, return_name=return_name), operation,
        )
        print(f"исходящий по возврату {number}: {kopecks(ret.get('sum')) / 100:.2f}", flush=True)
    elif reason and ours:
        _refresh_comment(ms, ours, comment_for(number, reason, return_name=return_name))
    same_reason = bool(reason_href(reason_value)) and reason_href(_attr(order, ATTR_ORDER_REASON)) == reason_href(reason_value)
    if state == STATE_RETURN:
        _put_order(ms, order_id, _status_body(None if same_reason else reason_value))
        print(f"заказ {number}: статус Отменён", flush=True)
        return "возврат закрыт"
    if reason and ours and not same_reason:
        attribute = _reason_attribute(reason_value)
        if attribute:
            _put_order(ms, order_id, {"attributes": [attribute]})
            print(f"заказ {number}: причина отмены перенесена", flush=True)
            return "причина перенесена"
    return "уже закрыто"


def handle_event(ms, event: dict) -> str:
    meta = event.get("meta") or {}
    kind = meta.get("type") or ""
    entity_id = lib.href_id(meta)
    action = event.get("action") or ""
    if not entity_id:
        return "пусто"
    if kind == "customerorder" and action == "UPDATE":
        fields = event.get("updatedFields")
        if isinstance(fields, list) and "state" not in fields and "attributes" not in fields:
            return "не статус"
        order = _get(ms, f"/entity/customerorder/{entity_id}")
        if isinstance(fields, list) and "state" not in fields:
            changed = False
        else:
            changed = _recently_cancelled(ms, entity_id)
        return handle_cancel(ms, order, state_changed=changed)
    if kind == "salesreturn" and action in ("CREATE", "UPDATE"):
        ret = _get(ms, f"/entity/salesreturn/{entity_id}")
        return handle_return(ms, ret)
    return "не наше событие"


def handle_payload(payload: dict, ms=None) -> None:
    events = payload.get("events") if isinstance(payload, dict) else None
    if not events:
        return
    ms = ms or lib.MS()
    with _WORK:
        for event in events:
            if not isinstance(event, dict):
                continue
            result = handle_event(ms, event)
            kind = ((event.get("meta") or {}).get("type")) or ""
            print(f"{kind}: {result}", flush=True)


def _selftest() -> None:
    assert should_drop_cancelled_sale(STATE_CANCELLED, False, True)
    assert not should_drop_cancelled_sale(STATE_CANCELLED, True, True)
    assert not should_drop_cancelled_sale(STATE_RETURN, False, True)
    assert not should_drop_cancelled_sale(STATE_CANCELLED, False, False)
    assert comment_for("02014", "").endswith("Причина отмены: не указана.")
    assert "02014" in comment_for("02014", "Брак")
    assert portion({"sum": 5000, "operations": [{"meta": {"href": "https://x/customerorder/o1"}, "linkedSum": 1500},
                                                {"meta": {"href": "https://x/customerorder/o2"}, "linkedSum": 3500}]},
                   {"o1"}) == 1500

    world = {
        "order": {
            "id": "o1", "name": "02014",
            "state": {"meta": {"href": f"https://x/states/{STATE_CANCELLED}"}},
            "agent": {"meta": {"href": "https://x/counterparty/c1", "type": "counterparty"}},
            "attributes": [],
        },
        "demands": [],
        "invoices": [{
            "id": "inv1",
            "payments": [{
                "id": "pin1",
                "linkedSum": 21000,
                "meta": {"href": "https://x/paymentin/pin1", "type": "paymentin"},
            }],
        }],
        "paymentin": [{
            "id": "pin1", "applicable": True, "sum": 21000,
            "meta": {"type": "paymentin"},
            "organization": {"meta": {"href": "https://x/organization/org", "type": "organization"}},
            "organizationAccount": {"meta": {"href": "https://x/account/acc", "type": "account"}},
            "agent": {"meta": {"href": "https://x/counterparty/c1", "type": "counterparty"}},
            "operations": [{"meta": {"href": "https://x/invoiceout/inv1", "type": "invoiceout"}, "linkedSum": 21000}],
        }],
        "cashin": [],
        "paymentout": [],
        "cashout": [],
    }
    posts = []

    class Fake:
        def req(self, path, params=None):
            params = params or {}
            filt = params.get("filter") or ""
            if path == "/entity/customerorder/o1":
                return 200, world["order"]
            if path == "/entity/paymentin/pin1":
                return 200, world["paymentin"][0]
            if path == "/entity/customerorder/o1/audit":
                from datetime import datetime as _dt
                return 200, {"rows": [{
                    "moment": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "diff": {"state": {"newValue": {"meta": {"href": f"https://x/states/{STATE_CANCELLED}"}}}},
                }], "meta": {"size": 1}}
            if path == "/entity/demand":
                return 200, {"rows": world["demands"], "meta": {"size": len(world["demands"])}}
            if path == "/entity/invoiceout":
                return 200, {"rows": world["invoices"], "meta": {"size": len(world["invoices"])}}
            if path == "/entity/paymentin" and "invoiceout/inv1" in filt:
                return 200, {"rows": world["paymentin"], "meta": {"size": 1}}
            if path in ("/entity/paymentin", "/entity/cashin"):
                return 200, {"rows": [], "meta": {"size": 0}}
            if path == "/entity/paymentout":
                return 200, {"rows": world["paymentout"], "meta": {"size": len(world["paymentout"])}}
            if path == "/entity/cashout":
                return 200, {"rows": [], "meta": {"size": 0}}
            return 200, {"rows": [], "meta": {"size": 0}}

        def send(self, method, path, body, headers=None):
            assert headers.get("X-Lognex-WebHook-Disable") == "true"
            posts.append((method, path, body))
            if method == "POST":
                world["paymentout"].append({
                    "id": "pout1", "applicable": True, "sum": body["sum"],
                    "description": body["description"], "meta": {"type": "paymentout"},
                    "operations": body.get("operations") or [], "agent": body.get("agent"),
                })
            return 200, {"id": "pout1"}

    fake = Fake()
    handle_payload({"events": [{"meta": {"type": "customerorder", "href": "https://x/customerorder/o1"},
                                "action": "UPDATE", "updatedFields": ["name"]}]}, fake)
    assert posts == []
    handle_payload({"events": [{"meta": {"type": "customerorder", "href": "https://x/customerorder/o1"},
                                "action": "UPDATE", "updatedFields": ["state"]}]}, fake)
    assert len(posts) == 1
    assert posts[0][1] == "/entity/paymentout"
    assert posts[0][2]["sum"] == 21000
    assert "operations" not in posts[0][2]
    assert posts[0][2]["agent"]["meta"]["href"].endswith("/c1")
    assert "02014" in posts[0][2]["description"]
    assert "не указана" in posts[0][2]["description"]
    handle_payload({"events": [{"meta": {"type": "customerorder", "href": "https://x/customerorder/o1"},
                                "action": "UPDATE", "updatedFields": ["state"]}]}, fake)
    assert len(posts) == 1

    world["demands"] = [{"id": "d1", "applicable": True, "sum": 21000}]
    posts.clear()
    world["paymentout"].clear()
    handle_payload({"events": [{"meta": {"type": "customerorder", "href": "https://x/customerorder/o1"},
                                "action": "UPDATE", "updatedFields": ["state"]}]}, fake)
    assert posts == []

    world["order"]["state"]["meta"]["href"] = f"https://x/states/{STATE_RETURN}"
    world["demands"] = [{"id": "d1", "applicable": True, "sum": 21000,
                         "customerOrder": {"meta": {"href": "https://x/customerorder/o1"}},
                         "organization": {"meta": {"href": "https://x/organization/org", "type": "organization"}}}]
    ret = {
        "id": "r1", "name": "00012", "applicable": True, "sum": 21000,
        "meta": {"href": "https://x/salesreturn/r1", "type": "salesreturn", "mediaType": "application/json"},
        "demand": {"meta": {"href": "https://x/demand/d1", "type": "demand"}},
        "attributes": [{"meta": {"href": f"https://x/attributes/{ATTR_RETURN_REASON}"},
                        "value": {"name": "Брак", "meta": {"href": "https://x/customentity/el1", "type": "customentity"}}}],
    }

    class FakeReturn(Fake):
        def req(self, path, params=None):
            if path == "/entity/salesreturn/r1":
                return 200, ret
            if path == "/entity/demand/d1":
                return 200, world["demands"][0]
            if path == "/entity/paymentout" and params and "salesreturn/r1" in (params.get("filter") or ""):
                return 200, {"rows": [row for row in world["paymentout"] if row.get("_linked")], "meta": {"size": 1}}
            return super().req(path, params)

        def send(self, method, path, body, headers=None):
            saved = super().send(method, path, body, headers)
            if method == "POST":
                world["paymentout"][-1]["_linked"] = True
                assert body["operations"][0]["linkedSum"] == 21000
                assert body["operations"][0]["meta"]["type"] == "salesreturn"
                assert "Брак" in body["description"]
            if method == "PUT" and path.startswith("/entity/customerorder/"):
                if "state" in body:
                    assert body["state"]["meta"]["href"].endswith(STATE_CANCELLED)
                    world["order"]["state"]["meta"]["href"] = body["state"]["meta"]["href"]
                if body.get("attributes"):
                    assert body["attributes"][0]["value"]["meta"]["href"].endswith("el1")
                    world["order"]["attributes"] = body["attributes"]
            return saved

    posts.clear()
    world["paymentout"].clear()
    handle_payload({"events": [{"meta": {"type": "salesreturn", "href": "https://x/salesreturn/r1"},
                                "action": "CREATE"}]}, FakeReturn())
    assert any(item[1] == "/entity/paymentout" for item in posts)
    assert any(item[1] == "/entity/customerorder/o1" for item in posts)

    world["order"]["state"]["meta"]["href"] = f"https://x/states/{STATE_CANCELLED}"
    posts.clear()
    handle_payload({"events": [{"meta": {"type": "salesreturn", "href": "https://x/salesreturn/r1"},
                                "action": "UPDATE"}]}, FakeReturn())
    assert posts == []

    ret["applicable"] = False
    world["order"]["state"]["meta"]["href"] = f"https://x/states/{STATE_RETURN}"
    posts.clear()
    world["paymentout"].clear()
    handle_payload({"events": [{"meta": {"type": "salesreturn", "href": "https://x/salesreturn/r1"},
                                "action": "CREATE"}]}, FakeReturn())
    assert posts == []
    class OldAudit:
        def req(self, path, params=None):
            return 200, {"rows": [{"moment": "2020-01-01 00:00:00", "diff": {
                "state": {"newValue": {"meta": {"href": f"https://x/states/{STATE_CANCELLED}"}}}}}], "meta": {"size": 1}}

        def send(self, *args, **kwargs):
            raise AssertionError("старая отмена не должна писать платёж")

    assert _recently_cancelled(OldAudit(), "o1") is False
    print("selftest ok")


if __name__ == "__main__":
    _selftest()
