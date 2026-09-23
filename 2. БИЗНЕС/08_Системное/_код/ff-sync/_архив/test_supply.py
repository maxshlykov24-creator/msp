"""Сквозной прогон поставок WB и сборки Ozon на моках площадок.

Живые кабинеты не трогаем: подменяем net.req и смотрим, что уходит в API и что
остаётся в базе. Запуск: PYTHONPATH=.. python3 _архив/test_supply.py
"""

import base64
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FF_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import db
import net

CALLS = []
WB_SENT = {}
SUPPLY_SEQ = []
# точки сдачи, которые «приняла» площадка: {supplyId: тело запроса}
POINT_SET = {}
# поставки, отсканированные в пункте отгрузки: точку у них менять поздно
SCANNED = set()
PVZ_POINT = {
    "id": 50095011, "name": "Москва", "address": "Москва, Домодедовская Улица 28",
    "city": "Москва", "officeType": "pp", "cargoTypes": [1],
    "latitude": 55.60476, "longitude": 37.712345, "fulfillment": False,
}
SC_POINT = {
    "id": 87609, "name": "Москва (Кавказский)", "address": "Москва, Кавказский бульвар, 57 стр. 1",
    "city": "Москва", "officeType": "sc", "cargoTypes": [1, 2, 3],
    "latitude": 55.63, "longitude": 37.72, "fulfillment": True,
}


class Fake:
    def __init__(self, status=200, data=None, raw=b""):
        self.status_code = status
        self._data = data
        self.content = raw or (json.dumps(data or {}).encode() if data is not None else b"")

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        if self._data is None:
            raise ValueError("нет json")
        return self._data


PNG = base64.b64decode(
    # 1x1 прозрачный png: важен сам факт картинки, а не её содержимое
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def fake_req(method, url, headers=None, **kw):
    CALLS.append((method, url, kw.get("json"), kw.get("params")))
    if url.endswith("/api/v3/supplies") and method == "POST":
        # у площадки номер каждой поставки свой: первая WB-GI-777, дальше по счёту
        SUPPLY_SEQ.append(1)
        n = len(SUPPLY_SEQ)
        return Fake(201, {"id": "WB-GI-777" if n == 1 else "WB-GI-77%s" % (7 + n)})
    if "/trbx/stickers" in url:
        ids = (kw.get("json") or {}).get("trbxIds") or []
        return Fake(200, {"stickers": [{"trbxId": t, "barcode": "$WBMP:1:1:%s" % t, "file": base64.b64encode(PNG).decode()} for t in ids]})
    if url.endswith("/trbx") and method == "POST":
        amount = int((kw.get("json") or {}).get("amount") or 1)
        return Fake(201, {"trbxIds": ["WB-TRBX-%s" % (i + 1) for i in range(amount)]})
    if url.endswith("/trbx") and method in ("PATCH", "DELETE"):
        return Fake(204)
    if "/supplies/" in url and url.endswith("/orders") and method == "PATCH":
        return Fake(204)
    if url.endswith("/deliver"):
        return Fake(204)
    if url.endswith("/barcode"):
        return Fake(200, {"barcode": "WB-GI-777", "file": base64.b64encode(PNG).decode()})
    if url.endswith("/v3/posting/fbs/get"):
        return Fake(200, {"result": {"posting_number": "0001-1", "products": [{"product_id": 55, "quantity": 2, "offer_id": "ART-1", "name": "Ремень"}], "status": "awaiting_deliver", "in_process_at": "2026-09-04T10:00:00Z"}})
    if url.endswith("/v4/posting/fbs/ship"):
        body = kw.get("json") or {}
        packs = body.get("packages") or []
        # одно грузовое место — номер у Ozon остаётся прежним, дробления нет
        if len(packs) < 2:
            return Fake(200, {"result": [body.get("posting_number")]})
        return Fake(200, {"result": ["0001-1-%s" % (i + 1) for i in range(len(packs))]})
    if url.endswith("/v6/fbs/posting/product/exemplar/create-or-get"):
        return Fake(200, {"posting_number": (kw.get("json") or {}).get("posting_number"), "products": [{
            "product_id": 55, "quantity": 2,
            "is_mandatory_mark_needed": True, "is_mandatory_mark_possible": True,
            "is_gtd_needed": False, "is_rnpt_needed": False, "is_jw_uin_needed": False, "has_imei": False,
            "exemplars": [{"exemplar_id": 91, "marks": []}, {"exemplar_id": 92, "marks": []}],
        }]})
    if url.endswith("/v5/fbs/posting/product/exemplar/validate"):
        prods = (kw.get("json") or {}).get("products") or []
        return Fake(200, {"products": [{"exemplars": [{"marks": [
            {"mark": (m or {}).get("mark"), "mark_type": "mandatory_mark",
             # мок Честного знака: негодным считаем код, оканчивающийся на XX
             "valid": not ((m or {}).get("mark") or "").endswith("XX"), "errors": []}
            for m in (ex.get("marks") or [])]} for ex in (p.get("exemplars") or [])]} for p in prods]})
    if url.endswith("/v6/fbs/posting/product/exemplar/set"):
        return Fake(200, {})
    if url.endswith("/v5/fbs/posting/product/exemplar/status"):
        return Fake(200, {"posting_number": (kw.get("json") or {}).get("posting_number"), "status": "ship_available"})
    if url.endswith("/api/marketplace/v3/orders/meta"):
        ids = (kw.get("json") or {}).get("orders") or []
        return Fake(200, {"orders": [{"id": i, "metaDetails": [
            {"key": "sgtin", "value": WB_SENT.get(str(i)), "decision": "filled" if WB_SENT.get(str(i)) else "required"}
        ]} for i in ids]})
    if "/orders/stickers" in url:
        ids = (kw.get("json") or {}).get("orders") or []
        return Fake(200, {"stickers": [{"orderId": i, "file": base64.b64encode(PNG).decode(), "partA": "A", "partB": "B"} for i in ids]})
    if url.endswith("/meta/sgtin") and method == "PUT":
        WB_SENT[url.rsplit("/orders/", 1)[1].split("/")[0]] = (kw.get("json") or {}).get("sgtins") or []
        return Fake(204)
    path = url.split("?")[0]
    if path.endswith("/api/marketplace/v3/fbs/shipping-points") and method == "GET":
        # город и габарит у метода обязательные: без них живой WB отвечает 400
        params = kw.get("params") or {}
        if not params.get("city") or not params.get("cargoType"):
            return Fake(400, {"code": "IncorrectParameter", "message": "Incorrect parameter"})
        if str(params.get("cargoType")) != "1":
            # ПВЗ берут только малогабарит, на остальные габариты приходят СЦ
            return Fake(200, {"shippingPoints": [SC_POINT]})
        return Fake(200, {"shippingPoints": [PVZ_POINT, SC_POINT]})
    if path.endswith("/api/marketplace/v3/fbs/supplies/shipping-method") and method == "PATCH":
        rows = (kw.get("json") or {}).get("data") or []
        out = []
        for row in rows:
            if str(row.get("supplyId")) in SCANNED:
                # поставку отсканировали в пункте: точку уже не поменять
                out.append({"supplyId": row.get("supplyId"), "error": {"detail": "supply already scanned"}})
                continue
            POINT_SET[str(row.get("supplyId"))] = row
            out.append({"supplyId": row.get("supplyId"), "success": True})
        return Fake(200, {"results": out})
    if url.endswith("/api/v3/orders/status") and method == "POST":
        ids = (kw.get("json") or {}).get("orders") or []
        return Fake(200, {"orders": [{"id": i, "supplierStatus": "confirm", "wbStatus": "waiting"} for i in ids]})
    if method == "GET" and path.endswith("/order-ids"):
        return Fake(200, {"orderIds": []})
    if method == "GET" and path.endswith("/trbx"):
        return Fake(200, {"trbxes": []})
    if method == "GET" and path.endswith("/api/v3/supplies"):
        # список поставок кабинета: пустой, конкретные карточки отдаём ниже.
        # Пустой список сам по себе поставку не закрывает — sync_open переспросит
        return Fake(200, {"supplies": [], "next": 0})
    if method == "GET" and "/api/v3/supplies/" in path:
        tail = path.split("/api/v3/supplies/", 1)[1].rstrip("/")
        if "/" not in tail:
            # в ЛК точку выбрали: так выглядит поставка, которую примет ПВЗ.
            # cargoType не подставляем — габарит в моке ведёт сама база
            return Fake(200, {
                "id": tail,
                "isPickupPointShipmentAllowed": True, "shippingPointId": 50095011,
            })
    return Fake(404, {"code": 404, "message": "мок не знает %s" % url})


net.req = fake_req
db.init_db()

client_id = db.insert_client("test", "Тест ООО", "", "", "")
wb_cab = db.insert_cabinet(client_id, "wb", "wb", "token-wb", "", 1, "", "")
oz_cab = db.insert_cabinet(client_id, "ozon", "ozon", "token-oz", "9999", 1, "", "")

ships = []
for i in range(4):  # четыре задания: WB даёт максимум половину числа заданий грузомест
    ships.append(
        db.upsert_shipment(
            client_id, wb_cab, "wb", "fbs", "10%s" % i, "Новый", "2026-09-04", "ART-1", "2000000000019",
            "Ремень кожаный", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:0%s" % i},
        )
    )
oz_ship = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-1", "Ожидает упаковки", "2026-09-04", "ART-1", "",
    "Ремень кожаный", 2, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:05"},
)

import statuses
import supply_flow

# 1. создание поставки
sup = supply_flow.create_supply(client_id, "Утро", "тест")
assert sup["ext_id"] == "WB-GI-777", sup
assert db.get_wb_supply(sup["id"])["state"] == "open"

# 2. задания в поставку: уходят батчем и получают отметку «на сборке»
res = supply_flow.add_orders(sup["id"], ships)
assert res["added"] == 4, res
rows = db.list_supply_shipments(wb_cab, "WB-GI-777")
assert len(rows) == 4 and all(r["work_state"] == "assembling" for r in rows), [dict(r) for r in rows]
batch = [c for c in CALLS if c[1].endswith("/supplies/WB-GI-777/orders")]
assert batch and sorted(batch[0][2]["orders"]) == [100, 101, 102, 103], batch

# отправление Ozon в поставку WB не пускаем
try:
    supply_flow.add_orders(sup["id"], [oz_ship])
    raise AssertionError("Ozon просочился в поставку WB")
except ValueError as exc:
    assert "только задания WB" in str(exc), exc

# 3. грузоместа
boxes = supply_flow.make_boxes(sup["id"], 2)["boxes"]
assert boxes == ["WB-TRBX-1", "WB-TRBX-2"], boxes
box_rows = db.list_wb_boxes(sup["id"])
assert [b["ext_id"] for b in box_rows] == boxes

# лимит грузомест: половина заданий, округление вниз (русская спека WB от
# 04.09.2026). Четыре задания — два короба, они уже есть: третий просить нельзя
try:
    supply_flow.make_boxes(sup["id"], 1)
    raise AssertionError("создали грузоместо сверх лимита WB")
except ValueError as exc:
    assert "не больше половины заданий" in str(exc), exc
import wb_supply
assert wb_supply.box_limit(2) == 1 and wb_supply.box_limit(3) == 1 and wb_supply.box_limit(10) == 5
assert wb_supply.box_limit(1) == 0 and wb_supply.box_limit(0) == 0
# отказ площадки 409 не роняет «Собрано», а уходит заметкой
db.delete_wb_boxes(sup["id"], ["WB-TRBX-2"])
_real_req = wb_supply.req
def _deny(method, url, **kw):
    if url.endswith("/trbx") and method == "POST":
        return Fake(409, {"code": "FailedToAddSupplyTrbx", "message": ""})
    return _real_req(method, url, **kw)
wb_supply.req = _deny
res = supply_flow.assemble(ships, boxes=1)
assert res["boxes"] == [] and any("WB отказал в коробе" in n for n in res["notes"]), res
wb_supply.req = _real_req
db.delete_wb_boxes(sup["id"], ["WB-TRBX-1"])
supply_flow.make_boxes(sup["id"], 2)
box_rows = db.list_wb_boxes(sup["id"])
assert [b["ext_id"] for b in box_rows] == ["WB-TRBX-1", "WB-TRBX-2"], [dict(b) for b in box_rows]

# 4. укладка: два задания в первое место. Площадку не трогаем — метода нет в API
packed = supply_flow.fill_box(box_rows[0]["id"], ships[:2])
assert packed["packed"] == 2 and packed["box"] == "WB-TRBX-1", packed
assert not [c for c in CALLS if c[0] == "PATCH" and c[1].endswith("/trbx")], "лезем в несуществующий метод WB"
counts = {b["ext_id"]: b["orders"] for b in db.list_wb_boxes(sup["id"])}
assert counts == {"WB-TRBX-1": 2, "WB-TRBX-2": 0}, counts

# вынули обратно — задание остаётся в поставке, но без коробки
assert supply_flow.empty_box(box_rows[0]["id"], ships[:1]) == {"taken": 1}
assert {b["ext_id"]: b["orders"] for b in db.list_wb_boxes(sup["id"])} == {"WB-TRBX-1": 1, "WB-TRBX-2": 0}
supply_flow.fill_box(box_rows[0]["id"], ships[:1])

# укладка задания, которого нет в поставке
try:
    supply_flow.fill_box(box_rows[1]["id"], [oz_ship])
    raise AssertionError("уложили чужое задание")
except ValueError as exc:
    assert "сначала добавь" in str(exc), exc

# 5. предупреждение перед доставкой
pre = supply_flow.preflight(sup["id"])
assert pre == {
    "ext_id": "WB-GI-777", "client": "Тест ООО", "orders": 4, "boxes": 2,
    "loose": 2, "empty_boxes": ["WB-TRBX-2"], "state": "open",
    "office": statuses.PVZ, "pickup": True, "warn": "",
}, pre

# без confirm не передаём
try:
    supply_flow.deliver(sup["id"])
    raise AssertionError("передали без подтверждения")
except ValueError as exc:
    assert "подтверждение" in str(exc), exc

# состав короба площадке не передаём: confirm достаточно, force не нужен
# 6. QR грузомест до передачи
pdf, notes, pages = supply_flow.boxes_pdf(sup["id"])
assert pdf[:4] == b"%PDF" and pages == 2, (pages, notes)

# 7. передача в доставку: WB закрывает, у нас поставка и задания — «ожидают отгрузки»
out = supply_flow.deliver(sup["id"], confirm=True)
assert out == {"ok": True, "orders": 4, "loose": 2, "office": statuses.PVZ, "warn": ""}, out
assert db.get_wb_supply(sup["id"])["state"] == "ready"
assert all(r["work_state"] == "ready" for r in db.list_supply_shipments(wb_cab, "WB-GI-777"))
# выгрузка после deliver ставит status_group=shipped — вкладка всё равно «ожидают отгрузки»
for r in db.list_supply_shipments(wb_cab, "WB-GI-777"):
    db.set_shipment_platform(r["id"], r["status"], "shipped")
eff = {r["ext_id"]: r["eff_group"] for r in db.list_assembly(client_id=client_id, marketplace="wb")}
assert all(eff.get(str(r["ext_id"])) == "ready" for r in db.list_supply_shipments(wb_cab, "WB-GI-777")), eff
try:
    supply_flow.make_boxes(sup["id"], 1)
    raise AssertionError("завели грузоместо в закрытой поставке")
except ValueError as exc:
    assert "передана в доставку" in str(exc), exc

# 8. QR поставки
pdf2, notes2, pages2 = supply_flow.supply_pdf(sup["id"])
assert pdf2[:4] == b"%PDF" and pages2 == 1, (pages2, notes2)

# 9. сборка Ozon с дроблением: 2 штуки → 2 отправления, старый номер уходит
ship = supply_flow.ship_ozon([oz_ship], split=True)
assert ship["shipped"] == 1, ship
assert any("разделено на 2" in n for n in ship["notes"]), ship["notes"]
body = [c for c in CALLS if c[1].endswith("/v4/posting/fbs/ship")][0][2]
assert body["packages"] == [
    {"products": [{"product_id": 55, "quantity": 1}]},
    {"products": [{"product_id": 55, "quantity": 1}]},
], body
left = {r["ext_id"] for r in db.list_assembly(client_id=client_id, marketplace="ozon")}
assert left == {"0001-1-1", "0001-1-2"}, left

# 10. один пакет без дробления
oz2 = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-1", "Ожидает упаковки", "2026-09-04", "ART-1", "",
    "Ремень", 2, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new"},
)
CALLS.clear()
supply_flow.ship_ozon([oz2], split=False)
body = [c for c in CALLS if c[1].endswith("/v4/posting/fbs/ship")][0][2]
assert body["packages"] == [{"products": [{"product_id": 55, "quantity": 2}]}], body

# 11. имя WB из каталога, не повтор артикула
import shipments_pull

db.replace_cache(client_id, wb_cab, [{
    "marketplace": "wb", "ext_key": "1", "ext_article": "PU-1",
    "ext_barcode": "2000000000019", "name": "Ремень кожаный PU",
    "size": "", "gtin": "", "tracking_type": "", "subject": "", "need_kiz": 0, "image": "http://img/1",
}])
cat = shipments_pull.catalog_of(client_id, "2000000000019", "PU-1")
assert cat["name"] == "Ремень кожаный PU", cat
assert cat["image"] == "http://img/1", cat
nameless = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "999", "Новый", "2026-09-04", "PU-1", "2000000000019",
    "PU-1", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new"},
)
assert shipments_pull.fill_wb_names() == 1
row = db.get_shipments_by_ids([nameless])[0]
assert row["name"] == "Ремень кожаный PU" and row["article"] == "PU-1", dict(row)

# 12. «Собрано» у Ozon: собранное на площадке не залипает на вкладке «На сборке».
# Номер после сборки прежний, поэтому складская отметка снимается — иначе она
# перебила бы статус площадки «ожидают отгрузки».
oz3 = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-9", "Ожидает упаковки", "2026-09-04", "ART-1", "",
    "Ремень", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:07"},
)
db.set_work_state([oz3], "assembling")
out = supply_flow.assemble([oz3], split=False)
assert out["shipped"] == 1 and out["marked"] == 0, out
row = db.get_shipments_by_ids([oz3])[0]
assert (row["work_state"] or "") == "", dict(row)
eff = {r["ext_id"]: r["eff_group"] for r in db.list_assembly(client_id=client_id, marketplace="ozon")}
assert eff.get("0001-9") == "ready", eff

# 13. смешанный выбор: Ozon уходит на площадку, WB получает складскую отметку
wb_new = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "555", "Новый", "2026-09-04", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:08"},
)
oz4 = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-8", "Ожидает упаковки", "2026-09-04", "ART-1", "",
    "Ремень", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:09"},
)
CALLS.clear()
out = supply_flow.assemble([wb_new, oz4], split=False)
assert out["shipped"] == 1 and out["marked"] == 1, out
assert db.get_shipments_by_ids([wb_new])[0]["work_state"] == "ready", dict(db.get_shipments_by_ids([wb_new])[0])
# на WB наружу не звоним: своего «собрать» у него нет
assert not [c for c in CALLS if "wildberries" in c[1]], [c[1] for c in CALLS]


# 14. коды маркировки: сколько ждёт площадка
import kiz

oz_kiz = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-7", "Ожидает упаковки", "2026-09-04", "ART-1", "",
    "Ремень", 2, None, 0, "2026-09-04T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-04 10:10"},
)
plan = kiz.plan(oz_kiz)
assert plan["need"] == 2 and plan["have"] == 0, plan
assert plan["marketplace"] == "ozon" and plan["ext_id"] == "0001-7", plan

wb_kiz = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "777", "На сборке", "2026-09-04", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-04T10:00:00", extra={"status_group": "assembling", "accepted_at": "2026-09-04 10:11"},
)
plan = kiz.plan(wb_kiz)
assert plan["need"] == 1 and plan["decision"] == "required", plan

# 15. передача кодов Ozon: экземпляры площадки, тип марки и запись у себя
CALLS.clear()
codes = ["0104630568317423215EirD_orEif7X\x1d91EE12", "0104630568317423215QwErTyUiOp12\x1d91EE13"]
out = kiz.submit(oz_kiz, codes)
assert out["sent"] == 2 and out["state"] == "ship_available", out
body = [c for c in CALLS if c[1].endswith("/v6/fbs/posting/product/exemplar/set")][0][2]
assert body["posting_number"] == "0001-7", body
assert [e["exemplar_id"] for e in body["products"][0]["exemplars"]] == [91, 92], body
assert body["products"][0]["exemplars"][0]["marks"] == [
    {"mark": codes[0], "mark_type": "mandatory_mark"}
], body
saved = [r["code"] for r in db.list_shipment_marks([oz_kiz])]
assert saved == codes, saved
assert db.get_shipments_by_ids([oz_kiz])[0]["marks_count"] == 2, dict(db.get_shipments_by_ids([oz_kiz])[0])
assert kiz.plan(oz_kiz)["codes"] == codes, "окно КиЗ открылось пустым и стёрло бы принятые коды"

# 16. Честный знак забраковал код: на площадку не отправляем и своё не затираем
CALLS.clear()
try:
    kiz.submit(oz_kiz, ["0104630568317423215EirD_orEifXX"])
    raise AssertionError("отправили код, который не прошёл проверку")
except kiz.KizError as exc:
    assert "не принял коды" in str(exc), exc
assert not [c for c in CALLS if c[1].endswith("/exemplar/set")], "полезли в set с негодным кодом"
assert [r["code"] for r in db.list_shipment_marks([oz_kiz])] == codes, "затёрли принятые коды"

# 17. WB: код уходит в sgtins, экранированный разделитель GS становится символом
CALLS.clear()
out = kiz.submit(wb_kiz, ["0104630568317423215EirD_orEif7X\\u001d91EE12"])
assert out["sent"] == 1, out
put = [c for c in CALLS if c[1].endswith("/meta/sgtin")][0]
assert put[0] == "PUT" and put[1].endswith("/api/v3/orders/777/meta/sgtin"), put
assert put[2]["sgtins"] == ["0104630568317423215EirD_orEif7X\x1d91EE12"], put[2]
assert out["state"] == "filled", out
# площадка приняла — теперь код больше не запрашивается
assert kiz.plan(wb_kiz)["need"] == 0, kiz.plan(wb_kiz)

# 17б. живые статусы WB: «введён в оборот» — принято, «не обязательно» — не долг
WB_SENT.clear()
for decision, need in (("sgtinIntroduced", 0), ("optional", 0), ("sgtinInvalidFormat", 1)):
    real = kiz._wb_meta
    kiz._wb_meta = lambda cab, ext, d=decision: {"id": ext, "metaDetails": [{"key": "sgtin", "value": None, "decision": d}]}
    try:
        got = kiz.plan(wb_kiz)
        assert got["need"] == need, (decision, got)
        assert "None" not in got["note"] and decision not in got["note"], (decision, got["note"])
    finally:
        kiz._wb_meta = real

# 18. кодов больше, чем экземпляров у площадки
try:
    kiz.submit(oz_kiz, codes + ["0104630568317423215ZzZzZzZzZz99"])
    raise AssertionError("передали лишний код")
except kiz.KizError as exc:
    assert "кодов больше" in str(exc), exc


# 19. «Взять в сборку»: поставка заводится сама, габаритные типы не смешиваются.
# WB держит в одной поставке только один cargoType, поэтому на каждый тип своя.
CALLS.clear()
mgt = []
kgt = []
for i, (num, cargo) in enumerate((("201", "1"), ("202", "1"), ("203", "3"))):
    sid = db.upsert_shipment(
        client_id, wb_cab, "wb", "fbs", num, "Новый", "2026-09-05", "ART-1", "2000000000019",
        "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
        extra={"status_group": "new", "accepted_at": "2026-09-05 10:0%s" % i, "cargo_type": cargo,
               "office": "ПВЗ Ленина 1"},
    )
    (mgt if cargo == "1" else kgt).append(sid)
oz_take = db.upsert_shipment(
    client_id, oz_cab, "ozon", "fbs", "0001-6", "Ожидает упаковки", "2026-09-05", "ART-1", "",
    "Ремень", 1, None, 0, "2026-09-05T10:00:00", extra={"status_group": "new", "accepted_at": "2026-09-05 10:05"},
)
out = supply_flow.take(mgt + kgt + [oz_take], author="тест")
assert len(out["supplies"]) == 2, out
assert out["marked"] == 1, out  # Ozon поставки не получает, только отметку
assert {s["orders"] for s in out["supplies"]} == {1, 2}, out["supplies"]
made = db.find_wb_supplies([s["ext_id"] for s in out["supplies"]])
assert {str(s["cargo_type"]) for s in made} == {"1", "3"}, [dict(s) for s in made]
assert all(db.get_shipments_by_ids([x])[0]["work_state"] == "assembling" for x in mgt + kgt)

# повторное нажатие в ту же поставку не льёт: задание уже в ней
again = supply_flow.take(mgt, author="тест")
assert not again["supplies"] and any("Уже в поставке" in n for n in again["notes"]), again

# 19б. открытая поставка есть — молча в неё не кладём. 22.09 так новые заказы
# уехали в поставку с двумя проблемными товарами, а вынуть их WB не даёт.
pick = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "205", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:20", "cargo_type": "1"},
)
held = supply_flow.take([pick], author="тест")
assert held["need_choice"] and not held["supplies"], held
assert not (db.get_shipments_by_ids([pick])[0]["supply_ext"] or ""), "положили без выбора"
key = held["groups"][0]["key"]
fresh_sup = supply_flow.take([pick], author="тест", choices={key: "new"})
assert len(fresh_sup["supplies"]) == 1, fresh_sup
assert fresh_sup["supplies"][0]["ext_id"] not in {s["ext_id"] for s in made}, fresh_sup
pick2 = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "206", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:21", "cargo_type": "1"},
)
mgt_sup = [s for s in made if str(s["cargo_type"]) == "1"][0]
joined = supply_flow.take([pick2], author="тест", choices={key: mgt_sup["id"]})
assert joined["supplies"][0]["ext_id"] == mgt_sup["ext_id"], joined
assert db.get_shipments_by_ids([pick2])[0]["supply_ext"] == mgt_sup["ext_id"]
pick3 = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "207", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:22", "cargo_type": "1"},
)
try:
    supply_flow.take([pick3], author="тест", choices={key: 99999})
    raise AssertionError("приняли чужую поставку")
except ValueError as exc:
    assert "закрыта" in str(exc) or "габарита" in str(exc), exc
assert not (db.get_shipments_by_ids([pick3])[0]["supply_ext"] or "")

# 20. габарит короба больше не запрещает: в ЛК выбрали ПВЗ, значит крупный
# товар тоже едет туда и получает грузоместо. Раньше мы отказывали сами и
# уводили поставку в СЦ, хотя площадка её на ПВЗ принимала
sc = [s for s in made if str(s["cargo_type"]) == "3"][0]
assert supply_flow.make_boxes(sc["id"], 1)["boxes"] == ["WB-TRBX-1"], "крупногабарит не получил короб"
assert db.get_shipments_by_ids(kgt)[0]["office"] == statuses.PVZ, dict(db.get_shipments_by_ids(kgt)[0])

# 21. «Собрано» с числом коробов: заводит грузоместа в поставке выборки.
# Два задания — один короб (половина), два короба уходят заметкой, отметка стоит
out = supply_flow.assemble(mgt, split=False, boxes=2)
assert not out["boxes"] and out["marked"] == 2 and any("половины" in n for n in out["notes"]), out
pvz = [s for s in made if str(s["cargo_type"]) == "1"][0]
out = supply_flow.assemble(mgt, split=False, boxes=1)
assert len(out["boxes"]) == 1 and out["marked"] == 2, out
assert len(db.list_wb_boxes(pvz["id"])) == 1, [dict(b) for b in db.list_wb_boxes(pvz["id"])]

# две поставки в выборке — короба не заводим, число у них своё
out = supply_flow.assemble(mgt + kgt, split=False, boxes=1)
assert not out["boxes"] and any("поставки" in n for n in out["notes"]), out

# 22. поставка из одного задания: по правилу половины предел ноль, но первый
# короб мы не блокируем, а спрашиваем площадку — решение за WB, не за нами
solo_id = db.insert_wb_supply(client_id, wb_cab, "WB-GI-SOLO", "Одно задание", "2026-09-05T11:00:00", "тест", "1")
one = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "204", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:09", "cargo_type": "1", "office": "ПВЗ Ленина 1"},
)
db.set_shipment_supply([one], "WB-GI-SOLO")
assert len(supply_flow.make_boxes(solo_id, 1)["boxes"]) == 1, "первый короб не дошёл до площадки"
try:
    supply_flow.make_boxes(solo_id, 1)
    raise AssertionError("второй короб на одном задании прошёл локальную проверку")
except ValueError as exc:
    assert "не больше половины заданий" in str(exc), exc

# 23. куда везти: решает выбранная в ЛК точка, а не габарит. Через API точку не
# задать (11.09: shipping-point/spot дают 404, в offices пунктов выдачи нет),
# поэтому адрес читаем с карточки поставки и пустой не подменяем догадкой.
import shipments_pull
import wb_supply

real_wb = wb_supply.req
assert statuses.dropoff("1", "1", "50095011") == statuses.PVZ
assert statuses.dropoff("1", "0", "") == statuses.SC
# флаг true без выбранной точки — ещё не ПВЗ: так выглядят поставки, созданные API
assert statuses.dropoff("1", "1", "") == ""
assert statuses.to_pickup("1", "1", "") is False
assert statuses.dropoff("1", "", "") == ""
# габарит точку сдачи не решает: крупногабарит с выбранным ПВЗ едет на ПВЗ
assert statuses.dropoff("3", "1", "50095011") == statuses.PVZ
assert statuses.cargo_label("3", "1", "50095011") == "крупногабаритный · ПВЗ"
assert statuses.cargo_label("1", "1", "") == "малогабаритный · " + statuses.PVZ_WAIT
assert statuses.cargo_kind("1") == "малогабаритный"
assert "Домодедовская" in statuses.PVZ and "28" in statuses.PVZ
assert "Кавказский" in statuses.SC and "57" in statuses.SC
# адрес приходит от выбранной точки, а не из константы: id знаем, адрес — нет
assert statuses.dropoff("1", "1", "50272214") == "точка 50272214"
assert statuses.dropoff("1", "1", "50272214", "Москва, Колодезный пер., 2А") == "Москва, Колодезный пер., 2А"
# о незакрытой поставке предупреждаем, о закрытой говорим фактом
assert "Куда везти" in statuses.dropoff_warning("open", "0", "")
assert "ушла" in statuses.dropoff_warning("ready", "0", "")
assert statuses.dropoff_warning("open", "1", "50095011") == ""
# выгрузка заданий адрес больше не выдумывает: до поставки везти некуда
assert not hasattr(shipments_pull, "wb_office")

# карточка WB — источник правды по точке: читаем флаг и shippingPointId как есть
card_id = db.insert_wb_supply(client_id, wb_cab, "WB-GI-CARD", "Своя", "2026-09-10T12:00:00", "тест", "1", "1", "")
def api_card(method, url, headers=None, **kw):
    path = url.split("?")[0]
    if method == "GET" and path.rstrip("/").endswith("/supplies/WB-GI-CARD"):
        return Fake(200, {"id": "WB-GI-CARD", "cargoType": 1, "isPickupPointShipmentAllowed": False})
    return real_wb(method, url, headers=headers, **kw)
wb_supply.req = api_card
try:
    flag, cargo, point = supply_flow._sync_dropoff(db.get_wb_supply(card_id))
finally:
    wb_supply.req = real_wb
assert (flag, cargo, point) == ("0", "1", ""), (flag, cargo, point)
assert str(db.get_wb_supply(card_id)["pickup_allowed"]) == "0"
assert statuses.dropoff("1", flag, point) == statuses.SC

# крупногабарит больше не отказ на входе: короба зависят от точки, спрашиваем WB
big = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "301", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень кожаный", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:10", "cargo_type": "3"},
)
out = supply_flow.take([big], author="тест", choices={"%s:%s:3" % (client_id, wb_cab): "new"})
assert len(out["supplies"]) == 1, out
big_sup = db.get_wb_supply(out["supplies"][0]["id"])
assert str(big_sup["cargo_type"]) == "3", dict(big_sup)
assert "СЦ" not in (big_sup["name"] or ""), dict(big_sup)

# отказ «pickup point» — единственный честный признак, что точку не выбрали
deny_id = db.insert_wb_supply(client_id, wb_cab, "WB-GI-DENY", "Смена", "2026-09-05T12:00:00", "тест", "1", "1", "50095011")
deny_ship = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "302", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:11", "cargo_type": "1"},
)
db.set_shipment_supply([deny_ship], "WB-GI-DENY")

def deny_pvz(method, url, headers=None, **kw):
    path = url.split("?")[0]
    if method == "POST" and path.endswith("/trbx"):
        return Fake(409, {"code": "FailedToAddSupplyTrbx", "message": "You should add boxes only to supplies shipped to the pickup points"})
    if method == "GET" and path.rstrip("/").endswith("/supplies/WB-GI-DENY"):
        return Fake(200, {"id": "WB-GI-DENY", "isPickupPointShipmentAllowed": False, "cargoType": 1})
    return real_wb(method, url, headers=headers, **kw)

wb_supply.req = deny_pvz
try:
    supply_flow.make_boxes(deny_id, 1)
    raise AssertionError("короб прошёл при отказе площадки")
except ValueError as exc:
    assert "не даёт короба" in str(exc) and "Куда везти" in str(exc), exc
finally:
    wb_supply.req = real_wb
# отказ переписал точку: раньше мы оставляли ПВЗ и врали складу
moved_to_sc = db.get_wb_supply(deny_id)
assert str(moved_to_sc["pickup_allowed"]) == "0" and not (moved_to_sc["shipping_point"] or ""), dict(moved_to_sc)
assert db.get_shipments_by_ids([deny_ship])[0]["office"] == statuses.SC

# предел коробов и отказ по точке различаются: тексты не должны путаться
limit_id = db.insert_wb_supply(client_id, wb_cab, "WB-GI-LIM", "Предел", "2026-09-05T12:00:00", "тест", "1", "1", "50095011")
limit_ship = db.upsert_shipment(
    client_id, wb_cab, "wb", "fbs", "303", "Новый", "2026-09-05", "ART-1", "2000000000019",
    "Ремень", 1, None, 0, "2026-09-05T10:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-05 10:12", "cargo_type": "1"},
)
db.set_shipment_supply([limit_ship], "WB-GI-LIM")

def deny_limit(method, url, headers=None, **kw):
    path = url.split("?")[0]
    if method == "POST" and path.endswith("/trbx"):
        return Fake(409, {"code": "FailedToAddSupplyTrbx", "message": "limit reached"})
    return real_wb(method, url, headers=headers, **kw)

wb_supply.req = deny_limit
try:
    supply_flow.make_boxes(limit_id, 1)
    raise AssertionError("короб прошёл при отказе по пределу")
except ValueError as exc:
    assert "отказал в коробе" in str(exc) and "личный кабинет" not in str(exc), exc
finally:
    wb_supply.req = real_wb
# по пределу точку не трогаем: она была выбрана и остаётся
assert str(db.get_wb_supply(limit_id)["shipping_point"]) == "50095011"

# поставку пересобрали в ЛК: подтягиваем новый номер, задания и короб
old_ext = "WB-GI-OLD"
new_ext = "WB-GI-NEW"
old_sid = db.insert_wb_supply(client_id, wb_cab, old_ext, "Старая", "2026-09-10T12:00:00", "тест", "1", "1")
moved = []
for num in ("401", "402", "403"):
    moved.append(db.upsert_shipment(
        client_id, wb_cab, "wb", "fbs", num, "на сборке / в работе", "2026-09-10", "ART-1", "2000000000019",
        "Ремень", 1, None, 0, "2026-09-10T12:00:00",
        extra={"status_group": "assembling", "accepted_at": "2026-09-10 12:00", "cargo_type": "1",
               "pickup_allowed": "1", "office": statuses.PVZ},
    ))
db.set_shipment_supply(moved, old_ext, trbx_ext="WB-MP-OLD")
db.set_work_state(moved, "shipped")

def refresh_fake(method, url, headers=None, **kw):
    path = url.split("?")[0]
    if method == "GET" and path.endswith("/supplies/" + new_ext):
        return Fake(200, {
            "id": new_ext, "name": "Поставка от 10.09.2026", "done": False, "cargoType": 1,
            "isPickupPointShipmentAllowed": True, "shippingPointId": 50095011,
            "createdAt": "2026-09-10T14:06:59Z",
        })
    if method == "GET" and path.endswith("/supplies/%s/order-ids" % new_ext):
        return Fake(200, {"orderIds": [401, 402, 403]})
    if method == "GET" and path.endswith("/supplies/%s/trbx" % new_ext):
        return Fake(200, {"trbxes": [{"id": "WB-MP-NEW", "orders": []}]})
    return real_wb(method, url, headers=headers, **kw)

wb_supply.req = refresh_fake
got = supply_flow.refresh_from_wb(wb_cab, new_ext)
wb_supply.req = real_wb
assert got["ext_id"] == new_ext and got["state"] == "open" and got["office"] == statuses.PVZ, got
assert got["orders"] == ["401", "402", "403"] and got["boxes"] == ["WB-MP-NEW"], got
assert str(got["shipping_point"]) == "50095011" and got["warn"] == "", got
fresh = db.get_wb_supply(got["id"])
assert fresh["state"] == "open" and str(fresh["pickup_allowed"]) == "1", dict(fresh)
assert str(fresh["shipping_point"]) == "50095011", dict(fresh)
rows = db.list_supply_shipments(wb_cab, new_ext)
assert len(rows) == 3 and {r["trbx_ext"] for r in rows} == {"WB-MP-NEW"}, [dict(r) for r in rows]
assert all(r["work_state"] == "assembling" for r in rows), [dict(r) for r in rows]
assert {r["office"] for r in rows} == {statuses.PVZ}, [dict(r) for r in rows]
assert not db.list_supply_shipments(wb_cab, old_ext)
assert db.get_wb_supply(old_sid)["ext_id"] == old_ext

# 23б. вчерашняя петля целиком: поставку пересобрали в ЛК и выбрали там ПВЗ, а
# следующее задание легло в нашу прежнюю поставку, ехавшую в СЦ. Теперь перед
# отбором сверяемся с площадкой, и задание уходит в поставку из кабинета.
lk_ext = "WB-GI-LK"
lk_client = client_id
stale_sid = db.insert_wb_supply(lk_client, wb_cab, "WB-GI-STALE", "Смена СЦ", "2026-09-10T09:08:00", "тест", "1", "0", "")
plus_one = db.upsert_shipment(
    lk_client, wb_cab, "wb", "fbs", "501", "Новый", "2026-09-10", "ART-1", "2000000000019",
    "Ремень", 1, None, 0, "2026-09-10T15:00:00",
    extra={"status_group": "new", "accepted_at": "2026-09-10 15:00", "cargo_type": "1"},
)

def lk_supplies(method, url, headers=None, **kw):
    path = url.split("?")[0]
    if method == "GET" and path.endswith("/api/v3/supplies"):
        return Fake(200, {"supplies": [
            {"id": lk_ext, "name": "Поставка от 10.09.2026", "done": False, "cargoType": 1,
             "isPickupPointShipmentAllowed": True, "shippingPointId": 50095011,
             "createdAt": "2026-09-10T14:06:59Z"},
        ], "next": 0})
    if method == "GET" and path.rstrip("/").endswith("/supplies/WB-GI-STALE"):
        # прежнюю поставку сдали через ЛК: карточка это подтверждает
        return Fake(200, {"id": "WB-GI-STALE", "done": True, "cargoType": 1,
                          "closedAt": "2026-09-10T14:01:56Z", "isPickupPointShipmentAllowed": False})
    if method == "GET" and path.rstrip("/").endswith("/supplies/" + lk_ext):
        return Fake(200, {"id": lk_ext, "name": "Поставка от 10.09.2026", "done": False, "cargoType": 1,
                          "isPickupPointShipmentAllowed": True, "shippingPointId": 50095011,
                          "createdAt": "2026-09-10T14:06:59Z"})
    if method == "GET" and path.endswith("/supplies/%s/order-ids" % lk_ext):
        return Fake(200, {"orderIds": []})
    if method == "GET" and path.endswith("/supplies/%s/trbx" % lk_ext):
        return Fake(200, {"trbxes": []})
    if method == "PATCH" and path.endswith("/supplies/%s/orders" % lk_ext):
        return Fake(204, {})
    return real_wb(method, url, headers=headers, **kw)

wb_supply.req = lk_supplies
try:
    # поставка из ЛК видна в выборе, прежняя СЦ туда не попадает: её уже сдали.
    # Молча в поставку из кабинета больше не кладём — склад подтверждает сам.
    looked = supply_flow.take([plus_one], author="тест")
    assert looked["need_choice"] and not looked["supplies"], looked
    assert not (db.get_shipments_by_ids([plus_one])[0]["supply_ext"] or "")
    assert db.get_wb_supply(stale_sid)["state"] == "delivered", dict(db.get_wb_supply(stale_sid))
    offers = looked["groups"][0]["supplies"]
    assert "WB-GI-STALE" not in [s["ext_id"] for s in offers], offers
    lk = next(s for s in offers if s["ext_id"] == lk_ext)
    took = supply_flow.take(
        [plus_one], author="тест", choices={looked["groups"][0]["key"]: lk["id"]}
    )
finally:
    wb_supply.req = real_wb
assert len(took["supplies"]) == 1, took
assert took["supplies"][0]["ext_id"] == lk_ext, took
landed = db.get_shipments_by_ids([plus_one])[0]
assert landed["supply_ext"] == lk_ext and landed["office"] == statuses.PVZ, dict(landed)

# 24. печать из окна поставки: своё задание, чужое не берём
pdf, notes, pages = supply_flow.print_labels(sup["id"], ships[:1], "product")
assert pages >= 1 and pdf[:4] == b"%PDF", (pages, pdf[:8])
try:
    supply_flow.print_labels(sup["id"], [oz_ship], "product")
    raise AssertionError("напечатали чужое задание")
except ValueError as exc:
    assert "таких заданий нет" in str(exc), exc
pdf, notes, pages = supply_flow.print_labels(sup["id"], ships[:1], "posting_box")
assert pdf[:4] == b"%PDF" and pages >= 1, (pages, notes)
pdf, notes, pages = supply_flow.print_labels(sup["id"], ships[:1], "both")
assert pdf[:4] == b"%PDF" and pages >= 1, (pages, notes)
pdf, notes, pages = supply_flow.print_labels(sup["id"], ships[:1], "posting_product_box")
assert pdf[:4] == b"%PDF" and pages >= 1, (pages, notes)
pdf, notes, pages = supply_flow.print_assembly(ships[:1], "product")
assert pdf[:4] == b"%PDF" and pages >= 1, (pages, notes)
pdf, notes, pages = supply_flow.print_assembly(ships[:1], "posting_box")
assert pdf[:4] == b"%PDF" and pages >= 1, (pages, notes)

# 25. «Все контрагенты»: открытая работа не режется сменой «принят»
other_id = db.insert_client("other", "Другой ИП", "", "", "")
other_cab = db.insert_cabinet(other_id, "wb", "wb2", "token-o", "", 1, "", "")
old_ready = db.upsert_shipment(
    other_id, other_cab, "wb", "fbs", "555001", "На сборке", "2026-09-10", "ART-X", "",
    "Сумка", 3, None, 0, "2026-09-10T11:00:00",
    extra={"status_group": "assembling", "accepted_at": "2026-09-10 11:00"},
)
db.set_work_state([old_ready], "ready")
db.set_shipment_supply([old_ready], "WB-GI-OLD")
db.insert_wb_supply(other_id, other_cab, "WB-GI-OLD", "вчерашняя", "2026-09-10 11:00", "тест")
db.upsert_shipment(
    other_id, other_cab, "wb", "fbs", "555002", "В доставке", "2026-09-10", "ART-X", "",
    "Сумка", 1, None, 0, "2026-09-10T12:00:00",
    extra={"status_group": "shipped", "accepted_at": "2026-09-10 12:00"},
)
since, until = "2026-09-11 00:00", "2026-09-11 23:59"
ready_all = db.list_assembly(group="ready", since=since, until=until)
assert any(r["ext_id"] == "555001" for r in ready_all), [r["ext_id"] for r in ready_all]
counts, _ = db.assembly_counts(since=since, until=until)
assert counts.get("ready", 0) >= 1, counts
assert "WB-GI-OLD" in db.list_assembly_supply_exts(group="ready", since=since, until=until)
# вчерашний отгруженный не должен надуть сегодняшнюю вкладку «Отгружены»
shipped_other = db.list_assembly(client_id=other_id, group="shipped", since=since, until=until)
assert shipped_other == [], [dict(r) for r in shipped_other]

# 26. Вкладка без потолка 100: галка «все» должна брать весь статус с фильтром
big_id = db.insert_client("big100", "Много новых", "", "", "")
big_cab = db.insert_cabinet(big_id, "wb", "wb", "t", "", 1, "", "")
for i in range(120):
    db.upsert_shipment(
        big_id, big_cab, "wb", "fbs", "N%05d" % i, "Новое", "2026-09-18", "A", "",
        "Товар", 1, None, 0, "2026-09-18T10:00:00",
        extra={"status_group": "new", "accepted_at": "2026-09-18 10:00"},
    )
capped = db.list_assembly(client_id=big_id, group="new", limit=100)
full = db.list_assembly(client_id=big_id, group="new")
art_only = db.list_assembly(client_id=big_id, group="new", article="NOPE")
counts_big, _ = db.assembly_counts(client_id=big_id)
assert len(capped) == 100, len(capped)
assert len(full) == 120, len(full)
assert counts_big.get("new") == 120, counts_big
assert art_only == [], art_only

# 27. точка сдачи ставится из софта: PATCH shipping-method, а не поход в ЛК.
# Проверено живым запросом 18.09 — метод лежит на /api/marketplace/v3/fbs/,
# а не на /api/v3/, где мы его 11.09 искали и не нашли.
wb_supply.req = fake_req
try:
    got = supply_flow.points(client_id, city="Москва", cargo_type="1", refresh=True)
finally:
    wb_supply.req = real_wb
assert got["city"] == "Москва" and not got["notes"], got
# список идёт по адресу: оператор ищет глазами улицу, а не номер точки
assert [p["id"] for p in got["points"]] == [50095011, 87609], got["points"]
assert {p["kind"] for p in got["points"]} == {"sc", "pp"}, got["points"]
# справочник лёг в базу: адрес выбранной точки теперь показываем без запроса к WB
assert supply_flow.point_address(50095011) == "Москва, Домодедовская Улица 28"
assert db.wb_points_count() >= 2, db.wb_points_count()
# поиск идёт по кэшу, площадку больше не трогаем
found = supply_flow.points(client_id, city="Москва", query="Домодедовская")
assert [p["id"] for p in found["points"]] == [50095011], found["points"]

# ПВЗ берёт только малогабарит: крупногабаритной поставке его не ставим
assert supply_flow.point_fits(50095011, "1") is True
assert supply_flow.point_fits(50095011, "3") is False
assert supply_flow.point_fits(87609, "3") is True
assert supply_flow.point_fits(50095011, "") is True

# точка по умолчанию: общая, своя у контрагента, и обе переживают перезапуск
assert supply_flow.dropoff_default()["point_id"] == statuses.PVZ_SHIPPING_POINT
supply_flow.set_dropoff_default(87609, client_id)
assert supply_flow.dropoff_default(client_id)["point_id"] == 87609
assert supply_flow.dropoff_default()["point_id"] == statuses.PVZ_SHIPPING_POINT
supply_flow.set_dropoff_default(50095011, client_id)

point_sid = db.insert_wb_supply(client_id, wb_cab, "WB-GI-POINT", "Смена", "2026-09-18T09:00:00", "тест", "1", "", "")
wb_supply.req = fake_req
try:
    res = supply_flow.set_dropoff(point_sid, 50095011)
finally:
    wb_supply.req = real_wb
assert res["point_id"] == 50095011 and res["address"] == "Москва, Домодедовская Улица 28", res
# дату WB требует вместе с точкой: пустую подставляем сами
sent = POINT_SET["WB-GI-POINT"]
assert sent["shippingType"] == "selfShipping" and len(sent["shippingDt"]) == 10, sent
saved = db.get_wb_supply(point_sid)
assert str(saved["shipping_point"]) == "50095011" and saved["shipping_dt"] == sent["shippingDt"], dict(saved)

# отсканированную поставку площадка уже не отдаёт: причина уходит оператору как есть
scan_sid = db.insert_wb_supply(client_id, wb_cab, "WB-GI-SCAN", "Смена", "2026-09-18T09:00:00", "тест", "1", "", "")
SCANNED.add("WB-GI-SCAN")
wb_supply.req = fake_req
try:
    supply_flow.set_dropoff(scan_sid, 50095011)
    raise AssertionError("точку поменяли на отсканированной поставке")
except ValueError as exc:
    assert "отсканировали в пункте" in str(exc), exc
finally:
    wb_supply.req = real_wb
    SCANNED.discard("WB-GI-SCAN")
assert not (db.get_wb_supply(scan_sid)["shipping_point"] or ""), dict(db.get_wb_supply(scan_sid))

# закрытую поставку не трогаем: точку после передачи в доставку не поменять
db.set_wb_supply_state(scan_sid, "ready", "2026-09-18T10:00:00")
try:
    supply_flow.set_dropoff(scan_sid, 50095011)
    raise AssertionError("точку поменяли у закрытой поставки")
except ValueError as exc:
    assert "передана в доставку" in str(exc), exc

# связь до WB с этой VPS рвётся регулярно: обрыв не должен ронять создание
# поставки, он приходит заметкой. Раньше ConnectTimeout летел мимо перехвата
import requests

drop_sid = db.insert_wb_supply(client_id, wb_cab, "WB-GI-DROP", "Смена", "2026-09-18T09:00:00", "тест", "1", "", "")

def broken(method, url, headers=None, **kw):
    raise requests.exceptions.ConnectTimeout("мок обрыва связи")

wb_supply.req = broken
try:
    out = supply_flow.ensure_dropoff(drop_sid)
finally:
    wb_supply.req = real_wb
assert not out["point_id"] and any("не ответил" in n for n in out["notes"]), out
assert not (db.get_wb_supply(drop_sid)["shipping_point"] or ""), dict(db.get_wb_supply(drop_sid))

# крупногабаритной поставке ПВЗ по умолчанию не ставим, а говорим почему
kgt_sid = db.insert_wb_supply(client_id, wb_cab, "WB-GI-KGT", "Смена", "2026-09-18T09:00:00", "тест", "3", "", "")
out = supply_flow.ensure_dropoff(kgt_sid)
assert not out["point_id"] and any("малогабарит" in n for n in out["notes"]), out
assert not (db.get_wb_supply(kgt_sid)["shipping_point"] or ""), dict(db.get_wb_supply(kgt_sid))

print("все проверки поставок, сборки и КиЗ прошли")
