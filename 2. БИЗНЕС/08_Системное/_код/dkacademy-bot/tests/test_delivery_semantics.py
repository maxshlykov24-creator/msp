"""Unit-тесты классификатора смысловых статусов доставки."""
from __future__ import annotations

from app.delivery_notify_templates import TEMPLATES, template_for
from app.delivery_semantics import (
    ACCEPTED_ORIGIN,
    DELIVERED,
    DELIVERY_FAILED,
    NOTIFY_WHITELIST,
    READY_PICKUP,
    SORTING,
    STORAGE_EXPIRING,
    TO_DEST_CITY,
    WITH_COURIER,
    classify_delivery_event,
    delivery_status_code,
)


def test_delivery_code_prefers_track_status():
    # callback: status=индексация, track_status=доставка
    assert delivery_status_code({"status": "1", "track_status": "0"}) == "0"
    assert delivery_status_code({"status": "0", "track_status": "1"}) == "1"
    # track/v2: только status
    assert delivery_status_code({"status": "1"}) == "1"


def test_code_1_ready_pickup():
    assert classify_delivery_event({"track_status": "1"}) == READY_PICKUP
    assert classify_delivery_event({"status": "1"}) == READY_PICKUP


def test_code_2_delivered():
    assert classify_delivery_event({"track_status": "2"}) == DELIVERED
    assert classify_delivery_event({"status": "2"}) == DELIVERED


def test_code_3_4_silent():
    assert classify_delivery_event({"track_status": "3"}) is None
    assert classify_delivery_event({"track_status": "4"}) is None


def test_alias_delivered_not_confused_with_pvz():
    assert (
        classify_delivery_event(
            {"track_status": "0", "track": [{"text": "Прибыло в место вручения"}]},
        )
        == READY_PICKUP
    )
    assert (
        classify_delivery_event(
            {"track_status": "0", "track": [{"text": "Вручен получателю"}]},
        )
        == DELIVERED
    )


def test_alias_post_ready():
    payload = {
        "track_status": "0",
        "track": [{"text": "Прибыло в место вручения", "operation": "Обработка"}],
    }
    # code 0, но текст ПВЗ — всё равно ready через алиас;
    # если одновременно code=1, приоритет у кода.
    assert classify_delivery_event(payload) == READY_PICKUP


def test_code_1_beats_courier_text():
    payload = {
        "track_status": "1",
        "track": [{"text": "Передано курьеру"}],
    }
    assert classify_delivery_event(payload) == READY_PICKUP


def test_alias_sorting_cdek():
    payload = {
        "status": "0",
        "track": [{"text": "Прибыло в сортировочный центр"}],
    }
    assert classify_delivery_event(payload) == SORTING


def test_alias_to_dest_city():
    payload = {
        "track_status": "0",
        "track": [{"text": "Отправлен в город получателя"}],
    }
    assert classify_delivery_event(payload) == TO_DEST_CITY


def test_alias_courier():
    payload = {
        "track_status": "0",
        "track": [{"text": "Передано курьеру на доставку"}],
    }
    assert classify_delivery_event(payload) == WITH_COURIER


def test_alias_failed():
    payload = {
        "track_status": "0",
        "track": [{"text": "Неудачная попытка вручения"}],
    }
    assert classify_delivery_event(payload) == DELIVERY_FAILED


def test_alias_storage():
    payload = {
        "track_status": "0",
        "track": [{"text": "Истекает срок хранения"}],
    }
    assert classify_delivery_event(payload) == STORAGE_EXPIRING


def test_alias_accepted_via_status_line():
    assert (
        classify_delivery_event(
            {"track_status": "0"},
            status_line="Принят в городе отправителя",
        )
        == ACCEPTED_ORIGIN
    )


def test_unknown_silent():
    assert (
        classify_delivery_event(
            {"track_status": "0", "track": [{"text": "Создан заказ в ИМ"}]},
        )
        is None
    )


def test_templates_cover_whitelist():
    for sid in NOTIFY_WHITELIST:
        assert sid in TEMPLATES
        assert template_for(sid)
    assert "assembled" not in TEMPLATES
