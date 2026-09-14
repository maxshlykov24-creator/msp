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
            "brief": "Про ДТП и кузов, хочет на осмотр. ДТП не было, кузов не чинили. Показ сегодня после 16, Автозаводская 18.",
            "lead_url": "https://divomotors.amocrm.ru/leads/detail/1",
        },
        0,
    )
    assert "Ждёт звонка" in text
    assert "BMW X6 2024" in text
    assert "+7 900 111-22-33" in text
    assert "Авито" in text
    assert "Контекст:" in text
    assert "Клиент:" not in text.split("Контекст:")[-1]
    assert "Никита:" not in text
    assert "ДТП не было" in text
    assert "Автозаводская 18" in text
    assert "Диалог:" not in text
    assert "перезвоните" not in text
    assert "Пиши как Никита" not in text
    assert "уже сказали" not in text
    assert "👉" not in text
    assert "толщиномер" not in text
    assert "микрон" not in text
    follow = format_alert({"wait": "call", "car": "X6", "reason": "phone"}, 15)
    assert "15 мин" in follow
    assert "остывает" in follow
    hour = format_alert({"wait": "chat", "car": "X6", "reason": "handoff"}, 60)
    assert "1 час" in hour
    assert "висит" in hour
    ping5 = format_alert(
        {
            "wait": "call",
            "name": "Иван",
            "phone": "79001112233",
            "car": "BMW X6 2024",
            "channel": "Авито",
            "reason": "phone",
            "brief": "Про ДТП и кузов, хочет на осмотр. ДТП не было, кузов не чинили. Показ сегодня после 16, Автозаводская 18.",
            "lead_url": "https://divomotors.amocrm.ru/leads/detail/1",
        },
        5,
    )
    assert "5 мин" in ping5
    assert "всё ещё ждёт звонка" in ping5
    assert "+7 900 111-22-33" in ping5
    assert "ДТП не было" in ping5
    assert "Автозаводская 18" in ping5
    assert ping5.splitlines()[0] != text.splitlines()[0]
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
            {"role": "user", "content": "битая была?"},
            {"role": "assistant", "content": "Добрый день, DIVO Motors, Никита, слушаю вас."},
            {"role": "assistant", "content": "ДТП не было, кузов не чинили."},
            {"role": "user", "content": "когда можно посмотреть?"},
            {
                "role": "assistant",
                "content": "Могу показать сегодня после 16, Автозаводская 18.",
            },
            {
                "role": "assistant",
                "content": "По микронам снимем видео толщиномером и пришлём в WhatsApp. Живой менеджер нужен для точных цифр.",
            },
        ],
        "phone",
    )
    assert "Клиент:" not in brief
    assert "Никита:" not in brief
    assert "про ДТП" in brief.lower() or "ДТП не было" in brief
    assert "осмотр" in brief.lower() or "Автозаводская" in brief
    assert "ДТП не было" in brief
    assert "Автозаводская" in brief
    assert "толщиномер" not in brief
    assert "микрон" not in brief.lower()
    assert "WhatsApp" not in brief
    assert "живой менеджер" not in brief.lower()
    assert "Крылатской" not in brief
    assert "уже сказали" not in brief
    assert "слушаю вас" not in brief.lower()
    credit = brief_from_history(
        [
            {"role": "user", "content": "в кредит продаете?"},
            {
                "role": "assistant",
                "content": "Эти автомобили продаём за наличный расчёт. Напишите контактный телефон, обсудим по звонку.",
            },
            {"role": "user", "content": "нет всей суммы, хочу в кредит"},
        ],
        "phone",
    )
    assert "Клиент:" not in credit
    assert "хочет в кредит" in credit.lower()
    assert "наличный расчёт" in credit
    assert "контактный телефон" not in credit.lower()
    keys = take_keyboard("abcd1234", "call")
    assert keys["inline_keyboard"][0][0]["callback_data"] == "take:abcd1234"
    taken = format_taken({"wait": "call", "car": "X6", "reason": "phone"}, "Максим", "14:51")
    assert "Связались" in taken
    assert "Максим" in taken
    assert "14:51" in taken


def test_prior_thread():
    from bot.avito_loop import PRIOR_GAP_SEC, had_prior_correspondence, is_noise, message_text

    now = 1_800_000_000
    fresh = [
        {"direction": "in", "created": now - 20, "content": {"text": "ещё актуально?"}},
        {"direction": "in", "created": now, "content": {"text": "и цена?"}},
    ]
    assert had_prior_correspondence(fresh, now) is False
    with_out = fresh + [{"direction": "out", "created": now - 5, "content": {"text": "да"}}]
    assert had_prior_correspondence(with_out, now) is True
    old = [
        {"direction": "in", "created": now - PRIOR_GAP_SEC - 10, "content": {"text": "привет"}},
        {"direction": "in", "created": now, "content": {"text": "ну что?"}},
    ]
    assert had_prior_correspondence(old, now) is True
    system_only = [{"type": "system", "direction": "out", "created": now - 9}]
    assert had_prior_correspondence(fresh + system_only, now) is False
    stub = {
        "direction": "in",
        "type": "text",
        "created": now - 9,
        "content": {
            "text": "Сообщение не поддерживается. Пожалуйста, перейдите в Авито мессенджер"
        },
    }
    assert is_noise(stub) is True
    assert message_text(stub) == ""
    assert had_prior_correspondence(fresh + [stub], now) is False


def test_amo_owner():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from amo_client import KNOWN_USERS, match_amo_user

    assert match_amo_user(KNOWN_USERS, tg_id=435207481, first="Максим") == 9490530
    assert match_amo_user(KNOWN_USERS, first="Никита", username="Nikita_Yamenskii") == 13334858
    assert match_amo_user(KNOWN_USERS, first="Evgeniy") == 13180098
    assert match_amo_user(KNOWN_USERS, first="Эльзар") == 13835174
    assert match_amo_user(KNOWN_USERS, first="Кто-то") is None


def test_pings():
    started = at(14, 0)
    assert next_ping(started, [], now=at(14, 0)) == 0
    assert next_ping(started, [0], now=at(14, 4)) is None
    assert next_ping(started, [0], now=at(14, 5)) == 5
    assert next_ping(started, [0, 5], now=at(14, 10)) == 10
    assert next_ping(started, [0, 5, 10], now=at(14, 15)) == 15
    assert next_ping(started, [0, 5, 10, 15], now=at(14, 20)) is None
    assert next_ping(started, [0, 5, 10, 15], now=at(14, 30)) == 30
    assert next_ping(started, [0, 5, 10, 15, 30], now=at(14, 59)) is None
    assert next_ping(started, [0, 5, 10, 15, 30], now=at(15, 0)) == 60
    assert next_ping(started, [0, 5, 10, 15, 30, 60], now=at(16, 0)) is None
    assert next_ping(started, [], now=at(21, 0)) is None
    late = at(19, 58)
    assert next_ping(late, [0], now=at(20, 3)) is None
    morning = datetime(2026, 9, 15, 10, 5, tzinfo=MSK)
    assert next_ping(late, [0], now=morning) == 5


def test_autoru_prior():
    from bot.autoru import is_offer_room, listing_from_offer, source_id
    from bot.autoru_loop import (
        PRIOR_GAP_SEC,
        created_of,
        had_prior_correspondence,
        is_out,
        message_text,
        we_sell,
    )

    me = "dealer-hash"
    now = 1_800_000_000
    iso = "2026-09-14T12:00:00.000Z"
    assert created_of({"created": iso}) > 1_700_000_000
    assert is_out({"me": True, "author": me}, me) is True
    assert is_out({"author": "client"}, me) is False
    assert is_offer_room({"room_type": "ROOM_TYPE_OFFER"}) is True
    assert is_offer_room({"room_type": "ROOM_TYPE_TECH_SUPPORT"}) is False

    offer = {
        "id": "111-abc",
        "car_info": {"mark_info": {"name": "BMW"}, "model_info": {"name": "X5"}},
        "documents": {"year": 2020},
        "price_info": {"price": 5400000},
        "url": "https://auto.ru/cars/used/sale/bmw/x5/111-abc/",
    }
    listing = listing_from_offer(offer)
    assert listing["title"] == "BMW X5 2020"
    assert "5 400 000" in listing["price"]

    room = {
        "room_type": "ROOM_TYPE_OFFER",
        "subject": {"offer": {"source": {"id": "111-abc"}}},
    }
    assert source_id(room) == "111-abc"
    own = {"111-abc": listing}
    assert we_sell(room, own, {"foreign": []}) is True
    buyer = {
        "room_type": "ROOM_TYPE_OFFER",
        "subject": {"offer": {"source": {"id": "999-zzz"}}},
    }
    assert we_sell(buyer, own, {"foreign": ["999-zzz"]}) is False
    assert we_sell(buyer, own, {"foreign": []}) is False

    fresh = [
        {
            "author": "client",
            "created": now - 20,
            "payload": {"content_type": "TEXT_PLAIN", "value": "ещё актуально?"},
        },
        {
            "author": "client",
            "created": now,
            "payload": {"content_type": "TEXT_PLAIN", "value": "и цена?"},
        },
    ]
    assert had_prior_correspondence(fresh, now, me) is False
    with_out = fresh + [
        {
            "author": me,
            "me": True,
            "created": now - 5,
            "payload": {"content_type": "TEXT_PLAIN", "value": "да"},
        }
    ]
    assert had_prior_correspondence(with_out, now, me) is True
    old = [
        {
            "author": "client",
            "created": now - PRIOR_GAP_SEC - 10,
            "payload": {"content_type": "TEXT_PLAIN", "value": "привет"},
        },
        {
            "author": "client",
            "created": now,
            "payload": {"content_type": "TEXT_PLAIN", "value": "ну что?"},
        },
    ]
    assert had_prior_correspondence(old, now, me) is True
    ad = {
        "author": "client",
        "created": now - 9,
        "payload": {"content_type": "TEXT_HTML", "value": "<p>отчёт</p>"},
        "properties": {"type": "ADV_FREE_REPORT"},
    }
    assert had_prior_correspondence(fresh + [ad], now, me) is False
    assert message_text(fresh[0], me) == "ещё актуально?"
    assert message_text(with_out[-1], me) == ""

    from bot.crm import _source_enum, snapshot

    snap = snapshot("ar:room1", [{"role": "user", "content": "привет"}], "handoff")
    assert snap["channel"] == "Авто.ру"
    assert _source_enum(snap) == 1641533


def test_focus_autoru():
    from bot.avito_match import focus_block

    text = focus_block("BMW X5 2020", "5 400 000 ₽", "https://auto.ru/x", channel="Авто.ру")
    assert "объявлению Авто.ру" in text
    assert "BMW X5 2020" in text


def test_avito_history_and_shot():
    from bot.avito_loop import (
        SHOT_IDS,
        history_from_messages,
        is_noise,
        is_shot_chat,
        message_text,
    )
    from bot.human import drop_regreeting

    vlad = [
        {"created": 6, "direction": "in", "type": "text", "content": {"text": "По автотеке что там?"}},
        {
            "created": 5,
            "direction": "out",
            "type": "text",
            "content": {"text": "Косметический окрас крыла. Напишите номер"},
        },
        {"created": 4, "direction": "in", "type": "text", "content": {"text": "Страховка?"}},
        {
            "created": 3,
            "direction": "in",
            "type": "text",
            "content": {"text": "Крыла или арки? По автотеке что там с ним, ДТП?"},
        },
        {
            "created": 2,
            "direction": "out",
            "type": "text",
            "content": {"text": "Добрый день! Один косметический окрас крыла."},
        },
        {
            "created": 1,
            "direction": "in",
            "type": "text",
            "content": {"text": "Привет! Он в родном окрасе?"},
        },
    ]
    turns, pending, cursor = history_from_messages(vlad)
    assert pending == ["По автотеке что там?"]
    assert cursor == 5
    assert [m["role"] for m in turns] == ["user", "assistant", "user", "assistant"]
    assert "Страховка?" in turns[2]["content"]
    assert "Добрый день" in turns[1]["content"]

    prem = [
        {"created": 9, "direction": "in", "type": "text", "content": {"text": "До 8 млн только моя доплата"}},
        {"created": 8, "direction": "in", "type": "text", "content": {"text": "Здравствуйте да актуально"}},
        {
            "created": 7,
            "direction": "in",
            "type": "system",
            "content": {"text": "[Системное сообщение] Мы аккуратно напомнили собеседнику о диалоге."},
        },
        {
            "created": 6,
            "direction": "out",
            "type": "system",
            "content": {"text": "[Системное сообщение] Здравствуйте! Вам ещё актуально объявление?"},
        },
        {"created": 5, "direction": "out", "type": "text", "content": {"text": "Когда готовы подъехать на оценку?"}},
        {"created": 4, "direction": "in", "type": "text", "content": {"text": "Договорились"}},
        {
            "created": 3,
            "direction": "out",
            "type": "text",
            "content": {"text": "Добрый день! Мы принимаем автомобили в трейд-ин."},
        },
        {"created": 2, "direction": "in", "type": "link", "content": {}},
        {"created": 1, "direction": "in", "type": "text", "content": {"text": "Здравствуйте обмен интересует?"}},
    ]
    assert is_noise(prem[2]) is True
    assert message_text(prem[2]) == ""
    turns, pending, cursor = history_from_messages(prem)
    assert pending == ["Здравствуйте да актуально", "До 8 млн только моя доплата"]
    assert cursor == 5
    assert not any("актуально объявление" in (m.get("content") or "") for m in turns)
    assert turns[0]["content"].startswith("Здравствуйте обмен")
    assert "Клиент прислал ссылку" in turns[0]["content"]

    turns, pending, cursor = history_from_messages(
        [
            vlad[1],
            vlad[2],
            vlad[3],
            vlad[4],
            vlad[5],
            {
                "created": 6,
                "direction": "in",
                "type": "system",
                "content": {"text": "Сообщение не поддерживается"},
            },
        ]
    )
    assert pending == []
    assert cursor == 6

    cid = next(iter(SHOT_IDS))
    assert is_shot_chat({"id": cid, "users": [], "last_message": {}}) is True
    assert is_shot_chat({"id": "u2i-other", "users": [{"id": 1, "name": "Марина"}]}) is False
    assert (
        is_shot_chat(
            {
                "id": "u2i-mikhail",
                "users": [{"id": 1, "name": "Михаил"}, {"id": 206268487, "name": "Диво Моторс"}],
                "last_message": {
                    "direction": "in",
                    "type": "text",
                    "content": {"text": "Цена реальная?"},
                },
            }
        )
        is True
    )
    assert drop_regreeting("Добрый день! По автотеке вот ссылка") == "По автотеке вот ссылка"
    assert drop_regreeting("Здравствуйте. DIVO MOTORS, Никита. Крыло в окрасе") == "Крыло в окрасе"
    assert drop_regreeting("По автотеке что там?") == "По автотеке что там?"
    assert drop_regreeting("Добрый день!") == ""


if __name__ == "__main__":
    test_phone()
    test_reasons()
    test_alert_text()
    test_pings()
    test_prior_thread()
    test_amo_owner()
    test_autoru_prior()
    test_focus_autoru()
    test_avito_history_and_shot()
    print("ok")
