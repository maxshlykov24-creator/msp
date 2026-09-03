#!/usr/bin/env python3
"""keris-bot — демо-прототип клиентского бота питомника Keris Club (мальтипу).

Фаза 1a (демо): витрина щенков (по одному, с навигацией), стоимость/бронирование,
«О питомнике», FAQ. Номер телефона бот не собирает — кнопка «Задать свой
вопрос» и контекстные ссылки в тексте ведут прямиком в Telegram-диалог
с менеджером с готовым черновиком сообщения. Переписка и лиды видны в
amoCRM через подключённый к аккаунту канал (без API-вызовов из бота).

Реализация — только stdlib (urllib), long polling. Без внешних зависимостей,
чтобы разворачиваться на чистой Ubuntu без pip. Состояние клиентов — в JSON.
Тексты и тон взяты из анализа канала (КОНТЕНТ_ИЗ_КАНАЛА_ДЛЯ_БОТА.md).

Этот же токен = клиентский бот груминга (Этап 2): напоминания 24ч/3ч шлёт сам
keris-server (RU, app/reminders.py) напрямую через Telegram API. На /start клиент
делится номером (request_contact) → POST /api/telegram/bind. Кнопку
«Подтверждаю» в напоминаниях ловит этот процесс (callback_query "rsvp:<id>").
"""
from __future__ import annotations

import json
import logging
import os
import socket
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

# IPv6 сначала: api.telegram.org с RU по IPv4 таймаутится, зарубежный VPS ботов —
# IPv6-only. Для api.telegram.org это единственный вариант; для API keris-server
# (RU, sslip.io) есть /etc/hosts с IPv6-записью, но на случай, если её не будет,
# даём fallback на IPv4 — так же, как в keris-admin-bot.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6_first(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    if family == 0:
        try:
            return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
        except socket.gaierror:
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv6_first

# ── Конфиг (только окружение / .env на сервере) ────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан — см. /root/keris-bot/.env")
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "keris_chat")
STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-bot/state.json")
# Онлайн-запись на груминг (тот же URL — Mini App в Telegram и страница в браузере).
BOOKING_URL = os.environ.get("BOOKING_URL", "https://194.87.118.214.sslip.io/").strip().rstrip("/") + "/"
# API keris-server (RU) — сюда уходит подтверждение записи по кнопке в напоминании.
API_BASE = os.environ.get("KERIS_SERVER_URL", "https://194.87.118.214.sslip.io").strip().rstrip("/")
# Канал с витриной щенков: https://t.me/kerisclub · пост — /115
CHANNEL_URL = "https://t.me/kerisclub"
PUPPIES_POST_URL = "https://t.me/kerisclub/115"

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def manager_url(prefill: str) -> str:
    """Ссылка на диалог с менеджером в Telegram с готовым черновиком текста."""
    return f"https://t.me/{MANAGER_USERNAME}?text={urllib.parse.quote(prefill)}"


def manager_link(anchor: str, prefill: str) -> str:
    """HTML-ссылка на менеджера — открывает диалог напрямую в Telegram,
    с черновиком сообщения под контекст экрана, с которого перешли."""
    return f'<a href="{manager_url(prefill)}">{anchor}</a>'

# ── Контент бота ──────────────────────────────────────────────────────────
# Тон — как у Карины в канале: тёплый, семейный, без канцелярита. Эмодзи —
# не декор, а структурные маркеры (📍 локация, 🚚 доставка, 💉 прививки,
# 💌 контакт, 🏷 статус) — так же, как в постах канала. Премиальность даёт
# темп (пауза «печатает…») и лаконичность абзацев, а не сухость текста.
ABOUT_TEXT = (
    "🐾 <b>Keris Club- это авторский питомник Teddy Maltipoo F1.</b>\n\n"
    "Keris Club — это пространство, где рождаются не просто красивые щенки, "
    "а будущие любимые члены семьи.\n\n"
    "Мы специализируемся исключительно на мальтипу F1 и более 8 лет занимаемся "
    "разведением породы. В основе нашей работы — ответственная селекция, лучшие "
    "линии Кореи и Китая, внимание к здоровью, психике, темпераменту и "
    "гармоничному развитию каждого малыша.\n\n"
    "Для нас важно не просто найти щенку новый дом, а помочь каждой семье "
    "встретить именно своего друга на долгие годы.\n\n"
    "🤍 <b>Будем рады познакомиться с вами и помочь найти вашего малыша.</b>"
)

PRICE_TEXT = (
    "💰 <b>Стоимость и условия бронирования</b>\n\n"
    "Стоимость щенков <b>начинается от 250 000 ₽</b> и зависит от размера "
    "(<b>стандарт, мини или микро</b>) и экстерьера.\n\n"
    "📌 Бронирование — <b>30%</b> от стоимости по договору. С этого момента щенок "
    "закрепляется за вашей семьёй.\n\n"
    "💳 <b>Беспроцентная рассрочка</b> предоставляется через банк на срок "
    "<b>от 3 до 6 месяцев</b>.\n\n"
    "<b>💌 " + manager_link(
        "Напишите нам",
        "Здравствуйте! Пишу из бота Keris Club — интересует стоимость щенка.",
    ) + " — с удовольствием расскажем о понравившемся щенке, его стоимости "
    "и поможем подобрать малыша, который подойдет именно вам.</b>"
)

PUPPY_CTA = (
    "\n\n<b>Ваше знакомство может начаться прямо сейчас. "
    + manager_link(
        "Напишите нам",
        "Здравствуйте! Пишу из бота Keris Club — хочу узнать о щенке.",
    )
    + " — с радостью расскажем о понравившемся щенке. 🐾</b>"
)

# Каждая карточка — «photo»/«video» (file_id или URL) добавим, когда придут
# материалы от Карины: show_puppy() сама переключится на sendPhoto/sendVideo
# с той же подписью, без изменений в тексте и навигации.
PUPPIES = [
    {
        "name": "Флокс 🩵",
        "photo": None,
        "video": None,
        "text": (
            "<b>Флокс</b> 🩵 — в поисках семьи.\n\n"
            "Ласковый компаньон с типичной baby-face мордочкой: спокойный, "
            "любит быть рядом, отлично ладит с детьми.\n\n"
            "Окрас gold · мини · взрослый вес до 3.5 кг\n"
            "💉 Привит по возрасту, документы и ветпаспорт\n\n"
            "🏷 Свободен (пример для демо)"
        ),
    },
    {
        "name": "Айс 🤍",
        "photo": None,
        "video": None,
        "text": (
            "<b>Айс</b> 🤍 — фирменный окрас питомника.\n\n"
            "Игривый и любознательный, быстро освоился с пелёнкой — из тех, "
            "кто сразу тянется знакомиться.\n\n"
            "Окрас ice gold · микро · взрослый вес до 2.5 кг\n"
            "💉 Привит по возрасту, документы и ветпаспорт\n\n"
            "🏷 Свободен (пример для демо)"
        ),
    },
    {
        "name": "Мокко 🤎",
        "photo": None,
        "video": None,
        "text": (
            "<b>Мокко</b> 🤎 — редкий тёплый окрас.\n\n"
            "Дружелюбный, легко обучаемый, тянется к человеку — из тех "
            "щенков, что быстро становятся частью семьи.\n\n"
            "Окрас gold brown · мини · взрослый вес до 3 кг\n"
            "💉 Привит по возрасту, документы и ветпаспорт\n\n"
            "🏷 Свободен (пример для демо)"
        ),
    },
]

FAQ = [
    ("Подходят ли мальтипу людям с аллергией?",
     "Мальтипу считаются условно гипоаллергенной породой благодаря отсутствию "
     "сезонной линьки и минимальному количеству подшёрстка. Однако реакция "
     "организма всегда индивидуальна."),
    ("Легко ли они обучаются?",
     "Да. Мальтипу ориентированы на человека, быстро учатся и с удовольствием "
     "взаимодействуют с семьёй."),
    ("Приучены ли щенки к пелёнке?",
     "Да. К моменту переезда все наши малыши уже приучены к пелёнке и знакомы "
     "с основными бытовыми правилами."),
    ("Подойдут ли для семьи с детьми?",
     "Да. Щенки с первых дней жизни растут рядом с людьми, проходят раннюю "
     "социализацию и при правильном отношении прекрасно чувствуют себя "
     "в семьях с детьми."),
    ("Что входит при покупке щенка?",
     "Каждый малыш переезжает домой с ветеринарным паспортом, документами, "
     "официальным договором, рекомендациями по уходу и авторским чек-листом "
     "нового владельца на 24 страницы."),
    ("Есть ли доставка?",
     "Да. Мы организуем бережную доставку по всей России и за границу. "
     "Если вы находитесь далеко, сначала можно познакомиться со щенком онлайн."),
]

FAQ_FOOTER = (
    "<b>💌 Не нашли ответ на свой вопрос? "
    + manager_link(
        "Напишите нам",
        "Здравствуйте! Пишу из бота Keris Club — у меня есть вопрос.",
    )
    + " — будем рады помочь.</b>"
)

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
          headers: Optional[dict] = None, timeout: float = 15.0) -> tuple[int, Any]:
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
def tg(method: str, payload: dict, timeout: float = 15.0, retries: int = 1) -> Any:
    data: Any = {}
    status = 0
    for attempt in range(max(1, retries)):
        status, data = _http("POST", f"{TG_API}/{method}", payload, timeout=timeout)
        if status == 200:
            return data
        if attempt + 1 < retries:
            time.sleep(1.2 * (attempt + 1))
    if status != 200:
        log.warning("tg %s -> %s %s", method, status, str(data)[:300])
    return data


def send(chat_id: int, text: str, keyboard: Optional[dict] = None, typing: bool = False) -> None:
    # typing по умолчанию выкл: sendChatAction с EU часто таймаутится на 15с
    # и /start «молчит», хотя апдейт уже обработан.
    if typing:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=3)
        time.sleep(min(0.4, 0.15 + len(text) / 900))
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000],
                               "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendMessage", payload, timeout=20.0, retries=3)


def api(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, Any]:
    return _http(method, f"{API_BASE}{path}", payload, timeout=10.0)


def send_photo(chat_id: int, photo: str, caption: str, keyboard: Optional[dict] = None) -> None:
    tg("sendChatAction", {"chat_id": chat_id, "action": "upload_photo"})
    time.sleep(0.4)
    payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo,
                               "caption": caption[:1024], "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendPhoto", payload)


def send_video(chat_id: int, video: str, caption: str, keyboard: Optional[dict] = None) -> None:
    tg("sendChatAction", {"chat_id": chat_id, "action": "upload_video"})
    time.sleep(0.4)
    payload: dict[str, Any] = {"chat_id": chat_id, "video": video,
                               "caption": caption[:1024], "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendVideo", payload)


def answer_callback(cb_id: str, text: str = "") -> None:
    payload: dict[str, Any] = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text[:200]
        payload["show_alert"] = True
    tg("answerCallbackQuery", payload)


# ── Клавиатуры ──────────────────────────────────────────────────────────────
def main_menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "✂️ Записаться на груминг", "callback_data": "booking"}],
        [{"text": "🏡 О питомнике", "callback_data": "about"}],
        [{"text": "💰 Стоимость и бронирование", "callback_data": "price"}],
        [{"text": "❓ Вопрос / ответ", "callback_data": "faq"}],
        [{"text": "🐾 Смотреть щенков", "url": PUPPIES_POST_URL}],
        [{"text": "💌 Задать свой вопрос", "url": manager_url(
            "Здравствуйте! Пишу из бота Keris Club — у меня есть вопрос.")}],
    ]}


def booking_kb() -> dict:
    """Два входа: Mini App (если Telegram открывает) и обычная ссылка в браузер."""
    return {"inline_keyboard": [
        [{"text": "✂️ Открыть запись в Telegram", "web_app": {"url": BOOKING_URL}}],
        [{"text": "🌐 Открыть в браузере", "url": BOOKING_URL}],
        [{"text": "‹ В меню", "callback_data": "menu"}],
    ]}


def back_menu() -> dict:
    return {"inline_keyboard": [[{"text": "‹ В меню", "callback_data": "menu"}]]}


def set_menu_button() -> None:
    tg("setChatMenuButton", {
        "menu_button": {
            "type": "web_app",
            "text": "Запись",
            "web_app": {"url": BOOKING_URL},
        }
    })


BOOKING_TEXT = (
    "✂️ <b>Онлайн-запись на груминг</b>\n\n"
    "Выберите удобный способ:\n"
    "• <b>В Telegram</b> — мини-приложение прямо в чате\n"
    "• <b>В браузере</b> — если приложение не открывается "
    f"(та же страница: {BOOKING_URL.rstrip('/')})\n\n"
    "Выберите питомца, услугу, мастера и время — подтверждение сразу."
)


def consent_kb() -> dict:
    """request_contact работает только на reply-клавиатуре, не на inline."""
    return {
        "keyboard": [[{"text": "Согласен и поделиться номером", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def puppy_kb(idx: int) -> dict:
    total = len(PUPPIES)
    nav = []
    if idx > 0:
        nav.append({"text": "‹", "callback_data": f"puppy:{idx - 1}"})
    nav.append({"text": f"{idx + 1} / {total}", "callback_data": "noop"})
    if idx < total - 1:
        nav.append({"text": "›", "callback_data": f"puppy:{idx + 1}"})
    name = PUPPIES[idx]["name"]
    ask_url = manager_url(f"Здравствуйте! Пишу из бота Keris Club — интересует {name}.")
    return {"inline_keyboard": [
        nav,
        [{"text": f"💌 Спросить про {name}", "url": ask_url}],
        [{"text": "‹ В меню", "callback_data": "menu"}],
    ]}


# ── Обработчики ─────────────────────────────────────────────────────────────
GREETING = (
    "🐾 <b>Рады приветствовать вас в Keris Club!</b>\n\n"
    "Здесь вы можете познакомиться с нашими щенками Teddy Maltipoo F1, "
    "узнать стоимость, условия рассрочки и оставить заявку. "
    "Менеджер свяжется с вами в удобное время. "
    "А также можете записать вашего питомца на груминг и получать напоминания.\n\n"
    "Для начала, пожалуйста, подтвердите согласие на обработку персональных данных "
    "и предоставьте ваш номер телефона. Это необходимо, чтобы мы могли связаться "
    "с вами и ответить на все вопросы."
)

PHONE_NEEDED = (
    "Чтобы присылать подтверждение и напоминания о записи, "
    "поделитесь номером телефона кнопкой ниже."
)

PHONE_CATCH_UP = (
    "🐾 <b>Мы уже знакомы: вы открывали бота Keris Club.</b>\n\n"
    "Чтобы найти вашу запись и присылать подтверждение и напоминания о визите, "
    "нам нужен номер телефона. Ник в Telegram для этого не подходит.\n\n"
    "Пожалуйста, подтвердите согласие на обработку персональных данных "
    "и поделитесь номером кнопкой ниже. Это нужно, чтобы мы могли связаться "
    "с вами и ничего не пропустить."
)


def show_menu(chat_id: int) -> None:
    send(chat_id, "Чем можем помочь? Выберите раздел 👇", main_menu())


def show_puppy(chat_id: int, idx: int) -> None:
    idx = idx % len(PUPPIES)
    p = PUPPIES[idx]
    kb = puppy_kb(idx)
    caption = p["text"] + PUPPY_CTA
    if p.get("video"):
        send_video(chat_id, p["video"], caption, kb)
    elif p.get("photo"):
        send_photo(chat_id, p["photo"], caption, kb)
    else:
        send(chat_id, caption, kb)


def handle_start(chat_id: int) -> None:
    st = user_state(chat_id)
    if st.get("phone"):
        show_menu(chat_id)
        return
    if st.get("consent"):
        send(chat_id, PHONE_NEEDED, consent_kb())
        return
    send(chat_id, GREETING, consent_kb())


def handle_rsvp(chat_id: int, message_id: int, cb_id: str, booking_id: str) -> None:
    """Кнопка «Подтверждаю приход» в напоминании о визите (24ч/3ч) — шлём в keris-server,
    он проставит attendance=2 в YCLIENTS. Кнопка нужна только тут, до основного меню/
    согласия ПДн эта запись не относится, поэтому обрабатываем раньше остальной логики."""
    status, res = api("POST", f"/api/bookings/{urllib.parse.quote(booking_id)}/confirm")
    if status and 200 <= status < 300:
        answer_callback(cb_id, "Спасибо! Ждём вас 🐾")
        tg("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id,
            "reply_markup": {"inline_keyboard": [[{"text": "✅ Приход подтверждён", "callback_data": "noop"}]]},
        })
    else:
        msg = res.get("detail") if isinstance(res, dict) else ""
        answer_callback(cb_id, f"Не получилось подтвердить: {msg or 'сервер недоступен'}. Попробуйте позже.")


def handle_callback(chat_id: int, message_id: int, cb_id: str, data: str) -> None:
    if data.startswith("rsvp:"):
        handle_rsvp(chat_id, message_id, cb_id, data.split(":", 1)[1])
        return
    answer_callback(cb_id)
    st = user_state(chat_id)

    if data == "consent":
        st["consent"] = True
        save_state()
        send(chat_id, PHONE_NEEDED, consent_kb())
        return

    if not st.get("phone"):
        handle_start(chat_id)
        return

    if data == "menu":
        show_menu(chat_id)
    elif data == "booking":
        send(chat_id, BOOKING_TEXT, booking_kb())
    elif data == "about":
        send(chat_id, ABOUT_TEXT, back_menu())
    elif data == "price":
        send(chat_id, PRICE_TEXT, back_menu())
    elif data == "puppies":
        show_puppy(chat_id, 0)
    elif data.startswith("puppy:"):
        show_puppy(chat_id, int(data.split(":", 1)[1]))
    elif data == "noop":
        pass
    elif data == "faq":
        parts = [f"<b>{q}</b>\n{a}" for q, a in FAQ]
        send(chat_id,
             "❓ <b>Частые вопросы</b>\n\n" + "\n\n".join(parts)
             + "\n\n" + FAQ_FOOTER,
             back_menu())
    else:
        show_menu(chat_id)


def handle_contact(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    from_id = (msg.get("from") or {}).get("id")
    contact = msg.get("contact") or {}
    if not contact.get("user_id") or contact.get("user_id") != from_id:
        send(chat_id, "Нужен ваш номер, привязанный к Telegram. Нажмите кнопку ниже.", consent_kb())
        return
    phone = contact.get("phone_number") or ""
    status, res = api("POST", "/api/telegram/bind", {"phone": phone, "chat_id": chat_id})
    if not status or status >= 300:
        msg_err = res.get("detail") if isinstance(res, dict) else ""
        send(chat_id, f"Не получилось сохранить номер: {msg_err or 'сервер недоступен'}. Попробуйте ещё раз.", consent_kb())
        return
    st = user_state(chat_id)
    st["consent"] = True
    st["phone"] = (res or {}).get("phone") or phone
    save_state()
    if st.pop("open_booking", None):
        save_state()
        send(chat_id, BOOKING_TEXT, booking_kb())
        return
    show_menu(chat_id)


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    if msg.get("contact"):
        handle_contact(msg)
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/start"):
        payload = text.split(maxsplit=1)[1].strip().lower() if " " in text else ""
        if payload in {"booking", "grooming", "zapis"}:
            st = user_state(chat_id)
            if st.get("phone"):
                send(chat_id, BOOKING_TEXT, booking_kb())
                return
            st["open_booking"] = True
            save_state()
            handle_start(chat_id)
            return
        handle_start(chat_id)
    elif text.startswith("/booking") or text.startswith("/zapis"):
        st = user_state(chat_id)
        if st.get("phone"):
            send(chat_id, BOOKING_TEXT, booking_kb())
            return
        st["open_booking"] = True
        save_state()
        handle_start(chat_id)
    elif text.startswith("/menu"):
        handle_start(chat_id)
    else:
        handle_start(chat_id)


def handle_update(upd: dict) -> None:
    try:
        if "message" in upd:
            handle_message(upd["message"])
        elif "callback_query" in upd:
            cb = upd["callback_query"]
            handle_callback(cb["message"]["chat"]["id"], cb["message"]["message_id"], cb["id"], cb.get("data", ""))
    except Exception:
        log.warning("handle_update failed: %s", str(upd)[:300], exc_info=True)


# Явно запрашиваем типы апдейтов, иначе застрявший на стороне Telegram фильтр
# allowed_updates может не отдавать callback_query (кнопки «не реагируют»).
ALLOWED_UPDATES = urllib.parse.quote('["message","callback_query"]')


def main() -> None:
    load_state()
    log.info("keris-bot запущен (long polling), booking=%s, api=%s", BOOKING_URL, API_BASE)
    # снять возможный webhook, чтобы long polling работал
    _http("POST", f"{TG_API}/deleteWebhook", {"drop_pending_updates": False})
    set_menu_button()
    offset = 0
    while True:
        try:
            t_poll = time.time()
            status, data = _http(
                "GET",
                f"{TG_API}/getUpdates?offset={offset}&timeout=25"
                f"&allowed_updates={ALLOWED_UPDATES}",
                None, None, timeout=35.0,
            )
            if status != 200 or not isinstance(data, dict) or not data.get("ok"):
                log.warning("getUpdates -> %s (%.2fs)", status, time.time() - t_poll)
                time.sleep(3)
                continue
            results = data.get("result", [])
            if results:
                log.info("получено %d апдейт(ов) за %.2fs опроса", len(results), time.time() - t_poll)
            for upd in results:
                offset = max(offset, upd["update_id"] + 1)
                t_h = time.time()
                handle_update(upd)
                log.info("обработан update %s за %.2fs", upd.get("update_id"), time.time() - t_h)
        except Exception:
            log.warning("poll loop error", exc_info=True)
            time.sleep(3)


def ask_phone_missing(*, force: bool = False) -> None:
    """Разовая рассылка тем, кто уже писал боту, но номер так и не отдал."""
    load_state()
    sent = skipped = failed = 0
    for cid, st in list(_state.items()):
        if not str(cid).isdigit():
            skipped += 1
            continue
        if (st or {}).get("phone"):
            skipped += 1
            continue
        if not force and (st or {}).get("phone_nudge_sent"):
            skipped += 1
            continue
        chat_id = int(cid)
        result = tg("sendMessage", {
            "chat_id": chat_id,
            "text": PHONE_CATCH_UP,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": consent_kb(),
        }, timeout=20.0, retries=2)
        if isinstance(result, dict) and result.get("ok"):
            user_state(chat_id)["phone_nudge_sent"] = True
            sent += 1
        else:
            failed += 1
            log.warning("ask-phone не доставлено chat_id=%s: %s", chat_id, str(result)[:200])
        time.sleep(0.08)
    save_state()
    log.info("ask-phone готово: sent=%s skipped=%s failed=%s", sent, skipped, failed)


if __name__ == "__main__":
    import sys
    if "--ask-phone" in sys.argv:
        ask_phone_missing(force="--force" in sys.argv)
    else:
        main()
