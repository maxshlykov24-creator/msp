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
        packs = (kw.get("json") or {}).get("packages") or []
        return Fake(200, {"result": ["0001-1-%s" % (i + 1) for i in range(len(packs))]})
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

print("все проверки поставок и сборки прошли")
