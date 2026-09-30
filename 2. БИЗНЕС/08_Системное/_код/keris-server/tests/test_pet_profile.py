"""Паспорт питомца: вес с последнего визита, правка заметки и клички."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.main import client_profile, update_client_pet
from app.main import PetUpdateIn
from app.models import Booking, BookingStatus, PetProfile, PetType


PHONE = "+79263097458"


def _booking(db, bid, starts, *, name="Боня", weight=None, comment="", pet_type=PetType.dog):
    row = Booking(
        id=bid, owner_name="Максим", owner_phone=PHONE,
        pet_name=name, pet_type=pet_type, pet_size="XS",
        pet_weight_kg=weight, service_id="dog_hygiene",
        addon_ids=[], free_addon_ids=[], applied_promos=[],
        master_id="svetlana", starts_at=starts, ends_at=starts + timedelta(hours=1),
        price=5500, status=BookingStatus.confirmed, personal_data_consent=True,
        comment=comment,
    )
    db.add(row)
    db.commit()
    return row


def _pet(db, name="Боня"):
    data = client_profile(PHONE, db)
    return next(pet for pet in data["pets"] if pet["name"] == name)


def test_weight_comes_from_latest_past_visit(db_session):
    _booking(db_session, "KERIS-8001", datetime(2026, 8, 1, 11, 0), weight=4, comment="боится фена")
    _booking(db_session, "KERIS-8002", datetime(2026, 9, 20, 11, 0), weight=5.5, comment="")

    pet = _pet(db_session)

    assert pet["weight_kg"] == 5.5
    assert pet["note"] == "боится фена"


def test_client_can_correct_weight_note_and_name(db_session):
    _booking(db_session, "KERIS-8001", datetime(2026, 8, 1, 11, 0), weight=4, comment="боится фена")
    _booking(db_session, "KERIS-8002", datetime(2026, 9, 20, 11, 0), weight=5.5)

    updated = update_client_pet(PetUpdateIn(
        phone=PHONE, pet_name="Боня", pet_type="dog", weight_kg=6.2, note="Стричь коротко",
    ), db_session)
    pet = next(item for item in updated["pets"] if item["name"] == "Боня")
    assert pet["weight_kg"] == 6.2
    assert pet["note"] == "Стричь коротко"

    renamed = update_client_pet(PetUpdateIn(
        phone=PHONE, pet_name="Боня", pet_type="dog", name="Бони",
    ), db_session)
    assert [item["name"] for item in renamed["pets"]] == ["Бони"]
    assert db_session.query(Booking).filter_by(pet_name="Бони").count() == 2

    row = db_session.query(PetProfile).filter_by(name_key="бони").one()
    row.weight_at = datetime(2026, 8, 1)
    db_session.commit()
    assert _pet(db_session, "Бони")["weight_kg"] == 5.5


def test_rename_asks_for_a_real_name_and_rejects_a_duplicate(db_session):
    _booking(db_session, "KERIS-8001", datetime(2026, 8, 1, 11, 0), name="Боня")
    _booking(db_session, "KERIS-8002", datetime(2026, 8, 2, 11, 0), name="Муся")

    with pytest.raises(HTTPException) as short:
        update_client_pet(PetUpdateIn(phone=PHONE, pet_name="Боня", pet_type="dog", name="Я"), db_session)
    assert short.value.status_code == 422

    with pytest.raises(HTTPException) as clash:
        update_client_pet(PetUpdateIn(phone=PHONE, pet_name="Боня", pet_type="dog", name="Муся"), db_session)
    assert clash.value.status_code == 409
