from app.config import settings
from app.identity import (
    find_contacts_by_phone,
    find_contacts_by_secondary,
    normalize_name,
    normalize_phone,
    phone_query_variants,
    same_person_name,
    secondary_keys,
)


def test_normalize_us_variants():
    assert normalize_phone("+1 (555) 123-4567") == "+15551234567"
    assert normalize_phone("5551234567") == "+15551234567"
    assert normalize_phone("15551234567") == "+15551234567"
    assert normalize_phone("p:+15551234567") == "+15551234567"


def test_normalize_unknown_returns_empty():
    assert normalize_phone("") == ""
    assert normalize_phone("instagram_nick") == ""
    assert normalize_phone(None) == ""


def test_normalize_rejects_stub_numbers():
    """У авто-контактов Wazzup в телефоне лежит один код страны — не ключ склейки."""
    assert normalize_phone("+1") == ""
    assert normalize_phone("+7") == ""
    assert normalize_phone("12345") == ""
    assert normalize_phone("+380671234567") == "+380671234567"


def test_normalize_intl_without_plus():
    """CallerID из Telnyx приходит без «+» — такой звонок не должен теряться."""
    assert normalize_phone("79689306832") == "+79689306832"
    assert normalize_phone("380671234567") == "+380671234567"
    assert normalize_phone("1234567890123456") == ""


def test_phone_query_variants_start_with_national():
    assert phone_query_variants("+17473361387") == ["7473361387", "17473361387", "+17473361387"]
    assert phone_query_variants("") == []


def test_find_by_phone_across_storage_formats(fake):
    """Боевой случай: один номер записан как 10 цифр, другой — с кодом страны."""
    a = fake.add_contact(name="Евгений", phone="7473361387")
    b = fake.add_contact(name="Евгений Власов", phone="+17473361387")
    found = sorted(c["id"] for c in find_contacts_by_phone(fake, "+17473361387"))
    assert found == sorted([a, b])


def test_find_by_phone_ignores_other_numbers(fake):
    fake.add_contact(name="Чужой", phone="+15550000000")
    target = fake.add_contact(name="Наш", phone="5551234567")
    found = [c["id"] for c in find_contacts_by_phone(fake, "+15551234567")]
    assert found == [target]


def test_normalize_name_transliterates():
    assert normalize_name("  Евгений  Коека ") == "evgeniy koeka"
    assert normalize_name("Evgeniy Koeka!") == "evgeniy koeka"


def test_same_person_across_alphabets_and_typos():
    """Все три пары — боевые: из-за них дубли не склеивались."""
    assert same_person_name("Евгений Коека", "Evgeniy Koeka")
    assert same_person_name("Eugene Smirnov", "Eugene Smirnoff")
    assert same_person_name("Ahad Gafarov", "Abduahad Gaforov")
    assert same_person_name("Valentyn Mihlei", "Valentyn")
    assert same_person_name("Иван Петров", "Петров Иван")
    # безымянная авто-карточка Wazzup не должна блокировать склейку
    assert same_person_name("", "Evgeniy Koeka")


def test_different_people_stay_different():
    assert not same_person_name("Bitcoin", "Руслан Атнагулов")
    assert not same_person_name("Ivan Petrov", "Petr Sidorov")


def test_messenger_ids_are_secondary_keys(fake):
    """Контакт из мессенджера без телефона ищется по id Wazzup."""
    cid = fake.add_contact(name="nick", cfs=[
        {"field_id": settings.field_wz_telegram_id, "values": [{"value": "tg-777"}]},
    ])
    keys = secondary_keys(fake.contacts[cid])
    assert keys["wz_telegram_id"] == "tg-777"
    found = find_contacts_by_secondary(fake, "wz_telegram_id", "tg-777")
    assert [c["id"] for c in found] == [cid]
