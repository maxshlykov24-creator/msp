"""Доп. поля записи YCLIENTS (кличка/порода/вес/дата рождения питомца, с 2026-08-17).

Коды полей ("Ключ для API") — настройка (`settings.yclients_cf_pet_*`), пустые по
умолчанию. Пока код не задан — соответствующее поле не читается и не пишется.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from types import SimpleNamespace

from app.config import settings
from app.models import Addon, Booking, BookingSource, BookingStatus, Master, PetType, Service
from app.sync import handle_yclients_webhook, selected_addon_yclients_ids
from app.yclients_client import (
    booking_to_yclients_payload,
    extract_custom_fields,
    pet_custom_fields,
)

CODES = dict(
    yclients_cf_pet_name="pet_name_code",
    yclients_cf_pet_breed="pet_breed_code",
    yclients_cf_pet_weight="pet_weight_code",
    yclients_cf_pet_birth_date="pet_birth_date_code",
)


def _with_codes(monkeypatch, **overrides):
    patched = dataclasses.replace(settings, **{**CODES, **overrides})
    monkeypatch.setattr("app.sync.settings", patched)
    monkeypatch.setattr("app.yclients_client.settings", patched)
    return patched


def _booking(**kwargs):
    base = dict(
        id="KERIS-2001",
        pet_name="Боня",
        pet_breed="мальтипу",
        pet_weight_kg=3.2,
        pet_birth_date="2024-05-01",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# --- pet_custom_fields (исходящий push) -------------------------------------------------


def test_pet_custom_fields_uses_configured_codes(monkeypatch):
    _with_codes(monkeypatch)
    fields = pet_custom_fields(_booking())
    assert fields == {
        "pet_name_code": "Боня",
        "pet_breed_code": "мальтипу",
        "pet_weight_code": 3.2,
        "pet_birth_date_code": "2024-05-01",
    }


def test_pet_custom_fields_skips_field_without_code(monkeypatch):
    _with_codes(monkeypatch, yclients_cf_pet_birth_date="")
    fields = pet_custom_fields(_booking())
    assert "pet_birth_date_code" not in fields
    assert set(fields) == {"pet_name_code", "pet_breed_code", "pet_weight_code"}


def test_pet_custom_fields_skips_empty_values(monkeypatch):
    _with_codes(monkeypatch)
    fields = pet_custom_fields(_booking(pet_name="", pet_breed="", pet_weight_kg=None, pet_birth_date=""))
    assert fields == {}


def test_pet_custom_fields_disabled_by_default():
    # settings реальные, коды не заданы в тестовом окружении — фича молчит.
    fields = pet_custom_fields(_booking())
    assert fields == {}


def test_booking_to_yclients_payload_includes_custom_fields_when_configured(monkeypatch):
    _with_codes(monkeypatch)
    booking = _booking(
        owner_name="Мария",
        owner_phone="+79990000781",
        starts_at=__import__("datetime").datetime(2026, 8, 20, 12, 0),
        ends_at=__import__("datetime").datetime(2026, 8, 20, 13, 30),
        comment="",
        media_consent=True,
        subscription_id=None,
        visits_charged=None,
    )
    payload = booking_to_yclients_payload(booking, master_yclients_staff_id=1, service_yclients_id=2)
    assert payload["custom_fields"] == {
        "pet_name_code": "Боня",
        "pet_breed_code": "мальтипу",
        "pet_weight_code": 3.2,
        "pet_birth_date_code": "2024-05-01",
    }


def test_booking_to_yclients_payload_omits_custom_fields_when_not_configured():
    booking = _booking(
        owner_name="Мария",
        owner_phone="+79990000781",
        starts_at=__import__("datetime").datetime(2026, 8, 20, 12, 0),
        ends_at=__import__("datetime").datetime(2026, 8, 20, 13, 30),
        comment="",
        media_consent=True,
        subscription_id=None,
        visits_charged=None,
    )
    payload = booking_to_yclients_payload(booking, master_yclients_staff_id=1, service_yclients_id=2)
    assert "custom_fields" not in payload


# --- extract_custom_fields (разбор входящего вебхука/GET record) ------------------------


def test_extract_custom_fields_dict_form():
    assert extract_custom_fields({"custom_fields": {"a": "1"}}) == {"a": "1"}


def test_extract_custom_fields_list_form():
    data = {"custom_fields": [{"code": "a", "value": "1"}, {"code": "b", "value": 2}]}
    assert extract_custom_fields(data) == {"a": "1", "b": 2}


def test_extract_custom_fields_empty():
    assert extract_custom_fields({"custom_fields": []}) == {}
    assert extract_custom_fields({}) == {}


# --- webhook: доп. поля из YCLIENTS попадают в питомца -----------------------------------

YC_STAFF_ALLA = 5824356
YC_SERVICE_HYGIENE_M = 30786306


def _mapped(db):
    db.get(Master, "alla").yclients_staff_id = YC_STAFF_ALLA
    db.get(Service, "dog_hygiene").yclients_service_ids = {"M": YC_SERVICE_HYGIENE_M}
    db.commit()


def _record_event(status: str = "create", record_id: int = 1900000001, **overrides) -> dict:
    data = {
        "id": record_id,
        "company_id": 2125383,
        "staff_id": YC_STAFF_ALLA,
        "date": "2026-08-20 12:00:00",
        "datetime": "2026-08-20T12:00:00+03:00",
        "seance_length": 5400,
        "comment": "запись из журнала",
        "deleted": False,
        "services": [{"id": YC_SERVICE_HYGIENE_M, "title": "Гигиена", "cost": 5500, "cost_to_pay": 5500}],
        "client": {"id": 1, "display_name": "Мария", "name": "Мария", "phone": "+79990000781"},
    }
    data.update(overrides)
    return {"company_id": 2125383, "resource": "record", "resource_id": record_id,
            "status": status, "data": data}


def test_webhook_create_fills_pet_fields_from_custom_fields(db_session, monkeypatch):
    _with_codes(monkeypatch)
    _mapped(db_session)
    event = _record_event(custom_fields={
        "pet_name_code": "Моня",
        "pet_breed_code": "мальтипу",
        "pet_weight_code": "3.5",
        "pet_birth_date_code": "2024-05-01",
    })
    result = handle_yclients_webhook(db_session, event)
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.pet_name == "Моня"
    assert booking.pet_breed == "мальтипу"
    assert booking.pet_weight_kg == 3.5
    assert booking.pet_birth_date == "2024-05-01"
    # кличка и порода уже известны — в заметке администратору их не просим уточнить
    assert "кличку" not in booking.admin_note
    assert "породу" not in booking.admin_note
    assert "согласия ПДн/фото" in booking.admin_note


def test_webhook_create_without_custom_fields_still_asks_admin(db_session, monkeypatch):
    _with_codes(monkeypatch)
    _mapped(db_session)
    result = handle_yclients_webhook(db_session, _record_event())
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.pet_name == ""
    assert "кличку" in booking.admin_note
    assert "породу" in booking.admin_note


def test_webhook_update_fills_pet_fields_added_later(db_session, monkeypatch):
    _with_codes(monkeypatch)
    _mapped(db_session)
    created = handle_yclients_webhook(db_session, _record_event())
    booking = db_session.get(Booking, created["booking_id"])
    assert booking.pet_name == ""

    updated = handle_yclients_webhook(db_session, _record_event(
        status="update",
        custom_fields={"pet_name_code": "Моня", "pet_breed_code": "мальтипу"},
    ))
    assert updated == {"status": "catalog_updated", "booking_id": created["booking_id"]}
    db_session.refresh(booking)
    assert booking.pet_name == "Моня"
    assert booking.pet_breed == "мальтипу"


def test_webhook_ignores_custom_fields_without_configured_codes(db_session):
    # коды не заданы (реальные settings) — доп. поля из вебхука не должны падать сервер
    _mapped(db_session)
    result = handle_yclients_webhook(db_session, _record_event(custom_fields={"whatever": "x"}))
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.pet_name == ""


def test_selected_addon_yclients_ids_ignores_welcome_gift(db_session):
    hydra = db_session.get(Addon, "dog_mask_hydra")
    hydra.yclients_service_ids = {"flat": 30812934}
    db_session.commit()
    booking = Booking(
        id="KERIS-2099",
        owner_name="Тест", owner_phone="+79990000299",
        pet_type=PetType.dog, pet_size="XS", service_id="dog_express_shed",
        addon_ids=[], free_addon_ids=["dog_mask_hydra"],
        master_id="svetlana",
        starts_at=datetime(2026, 8, 20, 20, 30),
        ends_at=datetime(2026, 8, 20, 21, 40),
        price=6000, status=BookingStatus.confirmed,
    )
    assert selected_addon_yclients_ids(db_session, booking) == []
    booking.addon_ids = ["dog_mask_hydra"]
    assert selected_addon_yclients_ids(db_session, booking) == [30812934]
