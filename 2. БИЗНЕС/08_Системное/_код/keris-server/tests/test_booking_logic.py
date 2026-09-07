from datetime import date, datetime, timedelta

import pytest

from app.booking_logic import (
    BookingError,
    active_subscription,
    booking_horizon_days,
    check_horizon,
    compute_price_and_duration,
    has_overlap,
    resolve_free_addons,
    service_and_addons,
    slots_for_master,
    subscription_charge,
)
from app.models import Booking, BookingStatus, Master, MasterShift, PetType, Subscription
from app.seed import compute_expiry


def test_price_and_duration_base_service(db_session):
    service, addons = service_and_addons(db_session, "dog_complex_cut", [])
    price, duration = compute_price_and_duration(service, addons, "M")
    assert price == 10700
    assert duration == 180


def test_duration_depends_on_size(db_session):
    service, addons = service_and_addons(db_session, "dog_hygiene", [])
    assert compute_price_and_duration(service, addons, "XS") == (5500, 60)
    assert compute_price_and_duration(service, addons, "XL") == (11900, 130)


def test_price_and_duration_with_addon(db_session):
    service, addons = service_and_addons(db_session, "dog_complex_cut", ["dog_mask_hydra"])
    price, duration = compute_price_and_duration(service, addons, "XS")
    assert price == 7800 + 3200
    assert duration == 90 + 15


def test_wait_and_glands_addons(db_session):
    service, addons = service_and_addons(db_session, "dog_complex_cut", ["dog_wait", "dog_glands"])
    price, duration = compute_price_and_duration(service, addons, "M")
    assert price == 10700 + 1000 + 1200
    assert duration == 180 + 60 + 5
    service, addons = service_and_addons(db_session, "cat_hygiene", ["cat_wait", "cat_glands"])
    price, duration = compute_price_and_duration(service, addons, "short_small")
    assert price == 4600 + 1000 + 1200
    assert duration == 50 + 60 + 5


def test_coloring_addon_price_by_size(db_session):
    service, addons = service_and_addons(db_session, "dog_complex_cut", ["dog_color_long"])
    price, duration = compute_price_and_duration(service, addons, "L")
    assert price == 16100 + 13200
    assert duration == 210 + 130


def test_free_addon_adds_time_but_not_price(db_session):
    service, addons = service_and_addons(db_session, "dog_complex_cut", ["dog_mask_hydra"])
    price, duration = compute_price_and_duration(service, addons, "XS", ["dog_mask_hydra"])
    assert price == 7800
    assert duration == 90 + 15


def test_addon_mismatch_pet_type_rejected(db_session):
    with pytest.raises(BookingError):
        service_and_addons(db_session, "cat_hygiene", ["dog_nails"])


def test_horizon_enforced(db_session):
    today = date(2026, 8, 6)
    check_horizon(today + timedelta(days=60), today=today)
    with pytest.raises(BookingError) as e:
        check_horizon(today + timedelta(days=61), today=today)
    assert e.value.code == "horizon_exceeded"
    with pytest.raises(BookingError):
        check_horizon(today - timedelta(days=1), today=today)


def test_horizon_follows_yclients_shifts(db_session):
    today = date(2026, 8, 27)
    db_session.add(MasterShift(
        master_id="svetlana", date_iso="2026-09-17", starts_at="10:00", ends_at="22:00",
    ))
    db_session.commit()
    assert booking_horizon_days(db_session, today) == 21
    check_horizon(date(2026, 9, 17), today=today, db=db_session)
    with pytest.raises(BookingError) as e:
        check_horizon(date(2026, 9, 18), today=today, db=db_session)
    assert e.value.code == "horizon_exceeded"


def test_slots_respect_min_lead_time(db_session):
    master = db_session.get(Master, "svetlana")
    now = datetime(2026, 8, 10, 9, 0)  # 10.08 — рабочий день Светланы по циклу 4/3
    # лид 2 ч: утренние слоты до 11:00 уже нельзя, с 11:00 — можно
    same = slots_for_master(db_session, master, date(2026, 8, 10), duration_min=90, now=now)
    assert "10:00" not in same and "10:30" not in same
    assert "11:00" in same
    slots = slots_for_master(db_session, master, date(2026, 8, 11), duration_min=90, now=now)
    assert "10:00" in slots
    assert "20:30" in slots  # салон работает до 22:00
    assert "21:00" not in slots  # 90 мин уже не влезают


def test_slots_exclude_busy_interval(db_session):
    master = db_session.get(Master, "svetlana")
    day = date(2026, 8, 12)  # рабочий день Светланы
    booking = Booking(
        id="KERIS-TEST-1",
        owner_name="Тест", owner_phone="+79990000000",
        pet_type=PetType.dog, pet_size="M",
        service_id="dog_complex_cut", addon_ids=[], master_id="svetlana",
        starts_at=datetime(2026, 8, 12, 12, 0), ends_at=datetime(2026, 8, 12, 14, 30),
        price=10700, status=BookingStatus.confirmed, personal_data_consent=True,
    )
    db_session.add(booking)
    db_session.commit()

    now = datetime(2026, 8, 10, 8, 0)
    slots = slots_for_master(db_session, master, day, duration_min=90, now=now)
    assert "12:00" not in slots
    assert "13:00" not in slots
    assert "14:30" in slots


def test_has_overlap_detects_conflict(db_session):
    booking = Booking(
        id="KERIS-TEST-2",
        owner_name="Тест", owner_phone="+79990000000",
        pet_type=PetType.dog, pet_size="M",
        service_id="dog_complex_cut", addon_ids=[], master_id="alla",
        starts_at=datetime(2026, 8, 3, 12, 0), ends_at=datetime(2026, 8, 3, 14, 30),
        price=10700, status=BookingStatus.confirmed, personal_data_consent=True,
    )
    db_session.add(booking)
    db_session.commit()

    assert has_overlap(db_session, "alla", datetime(2026, 8, 3, 13, 0), datetime(2026, 8, 3, 14, 0))
    assert not has_overlap(db_session, "alla", datetime(2026, 8, 3, 14, 30), datetime(2026, 8, 3, 15, 30))
    assert not has_overlap(db_session, "alla", datetime(2026, 8, 3, 12, 0), datetime(2026, 8, 3, 14, 30),
                            exclude_booking_id="KERIS-TEST-2")


# --- промо старта --------------------------------------------------------

def test_welcome_gift_disabled_for_new_client(db_session):
    service, _ = service_and_addons(db_session, "dog_complex_cut", [])
    free, applied = resolve_free_addons(db_session, "+79990000001", service)
    assert "dog_mask_hydra" not in free
    assert "dog_nails" not in free and "dog_ears" not in free
    assert not any("Welcome" in a for a in applied)


def test_welcome_gift_not_repeated(db_session):
    db_session.add(Booking(
        id="KERIS-TEST-3",
        owner_name="Тест", owner_phone="+79990000002",
        pet_type=PetType.dog, pet_size="M",
        service_id="dog_complex_cut", addon_ids=[], master_id="svetlana",
        starts_at=datetime(2026, 8, 1, 12, 0), ends_at=datetime(2026, 8, 1, 14, 0),
        price=10700, status=BookingStatus.completed, personal_data_consent=True,
        created_at=datetime(2026, 8, 1, 10, 0),
    ))
    db_session.commit()
    service, _ = service_and_addons(db_session, "dog_complex_cut", [])
    free, _ = resolve_free_addons(db_session, "+79990000002", service, now=datetime(2026, 8, 10))
    assert "dog_mask_hydra" not in free
    assert "dog_nails" not in free and "dog_ears" not in free

    free_later, _ = resolve_free_addons(db_session, "+79990000002", service, now=datetime(2026, 10, 1))
    assert free_later == []


def test_no_promo_for_bath_only(db_session):
    db_session.add(Booking(
        id="KERIS-TEST-4",
        owner_name="Тест", owner_phone="+79990000003",
        pet_type=PetType.dog, pet_size="M",
        service_id="dog_complex_cut", addon_ids=[], master_id="svetlana",
        starts_at=datetime(2026, 8, 1, 12, 0), ends_at=datetime(2026, 8, 1, 14, 0),
        price=10700, status=BookingStatus.completed, personal_data_consent=True,
        created_at=datetime(2026, 8, 1, 10, 0),
    ))
    db_session.commit()
    service, _ = service_and_addons(db_session, "dog_bath_dry", [])
    free, _ = resolve_free_addons(db_session, "+79990000003", service, now=datetime(2026, 8, 5))
    assert free == []


# --- абонементы ---------------------------------------------------------

def _make_sub(db_session, phone="+79990000010", plan_id="know", size="M", used=0.0, months=4):
    sub = Subscription(
        plan_id=plan_id, owner_phone=phone, size=size,
        visits_total=3.0, visits_used=used,
        expires_at=compute_expiry(months, now=datetime(2026, 8, 1)),
        purchased_at=datetime(2026, 8, 1),
    )
    db_session.add(sub)
    db_session.commit()
    return sub


def test_subscription_charge_by_coefficient(db_session):
    sub = _make_sub(db_session)
    service, _ = service_and_addons(db_session, "dog_spa", [])
    charged, top_up = subscription_charge(db_session, sub, service, now=datetime(2026, 8, 10))
    assert charged == 1.6
    assert top_up == 0


def test_subscription_top_up_when_balance_short(db_session):
    sub = _make_sub(db_session, phone="+79990000011", used=2.0)  # остаток 1.0
    service, _ = service_and_addons(db_session, "dog_spa", [])
    charged, top_up = subscription_charge(db_session, sub, service, now=datetime(2026, 8, 10))
    assert charged == 1.0
    # цена визита по сетке ПАКЕТА: 27600 / 3 = 9200; доплата за 0.6 визита
    assert top_up == round(0.6 * 9200)


def test_subscription_blocked_below_min_balance(db_session):
    sub = _make_sub(db_session, phone="+79990000012", used=2.7)  # остаток 0.3
    service, _ = service_and_addons(db_session, "dog_bath_dry", [])
    with pytest.raises(BookingError) as e:
        subscription_charge(db_session, sub, service, now=datetime(2026, 8, 10))
    assert e.value.code == "subscription_balance_low"
    assert active_subscription(db_session, "+79990000012", now=datetime(2026, 8, 10)) is None


def test_expired_subscription_rejected(db_session):
    sub = _make_sub(db_session, phone="+79990000013")
    service, _ = service_and_addons(db_session, "dog_hygiene", [])
    with pytest.raises(BookingError) as e:
        subscription_charge(db_session, sub, service, now=datetime(2027, 1, 1))
    assert e.value.code == "subscription_expired"


def test_subscription_includes_hygiene_addons(db_session):
    sub = _make_sub(db_session, phone="+79990000014")
    service, _ = service_and_addons(db_session, "dog_hygiene", [])
    free, applied = resolve_free_addons(db_session, "+79990000014", service, sub, now=datetime(2026, 9, 15))
    for addon_id in ("dog_nails", "dog_nails_file", "dog_ears", "dog_teeth", "dog_paw_wax", "dog_perfume"):
        assert addon_id in free
    assert any("Абонемент" in a for a in applied)
