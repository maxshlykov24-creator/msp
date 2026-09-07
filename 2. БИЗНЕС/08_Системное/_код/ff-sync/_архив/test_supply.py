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
        return Fake(201, {"id": "WB-GI-777"})
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
    if url.endswith("/meta/sgtin") and method == "PUT":
        WB_SENT[url.rsplit("/orders/", 1)[1].split("/")[0]] = (kw.get("json") or {}).get("sgtins") or []
        return Fake(204)
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

# лимит грузомест: не больше половины числа заданий
try:
    supply_flow.make_boxes(sup["id"], 1)
    raise AssertionError("создали грузоместо сверх лимита WB")
except ValueError as exc:
    assert "не больше половины" in str(exc), exc

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
}, pre

# без confirm не передаём
try:
    supply_flow.deliver(sup["id"])
    raise AssertionError("передали без подтверждения")
except ValueError as exc:
    assert "подтверждение" in str(exc), exc

# с confirm, но с заданием без коробки — второе предупреждение
try:
    supply_flow.deliver(sup["id"], confirm=True)
    raise AssertionError("передали с заданием вне коробки без force")
except ValueError as exc:
    assert "без грузоместа: 2 из 4" in str(exc), exc

# 6. QR грузомест до передачи
pdf, notes, pages = supply_flow.boxes_pdf(sup["id"])
assert pdf[:4] == b"%PDF" and pages == 2, (pages, notes)

# 7. передача в доставку
out = supply_flow.deliver(sup["id"], confirm=True, force=True)
assert out == {"ok": True, "orders": 4, "loose": 2}, out
assert db.get_wb_supply(sup["id"])["state"] == "delivered"
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

print("все проверки поставок, сборки и КиЗ прошли")
