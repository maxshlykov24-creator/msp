from datetime import date

from app.birthdays import CLIENT, FAIL, GIFT, PET, SUCCESS, WEEK1, WEEK2, deal_name, is_birthday, next_status


def test_booking_wins_from_any_stage():
    today = date(2026, 9, 25)
    for status in (PET, CLIENT, GIFT, WEEK1, WEEK2):
        assert next_status(status, today, today, today, booked=True) == SUCCESS


def test_birthday_stage_fails_after_a_month():
    created = date(2026, 8, 20)
    assert next_status(PET, created, None, date(2026, 9, 19), False) is None
    assert next_status(CLIENT, created, None, date(2026, 9, 20), False) == FAIL


def test_gift_weeks_then_fail_without_booking():
    gift = date(2026, 9, 1)
    assert next_status(GIFT, gift, gift, date(2026, 9, 8), False) == WEEK1
    assert next_status(WEEK1, gift, gift, date(2026, 9, 15), False) == WEEK2
    assert next_status(WEEK2, gift, gift, date(2026, 9, 21), False) is None
    assert next_status(WEEK2, gift, gift, date(2026, 9, 22), False) == FAIL


def test_name_and_leap_day():
    assert deal_name("Помпон", 25, 9, 2020) == "Помпон · 25.09.2020"
    assert deal_name("Анна", 3, 4, None) == "Анна · 03.04"
    assert is_birthday(29, 2, date(2027, 3, 1))
    assert not is_birthday(29, 2, date(2028, 3, 1))
