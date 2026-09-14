"""Проверка формулировок алертов, номера и рабочего окна."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import time

from bot.alerts import brief_from_history, compact_thread, format_alert, format_taken, next_ping, pretty_phone, take_keyboard
from bot.nudge import (
    extract_phone,
    extract_phone_from_history,
    is_caller_id_paste,
    is_complaint,
    asked_leasing,
    asked_torg,
    asks_about_call,
    needs_reply,
    urgent_reason,
    wants_call,
    wants_person,
)

MSK = ZoneInfo("Europe/Moscow")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 14, hour, minute, tzinfo=MSK)


def test_needs_reply():
    assert needs_reply("👍") is False
    assert needs_reply("👍🤝") is False
    assert needs_reply("ок") is False
    assert needs_reply("Хорошо") is False
    assert needs_reply("спасибо") is False
    assert needs_reply("ок спасибо") is False
    assert needs_reply("ок, а цена какая?") is True
    assert needs_reply("есть без ДТП?") is True
    assert needs_reply("8 900 111-22-33") is True
    assert needs_reply("Клиент прислал фото") is True
    assert needs_reply("да") is True
    assert needs_reply("нет") is True
    assert needs_reply("Сообщение удалено") is False
    assert needs_reply("Это вы мне звонили") is True


def test_phone():
    assert extract_phone("мой 8 900 111-22-33") == "79001112233"
    assert extract_phone("+79502292544") == "79502292544"
    assert extract_phone("пишите на 8 495 089 29 29") == ""

    hist = [
        {"role": "user", "content": "+79502292544"},
        {"role": "assistant", "content": "Принял, в ближайшее время наберу"},
        {"role": "user", "content": "Это и есть мой номер +79502292544"},
        {"role": "user", "content": "Вы звонили"},
        {"role": "assistant", "content": "Да, это мы. Наберу ещё раз в ближайшее время"},
        {"role": "user", "content": "+79275282888"},
        {"role": "user", "content": "Это вы мне звонили"},
    ]
    assert extract_phone_from_history(hist) == "79502292544"
    assert is_caller_id_paste("+79275282888", hist[:-2])
    assert not is_caller_id_paste("+79502292544", [])
    swapped = [
        {"role": "user", "content": "+79502292544"},
        {"role": "user", "content": "другой номер +79001112233"},
    ]
    assert extract_phone_from_history(swapped) == "79001112233"
    assert extract_phone_from_history([{"role": "user", "content": "8 900 111-22-33"}]) == "79001112233"


def test_urgent_reason():
    assert urgent_reason("+79502292544", []) == "phone"
    assert urgent_reason("это развод", []) == ""
    assert urgent_reason("дайте живого человека", []) == ""
    hist = [{"role": "user", "content": "мой 8 900 111-22-33"}]
    assert urgent_reason("это развод", hist) == "complaint"
    assert urgent_reason("дайте живого человека", hist) == "handoff"
    assert urgent_reason("перезвоните", hist) == "call"
    assert urgent_reason("Это вы мне звонили", hist) == "call"
    assert urgent_reason("Вы звонили", hist) == "call"
    assert urgent_reason("Это вы мне звонили", []) == ""
    assert urgent_reason("ок", hist) == ""
    assert urgent_reason("89001112233", hist) == ""
    call_hist = hist + [{"role": "user", "content": "Вы звонили"}]
    assert urgent_reason("+79275282888", call_hist) == "call"
    assert (
        extract_phone_from_history(call_hist + [{"role": "user", "content": "+79275282888"}])
        == "79001112233"
    )


def test_brief_skips_listing_spec():
    brief = brief_from_history(
        [
            {
                "role": "user",
                "content": "Здравствуйте, я с другого города. Можно приехать на осмотр?",
            },
            {
                "role": "assistant",
                "content": "Так, FAW Bestune NAT 2023 года, пробег 6798 км, белый, 1. 7 млн. Это наш экземпляр, всё верно",
            },
            {"role": "user", "content": "Вы звонили"},
        ],
        "call",
    )
    low = brief.lower()
    assert "так," not in low
    assert not low.startswith("так")
    assert "пробег" not in low
    assert "6798" not in brief
    assert "белый" not in low
    assert "1. 7" not in brief and "1.7" not in brief
    assert "наш экземпляр" not in low
    assert "наберу" not in low
    assert "года,, 1" not in brief
    assert "осмотр" in low
    assert "звон" in low
    assert "другого города" in low
    assert low.count("звон") == 1


def test_unsolicited():
    from bot.human import drop_unsolicited

    price = [
        {"role": "user", "content": "Цена реальная?"},
    ]
    assert not asked_torg(price)
    assert not asked_leasing(price)
    cut = drop_unsolicited(
        "Да, цена в объявлении реальная. Комплиментарный торг можем обсудить при осмотре",
        allow_torg=False,
        allow_leasing=False,
    )
    assert "торг" not in cut.lower()
    assert "реальная" in cut.lower() or "объявлен" in cut.lower()
    lease = drop_unsolicited(
        "Машина в наличии. В лизинг тоже можно оформить",
        allow_leasing=False,
    )
    assert "лизинг" not in lease.lower()
    assert "наличии" in lease.lower()
    keep = drop_unsolicited(
        "Лизинг только с полным НДС",
        allow_leasing=True,
    )
    assert "лизинг" in keep.lower()
    torg_ask = [{"role": "user", "content": "скидку сделаете?"}]
    assert asked_torg(torg_ask)
    assert asked_torg(
        [{"role": "user", "content": "Добрый день, за 3,5 млн продадите многодетной семье?"}]
    )
    assert asked_leasing([{"role": "user", "content": "а в лизинг можно?"}])
    from bot.human import soften_hard_torg

    slammed = soften_hard_torg(
        "Цена по этой машине зафиксирована в объявлении, 4 300 000 рублей. Продать за 3,5 не сможем",
        allow_torg=True,
    )
    assert "не сможем" not in slammed.lower()
    assert "зафиксирован" not in slammed.lower()
    assert "4 300 000" in slammed
    assert "разумных пределах" in slammed.lower()
    assert "осмотр" in slammed.lower()
    keep_hard = soften_hard_torg("Продать за 3,5 не сможем", allow_torg=False)
    assert "не сможем" in keep_hard.lower()


def test_greeting_and_paper():
    from bot.human import bang_greeting, drop_paper_talk, for_chat

    assert bang_greeting("Добрый день. Машина в наличии") == "Добрый день! Машина в наличии"
    assert bang_greeting("Добрый день! Машина в наличии") == "Добрый день! Машина в наличии"
    assert bang_greeting("Доброе утро, смотрите") == "Доброе утро! Смотрите"
    raw = (
        "Добрый день. Такой машины строки с ценой на юрлицо/НДС в базе пока нет. "
        "Продажу с НДС по ней подтвержу отдельно"
    )
    cut = drop_paper_talk(raw)
    assert "базе" not in cut.lower()
    assert "строк" not in cut.lower()
    assert "/" not in cut
    chat = for_chat(raw)
    assert chat.startswith("Добрый день!")
    assert "базе" not in chat.lower()
    assert "строк" not in chat.lower()
    assert "ндс" in chat.lower()
    leak = drop_paper_talk(
        "Отчёт автотеки я сходу не поднимал, чтобы вас не дезинформировать"
    )
    assert "сходу" not in leak.lower()
    assert "дезинформировать" not in leak.lower()
    comma = drop_paper_talk("Добрый день, в карточке нет дизельного нагревателя")
    assert comma.startswith("Добрый день!")
    from bot.human import ensure_greeting
    assert ensure_greeting(["Уточню по этой машине"], first=True)[0].startswith("Добрый день!")
    assert ensure_greeting(["Добрый день! Машина в наличии"], first=True)[0].startswith("Добрый день!")
    assert ensure_greeting(["Уточню"], first=False) == ["Уточню"]


def test_now_call():
    from bot.human import for_chat, soften_now_call

    cut = soften_now_call("Принял, сейчас наберём")
    assert "сейчас" not in cut.lower()
    assert "ближайшее время" in cut.lower()
    assert "наберём" in cut.lower() or "наберем" in cut.lower()
    start = soften_now_call("Сейчас наберу")
    assert start.startswith("В ближайшее время")
    after = soften_now_call("Наберу вас сейчас")
    assert "сейчас" not in after.lower()
    assert "ближайшее время" in after.lower()
    keep = soften_now_call("В продаже сейчас нет")
    assert keep == "В продаже сейчас нет"
    chat = for_chat("Принял, сейчас наберём")
    assert "сейчас" not in chat.lower()
    assert "ближайшее время" in chat.lower()


def test_where_choice():
    from bot.human import drop_where_choice, for_chat

    raw = (
        "Можно подъехать и посмотреть машину лично. "
        "Где вам удобнее, у нас в наличии до 20:00 ежедневно, мы на Автозаводской 18"
    )
    cut = drop_where_choice(raw)
    assert "где вам удобнее" not in cut.lower()
    assert "автозаводской" in cut.lower()
    assert "подъехать" in cut.lower()
    chat = for_chat(raw)
    assert "где вам удобнее" not in chat.lower()
    assert "с 10:00 до 20:00" in chat
    assert for_chat("посмотреть можно в любой день до 20:00").lower().count("10:00") == 1
    assert "с 10:00 до 20:00" in for_chat("работаем ежедневно с 10:00 до 20:00")


def test_merge_user_chunks():
    from bot.main import merge_user_chunks

    hist = [{"role": "assistant", "content": "напишите номер"}]
    out = merge_user_chunks(hist, ["+79502292544"])
    assert out[-1] == {"role": "user", "content": "+79502292544"}
    again = merge_user_chunks(out, ["+79502292544"])
    assert again == out
    merged = merge_user_chunks(out, ["привет", "+79502292544"])
    assert merged[-1]["content"] == "привет\n+79502292544"
    assert pretty_phone("79001112233") == "+7 900 111-22-33"


def test_reasons():
    assert wants_call("перезвоните мне")
    assert not wants_call("Это вы мне звонили")
    assert asks_about_call("Это вы мне звонили")
    assert asks_about_call("Вы звонили")
    assert not asks_about_call("какая цена")
    assert wants_person("дайте живого человека")
    assert is_complaint("это развод")
    called = brief_from_history(
        [
            {"role": "user", "content": "мой 8 900 111-22-33"},
            {"role": "assistant", "content": "Принял, в ближайшее время наберу"},
            {"role": "user", "content": "Это вы мне звонили"},
        ],
        "call",
    )
    assert "звон" in called.lower()


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
    assert "ждёт звонка" in follow.splitlines()[0]
    hour = format_alert({"wait": "chat", "car": "X6", "reason": "handoff"}, 60)
    assert "1 час" in hour
    assert "ждёт ответ в чате" in hour.splitlines()[0]
    complain = format_alert(
        {"wait": "call", "car": "X6", "reason": "complaint", "phone": "79001112233"},
        15,
    )
    assert "жалоба" in complain.splitlines()[0]
    assert "ждёт звонка" in complain.splitlines()[0]
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
    assert "про дтп" in brief.lower()
    assert "осмотр" in brief.lower()
    # Что мы уже ответили клиенту, в пуш не тащим: менеджер видит это в чате.
    assert "ДТП не было" not in brief
    assert "Автозаводская" not in brief
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
    assert "наличный расчёт" not in credit
    assert "контактный телефон" not in credit.lower()
    trade = brief_from_history(
        [
            {"role": "user", "content": "Здравствуйте обмен интересует?"},
            {
                "role": "assistant",
                "content": "Добрый день! Мы принимаем автомобили в трейд-ин. Окончательные условия с радостью обсудим у нас в автосалоне, после осмотра автомобиля. Когда готовы подъехать на оценку?",
            },
            {"role": "user", "content": "Договорились"},
            {"role": "user", "content": "До 8 млн только моя доплата"},
            {
                "role": "assistant",
                "content": "Хорошо, понял. Подскажите марку и год своей машины, или дайте ссылку на объявление, если продаёте где-то.",
            },
            {
                "role": "assistant",
                "content": "Вижу вашу ссылку, это V-класс 2017, 187 тысяч км. Доплату до 8 млн по G-классу понял. Напишите номер, наберу, обсудим обмен",
            },
        ],
        "handoff",
    )
    assert "интересует обмен" in trade.lower()
    assert "v-класс" in trade.lower()
    assert "2017" in trade
    assert "187" in trade
    assert "8 млн" in trade
    assert "принимаем" not in trade.lower()
    assert "с радостью" not in trade.lower()
    assert "окончательные условия" not in trade.lower()
    assert "хорошо, понял" not in trade.lower()
    assert "подскажите" not in trade.lower()
    assert "когда готовы" not in trade.lower()
    assert "напишите номер" not in trade.lower()
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


def test_widget_score():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from amo_client import score_widget_lead

    g = "Mercedes-Benz G-класс AMG 4.0 AT, 2021, 42 520 км"
    premium = score_widget_lead(
        g, peer="Premium Auto", car="Mercedes-Benz G-Класс AMG 2021",
        channel="Авито", from_name="Premium Auto", created_at=int(time.time()),
    )
    evgeniy = score_widget_lead(
        g, peer="Premium Auto", car="Mercedes-Benz G-Класс AMG 2021",
        channel="Авито", from_name="Евгений", created_at=int(time.time()),
    )
    assert premium > evgeniy
    assert premium >= 40
    mikhail = score_widget_lead(
        "FAW Bestune NAT AT, 2023, 6 798 км",
        peer="Михаил",
        car="Bestune NAT",
        channel="Авито",
        from_name="Михаил",
        created_at=int(time.time()),
    )
    assert mikhail >= 40
    assert score_widget_lead(
        "[АВТО.РУ] Tank 700",
        peer="Premium Auto",
        car="G-Класс",
        channel="Авито",
        from_name="Premium Auto",
    ) == 0


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
    g = focus_block(
        "Mercedes-Benz G-класс AMG 4.0 AT, 2021, 42 520 км",
        "14 100 000",
    )
    if "Это машина из стока" in g:
        assert "уже на этом объявлении" in g


def test_listing_context_cleanup():
    from bot.human import drop_dead_closer, drop_logistics_lecture, for_chat, split_bubbles
    from bot import prompt

    raw = (
        "Наугад не скажу, тема логистики и пошлин не моя. "
        "Если интересно по машинам, что есть у нас в наличии. Обращайтесь"
    )
    assert "логистик" not in drop_logistics_lecture(raw).lower()
    cut = for_chat(raw)
    assert "наугад" not in cut.lower()
    assert "пошлин" not in cut.lower()
    assert "обращайтесь" not in cut.lower()
    bubbles = split_bubbles(
        "Этот бензиновый. Дизельного гелика сейчас нет.\n\n"
        "Наугад не скажу, тема логистики и пошлин не моя. Обращайтесь"
    )
    assert len(bubbles) == 1
    assert "бензиновый" in bubbles[0].lower()
    assert "пошлин" not in bubbles[0].lower()
    keep = drop_dead_closer("Машина в наличии, можно приехать посмотреть")
    assert "наличии" in keep.lower()
    built = prompt.build()
    assert "Чат по объявлению держит эту машину" in built
    assert "Не выдумывай соседнюю тему" in built
    assert "Это вы звонили" in built


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
    assert is_noise(
        {"type": "text", "direction": "in", "content": {"text": "Сообщение удалено"}}
    )
    assert (
        message_text(
            {"type": "text", "direction": "in", "content": {"text": "Сообщение удалено"}}
        )
        == ""
    )
    assert message_text(prem[2]) == ""
    turns, pending, cursor = history_from_messages(prem)
    assert pending == ["Здравствуйте да актуально", "До 8 млн только моя доплата"]
    assert cursor == 5
    assert not any("актуально объявление" in (m.get("content") or "") for m in turns)
    assert turns[0]["content"].startswith("Здравствуйте обмен")
    assert "Клиент прислал ссылку" in turns[0]["content"]

    item_msg = {
        "created": 2,
        "direction": "in",
        "type": "item",
        "content": {
            "item": {
                "title": "Hyundai Creta 1.6 AT, 2020, 109 000 км",
                "price_string": "1 590 000 ₽",
                "item_url": "https://avito.ru/lesnoy_gorodok/avtomobili/hyundai_creta_1.6_at_2020_109_000_km_8172403370",
            }
        },
    }
    assert "Hyundai Creta" in message_text(item_msg)
    assert "1 590 000" in message_text(item_msg)
    creta = [
        {"created": 1, "direction": "in", "type": "text", "content": {"text": "Здравствуйте! Обмен интересует вас?"}},
        item_msg,
        {
            "created": 3,
            "direction": "out",
            "type": "text",
            "content": {"text": "Добрый день! Да, обмен готовы рассмотреть."},
        },
        {"created": 4, "direction": "in", "type": "text", "content": {"text": "Дистанционно"}},
    ]
    turns, pending, cursor = history_from_messages(creta)
    assert pending == ["Дистанционно"]
    assert "Клиент прислал объявление" in turns[0]["content"]
    assert "Hyundai Creta" in turns[0]["content"]

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
    only_in = [
        {
            "created": 1,
            "direction": "in",
            "type": "text",
            "content": {"text": "Здравствуйте, я с другого города"},
        },
        {
            "created": 2,
            "direction": "in",
            "type": "text",
            "content": {"text": "Цена реальная?"},
        },
    ]
    turns, pending, cursor = history_from_messages(only_in)
    assert turns == []
    assert pending == ["Здравствуйте, я с другого города", "Цена реальная?"]
    assert cursor == 0
    assert is_shot_chat(
        {
            "id": "u2u-GgGsxybRa8lF_hT4SUeHzw",
            "users": [{"id": 1, "name": "Михаил"}],
            "last_message": {
                "direction": "in",
                "type": "text",
                "content": {"text": "Цена реальная?"},
            },
        }
    )


def test_dialog_ids():
    from bot.store import is_dialog_id

    assert is_dialog_id(123)
    assert is_dialog_id("-1001")
    assert is_dialog_id("av:u2i-abc")
    assert is_dialog_id("ar:room1")
    assert is_dialog_id("autoteka") is False


def test_tradein_vin_phone():
    from bot.human import (
        client_listing,
        drop_reask_listing,
        drop_tradein_menu,
        for_chat,
        with_vin_phone,
        wants_remote_eval,
    )
    from bot import prompt

    dump = (
        "Хорошо. Напишите, пожалуйста, VIN своего автомобиля для загрузки "
        "истории, а если VIN нет под рукой. Ссылку на объявление или марку, "
        "модель и год"
    )
    cut = drop_tradein_menu(dump)
    assert "VIN" in cut
    assert "ссылк" not in cut.lower()
    assert "марку" not in cut.lower()
    chat = for_chat(dump)
    assert "ссылк" not in chat.lower()
    assert "марку" not in chat.lower()
    with_phone = with_vin_phone(cut)
    assert "телефон" in with_phone.lower()
    assert with_phone.lower().count("напишите") == 1
    already = with_vin_phone(
        "Напишите, пожалуйста, VIN код автомобиля для загрузки истории "
        "и контактный телефон для обратной связи"
    )
    assert already.count("телефон") == 1
    later = drop_tradein_menu("Напишите ссылку на объявление или марку, модель и год")
    assert "ссылку" in later.lower()
    hist = [
        {
            "role": "user",
            "content": (
                "Здравствуйте! Обмен интересует вас?\n"
                "Клиент прислал объявление: Hyundai Creta 1.6 AT, 2020, 109 000 км"
            ),
        }
    ]
    assert "Hyundai Creta" in client_listing(hist)
    reask = drop_reask_listing("Напишите ссылку на объявление или марку, модель и год")
    assert reask == ""
    assert wants_remote_eval("Дистанционно")
    built = prompt.build()
    assert "Дистанционная оценка обмена" in built
    assert "Объявление своей машины уже прислал" in built or "уже прислал" in built


def test_in_stock_dedupe():
    from bot.human import dedupe_in_stock, strip_in_stock
    from bot.nudge import build_text, history_said_in_stock

    invite = (
        "Алексей, машина у нас в наличии, можно приехать посмотреть вживую. "
        "Мы на Автозаводской 18, ТЦ Ривьера, -2 этаж"
    )
    cut = strip_in_stock(invite)
    assert "наличии" not in cut.lower()
    assert "посмотреть" in cut.lower()
    first = "Добрый день! Да, в наличии. FAW Bestune NAT 2023 года."
    two = dedupe_in_stock([first, invite])
    assert two[0].count("наличии") == 1
    assert "наличии" not in two[1].lower()
    already = dedupe_in_stock(
        ["Машина в наличии, посмотреть можно в любой день с 10:00 до 20:00"],
        already=True,
    )
    assert already
    assert "наличии" not in already[0].lower()
    hist = [
        {"role": "assistant", "content": "да, именно эта машина в наличии и готова к продаже"}
    ]
    assert history_said_in_stock(hist)
    nudge = build_text(
        1, "Максим", "FAW Bestune NAT", asked=False, address=True, said_stock=True
    )
    assert "наличии" not in nudge.lower()
    assert "посмотреть" in nudge.lower()
    assert "Максим," in nudge
    assert "с 10:00 до 20:00" in nudge


def test_tiggo_match_and_messenger():
    from tools.stock_sync import HEADER, warehouse_block
    from bot.avito_match import match_card, focus_block
    from bot.human import drop_paper_talk, phone_to_messenger
    from bot.nudge import wants_write_here

    vals = {h: "" for h in HEADER}
    vals["VIN"] = "LVVDB21B0ND229078"
    vals["Марка"] = "Chery"
    vals["Модель"] = "Tiggo 4"
    vals["Год выпуска"] = "2022"
    vals["Цвет"] = "серый"
    vals["Пробег"] = "122 507"
    vals["Автотека"] = "https://autoteka.ru/report/web/uuid/test"
    vals["Окрасы"] = "капот"
    stock = warehouse_block([[vals[h] for h in HEADER]])
    assert "## Chery Tiggo 4 2022" in stock
    assert "LVVDB21B0ND229078" in stock
    hit = match_card(
        "Chery Tiggo 4 1.5 CVT, 2022, 126 219 км",
        "960 000 ₽",
        stock=stock,
    )
    assert hit is not None
    assert "LVVDB21B0ND229078" in (hit.get("VIN") or "")
    focus = focus_block(
        "Chery Tiggo 4 1.5 CVT, 2022, 126 219 км",
        "960 000 ₽",
        stock=stock,
    )
    assert "Это машина из стока" in focus
    assert "не поднимал" not in focus.lower()
    miss = focus_block("Машина с другой планеты 1999")
    assert "не подцепилась" in miss
    assert "VIN в стоке не нашёл" not in miss

    leak = (
        "По этой Chery Tiggo 4 отчёт автотеки я сходу не поднимал, "
        "машину сверяю по стоку. Точной карточки с VIN у меня нет, "
        "по объявлению с Авито. Напишите контактный телефон"
    )
    cut = drop_paper_talk(leak)
    assert "сходу" not in cut.lower()
    assert "сверяю" not in cut.lower()
    assert "карточки" not in cut.lower()
    assert wants_write_here("Здесь напишите пожалуйста. У меня звонки не проходят")
    assert not wants_write_here("Напишите здесь цену")
    msg = phone_to_messenger(
        "Понимаю. Скиньте, пожалуйста, номер телефона, я уточню и напишу сюда же, если звонок неудобен"
    )
    assert "Telegram" in msg or "WhatsApp" in msg
    assert "если звонок" not in msg.lower()

    m8_stock = (
        "## GAC M8 2024\n"
        "- VIN: LMGMU1G82R1236593\n"
        "- Марка: GAC\n"
        "- Модель: M8\n"
        "- Пробег: 16 584 км\n"
        "- Цена в объявлении: 4 300 000 руб.\n"
        "- Автотека: https://autoteka.ru/report/web/uuid/test-m8\n"
    )
    m8 = match_card("GAC M8 2.0 AT, 2024, 72 887 км", "3 900 000 ₽", stock=m8_stock)
    assert m8 is not None
    assert "LMGMU1G82R1236593" in (m8.get("VIN") or "")
    assert "Автотека" in (m8.get("raw") or "")

    nat = {h: "" for h in HEADER}
    nat["VIN"] = "LFP8C7PC5P1D70967"
    nat["Марка"] = "FAW"
    nat["Модель"] = "Bestune NAT"
    nat["Год выпуска"] = "2023"
    nat_card = warehouse_block([[nat[h] for h in HEADER]])
    assert "Дизельный отопитель: да" in nat_card


def test_owner_legal():
    from bot.human import drop_owner_legal, for_chat
    from tools.stock_sync import autoteka_lines, speak_owners

    raw = (
        "1 владелец; Автомобилем владело юридическое лицо. "
        "Износ у таких машин, как правило, выше."
    )
    assert speak_owners(raw) == "1 владелец"
    assert speak_owners("Автомобилем владело юридическое лицо.") == ""
    blob = "\n".join(autoteka_lines({"владельцы": raw, "повреждения": "ДТП нет"}))
    assert "1 владелец" in blob
    assert "юридическ" not in blob.lower()
    assert "износ" not in blob.lower()
    chat = for_chat(
        "По этому Cayenne. 2019 год, пробег 81 085 км, "
        "один владелец по учёту юрлицом, окрасов нет"
    )
    assert "юрлиц" not in chat.lower()
    assert "один владелец" in chat.lower()
    assert "окрасов нет" in chat.lower()
    assert "юридическ" not in drop_owner_legal(raw).lower()
    vat = for_chat("Цена на юрлицо с НДС 10 400 000 рублей")
    assert "юрлицо" in vat.lower()
    assert "10 400 000" in vat


def test_many_paints():
    from bot.human import for_chat, soften_many_paints
    from tools.stock_sync import speak_paints

    hongqi = (
        "окрашены переднее правое крыло, передняя правая дверь, задняя правая дверь, "
        "задняя левая дверь, передняя левая дверь, крышка багажника и заднее левое крыло, "
        "с ремонтом"
    )
    assert speak_paints(hongqi) == "несколько косметических окрасов"
    assert "крыл" not in speak_paints(hongqi)
    assert speak_paints("окрашен капот") == "окрашен капот"
    assert speak_paints("окрашены переднее правое крыло и капот") == (
        "окрашены переднее правое крыло и капот"
    )
    maybach = (
        "окрашены переднее левое крыло, передняя левая дверь и задняя левая дверь, в плёнке"
    )
    assert speak_paints(maybach) == "несколько косметических окрасов, в плёнке"
    assert speak_paints("12. Автотека: https://autoteka.ru/x") == ""
    chat = for_chat(
        "По Hongqi окрашены переднее правое крыло, передняя правая дверь, "
        "задняя правая дверь и крышка багажника. Посмотреть можно в любой день "
        "с 10:00 до 20:00"
    )
    assert "крыл" not in chat.lower()
    assert "двер" not in chat.lower()
    assert "косметические окрасы" in chat.lower()
    assert "10:00" in chat
    keep = for_chat("Окрашен капот, ДТП не было")
    assert "капот" in keep.lower()
    assert "7 элементов" not in soften_many_paints("Окрашено 7 элементов, приезжайте").lower()


def test_two_vins_and_phone():
    """Живой чат Тимура: два VIN своих машин и номер, а бот просил номер снова."""
    from bot.alerts import brief_from_history, format_alert
    from bot.nudge import drop_phone_ask, extract_vins, history_has_phone

    history = [
        {"role": "user", "content": "Здравствуйте, автомобиль у вас на комиссии или выкуплен?"},
        {
            "role": "assistant",
            "content": "Машина выкуплена. По этому Cayenne 2019 год, один владелец, окрасов нет",
        },
        {
            "role": "user",
            "content": "У меня есть два авто, хотел бы обменять с доплатой, рассматриваете?",
        },
        {"role": "assistant", "content": "Да, обмен готовы рассмотреть. Хотели бы нас посетить или дистанционно?"},
        {"role": "user", "content": "Давайте попробуем дистанционно"},
        {"role": "assistant", "content": "Напишите VIN каждого автомобиля и контактный телефон"},
        {"role": "user", "content": "WP1ZZZ92ZGLA72981"},
        {"role": "assistant", "content": "Принял, а второй автомобиль и телефон для связи"},
        {"role": "user", "content": "TRUZZZFV5G1025930"},
        {"role": "user", "content": "89203337999"},
    ]
    vins = extract_vins(history)
    assert vins == ["WP1ZZZ92ZGLA72981", "TRUZZZFV5G1025930"]
    assert history_has_phone(history)
    cut = drop_phone_ask(
        "Хорошо, второй VIN принял. Теперь напишите, пожалуйста, контактный телефон для связи"
    )
    assert "телефон" not in cut.lower()
    assert "VIN принял" in cut
    assert drop_phone_ask("Все данные принял, наберу") == "Все данные принял, наберу"

    brief = brief_from_history(history, "phone")
    assert "интересует обмен" in brief.lower()
    assert "собственности" not in brief.lower()
    assert "окрас" not in brief.lower()
    assert len(brief) < 120

    snap = {
        "wait": "call",
        "name": "Тимур",
        "phone": "79203337999",
        "car": "Porsche Cayenne 2019",
        "channel": "Авито",
        "reason": "phone",
        "client_vins": vins,
        "brief": brief,
    }
    card = format_alert(snap, 0)
    assert "На обмен 2 авто" in card
    assert "WP1ZZZ92ZGLA72981" in card and "TRUZZZFV5G1025930" in card
    assert "юрлиц" not in card.lower()
    from bot.crm import handover_from_history

    note = handover_from_history(history, dict(snap, vin="WP1ZZZ9YZLDA01281"))
    assert "Переписка" not in note
    assert "Клиент:" in note and "Никита:" not in note
    assert "WP1ZZZ92ZGLA72981" in note and "TRUZZZFV5G1025930" in note
    assert "WP1ZZZ9YZLDA01281" in note
    assert "дистанц" in note.lower()
    assert "доплат" in note.lower()
    assert "2 авто" in note
    assert "комисси" in note.lower()
    assert "юрлиц" not in note.lower()
    assert "окрас" not in note.lower()


def test_claim_twice():
    """Двойной тап по кнопке: примечание в amo пишем один раз."""
    import asyncio

    from bot import crm, store

    doc = {
        "crm": {
            "lead_id": 45684819,
            "alert": {
                "active": False,
                "picked": "button",
                "picked_by": "Evgeniy",
                "picked_at": "2026-09-14T19:00:20+03:00",
                "snap": {"car": "Porsche Cayenne 2019", "lead_id": 45684819},
            },
        }
    }
    notes: list[str] = []
    saved = store.save_doc
    loaded = store.load_doc
    try:
        store.load_doc = lambda cid: doc
        store.save_doc = lambda cid, d: None
        crm.amo_client.add_note = lambda lead, text: notes.append(text)
        again = asyncio.run(crm.claim("av:test", "Evgeniy", "19:00", {}, user={"id": 282491919}))
    finally:
        store.load_doc = loaded
        store.save_doc = saved
    assert again is False
    assert notes == []


if __name__ == "__main__":
    test_needs_reply()
    test_phone()
    test_urgent_reason()
    test_brief_skips_listing_spec()
    test_unsolicited()
    test_greeting_and_paper()
    test_now_call()
    test_where_choice()
    test_merge_user_chunks()
    test_reasons()
    test_alert_text()
    test_pings()
    test_prior_thread()
    test_widget_score()
    test_amo_owner()
    test_autoru_prior()
    test_focus_autoru()
    test_listing_context_cleanup()
    test_avito_history_and_shot()
    test_dialog_ids()
    test_tradein_vin_phone()
    test_in_stock_dedupe()
    test_tiggo_match_and_messenger()
    test_owner_legal()
    test_many_paints()
    test_two_vins_and_phone()
    test_claim_twice()
    print("ok")
