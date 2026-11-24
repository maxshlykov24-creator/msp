"""Сверка КИЗ Вольготова со списком GTIN. Живой Ozon не вызывается.

Запуск: python3 _архив/test_gtin_pairs.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FF_DB"] = os.path.join(tempfile.mkdtemp(), "gtin.db")

import gtin_pairs
import db

CLIENT = gtin_pairs.CLIENT_CODE
EXT = gtin_pairs.CABINET_EXT
WHITE_L = "Комбинезон белый L"
WHITE_L_GTIN = "04681117137092"
DART_L_GTIN = "04681117137214"
TAIL = "ZZCRYPTOTAILZZ"


def code_of(gtin):
    return "01%s21%s" % (gtin, TAIL)


def test_list_holds():
    assert len(gtin_pairs._ROWS) == 33
    assert len(gtin_pairs._BY_ARTICLE) == 33
    assert len(gtin_pairs._BY_GTIN) == 33
    assert gtin_pairs.digits14("") == ""
    assert gtin_pairs.digits14("123") == ""
    for article, gtin in gtin_pairs._ROWS:
        assert gtin_pairs.listed_gtin(CLIENT, EXT, article) == gtin
        assert gtin_pairs.listed_gtin(CLIENT, EXT, article.replace("\u041c", "M")) == gtin
        assert gtin_pairs.scan_stop(CLIENT, EXT, article, gtin) == ""
        assert TAIL not in gtin_pairs.scan_stop(CLIENT, EXT, article, gtin)
    assert gtin_pairs.applies("другой", EXT) is False
    assert gtin_pairs.applies(CLIENT, "1") is False
    assert gtin_pairs.scan_stop("другой", "1", WHITE_L, DART_L_GTIN) == ""


def test_list_refuses_collisions():
    clash_letter = gtin_pairs.load_pairs((
        ("Комбинезон белый \u041c", "04681117137085"),
        ("Комбинезон белый M", "04681117137078"),
    ))
    assert clash_letter == ({}, {}, {})
    clash_gtin = gtin_pairs.load_pairs((
        ("Комбинезон белый L", WHITE_L_GTIN),
        ("Комбинезон белый S", WHITE_L_GTIN),
    ))
    assert clash_gtin == ({}, {}, {})


def test_messages_hide_the_code():
    wrong = gtin_pairs.scan_stop(CLIENT, EXT, WHITE_L, DART_L_GTIN)
    assert "Ждали артикул" in wrong and WHITE_L_GTIN in wrong and DART_L_GTIN in wrong
    assert TAIL not in wrong and code_of(DART_L_GTIN) not in wrong
    empty = gtin_pairs.scan_stop(CLIENT, EXT, WHITE_L, "")
    assert empty == "В коде нет номера. Отсканируй ещё раз."
    parked = gtin_pairs.scan_stop(CLIENT, EXT, "резинка 12 петель", WHITE_L_GTIN)
    assert "резинка 12 петель" in parked and WHITE_L in parked
    assert code_of(WHITE_L_GTIN) not in parked
    bound, conflict = gtin_pairs.bind_gtin(CLIENT, EXT, WHITE_L, "")
    assert bound == WHITE_L_GTIN and conflict == ""
    bound, conflict = gtin_pairs.bind_gtin(CLIENT, EXT, WHITE_L, "04681117139999")
    assert bound is None and "04681117139999" in conflict and WHITE_L_GTIN in conflict
    assert gtin_pairs.bind_gtin("другой", "1", WHITE_L, "") == (None, "")


class Fake:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.content = json.dumps(data or {}).encode()

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return self._data


def exemplars(product_id, article, item_gtin="", marks=None):
    return {
        "products": [{
            "product_id": product_id,
            "quantity": 1,
            "is_mandatory_mark_needed": True,
            "is_mandatory_mark_possible": True,
            "exemplars": [{"exemplar_id": 7, "marks": marks or []}],
        }]
    }


def seed():
    db.init_db()
    conn = db.connect()
    conn.execute("INSERT INTO clients(code,name,active) VALUES (?,?,1)", (CLIENT, "ИП Вольготов"))
    vol_id = conn.execute("SELECT id FROM clients WHERE code=?", (CLIENT,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO cabinets(client_id,marketplace,name,token,client_id_ext,active) VALUES (?,?,?,?,?,1)",
        (vol_id, "ozon", "ozon", "test-token", EXT),
    )
    vol_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (vol_id,)).fetchone()["id"]
    conn.execute("INSERT INTO clients(code,name,active) VALUES (?,?,1)", ("OTHER", "Чужой"))
    other_id = conn.execute("SELECT id FROM clients WHERE code=?", ("OTHER",)).fetchone()["id"]
    conn.execute(
        "INSERT INTO cabinets(client_id,marketplace,name,token,client_id_ext,active) VALUES (?,?,?,?,?,1)",
        (other_id, "ozon", "ozon", "test-token", "1"),
    )
    other_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (other_id,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return vol_id, vol_cab, other_id, other_cab


def ship(client_id, cabinet_id, ext_id, article, item_gtin=""):
    items = [{
        "product_id": 100,
        "article": article,
        "name": article,
        "qty": 1,
        "gtin": item_gtin,
    }]
    return db.upsert_shipment(
        client_id, cabinet_id, "ozon", "fbs", ext_id, "Ожидает упаковки", "",
        article, "", article, 1, None, 0, "2026-11-24T12:00:00",
        extra={"status_group": "new", "items_json": json.dumps(items, ensure_ascii=False)},
    )


def test_submit_stops_before_ozon():
    from unittest.mock import patch
    import kiz

    vol_id, vol_cab, other_id, other_cab = seed()
    good = ship(vol_id, vol_cab, "GOOD-1", WHITE_L, "")
    bad = ship(vol_id, vol_cab, "BAD-1", WHITE_L, "")
    clash = ship(vol_id, vol_cab, "CLASH-1", WHITE_L, "04681117139999")
    foreign = ship(other_id, other_cab, "FOREIGN-1", WHITE_L, "")
    calls = []

    def fake_req(method, url, **kwargs):
        calls.append(url)
        if url == kiz.OZON_VALIDATE:
            products = kwargs["json"]["products"]
            return Fake(200, {"products": [{
                "product_id": p["product_id"],
                "exemplars": [{"marks": [{"mark": m["mark"], "valid": True} for m in ex["marks"]]} for ex in p["exemplars"]],
            } for p in products]})
        if url == kiz.OZON_SET:
            return Fake(200, {})
        if url == kiz.OZON_STATUS:
            return Fake(200, {"status": "ship_available"})
        raise AssertionError(url)

    def run(ship_id, raw, data):
        calls.clear()
        with patch.object(kiz, "_ozon_edit_note", return_value=""), patch.object(kiz, "_ozon_create", return_value=data), patch.object(kiz, "req", side_effect=fake_req):
            return kiz.submit(ship_id, [raw])

    run(good, code_of(WHITE_L_GTIN), exemplars(100, WHITE_L))
    assert calls[0] == kiz.OZON_VALIDATE

    calls.clear()
    try:
        run(bad, code_of(DART_L_GTIN), exemplars(100, WHITE_L))
        raise AssertionError("чужой код ушёл дальше проверки")
    except kiz.KizError as exc:
        assert "Ждали артикул" in str(exc)
        assert TAIL not in str(exc)
        assert code_of(DART_L_GTIN) not in str(exc)
    assert calls == []

    calls.clear()
    try:
        run(bad, "не код", exemplars(100, WHITE_L))
        raise AssertionError("код без номера ушёл дальше проверки")
    except kiz.KizError as exc:
        assert str(exc) == "В коде нет номера. Отсканируй ещё раз."
    assert calls == []

    calls.clear()
    try:
        run(clash, code_of(WHITE_L_GTIN), exemplars(100, WHITE_L))
        raise AssertionError("спор номера ушёл в Ozon")
    except kiz.KizError as exc:
        assert "04681117139999" in str(exc) and WHITE_L_GTIN in str(exc)
        assert TAIL not in str(exc)
    assert calls == []

    run(foreign, code_of(DART_L_GTIN), exemplars(100, WHITE_L))
    assert calls[0] == kiz.OZON_VALIDATE

    elastic = ship(vol_id, vol_cab, "ELASTIC-1", "резинка 12 петель", "")
    conn = db.connect()
    conn.execute(
        "UPDATE shipments SET items_json=? WHERE id=?",
        (json.dumps([
            {"product_id": 100, "article": WHITE_L, "name": WHITE_L, "qty": 1, "gtin": ""},
            {"product_id": 200, "article": "резинка 12 петель", "name": "резинка", "qty": 1, "gtin": ""},
        ], ensure_ascii=False), elastic),
    )
    conn.commit()
    conn.close()
    both = {
        "products": [
            {"product_id": 100, "quantity": 1, "is_mandatory_mark_needed": True, "is_mandatory_mark_possible": True,
             "exemplars": [{"exemplar_id": 7, "marks": []}]},
            {"product_id": 200, "quantity": 1, "is_mandatory_mark_needed": False, "is_mandatory_mark_possible": True,
             "exemplars": [{"exemplar_id": 8, "marks": []}]},
        ]
    }
    calls.clear()
    raw = code_of(WHITE_L_GTIN)
    try:
        with patch.object(kiz, "_ozon_edit_note", return_value=""), patch.object(kiz, "_ozon_create", return_value=both), patch.object(kiz, "req", side_effect=fake_req):
            kiz.submit(elastic, [raw], [{"code": raw, "product_id": 200}])
        raise AssertionError("код списка повесился на резинку")
    except kiz.KizError as exc:
        assert "резинка 12 петель" in str(exc)
        assert TAIL not in str(exc)
    assert calls == []


def test_cache_refresh_keeps_the_list():
    vol_id = db.get_client(CLIENT)["id"]
    conn = db.connect()
    vol_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (vol_id,)).fetchone()["id"]
    conn.close()
    db.replace_cache(vol_id, vol_cab, [{
        "marketplace": "ozon", "ext_key": WHITE_L, "ext_article": WHITE_L,
        "ext_barcode": "OZN1", "name": WHITE_L, "gtin": "", "need_kiz": 0,
    }])
    assert gtin_pairs.listed_gtin(CLIENT, EXT, WHITE_L) == WHITE_L_GTIN
    assert gtin_pairs.bind_gtin(CLIENT, EXT, WHITE_L, "")[0] == WHITE_L_GTIN


def test_journal_replaces_only_a_real_answer():
    from unittest.mock import patch
    import kiz
    import shipments_pull

    vol_id = db.get_client(CLIENT)["id"]
    conn = db.connect()
    vol_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (vol_id,)).fetchone()["id"]
    conn.close()
    sid = ship(vol_id, vol_cab, "JOURNAL-1", WHITE_L, "")
    local = "LOCAL-EXTRA-NOT-ON-OZON"
    remote = code_of(WHITE_L_GTIN)
    db.replace_shipment_marks(sid, [
        {"code": local, "gtin": "", "article": WHITE_L},
        {"code": remote, "gtin": WHITE_L_GTIN, "article": WHITE_L},
    ])
    empty = exemplars(100, WHITE_L)
    with patch.object(kiz, "_ozon_edit_note", return_value=""), patch.object(kiz, "_ozon_create", return_value=empty):
        assert local in kiz.plan(sid)["codes"]
    kept = {row["code"] for row in db.list_shipment_marks([sid])}
    assert local in kept and remote in kept

    answered = exemplars(100, WHITE_L, marks=[{"mark": remote, "mark_type": "mandatory_mark"}])
    with patch.object(kiz, "_ozon_edit_note", return_value=""), patch.object(kiz, "_ozon_create", return_value=answered):
        opened = kiz.plan(sid)
    assert opened["codes"] == [remote]
    assert local not in {row["code"] for row in db.list_shipment_marks([sid])}

    client = db.get_client_by_id(vol_id)
    cab = db.get_cabinet(vol_cab)
    post = {
        "posting_number": "JOURNAL-1",
        "status": "awaiting_packaging",
        "products": [{"sku": 100, "offer_id": WHITE_L, "name": WHITE_L, "quantity": 1}],
    }
    with patch.object(shipments_pull, "pull_ozon_get", return_value=post):
        shipments_pull.handle_ozon(client, cab, "fbs", [post], {})
    assert local not in {row["code"] for row in db.list_shipment_marks([sid])}
    assert remote in {row["code"] for row in db.list_shipment_marks([sid])}


def test_assemble_stops_before_ship():
    from unittest.mock import patch
    import kiz
    import supply_flow

    vol_id = db.get_client(CLIENT)["id"]
    other_id = db.get_client("OTHER")["id"]
    conn = db.connect()
    vol_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (vol_id,)).fetchone()["id"]
    other_cab = conn.execute("SELECT id FROM cabinets WHERE client_id=?", (other_id,)).fetchone()["id"]
    conn.close()
    bad = ship(vol_id, vol_cab, "SHIP-BAD", WHITE_L, "")
    good = ship(other_id, other_cab, "SHIP-GOOD", "чужой товар", "")

    def fake_plan(row, cab):
        if row["ext_id"] == "SHIP-BAD":
            return {
                "need": 1, "have": 1, "note": "", "can_submit": True, "skip_scan": False,
                "state_blocked": False,
                "products": [{"article": WHITE_L, "required": True, "filled": 1, "quantity": 1}],
                "_remote_marks": [{
                    "code": code_of(DART_L_GTIN), "gtin": DART_L_GTIN, "article": WHITE_L,
                }],
            }
        return {
            "need": 0, "have": 0, "note": "", "can_submit": False, "skip_scan": True,
            "state_blocked": False,
            "products": [{"article": "чужой товар", "required": False, "filled": 0, "quantity": 1}],
            "_remote_marks": [],
        }

    def no_ship(*args, **kwargs):
        raise AssertionError("сборка вызвана")

    with patch.object(kiz, "_ozon_plan", side_effect=fake_plan), patch.object(supply_flow, "ship_ozon", side_effect=no_ship):
        try:
            supply_flow.assemble([bad, good])
            raise AssertionError("сборка прошла с чужим кодом")
        except ValueError as exc:
            assert "Ждали артикул" in str(exc)
            assert "Сними код" in str(exc)
            assert TAIL not in str(exc)
            assert code_of(DART_L_GTIN) not in str(exc)

    def fail_plan(row, cab):
        raise kiz.KizError("Ozon не отдал список экземпляров")

    with patch.object(kiz, "_ozon_plan", side_effect=fail_plan), patch.object(supply_flow, "ship_ozon", side_effect=no_ship):
        try:
            supply_flow.assemble([bad])
            raise AssertionError("сбой чтения пустил сборку")
        except kiz.KizError as exc:
            assert "не отдал" in str(exc)


if __name__ == "__main__":
    test_list_holds()
    test_list_refuses_collisions()
    test_messages_hide_the_code()
    test_submit_stops_before_ozon()
    test_cache_refresh_keeps_the_list()
    test_journal_replaces_only_a_real_answer()
    test_assemble_stops_before_ship()
    print("сверка КИЗ: список, скан, журнал и сборка проверены без отправки в Ozon")
