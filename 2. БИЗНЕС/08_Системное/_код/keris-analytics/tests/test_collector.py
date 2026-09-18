from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bookings import is_visit
from app.collector import (
    _kennel_period,
    _load_block,
    _sleeping_count,
    _stale_task,
    is_test_lead,
)
from app.config import settings


def _lead(status: int, created: datetime, name: str = "Заявка", price: int = 0, source=None, updated=None, closed=None):
    fields = []
    if source is not None:
        fields.append({"field_id": settings.field_source, "values": [{"value": source}]})
    payload = {
        "id": abs(hash((name, created.isoformat(), status))) % 10_000_000,
        "name": name,
        "status_id": status,
        "price": price,
        "created_at": int(created.timestamp()),
        "updated_at": int((updated or created).timestamp()),
        "custom_fields_values": fields,
    }
    if closed is not None:
        payload["closed_at"] = int(closed.timestamp())
    return payload


def test_skips_test_leads():
    assert is_test_lead({"name": "[ТЕСТ] Анна — бронь Морошки"})
    assert is_test_lead({"name": "[ТЕСТ бота — можно удалить]"})
    assert not is_test_lead({"name": "Заявка от (Елена "})


def test_cohort_funnel_booked_and_sold():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    start = now.replace(hour=0) - timedelta(days=7)
    created = now - timedelta(days=2)
    leads = [
        _lead(settings.status_new, created, "А"),
        _lead(settings.status_booked, created, "Б"),
        _lead(settings.status_sold, created, "В", price=150000, closed=created),
        _lead(settings.status_new, now - timedelta(days=20), "старая"),
        _lead(settings.status_sold, created, "[ТЕСТ] продано", price=1),
    ]
    live = [l for l in leads if not is_test_lead(l)]
    block = _kennel_period(live, start, now + timedelta(seconds=1), now)
    assert block["leads"] == 3
    assert block["booked"] == 2
    assert block["sold"] == 1
    assert block["sold_sum"] == 150000
    assert block["avg_check"] == 150000
    assert block["show_avg_check"] is True
    assert block["show_waiting"] is False
    assert block["show_source"] is False


def test_sold_uses_closed_at_not_created():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    start = now.replace(hour=0) - timedelta(days=7)
    old = now - timedelta(days=40)
    closed = now - timedelta(days=1)
    leads = [
        _lead(settings.status_sold, old, "старая продажа", price=180000, closed=closed),
    ]
    block = _kennel_period(leads, start, now + timedelta(seconds=1), now)
    assert block["leads"] == 0
    assert block["funnel"][-1]["count"] == 0
    assert block["sold"] == 1
    assert block["sold_sum"] == 180000
    assert block["avg_check"] == 180000


def test_load_counts_hours_and_skips_cancelled():
    start = datetime(2026, 9, 10, 0, 0)
    end = datetime(2026, 9, 11, 0, 0)
    shifts = [{"master_id": "a", "date_iso": "2026-09-10", "starts_at": "10:00", "ends_at": "18:00"}]
    bookings = [
        {
            "master_id": "a",
            "status": "confirmed",
            "starts_at": datetime(2026, 9, 10, 11, 0),
            "ends_at": datetime(2026, 9, 10, 13, 0),
        },
        {
            "master_id": "a",
            "status": "cancelled",
            "starts_at": datetime(2026, 9, 10, 14, 0),
            "ends_at": datetime(2026, 9, 10, 16, 0),
        },
    ]
    block = _load_block(bookings, shifts, start, end, {"a": "Алла"})
    assert block["available_min"] == 8 * 60
    assert block["occupied_min"] == 2 * 60
    assert block["occupied_h"] == 2
    assert block["available_h"] == 8
    assert block["show"] is True
    assert block["masters"][0]["name"] == "Алла"


def test_sleeping_skips_future_and_never_visited():
    now = datetime(2026, 9, 17, 12, 0)
    bookings = [
        {"owner_phone": "+1", "status": "confirmed", "starts_at": now - timedelta(days=70)},
        {"owner_phone": "+2", "status": "confirmed", "starts_at": now - timedelta(days=10)},
        {"owner_phone": "+3", "status": "confirmed", "starts_at": now - timedelta(days=80)},
        {"owner_phone": "+3", "status": "confirmed", "starts_at": now + timedelta(days=1)},
        {"owner_phone": "+4", "status": "cancelled", "starts_at": now - timedelta(days=90)},
    ]
    assert _sleeping_count(bookings, now) == 1


def test_source_hidden_when_empty_shown_when_filled():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    start = now.replace(hour=0) - timedelta(days=30)
    created = now - timedelta(days=1)
    empty = [_lead(settings.status_new, created, "А")]
    block = _kennel_period(empty, start, now, now)
    assert block["show_source"] is False
    filled = [
        _lead(settings.status_new, created, "А", source="Instagram"),
        _lead(settings.status_new, created, "Б", source="Instagram"),
        _lead(settings.status_new, created, "В", source="Сайт"),
    ]
    block = _kennel_period(filled, start, now, now)
    assert block["show_source"] is True
    assert block["top_source"]["name"] == "Instagram"
    assert block["top_source"]["count"] == 2
    assert block["top_source"]["of"] == 3


def test_stale_is_one_summary_card():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    old = now - timedelta(hours=30)
    leads = [_lead(settings.status_new, old - timedelta(days=i), f"Заявка {i}", updated=old) for i in range(5)]
    task = _stale_task(leads, now)
    assert task is not None
    assert task["kind"] == "stale_leads"
    assert "5 заявок" in task["title"] or "5 заявки" in task["title"] or "5 заявка" in task["title"]


def test_is_visit_matches_server():
    now = datetime(2026, 9, 17, 12, 0)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)
    assert is_visit("confirmed", past, now) is True
    assert is_visit("completed", past, now) is True
    assert is_visit("cancelled", past, now) is False
    assert is_visit("no_show", past, now) is False
    assert is_visit("confirmed", future, now) is False
