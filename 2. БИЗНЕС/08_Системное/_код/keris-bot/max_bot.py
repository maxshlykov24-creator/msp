#!/usr/bin/env python3
"""keris-max-bot — зеркало @kerisclubbot в MAX.

Отличия от Telegram:
- согласие ПДн — обычная кнопка, без request_contact / «поделиться номером»;
- номер клиент пишет текстом, любой бытовой формат → +7XXXXXXXXXX;
- «Записаться» — только ссылка на сайт (без open_app и без ухода в Telegram).
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

from phone import extract_phone_from_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("keris-max-bot")

MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
if not MAX_TOKEN:
    raise SystemExit("MAX_BOT_TOKEN не задан — см. /root/keris-max-bot/.env")

STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-max-bot/state.json")
BOOKING_URL = os.environ.get("BOOKING_URL", "https://194.87.118.214.sslip.io/").strip().rstrip("/") + "/"
API_BASE = os.environ.get("KERIS_SERVER_URL", "https://194.87.118.214.sslip.io").strip().rstrip("/")
MANAGER_MAX_URL = os.environ.get("MANAGER_MAX_URL", "").strip()
SITE_URL = os.environ.get("SITE_URL", "https://kerisclub.ru").strip().rstrip("/")
CONSENT_URL = "https://www.kerisclub.ru/agreement"
PRIVACY_URL = "https://www.kerisclub.ru/privacy_policy"

# platform-api2.max.ru требует корневой Минцифры; на VPS проходит platform-api.max.ru.
MAX_API = "https://platform-api.max.ru"
POLL_TIMEOUT = 30

# ── Контент (как у Telegram-бота, без ухода в t.me) ─────────────────────────
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
    f'<b>💌 <a href="{MANAGER_MAX_URL or SITE_URL}">Напишите нам</a> — с удовольствием '
    "расскажем о понравившемся щенке, его стоимости "
    "и поможем подобрать малыша, который подойдет именно вам.</b>"
)

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
    f'<b>💌 Не нашли ответ на свой вопрос? <a href="{MANAGER_MAX_URL or SITE_URL}">'
    "Напишите нам</a> — будем рады помочь.</b>"
)

GREETING = (
    "🐾 <b>Рады приветствовать вас в Keris Club!</b>\n\n"
    "Здесь вы можете познакомиться с нашими щенками Teddy Maltipoo F1, "
    "узнать стоимость, условия рассрочки и оставить заявку. "
    "Менеджер свяжется с вами в удобное время. "
    "А также можете записать вашего питомца на груминг.\n\n"
    "Нажимая кнопку, вы соглашаетесь на обработку персональных данных. "
    "Это нужно, чтобы мы могли связаться с вами и ответить на вопросы.\n\n"
    f'<a href="{CONSENT_URL}">Согласие</a> · '
    f'<a href="{PRIVACY_URL}">Политика</a>'
)

PHONE_ASK = (
    "Напишите номер телефона, чтобы мы узнали вас и могли связаться.\n\n"
    "Формат: <b>+79991234567</b>"
)

PHONE_RETRY = (
    "Не распознала номер. Напишите в формате <b>+79991234567</b>."
)

PHONE_SAVED = "Спасибо, номер сохранён."

BOOKING_TEXT = (
    "✂️ <b>Онлайн-запись на груминг</b>\n\n"
    "Откройте запись на сайте: выберите питомца, услугу, мастера и время. "
    "Подтверждение сразу, без звонков.\n\n"
    "Кнопка ниже ведёт на сайт, без перехода в другое приложение."
)


# ── Состояние ───────────────────────────────────────────────────────────────
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
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
    except Exception:
        log.warning("save_state failed", exc_info=True)


def user_state(user_id: int) -> dict[str, Any]:
    return _state.setdefault(str(user_id), {})


# ── HTTP ────────────────────────────────────────────────────────────────────
def _http(method: str, url: str, payload: Optional[dict] = None,
          headers: Optional[dict] = None, timeout: float = 40.0) -> tuple[int, Any]:
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"description": body}
        return e.code, parsed
    except Exception as e:
        log.warning("http %s %s failed: %s", method, url.split("?")[0], e)
        return 0, {}


def max_headers() -> dict[str, str]:
    return {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}


def max_get(path: str, params: Optional[dict] = None, timeout: float = 40.0) -> tuple[int, Any]:
    qs = f"?{urllib.parse.urlencode(params, doseq=True)}" if params else ""
    return _http("GET", f"{MAX_API}{path}{qs}", headers=max_headers(), timeout=timeout)


def max_post(path: str, payload: Optional[dict] = None, params: Optional[dict] = None,
             timeout: float = 15.0) -> tuple[int, Any]:
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    return _http("POST", f"{MAX_API}{path}{qs}", payload, max_headers(), timeout)


def api(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, Any]:
    return _http(method, f"{API_BASE}{path}", payload, timeout=10.0)


def send(user_id: int, text: str, buttons: Optional[list] = None) -> None:
    body: dict[str, Any] = {
        "text": text[:4000],
        "format": "html",
        "disable_link_preview": True,
    }
    if buttons:
        body["attachments"] = [
            {"type": "inline_keyboard", "payload": {"buttons": buttons}}
        ]
    status, data = max_post("/messages", body, {"user_id": str(user_id)})
    if status != 200:
        log.warning("send user=%s -> %s %s", user_id, status, str(data)[:300])


def answer_callback(callback_id: str, notification: str = "") -> None:
    payload: dict[str, Any] = {}
    if notification:
        payload["notification"] = notification[:200]
    status, data = max_post("/answers", payload or {"notification": " "}, {"callback_id": callback_id})
    if status != 200:
        log.warning("answer_callback -> %s %s", status, str(data)[:300])


# ── Клавиатуры MAX: только callback и link, без request_contact и open_app ──
def consent_kb() -> list:
    return [[{"type": "callback", "text": "Согласен", "payload": "consent"}]]


def main_menu() -> list:
    ask_url = MANAGER_MAX_URL or SITE_URL
    return [
        [{"type": "link", "text": "✂️ Записаться на груминг", "url": BOOKING_URL}],
        [{"type": "callback", "text": "🏡 О питомнике", "payload": "about"}],
        [{"type": "callback", "text": "💰 Стоимость и бронирование", "payload": "price"}],
        [{"type": "callback", "text": "❓ Вопрос / ответ", "payload": "faq"}],
        [{"type": "link", "text": "🐾 Смотреть щенков", "url": SITE_URL}],
        [{"type": "link", "text": "💌 Задать свой вопрос", "url": ask_url}],
    ]


def booking_kb() -> list:
    return [
        [{"type": "link", "text": "✂️ Открыть запись на сайте", "url": BOOKING_URL}],
        [{"type": "callback", "text": "‹ В меню", "payload": "menu"}],
    ]


def back_menu() -> list:
    return [[{"type": "callback", "text": "‹ В меню", "payload": "menu"}]]


# ── Сценарии ────────────────────────────────────────────────────────────────
def show_menu(user_id: int) -> None:
    send(user_id, "Чем можем помочь? Выберите раздел 👇", main_menu())


def ask_phone(user_id: int, retry: bool = False) -> None:
    send(user_id, PHONE_RETRY if retry else PHONE_ASK, buttons=None)


def handle_start(user_id: int, open_booking: bool = False) -> None:
    st = user_state(user_id)
    if open_booking:
        st["open_booking"] = True
        save_state()
    if st.get("phone"):
        if st.pop("open_booking", None):
            save_state()
            send(user_id, BOOKING_TEXT, booking_kb())
            return
        show_menu(user_id)
        return
    if st.get("consent"):
        ask_phone(user_id)
        return
    send(user_id, GREETING, consent_kb())


def bind_phone(user_id: int, raw: str) -> bool:
    phone = extract_phone_from_text(raw)
    if not phone:
        return False
    status, res = api("POST", "/api/max/bind", {"phone": phone, "user_id": user_id})
    if not status or status >= 300:
        msg_err = res.get("detail") if isinstance(res, dict) else ""
        send(user_id, f"Не получилось сохранить номер: {msg_err or 'сервер недоступен'}. Попробуйте ещё раз.")
        return True
    st = user_state(user_id)
    st["consent"] = True
    st["phone"] = (res or {}).get("phone") or phone
    st["phone_tries"] = 0
    save_state()
    send(user_id, PHONE_SAVED)
    if st.pop("open_booking", None):
        save_state()
        send(user_id, BOOKING_TEXT, booking_kb())
        return True
    show_menu(user_id)
    return True


def handle_rsvp(user_id: int, callback_id: str, booking_id: str) -> None:
    status, res = api("POST", f"/api/bookings/{urllib.parse.quote(booking_id)}/confirm")
    if status and 200 <= status < 300:
        answer_callback(callback_id, "Спасибо! Ждём вас 🐾")
        send(user_id, "✅ Приход подтверждён. Ждём вас в Keris Club.")
    else:
        msg = res.get("detail") if isinstance(res, dict) else ""
        answer_callback(callback_id, f"Не получилось: {msg or 'сервер недоступен'}")


def handle_callback(user_id: int, callback_id: str, data: str) -> None:
    if data.startswith("rsvp:"):
        handle_rsvp(user_id, callback_id, data.split(":", 1)[1])
        return
    answer_callback(callback_id)
    st = user_state(user_id)

    if data == "consent":
        st["consent"] = True
        st["phone_tries"] = 0
        save_state()
        ask_phone(user_id)
        return

    if not st.get("phone"):
        handle_start(user_id)
        return

    if data == "menu":
        show_menu(user_id)
    elif data == "booking":
        send(user_id, BOOKING_TEXT, booking_kb())
    elif data == "about":
        send(user_id, ABOUT_TEXT, back_menu())
    elif data == "price":
        send(user_id, PRICE_TEXT, back_menu())
    elif data == "faq":
        parts = [f"<b>{q}</b>\n{a}" for q, a in FAQ]
        send(user_id, "❓ <b>Частые вопросы</b>\n\n" + "\n\n".join(parts) + "\n\n" + FAQ_FOOTER, back_menu())
    else:
        show_menu(user_id)


def handle_text(user_id: int, text: str) -> None:
    low = text.strip().lower()
    if low in {"/start", "start", "/menu", "menu"}:
        handle_start(user_id)
        return
    if low in {"/booking", "/zapis", "booking", "zapis"}:
        handle_start(user_id, open_booking=True)
        return

    st = user_state(user_id)
    if st.get("phone"):
        show_menu(user_id)
        return
    if not st.get("consent"):
        if low in {"согласен", "согласна", "да", "ok", "ок"}:
            st["consent"] = True
            st["phone_tries"] = 0
            save_state()
            ask_phone(user_id)
            return
        handle_start(user_id)
        return

    if bind_phone(user_id, text):
        return
    tries = int(st.get("phone_tries") or 0) + 1
    st["phone_tries"] = tries
    save_state()
    ask_phone(user_id, retry=True)


def process_event(event: dict[str, Any]) -> None:
    etype = event.get("update_type") or event.get("updateType") or ""
    log.info("event type=%s", etype)
    if etype == "bot_started":
        user = event.get("user") or {}
        uid = user.get("user_id") or user.get("userId")
        if uid:
            payload = str(event.get("payload") or "").lower()
            handle_start(int(uid), open_booking=payload in {"booking", "grooming", "zapis"})
        return

    if etype == "message_created":
        msg = event.get("message") or {}
        sender = msg.get("sender") or {}
        uid = sender.get("user_id") or sender.get("userId")
        if not uid:
            return
        text = ((msg.get("body") or {}).get("text") or "").strip()
        if text:
            handle_text(int(uid), text)
        return

    if etype == "message_callback":
        cb = event.get("callback") or {}
        uid = (cb.get("user") or {}).get("user_id") or (cb.get("user") or {}).get("userId")
        if not uid:
            return
        payload = str(cb.get("payload") or "")
        callback_id = str(cb.get("callback_id") or cb.get("callbackId") or "")
        handle_callback(int(uid), callback_id, payload)


def main() -> None:
    load_state()
    status, me = max_get("/me")
    uname = (me or {}).get("username") or (me or {}).get("name") or "?"
    log.info("keris-max-bot запущен username=%s booking=%s api=%s me=%s", uname, BOOKING_URL, API_BASE, status)
    marker: Optional[int] = None
    while True:
        try:
            params: dict[str, Any] = {
                "timeout": POLL_TIMEOUT,
                "limit": 100,
                "types": "bot_started,message_created,message_callback",
            }
            if marker is not None:
                params["marker"] = marker
            status, data = max_get("/updates", params, timeout=POLL_TIMEOUT + 10)
            if status != 200 or not isinstance(data, dict):
                log.warning("getUpdates -> %s %s", status, str(data)[:200])
                time.sleep(3)
                continue
            nxt = data.get("marker")
            if nxt is not None:
                marker = nxt
            for event in data.get("updates") or []:
                try:
                    process_event(event)
                except Exception:
                    log.warning("process_event failed: %s", str(event)[:300], exc_info=True)
        except Exception:
            log.warning("poll loop error", exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
