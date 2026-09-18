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
    is_existing_buyer,
    asked_leasing,
    asked_torg,
    asked_heater,
    asked_media,
    asked_condition,
    asked_visit,
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
    assert needs_reply("Клиент прислал голосовое") is False
    assert needs_reply("Клиент прислал видео") is False
    assert needs_reply("не актуально") is False
    assert needs_reply("Неактуально, спасибо") is False
    assert needs_reply("Удачи парни") is False
    assert needs_reply("подумаю") is True
    assert needs_reply("да актуально") is True
    assert needs_reply("да") is True
    assert needs_reply("нет") is True
    assert needs_reply("Сообщение удалено") is False
    assert needs_reply("Это вы мне звонили") is True


def test_phone():
    from bot.nudge import (
        drop_messenger_choice,
        drop_phone_ask,
        history_has_phone,
        named_messenger,
        refresh,
        should_stop_nudge,
    )

    assert extract_phone("мой 8 900 111-22-33") == "79001112233"
    assert extract_phone("+79502292544") == "79502292544"
    assert extract_phone("пишите на 8 495 089 29 29") == ""
    assert extract_phone("+79192863777 Максим (телеграмм)") == "79192863777"
    assert extract_phone("89101822297") == "79101822297"
    assert extract_phone("9101822297") == "79101822297"
    assert extract_phone("910 182-22-97") == "79101822297"
    from bot.crm import amo_client as amo

    assert amo.amo_phone("89101822297") == "+79101822297"
    assert amo.amo_phone("9101822297") == "+79101822297"
    assert amo.amo_phone("+7 910 182-22-97") == "+79101822297"
    assert amo.amo_phone("79101822297") == "+79101822297"
    assert amo.amo_phone("994993845959") == "+994993845959"
    assert amo.contact_has_phone(
        {
            "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+7 910 182-22-97"}]}
            ]
        },
        "89101822297",
    )
    az = "+994993845959 если удобно напишите пожалуйста ватцап или тг данный момент нахожусь за границей"
    assert extract_phone(az) == "994993845959"
    assert extract_phone("994993845959") == "994993845959"
    assert history_has_phone([{"role": "user", "content": az}])
    assert named_messenger(az)
    assert pretty_phone("994993845959") == "+994993845959"
    hist = [
        {"role": "user", "content": az},
        {"role": "assistant", "content": "Хорошо, принял. Напишите, туда напишу. Ватсап или Телеграм"},
        {"role": "user", "content": "Хорошо спасибо"},
    ]
    assert should_stop_nudge(hist)
    assert refresh({"count": 0, "waiting": True}, hist)["waiting"] is False
    # Уже ушли два утренних догона: последнее слово за нами, номер всё равно есть.
    after = hist + [
        {"role": "assistant", "content": "Напишите, пожалуйста, ваш номер телефона для связи"},
        {
            "role": "assistant",
            "content": "Машина в наличии. Приезжайте посмотреть вживую. Мы на Автозаводской 18",
        },
    ]
    assert should_stop_nudge(after)
    assert refresh({"count": 2, "waiting": True}, after)["waiting"] is False
    cut = drop_messenger_choice("Хорошо, принял. Напишите, туда напишу. Ватсап или Телеграм")
    assert "или" not in cut.lower()
    assert "принял" in cut.lower()
    assert drop_phone_ask("Напишите, пожалуйста, ваш номер телефона для связи") == ""

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
    from bot.crm import PAUSE_REASONS, URGENT_REASONS

    assert "phone" in URGENT_REASONS
    assert "phone" not in PAUSE_REASONS
    assert {"call", "complaint", "handoff", "aftersale"} <= PAUSE_REASONS
    assert "aftersale" in URGENT_REASONS
    assert is_existing_buyer(
        "Здравствуйте! 3 сентября покупал автомобиль с номером 100. Залог пока не снят."
    )
    assert is_existing_buyer("Гасил залог в ВТБ, а тут уже 2 недели")
    assert not is_existing_buyer("Хочу купить X-Trail, цена какая?")
    assert urgent_reason(
        "3 сентября покупал автомобиль с номером 100. Залог пока не снят.",
        [],
    ) == "aftersale"
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
    assert asked_torg(
        [{"role": "user", "content": "Добрый вечер ! 6,5 готов приехать х."}]
    )
    assert asked_leasing([{"role": "user", "content": "а в лизинг можно?"}])
    assert not asked_heater([{"role": "user", "content": "За наличку торг есть?"}])
    assert asked_heater([{"role": "user", "content": "а вебасто стоит?"}])
    cut_heat = drop_unsolicited(
        "В разумных пределах торг и условия можем обсудить после осмотра. "
        "Машина живая, дизельный отопитель на ней есть. Можно посмотреть в шоуруме",
        allow_torg=True,
        allow_heater=False,
    )
    assert "отопител" not in cut_heat.lower()
    assert "торг" in cut_heat.lower()
    keep_heat = drop_unsolicited(
        "Да, дизельный отопитель стоит",
        allow_heater=True,
    )
    assert "отопител" in keep_heat.lower()
    photo = [
        {
            "role": "user",
            "content": "Я сам из Крыма настроен на покупку если скинете фото и жене понравится на выходных могу приехать к вам",
        }
    ]
    assert asked_media(photo)
    assert not asked_condition(photo)
    dump = drop_unsolicited(
        "Понял, тогда фото по этому Monjaro сейчас отправим. "
        "По состоянию: пробег 70 тысяч, состояние хорошее, машина не новая, но не била. "
        "Автотеки по этому экземпляру нет, так что данных по ДТП и окрасам из отчёта не покажу, "
        "но по кузову всё видно при осмотре. На выходных ждём, если жене понравится",
        allow_condition=False,
    )
    assert "пробег" not in dump.lower()
    assert "дтп" not in dump.lower()
    assert "автотек" not in dump.lower()
    assert "фото" in dump.lower()
    assert "выходных" in dump.lower()
    keep_cond = drop_unsolicited(
        "ДТП не было, кузов не чинили",
        allow_condition=True,
    )
    assert "дтп" in keep_cond.lower()
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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.human import bang_greeting, drop_paper_talk, ensure_greeting, for_chat, greeting_now, glue_lonely_greeting, greeting_only

    msk = ZoneInfo("Europe/Moscow")
    day = datetime(2026, 9, 15, 14, 0, tzinfo=msk)
    night = datetime(2026, 9, 15, 2, 41, tzinfo=msk)
    morn = datetime(2026, 9, 15, 8, 0, tzinfo=msk)
    eve = datetime(2026, 9, 15, 19, 0, tzinfo=msk)
    late = datetime(2026, 9, 15, 23, 10, tzinfo=msk)
    assert greeting_now(morn) == "Доброе утро!"
    assert greeting_now(day) == "Добрый день!"
    assert greeting_now(eve) == "Добрый вечер!"
    assert greeting_now(late) == "Доброй ночи!"
    assert greeting_now(night) == "Доброй ночи!"
    assert greeting_now(datetime(2026, 9, 15, 4, 0, tzinfo=msk)) == "Доброе утро!"
    assert greeting_now(datetime(2026, 9, 15, 18, 0, tzinfo=msk)) == "Добрый вечер!"
    assert bang_greeting("Добрый день. Машина в наличии", day) == "Добрый день! Машина в наличии"
    assert bang_greeting("Добрый день! Машина в наличии", day) == "Добрый день! Машина в наличии"
    assert bang_greeting("Доброе утро, смотрите", morn) == "Доброе утро! Смотрите"
    assert bang_greeting("Добрый день!", night) == "Доброй ночи!"
    raw = (
        "Добрый день. Такой машины строки с ценой на юрлицо/НДС в базе пока нет. "
        "Продажу с НДС по ней подтвержу отдельно"
    )
    cut = drop_paper_talk(raw)
    assert "базе" not in cut.lower()
    assert "строк" not in cut.lower()
    assert "/" not in cut
    chat = for_chat(raw)
    assert chat.startswith(greeting_now())
    assert "базе" not in chat.lower()
    assert "строк" not in chat.lower()
    assert "ндс" in chat.lower()
    leak = drop_paper_talk(
        "Отчёт автотеки я сходу не поднимал, чтобы вас не дезинформировать"
    )
    assert "сходу" not in leak.lower()
    assert "дезинформировать" not in leak.lower()
    comma = drop_paper_talk("Добрый день, в карточке нет дизельного нагревателя")
    assert comma.startswith(greeting_now())
    assert ensure_greeting(["Уточню по этой машине"], first=True, moment=day)[0].startswith(
        "Добрый день!"
    )
    assert ensure_greeting(["Добрый день! Машина в наличии"], first=True, moment=night)[
        0
    ].startswith("Доброй ночи!")
    assert ensure_greeting(["Уточню"], first=False) == ["Уточню"]
    assert greeting_only("Доброе утро!")
    assert greeting_only("Добрый день!")
    assert not greeting_only("Доброе утро! Машина в наличии")
    assert glue_lonely_greeting(
        ["Доброе утро!", "Посмотреть можно в любой день с 10:00 до 20:00"]
    ) == ["Доброе утро! Посмотреть можно в любой день с 10:00 до 20:00"]
    assert glue_lonely_greeting(["Машина в наличии"]) == ["Машина в наличии"]
    from bot.human import INVITE_FIRST

    q = "Здравствуйте, когда можно приехать посмотреть автомобиль"
    assert asked_visit(q)
    filled = "Доброе утро! " + INVITE_FIRST
    assert "с 10:00 до 20:00" in filled
    assert "Автозаводской 18" in filled


def test_after_contact_no_push():
    from bot.human import drop_push_after_contact
    from bot import store

    link = "https://autoteka.ru/report/web/uuid/abc"
    raw = "Отчёт вот: %s. Приезжайте посмотреть. В ближайшее время наберу" % link
    cut = drop_push_after_contact(raw)
    assert "autoteka.ru" in cut
    assert "приезжайте" not in cut.lower()
    assert "набер" not in cut.lower()
    assert drop_push_after_contact("Посмотреть можно в любой день с 10:00 до 20:00") == ""
    keep = drop_push_after_contact(
        "Посмотреть можно в любой день с 10:00 до 20:00",
        allow_invite=True,
    )
    assert "10:00" in keep
    assert "complaint" in store.HARD_PAUSE
    assert "call" not in store.HARD_PAUSE
    assert "handoff" not in store.HARD_PAUSE


def test_alert_always_has_button():
    from bot.alerts import take_keyboard
    from bot.crm import _ensure_token

    chat_btn = take_keyboard("ab12", "chat")
    assert chat_btn["inline_keyboard"][0][0]["text"] == "✍️ Беру"
    assert chat_btn["inline_keyboard"][0][0]["callback_data"] == "take:ab12"
    call_btn = take_keyboard("ab12", "call")
    assert call_btn["inline_keyboard"][0][0]["text"] == "📞 Звоню"
    alert = {}
    token = _ensure_token(alert)
    assert len(token) == 8
    assert alert["token"] == token
    assert _ensure_token(alert) == token


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
    media = format_alert(
        {
            "wait": "chat",
            "name": "Алексей",
            "car": "Tank 300",
            "channel": "Авито",
            "reason": "media",
            "media_kind": "фото",
            "phone": "79786991500",
            "lead_url": "https://divomotors.amocrm.ru/leads/detail/1",
            "brief": "Вопрос цены, просил автотеку. Хочет на осмотр.",
        },
        0,
    )
    assert "chat_id" not in media.lower()
    assert "av:u2i" not in media
    assert "Последнее:" not in media
    assert "Нужно отправить фото" in media
    assert "📷" in media
    assert "▪️ <b>Авто:</b>" in media
    assert "▪️ <b>Клиент:</b>" in media
    assert "▪️ <b>Телефон:</b>" in media
    assert "▪️ <b>Канал:</b>" in media
    assert "▪️ <b>Повод:</b>" in media
    assert "▪️ <b>Сделка:</b>" in media
    assert "▪️ <b>Контекст:</b>" in media
    assert "клиент просит фото" in media
    assert "Вопрос цены" in media
    buyer = format_alert(
        {
            "wait": "chat",
            "name": "Роман",
            "car": "FAW Bestune NAT",
            "channel": "Авито",
            "reason": "aftersale",
            "existing_buyer": True,
            "phone": "79001112233",
            "brief": "Уже покупал у нас. Вопрос по залогу.",
        },
        0,
    )
    assert "Наш клиент, уже покупал" in buyer
    assert "уже покупал у нас" in buyer
    assert "сервисный вопрос" in buyer
    assert "Роман" in buyer
    assert "Крыма" not in media
    from bot.alerts import with_media_brief

    merged = with_media_brief("Вопрос цены, просил автотеку.", "фото")
    assert merged.startswith("Вопрос цены")
    assert "Нужно отправить фото" in merged
    assert with_media_brief(merged, "фото") == merged
    from bot.alerts import media_context, media_where

    video_hist = [
        {"role": "user", "content": "какую цену можете дать"},
        {"role": "assistant", "content": "Цена 2 150 000, торг обсудим при осмотре"},
        {
            "role": "user",
            "content": "Перед дорогой из Краснодара скиньте подробное видео",
        },
        {
            "role": "assistant",
            "content": "Напишите телефон и Telegram или WhatsApp, коллега пришлёт видео",
        },
        {"role": "user", "content": "+79192863777 Максим (телеграмм)"},
    ]
    assert media_where(video_hist) == "в Telegram"
    video_ctx = media_context(video_hist, "видео")
    assert "Вопрос цены" in video_ctx
    assert "Видео в Telegram" in video_ctx
    video_card = format_alert(
        {
            "wait": "chat",
            "name": "Максим",
            "car": "FAW Bestune NAT 2023",
            "channel": "Авито",
            "reason": "media",
            "media_kind": "видео",
            "phone": "79192863777",
            "url": "https://avito.ru/moskva/avtomobili/faw_bestune_nat_at_2023_6_798_km_8323968508",
            "brief": video_ctx,
        },
        0,
    )
    assert "Нужно отправить видео" in video_card
    assert "📷" in video_card
    assert "▪️ <b>Авто:</b> FAW Bestune NAT 2023" in video_card
    assert "▪️ <b>Клиент:</b> Максим" in video_card
    assert "▪️ <b>Телефон:</b> +7 919 286-37-77" in video_card
    assert "▪️ <b>Канал:</b> Авито" in video_card
    assert "клиент просит видео" in video_card
    assert "▪️ <b>Объявление:</b> https://avito.ru/moskva/avtomobili/faw_bestune_nat_at_2023_6_798_km_8323968508" in video_card
    assert "Сделка:" not in video_card
    assert "chat_id" not in video_card.lower()
    assert "Последнее:" not in video_card
    keys = take_keyboard("tok", "chat")
    assert keys["inline_keyboard"][0][0]["text"] == "✍️ Беру"
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
    assert match_amo_user(KNOWN_USERS, first="El’zar") == 13835174
    assert match_amo_user(KNOWN_USERS, first="El'zar") == 13835174
    assert match_amo_user(KNOWN_USERS, tg_id=434232049, first="El’zar") == 13835174
    assert match_amo_user(KNOWN_USERS, first="El’zar", username="Elzar_Asadzade") == 13835174
    assert match_amo_user(KNOWN_USERS, username="Elzar_Asadzade") == 13835174
    assert match_amo_user(KNOWN_USERS, last="Asadzade") == 13835174
    assert match_amo_user(KNOWN_USERS, first="Nazar") == 14181846
    assert match_amo_user(KNOWN_USERS, first="U") is None
    assert match_amo_user(KNOWN_USERS, first="Кто-то") is None


def test_llm_pause_reason():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.main import is_llm_pause_reason

    assert is_llm_pause_reason("эскалация: llm") is True
    assert is_llm_pause_reason("LLM недоступен") is True
    assert is_llm_pause_reason("эскалация: phone") is False
    assert is_llm_pause_reason("команда владельца") is False


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
    assert next_ping(started, [], now=at(21, 0)) == 0
    assert next_ping(started, [0], now=at(21, 0)) is None
    assert next_ping(at(8, 33), [], now=at(8, 33)) == 0
    late = at(19, 58)
    assert next_ping(late, [0], now=at(20, 3)) is None
    morning = datetime(2026, 9, 15, 10, 5, tzinfo=MSK)
    assert next_ping(late, [0], now=morning) == 5


def test_nudge_step_cooldown():
    """Шаг 2 не должен уйти через минуту после шага 1, даже если дедлайн шага 2 уже прошёл."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from bot import nudge as n

    frozen = datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    orig = n.now_msk
    n.now_msk = lambda: frozen
    try:
        asked = (frozen - timedelta(hours=20)).isoformat(timespec="seconds")
        # Счётчик есть, метки касания нет: наутро шаг 2 иначе уходит сразу.
        assert n.ready_to_send({"waiting": True, "count": 1, "asked_at": asked}) == 0
        meta = {
            "waiting": True,
            "count": 1,
            "asked_at": asked,
            "nudged_at": frozen.isoformat(timespec="seconds"),
        }
        assert n.ready_to_send(meta) == 0
        meta["nudged_at"] = (frozen - timedelta(hours=3)).isoformat(timespec="seconds")
        assert n.ready_to_send(meta) == 2
    finally:
        n.now_msk = orig


def test_nudge_after_hours():
    from bot.nudge import due_at

    last = datetime(2026, 9, 14, 21, 31, tzinfo=MSK)
    due = due_at(last, 1)
    assert due.year == 2026 and due.month == 9 and due.day == 15
    assert due.hour == 10 and due.minute == 0
    edge = datetime(2026, 9, 14, 19, 50, tzinfo=MSK)
    due_edge = due_at(edge, 1)
    assert due_edge.day == 15 and due_edge.hour == 10
    day = datetime(2026, 9, 14, 16, 0, tzinfo=MSK)
    due_day = due_at(day, 1)
    assert due_day.day == 14 and due_day.hour == 16 and due_day.minute == 20


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

    voice = {
        "created": 10,
        "direction": "in",
        "type": "voice",
        "content": {"voice": {"voice_id": "59f592da-11d5-4fec-bd55-44fe76116685"}},
    }
    assert message_text(voice) == "Клиент прислал голосовое"
    turns, pending, cursor = history_from_messages(
        [
            {
                "created": 8,
                "direction": "in",
                "type": "text",
                "content": {"text": "Подскажите какие были ремонты серьезные?"},
            },
            {
                "created": 9,
                "direction": "out",
                "type": "text",
                "content": {"text": "ДТП не было, кузов не чинили"},
            },
            voice,
        ]
    )
    assert pending == ["Клиент прислал голосовое"]
    assert cursor == 9
    assert turns[-1]["role"] == "assistant"

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
    from bot.human import drop_paper_talk, drop_max_app, phone_to_messenger, speak_messengers
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
    assert "Телеграм" in msg and "Ватсап" in msg
    assert "если звонок" not in msg.lower()
    maxed = drop_max_app("Фото на Макс отправим, номер увидел, зафиксировал")
    assert "макс" not in maxed.lower()
    assert "Телеграм" in maxed and "Ватсап" in maxed
    assert drop_max_app("Максим, напишите номер") == "Максим, напишите номер"
    assert "MAX" not in drop_max_app("Telegram, WhatsApp, MAX")
    spoken = speak_messengers("Пришлём в Telegram или WhatsApp")
    assert "Telegram" not in spoken
    assert "Телеграм или Ватсап" in spoken

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


def test_mercedes_class_not_confused():
    from bot.avito_match import match_card

    stock = (
        "## Mercedes-Benz G-Класс AMG 2021\n"
        "- VIN: W1N4632761X412622\n"
        "- Марка: Mercedes-Benz\n"
        "- Модель: G-Класс AMG\n"
        "- Пробег: 70 284 км\n"
        "- Цена в объявлении: 15 000 000 руб. (наличный расчет, без НДС)\n"
        "- Автотека: https://autoteka.ru/report/web/uuid/91df1c66-fake\n"
        "\n"
        "## Mercedes-Benz S-Класс 2019\n"
        "- VIN: WDD2221861A505798\n"
        "- Марка: Mercedes-Benz\n"
        "- Модель: S-Класс\n"
        "- Пробег: 91 817 км\n"
        "- Цена в объявлении: 7 700 000 руб. (наличный расчет, без НДС)\n"
    )
    v = match_card(
        "Mercedes-Benz V-класс 2.0 AT, 2021, 105 863 км",
        "5 400 000 ₽",
        stock=stock,
    )
    assert v is None
    g = match_card(
        "Mercedes-Benz G-класс AMG 4.0 AT, 2021, 70 284 км",
        "15 000 000 ₽",
        stock=stock,
    )
    assert g is not None
    assert "W1N4632761X412622" in (g.get("VIN") or "")
    s = match_card(
        "Mercedes-Benz S-класс 2.9 AT, 2021, 109 343 км",
        "7 700 000 ₽",
        stock=stock,
    )
    assert s is not None
    assert "WDD2221861A505798" in (s.get("VIN") or "")


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


def test_no_connection_excuse():
    from bot.human import drop_connection_talk, for_chat

    raw = "Что-то со связью на моей стороне. Повторите, пожалуйста, сообщение"
    assert drop_connection_talk(raw) == ""
    assert "связ" not in for_chat(raw).lower()
    mixed = (
        "Добрый день! Что-то со связью на моей стороне. "
        "Цена 15 000 000 рублей"
    )
    kept = for_chat(mixed)
    assert "связ" not in kept.lower()
    assert "15 000 000" in kept
    assert drop_connection_talk("Тут связь подвисла, сейчас коллега подхватит и ответит вам.") == ""


def test_autoteka_missing_used_vs_new():
    from bot.human import fix_buyout_and_report, for_chat
    from tools.stock_sync import HEADER, autoteka_field, is_new_import

    used = {h: "" for h in HEADER}
    used["VIN"] = "LB37852DXPS090220"
    used["Марка"] = "Geely"
    used["Модель"] = "Monjaro"
    used["Пробег"] = "70007"
    used["Учёт в РФ"] = "Да"
    used["Без пробега РФ"] = "Нет"
    assert is_new_import(used) is False
    line = autoteka_field(used)
    assert "не стоит" in line
    assert line != "нет"

    new = dict(used)
    new["Пробег"] = "25"
    new["Учёт в РФ"] = "Нет"
    new["Без пробега РФ"] = "Да"
    assert is_new_import(new) is True
    assert "новая" in autoteka_field(new)

    lied = (
        "По этой машине данных о лизинге нет, автотеки на неё нет вообще. "
        "Уточню точно, наберу вас в ближайшее время"
    )
    fixed = for_chat(lied)
    assert "выкуплен" in fixed.lower()
    assert "данных о лизинге нет" not in fixed.lower()
    assert "нет вообще" not in fixed.lower()
    assert "автотек" not in fixed.lower()
    assert fix_buyout_and_report("ДТП не было, кузов не чинили") == (
        "ДТП не было, кузов не чинили"
    )


def test_speak_damage_no_false_dtp():
    from tools.stock_sync import autoteka_lines, speak_damage

    empty = "по отчёту ДТП, страховых выплат, кузовного ремонта не найдено"
    assert speak_damage(empty) == "ДТП не было, кузов не чинили"
    assert speak_damage("ДТП, страховых выплат и кузовного ремонта нет") == (
        "ДТП не было, кузов не чинили"
    )
    assert speak_damage("ДТП нет") == "ДТП не было, кузов не чинили"
    real = "одно ДТП, 27 января 2024 года, ремонта и страховых выплат нет"
    assert speak_damage(real) == real
    blob = "\n".join(autoteka_lines({"повреждения": empty}))
    assert "ДТП не было, кузов не чинили" in blob
    assert "ДТП, страховых" not in blob


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


def test_sales_lead_gets_phone():
    """Виджет уже в Продажах: номер из чата всё равно пишем в карточку контакта."""
    from bot import crm

    patched: list[tuple[int, str]] = []
    orig_get = crm.amo_client.get_lead
    orig_set = crm.amo_client.set_contact_phone
    orig_note = crm.amo_client.add_note
    orig_url = crm.amo_client.lead_url
    try:
        crm.amo_client.get_lead = lambda i: {
            "id": int(i),
            "pipeline_id": crm.amo_client.PIPELINE_SALES,
            "status_id": crm.amo_client.STATUS_NEW,
            "_embedded": {"contacts": [{"id": 77}]},
        }
        crm.amo_client.set_contact_phone = lambda cid, phone: patched.append((int(cid), phone))
        crm.amo_client.add_note = lambda *a, **k: None
        crm.amo_client.lead_url = lambda i: "https://divomotors.amocrm.ru/leads/detail/%s" % i
        doc = {"crm": {"lead_id": 45269007}, "messages": []}
        snap = {
            "phone": "89101822297",
            "channel": "Авито",
            "name": "Vsn",
            "car": "Tank 700",
        }
        out = crm.ensure_lead(snap, doc)
        assert out.get("lead_id") == 45269007
        assert patched == [(77, "89101822297")]
        assert doc["crm"].get("phone") == "+79101822297"
        assert doc["crm"].get("contact_id") == 77
    finally:
        crm.amo_client.get_lead = orig_get
        crm.amo_client.set_contact_phone = orig_set
        crm.amo_client.add_note = orig_note
        crm.amo_client.lead_url = orig_url


def test_one_card_per_phone():
    import asyncio

    from bot import crm, store

    assert crm.phone_key("+7 927 743-25-01") == "79277432501"
    assert crm.phone_key("89277432501") == "79277432501"
    assert crm.ping_allowed({"active": True, "nags": True}) is True
    assert crm.ping_allowed({"active": False, "picked": "button", "nags": False}) is False
    assert crm.ping_allowed({"active": True, "nags": False}) is False

    docs = {
        "av:a": {
            "crm": {
                "alert": {
                    "active": True,
                    "token": "aa",
                    "pings": [0],
                    "started_at": "2026-09-15T09:59:09+03:00",
                    "tg": {"chat_id": -100, "message_id": 62},
                    "snap": {"phone": "79277432501"},
                }
            }
        },
        "av:b": {"crm": {"alert": {}}},
    }
    loaded = store.load_doc
    ids = store.all_chat_ids
    store.load_doc = lambda cid: docs[str(cid)]
    store.all_chat_ids = lambda: list(docs)
    try:
        new = {"token": "bb", "nags": True, "pings": [], "started_at": "now"}
        crm._adopt_open_card(new, "79277432501", "av:b")
    finally:
        store.load_doc = loaded
        store.all_chat_ids = ids
    assert new["tg"]["message_id"] == 62
    assert new["token"] == "aa"
    assert new["nags"] is False
    assert new["pings"] == [0]

    claimed = {
        "chat_id": "av:x",
        "messages": [],
        "crm": {
            "alert": {
                "active": False,
                "nags": False,
                "picked": "button",
                "started_at": "2026-09-15T09:59:09+03:00",
                "pings": [0],
                "snap": {"phone": "79277432501", "wait": "call"},
                "tg": {"chat_id": -1, "message_id": 62},
                "posts": [
                    {"chat_id": -1, "message_id": 62},
                    {"chat_id": -1, "message_id": 99},
                ],
            }
        },
    }
    sent: list[str] = []
    deleted: list[int] = []

    class Fake:
        async def send(self, text, markup=None):
            sent.append("send")
            return (-1, 100)

        async def edit(self, *a, **k):
            return True

        async def delete(self, chat, mid):
            deleted.append(int(mid))
            return True

    old_bot = crm.bot
    old_load, old_save, old_ids = store.load_doc, store.save_doc, store.all_chat_ids
    crm.bot = Fake()
    store.load_doc = lambda cid: claimed
    store.save_doc = lambda cid, d: claimed.update(d)
    store.all_chat_ids = lambda: ["av:x"]
    try:
        asyncio.run(crm._tick_one("av:x"))
    finally:
        crm.bot = old_bot
        store.load_doc = old_load
        store.save_doc = old_save
        store.all_chat_ids = old_ids
    assert sent == []
    assert deleted == [99]
    assert [p["message_id"] for p in claimed["crm"]["alert"]["posts"]] == [62]


def test_persist_media_without_nags():
    """Медиа без пингов всё равно держит номер, ссылку и новый mid после пересылки."""
    from bot import crm, store

    doc = {
        "crm": {
            "alert": {
                "active": True,
                "nags": False,
                "reason": "media",
                "tg": {"chat_id": -1004386490638, "message_id": 79},
                "snap": {"phone": "", "wait": "chat"},
            }
        }
    }
    loaded, saved = store.load_doc, store.save_doc
    store.load_doc = lambda cid: doc
    store.save_doc = lambda cid, d: doc.update(d)
    try:
        crm._persist_alert(
            "av:x",
            {
                "active": True,
                "nags": False,
                "tg": {"chat_id": -1004386490638, "message_id": 80},
                "snap": {
                    "phone": "79192863777",
                    "url": "https://avito.ru/x",
                    "wait": "chat",
                    "reason": "media",
                },
                "posts": [{"chat_id": -1004386490638, "message_id": 80}],
            },
        )
    finally:
        store.load_doc = loaded
        store.save_doc = saved
    alert = doc["crm"]["alert"]
    assert alert["tg"]["message_id"] == 80
    assert alert["snap"]["phone"] == "79192863777"
    assert alert["snap"]["url"] == "https://avito.ru/x"


def test_stop_nudge_on_closed_and_voice():
    from bot.nudge import (
        is_closed,
        is_unheard_media,
        refresh,
        should_stop_nudge,
        wants_stop,
    )

    assert is_closed("не актуально")
    assert is_closed("неактуально")
    assert is_closed("не актуален")
    assert is_closed("Удачи парни")
    assert is_closed("уже купил")
    assert not is_closed("да актуально")
    assert not is_closed("неудачи по кредиту")
    assert not is_closed("не надоело ещё")
    assert wants_stop("подумаю")
    assert not is_closed("подумаю")
    assert is_unheard_media("Клиент прислал голосовое")
    assert not is_unheard_media("Клиент прислал фото")

    after_us = [
        {"role": "user", "content": "какие ремонты серьезные?"},
        {"role": "assistant", "content": "ДТП не было, кузов не чинили"},
    ]
    assert should_stop_nudge(after_us) is False
    waiting = refresh({"count": 0}, after_us)
    assert waiting["waiting"] is True

    voice = after_us + [{"role": "user", "content": "Клиент прислал голосовое"}]
    assert should_stop_nudge(voice) is True
    assert refresh({"count": 1, "waiting": True}, voice)["waiting"] is False

    closed = after_us + [{"role": "user", "content": "не актуально"}]
    assert should_stop_nudge(closed) is True
    assert refresh({"count": 0, "waiting": True}, closed)["waiting"] is False

    after_think = after_us + [
        {"role": "user", "content": "подумаю"},
        {"role": "assistant", "content": "Хорошо, подумайте. Посмотреть можно в любой день с 10:00 до 20:00"},
    ]
    assert should_stop_nudge(after_think) is True
    assert refresh({"count": 0, "waiting": True}, after_think)["waiting"] is False


def test_vat_from_listing_without_cme_flag():
    from bot.avito_match import focus_block, vat_for
    from bot.human import ensure_vat_said, vat_digits
    from bot.nudge import asked_vat, drop_phone_ask
    from tools.stock_sync import HEADER, card, vat_price

    assert vat_price("6 400 000") == "7 400 000 руб."
    assert vat_for("6 400 000 ₽") == "7 400 000 руб."
    vals = {h: "" for h in HEADER}
    vals["VIN"] = "LGWFG9A71RH963260"
    vals["Марка"] = "Tank"
    vals["Модель"] = "700"
    vals["Год выпуска"] = "2024"
    vals["Пробег"] = "24441"
    vals["Цена продажи"] = "6400000"
    vals["НДС"] = ""
    text = card([vals[h] for h in HEADER])
    assert "Цена на юрлицо с НДС: 7 400 000 руб." in text
    assert "не подтверждена" not in text
    focus = focus_block(
        "Tank 700 3.0 AT, 2024, 14 498 км",
        "6 400 000 ₽",
        stock=text,
    )
    assert "7 400 000" in focus
    no_price = dict(vals)
    no_price["Цена продажи"] = ""
    warehouse = card([no_price[h] for h in HEADER])
    focus_listing = focus_block(
        "Tank 700 3.0 AT, 2024, 14 498 км",
        "6 400 000 ₽",
        stock=warehouse,
    )
    assert "7 400 000" in focus_listing
    hedge = (
        "По этой машине готовой цены с НДС у меня нет под рукой, уточню. "
        "Скиньте, пожалуйста, номер телефона, наберу и посчитаем"
    )
    said = drop_phone_ask(ensure_vat_said(hedge, "7 400 000 руб."))
    assert "7400000" in vat_digits(said)
    assert "под рукой" not in said.lower()
    assert "номер" not in said.lower()
    hist = [
        {"role": "user", "content": "С НДС продажа?"},
        {"role": "user", "content": "Посчитайте и здесь напишите"},
    ]
    assert asked_vat(hist) is True


def test_dead_post_leaves_queue():
    """Снятое руками сообщение уходит из posts, а не долбит API вечно."""
    import asyncio

    from bot import crm
    from bot.alerts import AlertBot, delete_final

    assert delete_final("deleteMessage: Bad Request: message to delete not found") is True
    assert delete_final("deleteMessage: Bad Request: message can't be deleted") is True
    assert delete_final("deleteMessage: Forbidden: bot was kicked") is True
    assert delete_final("deleteMessage: Bad Request: Too Many Requests: retry after 7") is False

    calls: list[str] = []

    bot = AlertBot.__new__(AlertBot)

    async def fake_call(method, payload=None):
        calls.append(method)
        raise RuntimeError("deleteMessage: Bad Request: message to delete not found")

    bot._call = fake_call
    assert asyncio.run(bot.delete(-100, 77)) is True

    alert = {
        "tg": {"chat_id": -100, "message_id": 90},
        "posts": [
            {"chat_id": -100, "message_id": 90},
            {"chat_id": -100, "message_id": 77},
        ],
    }
    old_bot = crm.bot
    crm.bot = bot
    try:
        asyncio.run(crm._sweep_posts(alert))
    finally:
        crm.bot = old_bot
    assert alert["posts"] == [{"chat_id": -100, "message_id": 90}]
    assert len(calls) == 2


def test_nudge_stops_when_blocked():
    """Клиент заблокировал бота: догон гаснет, а не ломится каждую минуту."""
    import asyncio

    from bot import main as bot_main, nudge, store

    assert bot_main.chat_gone("sendMessage: Forbidden: bot was blocked by the user") is True
    assert bot_main.chat_gone("sendMessage: Bad Request: chat not found") is True
    assert bot_main.chat_gone("sendMessage: Bad Request: Too Many Requests") is False

    doc = {
        "chat_id": "5001",
        "messages": [
            {"role": "user", "content": "Кулрей 2023 актуален?"},
            {"role": "assistant", "content": "Да, в наличии. Хотите посмотреть?"},
        ],
        "nudge": {"waiting": True, "count": 0, "name": "Николай", "car": "Geely Coolray"},
    }
    saved: dict = {}

    class Blocked:
        async def typing(self, chat_id):
            return None

        async def send(self, chat_id, text):
            raise RuntimeError("sendMessage: Forbidden: bot was blocked by the user")

    old = (
        store.load_doc,
        store.save_doc,
        store.is_paused,
        nudge.ready_to_send,
        bot_main.type_and_wait,
        dict(bot_main.CHANNELS),
    )
    store.load_doc = lambda cid: doc
    store.save_doc = lambda cid, d: saved.update(d)
    store.is_paused = lambda cid: False
    nudge.ready_to_send = lambda meta: 1
    bot_main.type_and_wait = lambda channel, chat_id, delay: asyncio.sleep(0)
    bot_main.CHANNELS["tg"] = Blocked()
    try:
        asyncio.run(bot_main.send_nudge("5001"))
    finally:
        (
            store.load_doc,
            store.save_doc,
            store.is_paused,
            nudge.ready_to_send,
            bot_main.type_and_wait,
        ) = old[:5]
        bot_main.CHANNELS.clear()
        bot_main.CHANNELS.update(old[5])

    assert saved["nudge"]["waiting"] is False
    assert int(saved["nudge"].get("count") or 0) == 0


def test_catchup_returns_to_client():
    """Сбой модели не хоронит лид: очередь разбирается сама, без дублей."""
    import asyncio

    from bot import main as bot_main, store

    docs = {
        # Клиент написал последним, бот молчит после сбоя модели.
        "av:wait": {
            "chat_id": "av:wait",
            "messages": [
                {"role": "assistant", "content": "Машина в наличии"},
                {"role": "user", "content": "А обмен рассмотрите?"},
            ],
            "nudge": {},
        },
        # Тут бот уже ответил: в очередь чат попадать не должен.
        "av:done": {
            "chat_id": "av:done",
            "messages": [
                {"role": "user", "content": "Пробег какой?"},
                {"role": "assistant", "content": "70 078 км"},
            ],
            "nudge": {},
        },
    }
    answered: list[str] = []

    class Channel:
        async def typing(self, chat_id):
            return None

        async def send(self, chat_id, text):
            return None

    async def fake_answer(channel, chat_id, chunks):
        answered.append(str(chat_id))
        docs[str(chat_id)]["messages"].append(
            {"role": "assistant", "content": "Обмен рассмотрим, приезжайте"}
        )

    old = (
        store.load_doc,
        store.all_chat_ids,
        store.pause_info,
        store.is_paused,
        bot_main._answer_locked,
        dict(bot_main.CHANNELS),
    )
    store.load_doc = lambda cid: docs[str(cid)]
    store.all_chat_ids = lambda: list(docs)
    store.pause_info = lambda cid: {"reason": "llm"}
    store.is_paused = lambda cid: False
    bot_main._answer_locked = fake_answer
    bot_main.CHANNELS["avito"] = Channel()
    bot_main.CHANNELS["tg"] = Channel()
    try:
        assert bot_main.waiting_for_bot("av:wait") is True
        assert bot_main.waiting_for_bot("av:done") is False
        sent = asyncio.run(bot_main.catchup_waiting())

        # Второй заход, пока по чату уже идёт ответ: дубля быть не должно.
        busy = asyncio.new_event_loop()
        try:
            docs["av:wait"]["messages"].append({"role": "user", "content": "Ну что?"})
            bot_main.inflight.add("av:wait")
            again = busy.run_until_complete(bot_main.catchup_waiting())
        finally:
            bot_main.inflight.discard("av:wait")
            busy.close()
    finally:
        (
            store.load_doc,
            store.all_chat_ids,
            store.pause_info,
            store.is_paused,
            bot_main._answer_locked,
        ) = old[:5]
        bot_main.CHANNELS.clear()
        bot_main.CHANNELS.update(old[5])

    assert answered == ["av:wait"]
    assert sent == 1
    assert again == 0


def test_invite_survives_cleanup():
    """Вопрос про день визита остаётся: раньше фильтр съедал его целиком."""
    from bot.human import drop_where_choice, for_chat

    invite = "На какой день вам удобнее заехать, посмотреть можно с 10:00 до 20:00"
    assert for_chat(invite) == invite
    assert for_chat("Когда вам удобнее заехать?") == "Когда вам удобнее заехать?"
    # Выбор места по-прежнему мусор: салон один.
    assert "удобнее" not in drop_where_choice("Где вам удобнее посмотреть машину?")
    assert "удобнее" not in drop_where_choice("Где вам удобнее, напишите номер телефона")


def test_dash_keeps_clause_whole():
    """Пояснение после тире не превращается в обрубок «На 13,5.»."""
    from bot.human import drop_clause_dashes

    assert (
        drop_clause_dashes("На 13,5 — торг в разумных пределах, обсудим после осмотра")
        == "На 13,5, торг в разумных пределах, обсудим после осмотра"
    )
    assert (
        drop_clause_dashes("Цена по этой машине — 10 300 000, наличный расчет")
        == "Цена по этой машине, 10 300 000, наличный расчет"
    )
    assert (
        drop_clause_dashes("Porsche Cayenne 2019, синий, 5 800 000 — можно посмотреть")
        == "Porsche Cayenne 2019, синий, 5 800 000, можно посмотреть"
    )
    # Самостоятельная мысль с заглавной остаётся отдельным предложением.
    assert (
        drop_clause_dashes("Машина в наличии — Приезжайте смотреть")
        == "Машина в наличии. Приезжайте смотреть"
    )
    assert drop_clause_dashes("Coolray 1.5 AMT") == "Coolray 1.5 AMT"


if __name__ == "__main__":
    test_needs_reply()
    test_phone()
    test_urgent_reason()
    test_brief_skips_listing_spec()
    test_unsolicited()
    test_greeting_and_paper()
    test_after_contact_no_push()
    test_alert_always_has_button()
    test_now_call()
    test_where_choice()
    test_merge_user_chunks()
    test_reasons()
    test_alert_text()
    test_pings()
    test_nudge_after_hours()
    test_nudge_step_cooldown()
    test_prior_thread()
    test_widget_score()
    test_amo_owner()
    test_llm_pause_reason()
    test_autoru_prior()
    test_focus_autoru()
    test_listing_context_cleanup()
    test_avito_history_and_shot()
    test_dialog_ids()
    test_tradein_vin_phone()
    test_in_stock_dedupe()
    test_tiggo_match_and_messenger()
    test_mercedes_class_not_confused()
    test_owner_legal()
    test_no_connection_excuse()
    test_autoteka_missing_used_vs_new()
    test_speak_damage_no_false_dtp()
    test_many_paints()
    test_two_vins_and_phone()
    test_claim_twice()
    test_sales_lead_gets_phone()
    test_one_card_per_phone()
    test_persist_media_without_nags()
    test_stop_nudge_on_closed_and_voice()
    test_vat_from_listing_without_cme_flag()
    test_dead_post_leaves_queue()
    test_nudge_stops_when_blocked()
    test_catchup_returns_to_client()
    print("ok")
