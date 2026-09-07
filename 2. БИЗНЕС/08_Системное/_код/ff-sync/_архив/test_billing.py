"""Прогон новой модели денег и выгрузки КиЗ на моках МойСклад.

Проверяем три вещи из созвона: ставка хранения одна на всех, «тариф» позиции —
это сборка в ₽ за штуку, счётчик стартует после фактической приёмки. Плюс что
выгрузка кодов маркировки отдаёт артикул, GTIN и наименование.

Запуск: PYTHONPATH=.. python3 _архив/test_billing.py
"""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FF_DB"] = os.path.join(tempfile.mkdtemp(), "bill.db")
os.environ["MS_TOKEN"] = "test-token"

import db
import net

CALLS = []
ORDER_ID = "PO-1"
PRODUCT_ID = "PROD-1"
SUPPLY_ID = "SUP-1"
supply_applicable = True
supply_deleted = None


class Fake:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    @property
    def content(self):
        return json.dumps(self._data or {}).encode()

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("мок отдал %s" % self.status_code)


def fake_req(method, url, headers=None, **kw):
    CALLS.append((method, url, kw.get("json"), kw.get("params")))
    if url.endswith("/entity/purchaseorder/%s" % ORDER_ID):
        return Fake(200, {"id": ORDER_ID, "supplies": [{"meta": {"href": net.MS_BASE + "/entity/supply/" + SUPPLY_ID}}]})
    if url.endswith("/entity/supply/%s/positions" % SUPPLY_ID):
        return Fake(200, {
            "meta": {"size": 1},
            "rows": [{"quantity": 10, "assortment": {"meta": {"href": net.MS_BASE + "/entity/product/" + PRODUCT_ID}}}],
        })
    if url.endswith("/entity/supply/%s" % SUPPLY_ID):
        return Fake(200, {"id": SUPPLY_ID, "applicable": supply_applicable, "deleted": supply_deleted, "moment": "2026-09-01 12:30:00"})
    if url.endswith("/entity/invoiceout"):
        return Fake(200, {"id": "INV-1", "name": "СЧ-1"})
    if "/entity/service" in url:
        return Fake(200, {"rows": [{"id": "SRV-%s" % i, "name": n} for i, n in enumerate(("Хранение на складе", "Приёмка товара", "Сборка заказов"))]})
    return Fake(404, {"message": "мок не знает %s" % url})


net.req = fake_req
db.init_db()

import account
import billing
from export_xlsx import build_marks

client_id = db.insert_client("test", "Тест ООО", "AGENT-1", "ORG-1", "STORE-1")
db.update_client(client_id, tariff_pick=3.0)

# партия завелась сегодня, приёмки ещё нет: счётчик стоять
lot_id = db.insert_lot(
    client_id, PRODUCT_ID, "ART-1", "2000000000019", "02000000000019", "Ремень кожаный",
    "Не маркируется", 2.0, 10, "2026-09-01T09:00:00", ORDER_ID, pick_rate=None, dims="10x10x20",
)

rows = account.lot_rows(only_open=False)
row = rows[0]
assert row["accepted"] == "", "до приёмки даты быть не должно: %r" % row["accepted"]
assert row["bill_days"] == 0 and row["storage"] == 0, "непринятая партия не должна ничего стоить: %r" % row
assert row["rate"] == 0.15, "ставка хранения общая: %r" % row["rate"]
assert row["pick_rate"] == 3.0, "сборка берётся у клиента, когда у позиции пусто: %r" % row["pick_rate"]

try:
    billing.preview([lot_id])
    raise AssertionError("счёт по непринятой партии выставлять нельзя")
except ValueError as exc:
    assert "не приняты" in str(exc), exc

# черновик приёмки склад не двигает
supply_applicable = False
import accept

res = accept.run()
assert res["accepted"] == [], "непроведённая приёмка не должна ставить дату: %r" % res

# приёмка в корзине остаток уже не держит
supply_applicable = True
supply_deleted = "2026-09-02 10:00:00"
res = accept.run()
assert res["accepted"] == [], "удалённая приёмка не должна ставить дату: %r" % res

supply_deleted = None
res = accept.run()
assert len(res["accepted"]) == 1, "приёмку не увидел: %r" % res
assert res["accepted"][0]["at"].startswith("2026-09-01T12:30"), res["accepted"][0]

# повторный прогон не переписывает дату и не дёргает МойСклад
before = len(CALLS)
res = accept.run()
assert res["checked"] == 0 and len(CALLS) == before, "принятые партии больше не опрашиваем"

row = account.lot_rows(only_open=False)[0]
assert row["accepted"] == "01.09.2026", row["accepted"]
assert row["bill_days"] > 0, "после приёмки счётчик должен идти: %r" % row["bill_days"]

# хранение: 10 шт × 2 л × 0,15 ₽ за каждые сутки периода
day = account.storage_of(db.get_lot(lot_id), db.get_client_by_id(client_id), None, account.as_day("2026-09-05"))
assert day["days"] == 5, day
assert day["liter_days"] == 100.0, day
assert day["storage"] == 15.0, day

# сборка: уехало 4 шт по ставке позиции 3 ₽
db.set_lot_pick_rate(lot_id, 3.0)
account.consume_fifo(client_id, PRODUCT_ID, 4, "0001-1", "2026-09-03T10:00:00")
calc = account.money(db.get_lot(lot_id), db.get_client_by_id(client_id), None, account.as_day("2026-09-05"))
assert calc["pick_qty"] == 4.0, calc
assert calc["pick"] == 12.0, calc
assert calc["total"] == round(calc["storage"] + 12.0, 2), calc

prev = billing.preview([lot_id], date_to="2026-09-05")
names = [l["name"] for l in prev["lines"]]
assert names == ["Хранение на складе", "Сборка заказов"], names
assert prev["total"] == round(sum(l["sum"] for l in prev["lines"]), 2), prev

inv = billing.create([lot_id], author="test", date_to="2026-09-05")
payload = [c[2] for c in CALLS if c[1].endswith("/entity/invoiceout")][-1]
assert len(payload["positions"]) == 2, payload["positions"]
assert inv["total"] == prev["total"], (inv, prev)
stored = db.get_invoice(inv["id"])
assert round(stored["ship"], 2) == 12.0, stored["ship"]
assert round(stored["storage"] + stored["ship"], 2) == round(stored["total"], 2), dict(stored)

# после счёта период закрыт: та же партия второй раз те же дни не берёт
again = account.money(db.get_lot(lot_id), db.get_client_by_id(client_id), None, account.as_day("2026-09-05"))
assert again["storage"] == 0 and again["pick"] == 0, again

# выгрузка КиЗ: плоский список, у каждого кода артикул, GTIN и наименование
cab = db.insert_cabinet(client_id, "wb", "wb", "token", "", 1, "", "")
db.replace_cache(client_id, cab, [{
    "marketplace": "wb",
    "ext_key": "1", "ext_article": "ART-1", "ext_barcode": "2000000000019", "name": "Ремень кожаный",
    "size": "", "gtin": "02000000000019", "tracking_type": "Одежда", "subject": "Ремни", "need_kiz": 1, "image": "",
}])
ship_id = db.upsert_shipment(
    client_id, cab, "wb", "fbs", "555", "Новый", "2026-09-04", "ART-1", "2000000000019",
    "Ремень кожаный", 2, None, 2, "2026-09-04T10:00:00", extra={"status_group": "new"},
)
db.replace_shipment_marks(ship_id, [
    {"code": "010200000000001921abcdefgh", "gtin": "02000000000019", "article": "ART-1"},
    {"code": "0104607001112223215xyzqwerty", "gtin": "", "article": ""},
])

name, raw = build_marks([ship_id])
from openpyxl import load_workbook

ws = load_workbook(io.BytesIO(raw)).active
head = [c.value for c in ws[1]]
assert head == ["Код маркировки", "Артикул", "GTIN", "Наименование"], head
first = [c.value for c in ws[2]]
assert first[1:] == ["ART-1", "02000000000019", "Ремень кожаный"], first
second = [c.value for c in ws[3]]
assert second[1] == "ART-1", "артикул подставляем из отправления: %r" % second
assert second[2] == "02000000000019", "GTIN добираем из каталога: %r" % second
assert ws.cell(row=4, column=1).value == "Кодов: 2", ws.cell(row=4, column=1).value

# выгрузки учёта и календаря собираются на новых полях
from export_xlsx import build, build_calendar

xlsx_name, xlsx_raw = build()
head = [c.value for c in load_workbook(io.BytesIO(xlsx_raw)).active[1]]
assert "Ставка хранения, ₽/л/сутки" in head and "Сборка, ₽/шт" in head, head
assert "Приёмка на склад" in head, head
cal_name, cal_raw = build_calendar()
assert cal_raw, "календарь не собрался"

print("хранение, сборка, приёмка и КиЗ: прогон прошёл")
