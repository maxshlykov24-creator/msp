#!/usr/bin/env python3
"""keris-bot — демо-прототип клиентского бота питомника Keris (мальтипу).

Фаза 1a (демо): витрина щенков, цена/рассрочка, «О питомнике», FAQ,
кнопка «Связаться с менеджером» → создание контакта + сделки в amoCRM.

Реализация — только stdlib (urllib), long polling. Без внешних зависимостей,
чтобы разворачиваться на чистой Ubuntu без pip. Состояние клиентов — в JSON.
Тексты и тон взяты из анализа канала (КОНТЕНТ_ИЗ_КАНАЛА_ДЛЯ_БОТА.md).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("keris-bot")

# ── Конфиг (через окружение, дефолты — из ДОСТУПЫ.md) ─────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8544449405:AAGvRzRu0Cy6Vw8Q0IfcCrYMuhneF837Jzw")
AMO_SUBDOMAIN = os.environ.get("AMO_SUBDOMAIN", "kerisclub")
AMO_TOKEN = os.environ.get("AMO_TOKEN", "")  # долгосрочный токен, задаётся в env
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "keris_chat")
STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-bot/state.json")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
AMO_BASE = f"https://{AMO_SUBDOMAIN}.amocrm.ru"

# amoCRM IDs (факт из ДОСТУПЫ.md)
AMO_PIPELINE_SALES = 11036674
AMO_STAGE_NEW = 86717266  # Новая заявка
F_CONTACT_SOURCE = 1820795  # Изначальный источник (select)
F_CONTACT_CONSENT = 1820799  # Согласие ПДн (checkbox)
F_CONTACT_TG_BOT = 1820899  # Подписан на TG-бот (checkbox)
F_LEAD_SOURCE = 1820835  # Источник (select)
F_LEAD_CONTACT_WAY = 1820923  # Способ связи (select)
SOURCE_VALUE = "Телеграм-канал"
LEAD_WAY_VALUE = "TG-бот"

# ── Контент бота ──────────────────────────────────────────────────────────
ABOUT_TEXT = (
    "🐾 <b>Питомник Keris</b>\n\n"
    "Мы растим мальтипу F1 от элитных линий из Кореи и Китая. "
    "Наши фирменные окрасы — <b>ice gold, gold, gold brown</b> — редкие и узнаваемые.\n\n"
    "Мы создаём семьи, а не просто продаём щенков: каждый малыш уезжает "
    "с документами, ветпаспортом и официальным договором, уже приучённым к пелёнке.\n\n"
    "📍 Москва · 🚚 доставка по России и за границу\n"
    "💳 беспроцентная рассрочка · онлайн-знакомство для тех, кто далеко\n"
    "🎁 фирменный чек-лист нового хозяина на 24 страницы — в подарок каждому\n\n"
    f"💌 По всем вопросам — @{MANAGER_USERNAME}"
)

PRICE_TEXT = (
    "💰 <b>Цена и рассрочка</b>\n\n"
    "Стоимость щенка зависит от <b>окраса, размера и родословной</b> — "
    "фирменные окрасы (ice gold) и микро-размер ценятся выше.\n\n"
    "• Доступна <b>беспроцентная рассрочка</b> (3 / 6 / 10 мес).\n"
    "• Оплата любой картой; щенок закрепляется за вами с момента брони.\n"
    "• Возможна доставка по РФ и за границу.\n\n"
    "Точную цену по конкретному малышу назовёт менеджер — так честнее, "
    "чем усреднённый прайс.\n\n"
    "👇 Нажмите «Связаться с менеджером», и мы подберём щенка под ваш бюджет."
)

PUPPIES = [
    {
        "name": "Флокс 🩵",
        "text": (
            "<b>Флокс</b> 🩵 — ласковый компаньон с baby-face мордочкой.\n\n"
            "Окрас: <b>gold</b> · размер: мини · взрослый вес до 3.5 кг\n"
            "Спокойный, любит быть рядом, отлично ладит с детьми.\n\n"
            "💉 Привит по возрасту, документы + ветпаспорт\n"
            "🏷 Статус: свободен (пример для демо)"
        ),
    },
    {
        "name": "Айс 🤍",
        "text": (
            "<b>Айс</b> 🤍 — фирменный окрас питомника ice gold.\n\n"
            "Окрас: <b>ice gold</b> · размер: микро · взрослый вес до 2.5 кг\n"
            "Игривый и любознательный, быстро привыкает к пелёнке.\n\n"
            "💉 Привит по возрасту, документы + ветпаспорт\n"
            "🏷 Статус: свободен (пример для демо)"
        ),
    },
    {
        "name": "Мокко 🤎",
        "text": (
            "<b>Мокко</b> 🤎 — редкий тёплый окрас gold brown.\n\n"
            "Окрас: <b>gold brown</b> · размер: мини · взрослый вес до 3 кг\n"
            "Дружелюбный, обучаемый, ориентирован на человека.\n\n"
            "💉 Привит по возрасту, документы + ветпаспорт\n"
            "🏷 Статус: свободен (пример для демо)"
        ),
    },
]

FAQ = [
    ("Подходят ли людям с аллергией?",
     "Чаще да — у мальтипу минимальная линька и нет плотного подшёрстка. "
     "Реакция индивидуальна, но порода считается условно гипоаллергенной."),
    ("Легко ли обучаются?",
     "Да, мальтипу — одна из самых обучаемых декоративных пород. "
     "Ориентированы на человека, редко упрямятся."),
    ("Приучены ли к пелёнке?",
     "Да, наши щенки уезжают в семью уже приучёнными к пелёнке."),
    ("Подходят для семьи с детьми?",
     "Да, при правильном подходе. Щенки знакомы с детьми с рождения."),
    ("Что входит при покупке?",
     "Документы, ветпаспорт, официальный договор и фирменный чек-лист "
     "нового хозяина на 24 страницы со скидками от партнёров."),
    ("Есть ли доставка?",
     "Да — по всей России и за границу. Для далёких городов возможно "
     "онлайн-знакомство со щенком перед бронью."),
]

# ── Состояние клиентов (согласие ПДн) ───────────────────────────────────────
_state: dict[str, dict[str, Any]] = {}


def load_state() -> None:
    global _state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            _state = json.load(f)
    except Exception:
        _state = {}


def save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
    except Exception:
        log.warning("save_state failed", exc_info=True)


def user_state(chat_id: int) -> dict[str, Any]:
    return _state.setdefault(str(chat_id), {})


# ── HTTP helpers ────────────────────────────────────────────────────────────
def _http(method: str, url: str, payload: Optional[dict] = None,
          headers: Optional[dict] = None, timeout: float = 35.0) -> tuple[int, Any]:
    data = None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        log.warning("http %s %s failed: %s", method, url.split("?")[0], e)
        return 0, {}


# ── Telegram API ────────────────────────────────────────────────────────────
def tg(method: str, payload: dict) -> Any:
    status, data = _http("POST", f"{TG_API}/{method}", payload)
    if status != 200:
        log.warning("tg %s -> %s %s", method, status, str(data)[:300])
    return data


def send(chat_id: int, text: str, keyboard: Optional[dict] = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000],
                               "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendMessage", payload)


def answer_callback(cb_id: str) -> None:
    tg("answerCallbackQuery", {"callback_query_id": cb_id})


# ── Клавиатуры ──────────────────────────────────────────────────────────────
def main_menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "🐾 Смотреть щенков", "callback_data": "puppies"}],
        [{"text": "💰 Цена и рассрочка", "callback_data": "price"}],
        [{"text": "ℹ️ О питомнике", "callback_data": "about"}],
        [{"text": "❓ Вопрос / ответ", "callback_data": "faq"}],
        [{"text": "📞 Связаться с менеджером", "callback_data": "contact"}],
    ]}


def back_menu() -> dict:
    return {"inline_keyboard": [[{"text": "◀️ В меню", "callback_data": "menu"}]]}


def consent_kb() -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Согласен, продолжить", "callback_data": "consent"}],
    ]}


def phone_kb() -> dict:
    return {
        "keyboard": [[{"text": "📱 Отправить телефон", "request_contact": True}]],
        "resize_keyboard": True, "one_time_keyboard": True,
    }


# ── amoCRM ──────────────────────────────────────────────────────────────────
def amo_headers() -> dict:
    return {"Authorization": f"Bearer {AMO_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/hal+json"}


def amo_create_contact(name: str, phone: str) -> Optional[int]:
    if not AMO_TOKEN:
        log.warning("AMO_TOKEN пуст — пропуск создания контакта")
        return None
    body = [{
        "name": name or "Клиент из TG-бота",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]},
            {"field_id": F_CONTACT_SOURCE, "values": [{"value": SOURCE_VALUE}]},
            {"field_id": F_CONTACT_CONSENT, "values": [{"value": True}]},
            {"field_id": F_CONTACT_TG_BOT, "values": [{"value": True}]},
        ],
    }]
    status, data = _http("POST", f"{AMO_BASE}/api/v4/contacts", body, amo_headers())
    if status >= 400:
        log.warning("amo create_contact -> %s %s", status, str(data)[:400])
        return None
    try:
        return int(data["_embedded"]["contacts"][0]["id"])
    except Exception:
        log.warning("amo create_contact unexpected: %s", str(data)[:300])
        return None


def amo_create_lead(contact_id: int, name: str) -> Optional[int]:
    if not AMO_TOKEN or not contact_id:
        return None
    body = [{
        "name": f"Заявка из TG-бота — {name}"[:200],
        "pipeline_id": AMO_PIPELINE_SALES,
        "status_id": AMO_STAGE_NEW,
        "custom_fields_values": [
            {"field_id": F_LEAD_SOURCE, "values": [{"value": SOURCE_VALUE}]},
            {"field_id": F_LEAD_CONTACT_WAY, "values": [{"value": LEAD_WAY_VALUE}]},
        ],
        "_embedded": {"contacts": [{"id": contact_id, "is_main": True}]},
    }]
    status, data = _http("POST", f"{AMO_BASE}/api/v4/leads", body, amo_headers())
    if status >= 400:
        log.warning("amo create_lead -> %s %s", status, str(data)[:400])
        return None
    try:
        return int(data["_embedded"]["leads"][0]["id"])
    except Exception:
        return None


def push_to_amo(chat_id: int, name: str, phone: str) -> None:
    try:
        cid = amo_create_contact(name, phone)
        lid = amo_create_lead(cid, name) if cid else None
        log.info("amo: contact=%s lead=%s (chat=%s)", cid, lid, chat_id)
    except Exception:
        log.warning("push_to_amo failed", exc_info=True)


# ── Обработчики ─────────────────────────────────────────────────────────────
GREETING = (
    "🐾 Добро пожаловать в питомник <b>Keris</b>!\n\n"
    "Здесь вы можете посмотреть наших щенков мальтипу, узнать про цену и "
    "рассрочку и связаться с менеджером.\n\n"
    "Перед началом подтвердите согласие на обработку персональных данных "
    "(нужно, чтобы менеджер мог с вами связаться)."
)


def show_menu(chat_id: int) -> None:
    send(chat_id, "Чем можем помочь? Выберите раздел 👇", main_menu())


def handle_start(chat_id: int) -> None:
    st = user_state(chat_id)
    if st.get("consent"):
        show_menu(chat_id)
    else:
        send(chat_id, GREETING, consent_kb())


def handle_callback(chat_id: int, cb_id: str, data: str) -> None:
    answer_callback(cb_id)
    st = user_state(chat_id)

    if data == "consent":
        st["consent"] = True
        save_state()
        send(chat_id, "Спасибо! Согласие принято ✅")
        show_menu(chat_id)
        return

    if not st.get("consent"):
        send(chat_id, GREETING, consent_kb())
        return

    if data == "menu":
        show_menu(chat_id)
    elif data == "about":
        send(chat_id, ABOUT_TEXT, back_menu())
    elif data == "price":
        send(chat_id, PRICE_TEXT, back_menu())
    elif data == "puppies":
        send(chat_id, "🐾 <b>Наши малыши</b> (примеры для демо):", None)
        for p in PUPPIES:
            send(chat_id, p["text"], None)
        send(chat_id, "Понравился кто-то? Свяжитесь с менеджером 👇", main_menu())
    elif data == "faq":
        parts = [f"<b>{q}</b>\n{a}" for q, a in FAQ]
        send(chat_id, "❓ <b>Частые вопросы</b>\n\n" + "\n\n".join(parts), back_menu())
    elif data == "contact":
        send(chat_id,
             "Оставьте телефон — менеджер свяжется с вами и подберёт щенка "
             "под ваш запрос. Нажмите кнопку ниже 👇",
             phone_kb())
    else:
        show_menu(chat_id)


def handle_contact(chat_id: int, contact: dict, from_user: dict) -> None:
    phone = str(contact.get("phone_number") or "").strip()
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    name = " ".join(x for x in [from_user.get("first_name"),
                                from_user.get("last_name")] if x) or "Клиент из TG-бота"
    st = user_state(chat_id)
    st["phone"] = phone
    st["name"] = name
    save_state()
    send(chat_id,
         "Спасибо! 🎉 Заявка принята — менеджер свяжется с вами в ближайшее время.\n\n"
         f"А пока можете написать напрямую: @{MANAGER_USERNAME}",
         {"remove_keyboard": True})
    push_to_amo(chat_id, name, phone)
    show_menu(chat_id)


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    from_user = msg.get("from", {})
    if "contact" in msg:
        handle_contact(chat_id, msg["contact"], from_user)
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/start"):
        handle_start(chat_id)
    elif text.startswith("/menu"):
        if user_state(chat_id).get("consent"):
            show_menu(chat_id)
        else:
            send(chat_id, GREETING, consent_kb())
    else:
        handle_start(chat_id)


def handle_update(upd: dict) -> None:
    try:
        if "message" in upd:
            handle_message(upd["message"])
        elif "callback_query" in upd:
            cb = upd["callback_query"]
            handle_callback(cb["message"]["chat"]["id"], cb["id"], cb.get("data", ""))
    except Exception:
        log.warning("handle_update failed: %s", str(upd)[:300], exc_info=True)


def main() -> None:
    load_state()
    log.info("keris-bot запущен (long polling). amo=%s", "on" if AMO_TOKEN else "off")
    # снять возможный webhook, чтобы long polling работал
    _http("POST", f"{TG_API}/deleteWebhook", {"drop_pending_updates": False})
    offset = 0
    while True:
        try:
            status, data = _http(
                "GET",
                f"{TG_API}/getUpdates?offset={offset}&timeout=30",
                None, None, timeout=40.0,
            )
            if status != 200 or not isinstance(data, dict) or not data.get("ok"):
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                handle_update(upd)
        except Exception:
            log.warning("poll loop error", exc_info=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
