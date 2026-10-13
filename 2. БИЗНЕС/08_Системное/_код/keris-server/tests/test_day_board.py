from datetime import date, datetime

from app.day_board import prev_week_range, week_range


def test_actual_payments_goods_refunds_and_future_advances(db_session):
    from datetime import timedelta
    from app.models import Booking, BookingStatus, PetType
    from app.day_board import refresh_finance, period_stats
    now = datetime(2026, 10, 13, 18)
    for rid, status, at in [(1, BookingStatus.completed, now-timedelta(days=2)),
                            (2, BookingStatus.no_show, now-timedelta(days=1)),
                            (3, BookingStatus.confirmed, now+timedelta(days=1)),
                            (4, BookingStatus.completed, now-timedelta(days=1))]:
        db_session.add(Booking(id=f"test-{rid}", owner_name="Test", owner_phone="+79990000000",
            pet_type=PetType.dog, pet_size="XS", service_id="dog_complex_cut", master_id="svetlana",
            starts_at=at, ends_at=at+timedelta(hours=2), price=10000, status=status, yclients_record_id=rid))
    db_session.commit()
    source = [
        {"id": 10, "date": "2026-10-13T10:00:00+0300", "amount": 3000, "sold_item_type": "service", "record_id": 1},
        {"id": 11, "date": "2026-10-13T11:00:00+0300", "amount": 2000, "sold_item_type": "service", "record_id": 1},
        {"id": 12, "date": "2026-10-13T12:00:00+0300", "amount": -500, "sold_item_type": "service", "record_id": 1},
        {"id": 13, "date": "2026-10-13T12:00:00+0300", "amount": 750.50, "sold_item_type": "goods_transaction", "record_id": 99, "visit_id": 0},
        {"id": 14, "date": "2026-10-13T12:00:00+0300", "amount": 9999, "sold_item_type": "service", "record_id": 2},
        {"id": 15, "date": "2026-10-13T12:00:00+0300", "amount": 1000, "sold_item_type": "service", "record_id": 3},
    ]
    assert refresh_finance(db_session, transactions=source, now=now)["ok"]
    stats = period_stats(db_session, now.date(), now.date()+timedelta(days=1), now=now)
    assert stats["revenue_services"] == 4500
    assert stats["revenue_goods"] == 750.5
    assert stats["revenue"] == 5250.5
    assert stats["prepayments"] == 1000
    assert stats["paid_visits"] == 1
    assert stats["visits"] == 0  # Оплата сегодня за визит позавчера; будущий визит не считается.
    # Удаление оплаты/возврат в YCLIENTS корректирует сумму; повторная сверка не дублирует.
    assert refresh_finance(db_session, transactions=source[1:], now=now)["ok"]
    stats = period_stats(db_session, now.date(), now.date()+timedelta(days=1), now=now)
    assert stats["revenue_services"] == 1500


def test_duplicate_finance_pages_preserve_last_complete_snapshot(db_session, monkeypatch):
    import dataclasses
    from app import day_board, yclients_client
    from app.config import settings
    from app.models import FinanceSnapshot
    monkeypatch.setattr("app.config.settings", dataclasses.replace(settings,
        yclients_partner_token="t", yclients_user_token="u", yclients_company_id="test"))
    moment = datetime(2026, 10, 13, 18)
    tx = {"id": 1, "date": "2026-10-13T12:00:00+0300", "amount": 100,
          "sold_item_type": "goods_transaction", "record_id": 0}
    assert day_board.refresh_finance(db_session, transactions=[tx], now=moment)["ok"]
    monkeypatch.setattr(yclients_client, "_request", lambda *a, **kw: {"success": True, "data": [tx]})
    assert not day_board.refresh_finance(db_session, now=moment)["ok"]
    saved = db_session.get(FinanceSnapshot, "test")
    assert saved.data["transactions"][0]["amount_kop"] == 10000


def test_goods_only_visit_uses_yclients_attendance_metadata(db_session):
    from app.day_board import finance_days
    tx = [{"id": 1, "paid_at": "2026-10-13T12:00:00", "amount_kop": 1170000,
           "kind": "goods_transaction", "record_id": 99}]
    meta = {"99": {"starts_at": "2026-10-13T11:00:00", "status": "completed"}}
    assert finance_days(db_session, tx, now=datetime(2026, 10, 13, 18), record_meta=meta)[0]["goods_kop"] == 1170000
    meta["99"]["status"] = "no_show"
    assert finance_days(db_session, tx, now=datetime(2026, 10, 13, 18), record_meta=meta)[0]["goods_kop"] == 0


def test_week_runs_monday_through_sunday():
    wednesday = datetime(2026, 9, 30, 11, 0)
    assert week_range(wednesday) == (date(2026, 9, 28), date(2026, 10, 5))
    assert prev_week_range(wednesday) == (date(2026, 9, 21), date(2026, 9, 28))

    monday = datetime(2026, 9, 28, 0, 1)
    assert week_range(monday) == (date(2026, 9, 28), date(2026, 10, 5))

    sunday = datetime(2026, 10, 4, 23, 0)
    assert week_range(sunday) == (date(2026, 9, 28), date(2026, 10, 5))
    assert prev_week_range(sunday) == (date(2026, 9, 21), date(2026, 9, 28))
