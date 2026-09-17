from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bookings import is_visit
from app.collector import _kennel_period, _stale_task, is_test_lead
from app.config import settings


def _lead(status: int, created: datetime, name: str = "Заявка", price: int = 0, source=None, updated=None):
    fields = []
    if source is not None:
        fields.append({"field_id": settings.field_source, "values": [{"value": source}]})
    return {
        "id": abs(hash((name, created.isoformat(), status))) % 10_000_000,
        "name": name,
        "status_id": status,
        "price": price,
        "created_at": int(created.timestamp()),
        "updated_at": int((updated or created).timestamp()),
        "custom_fields_values": fields,
    }


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
        _lead(settings.status_sold, created, "В", price=150000),
        _lead(settings.status_new, now - timedelta(days=20), "старая"),
        _lead(settings.status_sold, created, "[ТЕСТ] продано", price=1),
    ]
    live = [l for l in leads if not is_test_lead(l)]
    block = _kennel_period(live, start, now + timedelta(seconds=1), now)
    assert block["leads"] == 3
    assert block["booked"] == 2
    assert block["sold"] == 1
    assert block["sold_sum"] == 150000
    assert block["show_waiting"] is False
    assert block["show_source"] is False


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
