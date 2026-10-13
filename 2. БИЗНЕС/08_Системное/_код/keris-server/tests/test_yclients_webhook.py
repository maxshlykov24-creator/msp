"""Вебхук YCLIENTS → наша БД: запись с карт, перенос в журнале, удаление.

Payload — урезанная копия живого события от платформы (2026-08-07).
"""
from datetime import datetime, timedelta

from app.booking_logic import has_overlap, slots_for_master
from app.models import Addon, Booking, BookingSource, BookingStatus, Master, Service
from app.sync import handle_yclients_webhook, yclients_record_source

YC_STAFF_ALLA = 5824356
YC_SERVICE_HYGIENE_M = 30786306  # "Гигиена (собака)" — размер M (см. yclients_setup.sync_catalog)
YC_ADDON_NAILS = 30800001  # доп с фикс. ценой (не зависит от размера) — ключ "flat"


def test_update_does_not_overwrite_manually_changed_time(monkeypatch):
    from app import yclients_client as yc
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, kwargs.get("json")))
        if method == "GET":
            return {"data": {"datetime": "2026-10-15T14:00:00+03:00", "staff_id": 9,
                             "seance_length": 7200}}
        return {"success": True}
    monkeypatch.setattr(yc, "_request", request)
    yc.update_record(1, {"datetime": "2026-10-15T12:00:00", "staff_id": 7, "seance_length": 5400})
    assert calls[-1][1] == {"datetime": "2026-10-15T14:00:00+03:00", "staff_id": 9,
                           "seance_length": 7200}
    yc.update_record(1, {"datetime": "2026-10-16T13:00:00", "staff_id": 9, "seance_length": 5400},
                     allow_time_change=True)
    assert calls[-1][1]["datetime"] == "2026-10-16T13:00:00"
    assert calls[-1][1]["seance_length"] == 7200


def test_update_stops_when_current_timing_cannot_be_read(monkeypatch):
    import pytest
    from app import yclients_client as yc
    calls = []
    def request(method, path, **kwargs):
        calls.append(method)
        return {"data": {}}
    monkeypatch.setattr(yc, "_request", request)
    with pytest.raises(yc.YClientsError):
        yc.update_record(1, {"seance_length": 5400})
    assert calls == ["GET"]


def test_update_preserves_old_prices_discount_and_quantity(monkeypatch):
    from app import yclients_client as yc
    old_services = [{"id": 1, "cost": 8000, "first_cost": 10000, "discount": 20, "amount": 2},
                    {"id": 2, "cost": 0, "first_cost": 0, "discount": 0, "amount": 1}]
    captured = []
    def request(method, path, **kwargs):
        if method == "GET":
            return {"data": {"datetime": "2026-10-15T14:00:00+03:00", "staff_id": 9,
                             "seance_length": 7200, "services": old_services}}
        captured.append(kwargs["json"])
        return {"success": True}
    monkeypatch.setattr(yc, "_request", request)
    yc.update_record(1, {"datetime": "2026-10-16T12:00:00", "staff_id": 9,
                         "seance_length": 5400, "services": [{"id": 1}, {"id": 2}]}, allow_time_change=True)
    assert captured[0]["services"] == old_services


def test_zero_manual_service_cost_is_not_replaced_by_catalog(db_session):
    from app.sync import _yclients_cost
    assert _yclients_cost({"services": [{"cost_to_pay": 0, "cost": 5000}]}) == 0
    mapped(db_session)
    created = handle_yclients_webhook(db_session, record_event())
    handle_yclients_webhook(db_session, record_event(status="update", services=[
        {"id": YC_SERVICE_HYGIENE_M, "cost": 0, "cost_to_pay": 0}]))
    assert db_session.get(Booking, created["booking_id"]).price == 0


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
        "online": True,
        "bookform_id": 2453598,
        "from_url": "https://yandex.ru/maps",
        "record_from": "\"Keris Club\" новый виджет",
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


def test_journal_record_is_not_maps(db_session):
    """Ручная запись в журнале: нет online, формы и ссылки."""
    mapped(db_session)
    result = handle_yclients_webhook(db_session, record_event(
        record_id=1891402999,
        online=False,
        bookform_id=0,
        from_url="",
        record_from="",
        comment="",
    ))
    assert result["status"] == "created"
    booking = db_session.get(Booking, result["booking_id"])
    assert booking.source == BookingSource.yclients_journal
    assert "журнала" in (booking.admin_note or "")


def test_record_source_signals():
    assert yclients_record_source({"online": True}) == BookingSource.yclients_maps
    assert yclients_record_source({"bookform_id": 12}) == BookingSource.yclients_maps
    assert yclients_record_source({"from_url": "https://2gis.ru"}) == BookingSource.yclients_maps
    assert yclients_record_source({"record_from": "виджет"}) == BookingSource.yclients_maps
    assert yclients_record_source({
        "online": False, "bookform_id": 0, "from_url": "", "record_from": "",
        "created_user_id": 14456148,
    }) == BookingSource.yclients_journal


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


def test_partial_future_no_show_keeps_master_and_releases_slot(db_session, monkeypatch):
    from app import sync
    mapped(db_session)
    future = datetime.now() + timedelta(days=3)
    future = future.replace(hour=12, minute=0, second=0, microsecond=0)
    result = handle_yclients_webhook(db_session, record_event(datetime=future.isoformat(), attendance=0))
    booking = db_session.get(Booking, result["booking_id"])
    before = (booking.master_id, booking.starts_at, booking.ends_at, booking.price)
    monkeypatch.setattr(sync.yclients_client, "get_record", lambda *_: {"data": {}})
    partial = {"resource": "record", "status": "update", "data": {"id": booking.yclients_record_id, "visit_attendance": -1}}
    assert handle_yclients_webhook(db_session, partial)["status"] == "no_show"
    assert (booking.master_id, booking.starts_at, booking.ends_at, booking.price) == before
    assert not has_overlap(db_session, booking.master_id, booking.starts_at, booking.ends_at)
    assert "12:00" in slots_for_master(db_session, db_session.get(Master, booking.master_id), future.date(), 90, now=future-timedelta(days=1))


def test_missed_no_show_webhook_reconciles_without_writes(db_session, monkeypatch):
    import dataclasses
    from app import sync
    mapped(db_session)
    future = datetime.now() + timedelta(days=3)
    result = handle_yclients_webhook(db_session, record_event(datetime=future.isoformat(), attendance=0))
    booking = db_session.get(Booking, result["booking_id"])
    before = (booking.master_id, booking.starts_at, booking.ends_at, booking.price)
    monkeypatch.setattr(sync, "settings", dataclasses.replace(sync.settings, yclients_company_id="2125383", yclients_partner_token="p", yclients_user_token="u"))
    monkeypatch.setattr(sync.yclients_client, "get_record", lambda rid: {"data": {"id": rid, "attendance": -1}})
    monkeypatch.setattr(sync.yclients_client, "update_record", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no PUT")))
    assert sync.refresh_slot_statuses(db_session, force=True)["released"] == 1
    assert booking.status == BookingStatus.no_show
    assert (booking.master_id, booking.starts_at, booking.ends_at, booking.price) == before
    assert not has_overlap(db_session, booking.master_id, booking.starts_at, booking.ends_at)
    assert sync.refresh_slot_statuses(db_session) == {"skipped": True}


def test_hydration_includes_attendance(db_session, monkeypatch):
    from app import sync
    mapped(db_session)
    result = handle_yclients_webhook(db_session, record_event(attendance=0))
    monkeypatch.setattr(sync.yclients_client, "get_record", lambda rid: {"data": {"id": rid, "attendance": -1}})
    assert handle_yclients_webhook(db_session, record_event(status="update", services=[]))["status"] == "no_show"
    assert db_session.get(Booking, result["booking_id"]).master_id == "alla"
