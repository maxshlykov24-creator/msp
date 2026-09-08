from app.filter_cars import filter_stock, filter_warehouse
from app.map_row import CORE_HEADER, HEADER, dedup_sort, map_row
from app.state import guard_write


def test_core_header_is_amo_contract():
    """A–O читает amo через FILTER на Sheet1. Порядок и ширина неприкосновенны."""
    assert len(CORE_HEADER) == 15
    assert HEADER[:15] == CORE_HEADER
    assert HEADER[0] == "VIN"
    assert HEADER[13] == "Комплектация"
    assert HEADER[14] == "Цена продажи"


def test_extra_columns_go_right():
    assert len(HEADER) == 25
    assert HEADER[15] == "Поколение"
    assert HEADER[-2] == "НДС"
    assert HEADER[-1] == "История"


def test_vat_only_on_explicit_flag():
    """Пусто = «не подтверждено». False и None одинаково пустые, «Нет» не пишем."""
    assert map_row({"vin": "A", "isAbleToSellWithVat": True})[-2] == "Да"
    assert map_row({"vin": "A", "isAbleToSellWithVat": False})[-2] == ""
    assert map_row({"vin": "A"})[-2] == ""


def test_history_taxi_and_carsharing():
    taxi = map_row({"vin": "A", "anyCommentDescription": "Без ДТП. Использовался в такси"})
    assert taxi[-1] == "такси"
    share = map_row(
        {"vin": "B", "anyCommentDescription": "1. Источник приема: выкуп корпоративного парка Яндекс"}
    )
    assert share[-1] == "каршеринг (корпоративный парк Яндекса)"
    # отрицание не превращается в пометку «такси»
    clean = map_row({"vin": "C", "anyCommentDescription": "В такси не использовался"})
    assert clean[-1] == ""
    assert map_row({"vin": "D"})[-1] == ""


def test_map_pdf_sample():
    row = map_row(
        {
            "vin": "XW8ZZZ5NZFG107190",
            "brand": "Volkswagen",
            "model": "Tiguan",
            "generation": "I Рестайлинг",
            "year": 2014,
            "color": "black",
            "mileage": 109332,
            "vehicleState": "good",
            "gear": "at",
            "drive": "awd",
            "volume": 2.4,
            "power": 170,
            "engine": "petrol",
            "ownersAmount": 2,
            "complectation": "Comfortline",
            "sellingPrice": 992000,
        }
    )
    assert row[0] == "XW8ZZZ5NZFG107190"
    assert row[4] == "черный"
    assert row[5] == "109 332"
    assert row[6] == "Хорошее (B)"
    assert row[7] == "Автомат"
    assert row[8] == "Полный"
    assert row[9] == "2,40"
    assert row[10] == "170"
    assert row[11] == "Бензин"
    assert row[13] == "Comfortline"
    assert row[14] == "992000"
    assert "Рестайлинг" not in row


def test_electric_does_not_shift_columns():
    row = map_row(
        {
            "vin": "LFP8C7PC1P1D43412",
            "brand": "FAW",
            "model": "Bestune NAT",
            "year": 2023,
            "color": "белый",
            "mileage": 4726,
            "vehicleState": "excellent",
            "gear": "at",
            "drive": "fwd",
            "volume": 163,
            "power": 163,
            "engine": "electric",
            "ownersAmount": 1,
            "equipment": "Delight",
            "sellingPrice": 1685000,
        }
    )
    assert row[9] == ""  # объём пустой
    assert row[10] == "163"
    assert row[11] == "Электро"
    assert row[12] == "1"
    assert row[13] == "Delight"
    assert row[14] == "1685000"


def test_generation_is_not_complectation():
    row = map_row({"vin": "X", "generation": "I Рестайлинг", "engine": "petrol"})
    assert row[13] == ""


def test_dedup_sort_by_vin():
    rows = [
        ["BBB", "b"],
        ["AAA", "a1"],
        ["AAA", "a2"],
        ["", "skip"],
    ]
    out = dedup_sort(rows)
    assert [r[0] for r in out] == ["AAA", "BBB"]
    assert out[0][1] == "a2"


def test_filter_require_without_publish_field():
    cars = [
        {"vin": "A", "stockState": "in", "saleStatus": "onsale"},
        {"vin": "B", "stockState": "out", "saleStatus": "onsale"},
    ]
    res = filter_stock(cars, mode="require")
    assert res.kept == []
    assert "публикац" in res.reason


def test_filter_in_stock_only():
    cars = [
        {"vin": "A", "stockState": "in", "saleStatus": "onsale"},
        {"vin": "B", "stockState": "in", "saleStatus": "offsale"},
        {"vin": "C", "stockState": "out", "saleStatus": "onsale"},
    ]
    res = filter_stock(cars, mode="in_stock_only")
    assert [c["vin"] for c in res.kept] == ["A"]


def test_filter_published_flag():
    cars = [
        {"vin": "A", "stockState": "in", "saleStatus": "onsale", "published": True},
        {"vin": "B", "stockState": "in", "saleStatus": "onsale", "published": False},
    ]
    res = filter_stock(cars, mode="require")
    assert res.publish_field == "published"
    assert [c["vin"] for c in res.kept] == ["A"]


def test_warehouse_is_in_stock_minus_on_sale():
    cars = [
        {"vin": "A", "stockState": "in", "saleStatus": "onsale", "published": True},
        {"vin": "B", "stockState": "in", "saleStatus": "offsale", "published": False},
        {"vin": "C", "stockState": "in", "saleStatus": "onsale", "published": False},
        {"vin": "D", "stockState": "out", "saleStatus": "offsale", "published": False},
    ]
    res = filter_stock(cars, mode="require")
    assert [c["vin"] for c in res.kept] == ["A"]
    # B снята с продажи, C в продаже но не опубликована — обе на складе есть.
    # D со склада уехала, её в списке быть не должно.
    stored = filter_warehouse(cars, kept=res.kept)
    assert [c["vin"] for c in stored] == ["B", "C"]


def test_guard_empty():
    assert "пустой" in guard_write(0, 55, False, 0.5)
    assert guard_write(0, 55, True, 0.5) == ""


def test_guard_drop():
    assert "обвал" in guard_write(20, 55, False, 0.5)
    assert guard_write(40, 55, False, 0.5) == ""
    assert guard_write(55, None, False, 0.5) == ""
