"""Вебхук YCLIENTS → наша БД: запись с карт, перенос в журнале, удаление.

Payload — урезанная копия живого события от платформы (2026-08-07).
"""
from datetime import datetime, timedelta

from app.booking_logic import has_overlap, slots_for_master
from app.models import Addon, Booking, BookingSource, BookingStatus, Master, Service
from app.sync import handle_yclients_webhook

YC_STAFF_ALLA = 5824356
YC_SERVICE_HYGIENE_M = 30786306  # "Гигиена (собака)" — размер M (см. yclients_setup.sync_catalog)
YC_ADDON_NAILS = 30800001  # доп с фикс. ценой (не зависит от размера) — ключ "flat"


def mapped(db):
    """Маппинг проставлен yclients_setup.py --sync-catalog, в сиде его нет — ставим руками.
    Каждый размер — своя услуга YCLIENTS (yclients_service_ids: {размер: yc_id})."""
    db.get(Master, "alla").yclients_staff_id = YC_STAFF_ALLA
    db.get(Service, "dog_hygiene").yclients_service_ids = {"M": YC_SERVICE_HYGIENE_M}
    nails = db.get(Addon, "dog_nails")
    if nails is not None:
        nails.yclients_service_ids = {"flat": YC_ADDON_NAILS}
    db.commit()


def record_event(status: str = "create", record_id: int = 1891402563, **overrides) -> dict:
    data = {
        "id": record_id,
        "company_id": 2125383,
        "staff_id": YC_STAFF_ALLA,
        "date": "2026-08-09 17:00:00",
        "datetime": "2026-08-09T17:00:00+03:00",
        "seance_length": 5400,
        "comment": "запись с Яндекс.Карт",
        "deleted": False,
        "services": [{"id": YC_SERVICE_HYGIENE_M, "title": "Гигиена", "cost": 5500, "cost_to_pay": 5500}],
        "client": {"id": 430559571, "display_name": "Мария", "name": "Мария", "phone": "+79990000781"},
    }
    data.update(overrides)
    return {"company_id": 2125383, "resource": "record", "resource_id": record_id,
            "status": status, "data": data}


def test_record_from_maps_creates_booking(db_session):
    mapped(db_session)
    result = handle_yclients_webhook(db_session, record_event())
    assert result["status"] == "created"

    booking = db_session.get(Booking, result["booking_id"])
    assert booking.source == BookingSource.yclients_maps
    assert booking.status == BookingStatus.confirmed
    assert booking.master_id == "alla"
    assert booking.service_id == "dog_hygiene"
    assert booking.starts_at == datetime(2026, 8, 9, 17, 0)
    assert booking.ends_at == datetime(2026, 8, 9, 18, 30)  # seance_length 5400 сек
    assert booking.owner_phone == "+79990000781"
    assert booking.price == 5500
    assert booking.yclients_record_id == 1891402563
    assert booking.admin_note


def test_same_event_twice_is_idempotent(db_session):
    mapped(db_session)
    handle_yclients_webhook(db_session, record_event())
    again = handle_yclients_webhook(db_session, record_event())
    assert again == {"status": "duplicate_ignored"}
    assert db_session.query(Booking).count() == 1


def test_echo_of_our_own_push_does_not_duplicate(db_session):
    """Наш push создаёт record в YCLIENTS, событие возвращается эхом."""
    mapped(db_session)
    handle_yclients_webhook(db_session, record_event())
    echo = handle_yclients_webhook(db_session, record_event(status="update"))
    assert echo["status"] == "unchanged"
    assert db_session.query(Booking).count() == 1


def test_move_in_yclients_journal_reschedules(db_session):
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    moved = handle_yclients_webhook(
        db_session, record_event(status="update", datetime="2026-08-10T12:00:00+03:00",
                                 date="2026-08-10 12:00:00")
    )
    assert moved == {"status": "updated", "booking_id": created["booking_id"]}
    assert db_session.get(Booking, created["booking_id"]).starts_at == datetime(2026, 8, 10, 12, 0)


def test_delete_in_yclients_cancels_booking(db_session):
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    handle_yclients_webhook(db_session, record_event(status="delete", deleted=True))
    assert db_session.get(Booking, created["booking_id"]).status == BookingStatus.cancelled


def test_no_show_in_journal_frees_the_slot(db_session):
    """Администратор ставит «Не пришел» (attendance=-1) — у нас no_show, слот свободен."""
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    booking = db_session.get(Booking, created["booking_id"])
    day = booking.starts_at.date()
    assert has_overlap(db_session, "alla", booking.starts_at, booking.ends_at)

    result = handle_yclients_webhook(db_session, record_event(status="update", attendance=-1))
    assert result == {"status": "no_show", "booking_id": booking.id}
    db_session.refresh(booking)
    assert booking.status == BookingStatus.no_show
    assert not has_overlap(db_session, "alla", booking.starts_at, booking.ends_at)
    # слот снова виден в онлайн-записи
    free = slots_for_master(db_session, db_session.get(Master, "alla"), day, 90,
                            now=booking.starts_at - timedelta(days=2))
    assert booking.starts_at.strftime("%H:%M") in free


def test_came_in_journal_completes_booking(db_session):
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    handle_yclients_webhook(db_session, record_event(status="update", attendance=1))
    assert db_session.get(Booking, created["booking_id"]).status == BookingStatus.completed


def test_waiting_attendance_does_not_revive_cancelled(db_session):
    """attendance=0 («Ожидание») не должен снимать отмену — иначе слот снова занят."""
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    handle_yclients_webhook(db_session, record_event(status="delete", deleted=True))
    handle_yclients_webhook(db_session, record_event(status="update", attendance=0))
    assert db_session.get(Booking, created["booking_id"]).status == BookingStatus.cancelled


def test_move_after_no_show_revives_booking(db_session):
    """Запись вернули в журнал переносом — снимаем no_show."""
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    handle_yclients_webhook(db_session, record_event(status="update", attendance=-1))
    handle_yclients_webhook(db_session, record_event(status="update", attendance=-1,
                                                     datetime="2026-08-11T15:00:00+03:00",
                                                     date="2026-08-11 15:00:00"))
    booking = db_session.get(Booking, created["booking_id"])
    assert booking.status == BookingStatus.no_show  # attendance всё ещё «не пришёл»
    assert booking.starts_at == datetime(2026, 8, 11, 15, 0)

    handle_yclients_webhook(db_session, record_event(status="update", attendance=0,
                                                     datetime="2026-08-12T15:00:00+03:00",
                                                     date="2026-08-12 15:00:00"))
    db_session.refresh(booking)
    assert booking.status == BookingStatus.confirmed


def test_unknown_staff_is_logged_not_created(db_session):
    result = handle_yclients_webhook(db_session, record_event())  # маппинг не проставлен
    assert result == {"status": "skipped", "reason": "staff_not_mapped"}
    assert db_session.query(Booking).count() == 0


def test_non_record_resource_only_logged(db_session):
    event = {"company_id": 2125383, "resource": "client", "status": "create",
             "data": {"id": 430559571, "name": "Мария"}}
    assert handle_yclients_webhook(db_session, event) == {"status": "ignored", "resource": "client"}
    assert db_session.query(Booking).count() == 0


def test_record_with_addon_maps_addon_ids(db_session):
    mapped(db_session)
    event = record_event(services=[
        {"id": YC_SERVICE_HYGIENE_M, "title": "Гигиена", "cost": 5500, "cost_to_pay": 5500},
        {"id": YC_ADDON_NAILS, "title": "Стрижка когтей", "cost": 1900, "cost_to_pay": 1900},
    ])
    result = handle_yclients_webhook(db_session, event)
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.service_id == "dog_hygiene"
    assert booking.pet_size == "M"
    assert booking.addon_ids == ["dog_nails"]
    assert booking.price == 7400


def test_record_maps_exact_size_from_yclients_service(db_session):
    """Разные размерные услуги YCLIENTS → у нас точный pet_size, без хардкода 'M'."""
    mapped(db_session)
    db_session.get(Service, "dog_hygiene").yclients_service_ids = {"M": YC_SERVICE_HYGIENE_M, "L": 30786399}
    db_session.commit()
    event = record_event(record_id=1891402999,
                          services=[{"id": 30786399, "title": "Гигиена", "cost": 10200, "cost_to_pay": 10200}])
    result = handle_yclients_webhook(db_session, event)
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.pet_size == "L"
    assert booking.price == 10200


def test_empty_services_are_hydrated_from_api(db_session, monkeypatch):
    """Журнал шлёт create со слотом и клиентом, услуги дописывают следом — без GET бот молчит."""
    mapped(db_session)
    from app import sync as sync_mod

    def fake_get_record(record_id, company_id=None):
        assert record_id == 1891402563
        return {"data": {
            "id": record_id,
            "staff_id": YC_STAFF_ALLA,
            "date": "2026-08-09 17:00:00",
            "datetime": "2026-08-09T17:00:00+03:00",
            "seance_length": 5400,
            "services": [{"id": YC_SERVICE_HYGIENE_M, "title": "Гигиена", "cost": 5500, "cost_to_pay": 5500}],
            "client": {"display_name": "Мария", "name": "Мария", "phone": "+79990000781"},
        }}

    monkeypatch.setattr(sync_mod.yclients_client, "get_record", fake_get_record)
    result = handle_yclients_webhook(db_session, record_event(services=[]))
    assert result["status"] == "created"
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.service_id == "dog_hygiene"
    assert booking.price == 5500


def test_empty_services_without_api_still_create(db_session, monkeypatch):
    """Журнал шлёт слот без услуг, GET тоже пустой — слот всё равно создаём, иначе бот молчит."""
    mapped(db_session)
    from app import sync as sync_mod
    from app.yclients_client import YClientsError

    monkeypatch.setattr(
        sync_mod.yclients_client, "get_record",
        lambda *a, **k: (_ for _ in ()).throw(YClientsError("down")),
    )
    sent = []
    monkeypatch.setattr(sync_mod.notify_admins, "notify", sent.append)
    monkeypatch.setattr(sync_mod.notify_karina, "notify", lambda t: None)
    result = handle_yclients_webhook(db_session, record_event(services=[]))
    assert result["status"] == "created"
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.service_id == "dog_hygiene"
    assert booking.price == 0
    assert "не пришла" in booking.admin_note
    assert sent and "запись в журнале" in sent[0]


def test_update_fills_service_after_empty_create(db_session, monkeypatch):
    mapped(db_session)
    from app import sync as sync_mod

    monkeypatch.setattr(sync_mod.yclients_client, "get_record", lambda *a, **k: {"data": {}})
    created = handle_yclients_webhook(db_session, record_event(services=[]))
    updated = handle_yclients_webhook(db_session, record_event(status="update"))
    assert updated == {"status": "catalog_updated", "booking_id": created["booking_id"]}
    booking = db_session.get(Booking, created["booking_id"])
    assert booking.service_id == "dog_hygiene"
    assert booking.price == 5500


def test_unmapped_service_title_still_creates(db_session, monkeypatch):
    mapped(db_session)
    from app import sync as sync_mod

    sent = []
    monkeypatch.setattr(sync_mod.notify_admins, "notify", sent.append)
    monkeypatch.setattr(sync_mod.notify_karina, "notify", lambda t: None)
    result = handle_yclients_webhook(db_session, record_event(services=[
        {"id": 99999999, "title": "Комплекс со стрижкой", "cost": 7800, "cost_to_pay": 7800},
    ]))
    assert result["status"] == "created"
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.price == 7800
    assert "Комплекс со стрижкой" in booking.admin_note
    assert sent and "Комплекс со стрижкой" in sent[0]


def test_failed_webhook_is_retried(db_session, monkeypatch):
    """INTEGER overflow и прочий сбой INSERT не должны навсегда блокировать событие."""
    mapped(db_session)
    from app import sync as sync_mod

    calls = {"n": 0}
    orig = sync_mod._create_from_yclients

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OverflowError("integer out of range")
        return orig(*args, **kwargs)

    monkeypatch.setattr(sync_mod, "_create_from_yclients", boom)
    first = handle_yclients_webhook(db_session, record_event())
    assert first["status"] == "failed"
    assert db_session.query(Booking).count() == 0

    second = handle_yclients_webhook(db_session, record_event())
    assert second["status"] == "created"
    assert db_session.query(Booking).count() == 1
    from app.models import SyncLog, SyncLogDirection
    log_row = db_session.query(SyncLog).filter_by(
        direction=SyncLogDirection.webhook_from_yclients
    ).one()
    assert log_row.status == "ok"
    assert log_row.booking_id == second["booking_id"]
    assert log_row.attempts == 2
