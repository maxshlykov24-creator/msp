"""Проверка формулировок алертов, номера и рабочего окна."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.alerts import brief_from_history, compact_thread, format_alert, format_taken, next_ping, pretty_phone, take_keyboard
from bot.nudge import extract_phone, is_complaint, wants_call, wants_person

MSK = ZoneInfo("Europe/Moscow")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 14, hour, minute, tzinfo=MSK)


def test_phone():
    assert extract_phone("мой 8 900 111-22-33") == "79001112233"
    assert extract_phone("пишите на 8 495 089 29 29") == ""
    assert pretty_phone("79001112233") == "+7 900 111-22-33"


def test_reasons():
    assert wants_call("перезвоните мне")
    assert wants_person("дайте живого человека")
    assert is_complaint("это развод")


def test_alert_text():
    text = format_alert(
        {
            "wait": "call",
            "name": "Иван",
            "phone": "79001112233",
            "car": "BMW X6 2024",
            "channel": "Авито",
            "reason": "phone",
            "brief": "В наличии на Крылатской, показ после 16. ДТП не было, кузов не чинили.",
            "lead_url": "https://divomotors.amocrm.ru/leads/detail/1",
        },
        0,
    )
    assert "Ждёт звонка" in text
    assert "BMW X6 2024" in text
    assert "+7 900 111-22-33" in text
    assert "Авито" in text
    assert "Контекст:" in text
    assert "Диалог:" not in text
    assert "перезвоните" not in text
    assert "Пиши как Никита" not in text
    assert "уже сказали" not in text
    assert "👉" not in text
    follow = format_alert({"wait": "call", "car": "X6", "reason": "phone"}, 15)
    assert "15 мин" in follow
    assert "остывает" in follow
    thread = compact_thread(
        [
            {"role": "user", "content": "привет"},
            {"role": "assistant", "content": "я Никита"},
            {"role": "user", "content": "номер 8900"},
        ]
    )
    assert thread[-1][0] == "Клиент"
    brief = brief_from_history(
        [
            {"role": "user", "content": "СЕКРЕТНАЯ_ФРАЗА_КЛИЕНТА перезвоните"},
            {"role": "assistant", "content": "Добрый день, DIVO Motors, Никита, слушаю вас."},
            {"role": "assistant", "content": "ДТП не было, кузов не чинили."},
            {"role": "assistant", "content": "Машина в наличии на Крылатской, могу показать сегодня после 16."},
        ],
        "phone",
    )
    assert "СЕКРЕТНАЯ" not in brief
    assert "ДТП не было" in brief
    assert "Крылатской" in brief
    assert "уже сказали" not in brief
    assert "оставил номер" not in brief
    assert "слушаю вас" not in brief.lower()
    keys = take_keyboard("abcd1234", "call")
    assert keys["inline_keyboard"][0][0]["callback_data"] == "take:abcd1234"
    taken = format_taken({"wait": "call", "car": "X6", "reason": "phone"}, "Максим", "14:51")
    assert "Связались" in taken
    assert "Максим" in taken
    assert "14:51" in taken


def test_pings():
    started = at(14, 0)
    assert next_ping(started, [], now=at(14, 0)) == 0
    assert next_ping(started, [0], now=at(14, 4)) is None
    assert next_ping(started, [0], now=at(14, 5)) == 5
    assert next_ping(started, [0, 5], now=at(14, 10)) == 10
    assert next_ping(started, [0, 5, 10], now=at(14, 15)) == 15
    assert next_ping(started, [0, 5, 10, 15], now=at(14, 20)) is None
    assert next_ping(started, [], now=at(21, 0)) is None
    late = at(19, 58)
    assert next_ping(late, [0], now=at(20, 3)) is None
    morning = datetime(2026, 9, 15, 10, 5, tzinfo=MSK)
    assert next_ping(late, [0], now=morning) == 5


if __name__ == "__main__":
    test_phone()
    test_reasons()
    test_alert_text()
    test_pings()
    print("ok")
