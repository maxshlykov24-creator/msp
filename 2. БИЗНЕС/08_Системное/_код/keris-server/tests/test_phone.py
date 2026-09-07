from app.booking_logic import extract_phone_from_text, is_canonical_phone, normalize_phone


CANON = "+79991234567"


def test_extract_unifies_common_ru_formats():
    variants = [
        "+7 999 123-45-67",
        "+7 (999) 123 45 67",
        "8 (999) 123-45-67",
        "8-999-123-45-67",
        "89991234567",
        "79991234567",
        "9991234567",
        "мой номер 8 999 123-45-67, спасибо",
        "тел: +7.999.123.45.67",
    ]
    got = [extract_phone_from_text(v) for v in variants]
    assert got == [CANON] * len(variants)
    assert len(set(got)) == 1


def test_extract_rejects_garbage():
    assert extract_phone_from_text("") == ""
    assert extract_phone_from_text("123") == ""
    assert extract_phone_from_text("нет номера") == ""
    assert extract_phone_from_text("12345") == ""


def test_normalize_still_returns_raw_on_garbage():
    # старый контракт: вызывающий код (не extract) сам решает
    assert normalize_phone("123") == "123"
    assert not is_canonical_phone(normalize_phone("123"))
