from puppy_parse import parse_birth, parse_price


def test_birth_is_day_dot_month_of_this_year():
    assert parse_birth("2.08", year=2026) == ("2026-08-02", "2.08")
    assert parse_birth("24.8", year=2026) == ("2026-08-24", "24.08")
    assert parse_birth("2026-08-02", year=2026) is None
    assert parse_birth("31.02", year=2026) is None


def test_price_accepts_salon_formats_and_rejects_outliers():
    assert parse_price("350 000") == 350_000
    assert parse_price("350.000") == 350_000
    assert parse_price("100 000") == 100_000
    assert parse_price("350") == 350_000
    assert parse_price("800") == 800_000
    assert parse_price("10 000") is None
    assert parse_price("1000000") is None
    assert parse_price("1.000.000") is None
    assert parse_price("999000") == 999_000
