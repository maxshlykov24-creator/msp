#!/usr/bin/env python3
"""Личный Telegram-бот Карины — админ-интерфейс Keris Club (Этап 2, груминг).

Уведомления (новая запись/отмена/перенос) шлёт сам keris-server напрямую
(app/notify_karina.py) — этот процесс отвечает только за входящие команды:
пошаговые диалоги с подтверждением, вызывающие admin-API keris-server.
Доступ — whitelist по Telegram ID (KARINA_TELEGRAM_IDS). Karина в CRM/YCLIENTS
не заходит — весь её интерфейс здесь.

Только stdlib (urllib), long polling — тот же стиль, что keris-bot/keris-booking-bot.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("keris-admin-bot")

# IPv6: api.telegram.org с RU по IPv4 таймаутится; зарубежный бот-VPS — IPv6-only.
# Admin-API к keris-server (RU) тоже должен резолвиться в IPv4/dual — для хоста
# с A-записью (sslip.io) AF_INET6 может не найтись; тогда fallback на dual-stack.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6_first(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    if family == 0:
        try:
            return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
        except socket.gaierror:
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv6_first

BOT_TOKEN = os.environ.get("KARINA_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("KARINA_BOT_TOKEN не задан — см. .env")

ALLOWED_IDS = {x.strip() for x in os.environ.get("KARINA_TELEGRAM_IDS", "").split(",") if x.strip()}
if not ALLOWED_IDS:
    log.warning("KARINA_TELEGRAM_IDS пуст — бот отвечать никому не будет, кроме отладки")

API_BASE = os.environ.get("KERIS_SERVER_URL", "http://127.0.0.1:8091").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-admin-bot/state.json")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

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


def reset_flow(chat_id: int) -> None:
    st = user_state(chat_id)
    st.pop("flow", None)
    st.pop("step", None)
    st.pop("data", None)
    save_state()


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


def tg(method: str, payload: dict) -> Any:
    status, data = _http("POST", f"{TG_API}/{method}", payload)
    if status != 200:
        log.warning("tg %s -> %s %s", method, status, str(data)[:300])
    return data


def api(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, Any]:
    return _http(method, f"{API_BASE}{path}", payload, headers={"X-Admin-Key": ADMIN_API_KEY})


def send(chat_id: int, text: str, keyboard: Optional[dict] = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendMessage", payload)


def answer_callback(cb_id: str, text: str = "") -> None:
    tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:200]})


# ── Клавиатуры ──────────────────────────────────────────────────────────────
def main_menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "📋 Записи сегодня", "callback_data": "bk:today"},
         {"text": "📋 Записи завтра", "callback_data": "bk:tomorrow"}],
        [{"text": "📅 Записи на дату", "callback_data": "flow:bookings_date"}],
        [{"text": "🧾 Скорректировать сумму", "callback_data": "flow:bookingprice"}],
        [{"text": "🎟 Абонемент клиента", "callback_data": "flow:subcheck"},
         {"text": "⏳ Продлить", "callback_data": "flow:subextend"}],
        [{"text": "🚫 Выходной мастера", "callback_data": "flow:dayoff"}],
        [{"text": "➕ Добавить мастера", "callback_data": "flow:master"}],
        [{"text": "💲 Изменить цену услуги", "callback_data": "flow:price"}],
        [{"text": "🔒 Закрыть день для записи", "callback_data": "flow:closeday"}],
        [{"text": "🐶 Добавить щенка", "callback_data": "flow:puppy"}],
    ]}


def cancel_kb() -> dict:
    return {"inline_keyboard": [[{"text": "✖️ Отменить", "callback_data": "cancel"}]]}


MASTERS_HINT = "ID мастера — коротко латиницей (например: anna, maria, sofia, olga)."


# ── Команды с результатом (без диалога) ─────────────────────────────────────
DAY_LABEL = {"today": "сегодня", "tomorrow": "завтра"}


def show_bookings(chat_id: int, when: str) -> None:
    status, data = api("GET", f"/admin/bookings?when={urllib.parse.quote(when)}")
    label = DAY_LABEL.get(when, when)
    if status != 200 or not isinstance(data, list):
        msg = data.get("detail") if isinstance(data, dict) else ""
        send(chat_id, f"⚠️ Не удалось получить записи с сервера. {msg}".strip(), main_menu())
        return
    if not data:
        send(chat_id, f"На {label} записей нет.", main_menu())
        return
    lines = [f"<b>Записи на {label}:</b>"]
    for b in data:
        pet = f"{b.get('pet') or 'питомец'} ({b.get('size', '')})".replace(" ()", "")
        head = f"\n<b>{b['time']}</b> · {b['service']} · {b.get('master_id', '')}"
        lines.append(head)
        lines.append(f"{b['owner']} · {b.get('phone', '')} · {pet}")
        if b.get("extras"):
            lines.append("Допы: " + ", ".join(b["extras"]))
        if b.get("gifts"):
            lines.append("🎁 Бесплатно: " + ", ".join(b["gifts"]))
        money = "по абонементу" if b.get("by_subscription") else f"{b['price']} ₽"
        if b.get("by_subscription"):
            money += f" (списано визитов: {b.get('visits_charged')})"
            if b.get("price"):
                money += f", доплата {b['price']} ₽"
        lines.append(f"💰 {money} · <code>{b['id']}</code>")
        if b.get("comment"):
            lines.append(f"💬 {b['comment']}")
        if b.get("admin_note"):
            lines.append(f"✏️ {b['admin_note']}")
    send(chat_id, "\n".join(lines), main_menu())


def show_subscriptions(chat_id: int, phone: str) -> None:
    status, data = api("GET", f"/admin/subscriptions?phone={urllib.parse.quote(phone)}")
    if status != 200 or not isinstance(data, list):
        send(chat_id, "⚠️ Не удалось получить абонементы с сервера.", main_menu())
        return
    if not data:
        send(chat_id, f"Абонементов по номеру {phone} нет.", main_menu())
        return
    lines = [f"<b>Абонементы {phone}:</b>"]
    for s in data:
        expires = str(s.get("expires_at", ""))[:10]
        lines.append(
            f"\n#{s['id']} · {s.get('plan_name') or s['plan_id']} · размер {s['size']}"
            f"\nВизитов: {s['visits_left']} из {s['visits_total']} · действует до {expires}"
        )
        if s.get("bonus"):
            lines.append(f"🎁 {s['bonus']}")
    lines.append("\nЧтобы продлить — «⏳ Продлить» и номер абонемента (после #).")
    send(chat_id, "\n".join(lines), main_menu())


# ── Пошаговые диалоги ────────────────────────────────────────────────────────
FLOWS = {
    "dayoff": {
        "fields": [("master_id", f"ID мастера, которому ставим выходной.\n{MASTERS_HINT}"),
                   ("date_iso", "Дата выходного (ГГГГ-ММ-ДД):"),
                   ("reason", "Причина (или «-» если не важно):")],
        "submit": lambda data: api("POST", f"/admin/masters/{data['master_id']}/day-off",
                                     {"date_iso": data["date_iso"], "reason": data.get("reason", "")}),
        "done": lambda data, res: f"✅ Выходной {data['date_iso']} для мастера {data['master_id']} сохранён.",
    },
    "master": {
        "fields": [("id", f"ID нового мастера.\n{MASTERS_HINT}"),
                   ("name", "Имя мастера:"),
                   ("caption", "Короткая подпись (специализация), или «-»:"),
                   ("work_start", "Начало рабочего дня (ЧЧ:MM), например 10:00:"),
                   ("work_end", "Конец рабочего дня (ЧЧ:MM), например 20:00:")],
        "submit": lambda data: api("POST", "/admin/masters", data),
        "done": lambda data, res: f"✅ Мастер {data['name']} ({data['id']}) добавлен.",
    },
    "price": {
        "fields": [("service_id", "ID услуги (например dog_complex_cut):"),
                   ("size", "Размер/категория (например M, short_small):"),
                   ("price", "Новая цена, ₽ (число):")],
        "submit": lambda data: api("PATCH", f"/admin/services/{data['service_id']}/price",
                                     {"size": data["size"], "price": int(data["price"])}),
        "done": lambda data, res: f"✅ Цена {data['service_id']} / {data['size']} обновлена: {data['price']} ₽.",
    },
    "closeday": {
        "fields": [("date_iso", "Какой день закрыть для записи (ГГГГ-ММ-ДД)?"),
                   ("reason", "Причина (форс-мажор), или «-»:")],
        "submit": lambda data: api("POST", f"/admin/days/{data['date_iso']}/close", {"reason": data.get("reason", "")}),
        "done": lambda data, res: f"✅ День {data['date_iso']} закрыт для записи.",
    },
    # Отказ мастера в услуге (п. 2.2 оферты) и любая правка объёма на месте.
    "bookingprice": {
        "fields": [("booking_id", "Номер записи (как в уведомлении, например <code>KERIS-1234</code>):"),
                   ("price", "Итоговая сумма к оплате, ₽ (число):"),
                   ("reason", "Причина (отказ в услуге / изменили объём / …):")],
        "submit": lambda data: api("PATCH", f"/admin/bookings/{data['booking_id'].upper()}/price",
                                   {"price": int(data["price"]), "reason": data.get("reason", "")}),
        "done": lambda data, res: (
            f"✅ Запись {data['booking_id'].upper()}: {res.get('was')} → {res.get('price')} ₽.\n"
            f"Минимум по оферте при отказе — {res.get('min_refusal_fee')} ₽."
        ),
    },
    "subextend": {
        "fields": [("subscription_id", "Номер абонемента (цифра после # в списке):"),
                   ("days", "На сколько дней продлить (число):"),
                   ("reason", "Причина (болезнь / отпуск / …), или «-»:")],
        "submit": lambda data: api("POST", f"/admin/subscriptions/{data['subscription_id']}/extend",
                                   {"days": int(data["days"]), "reason": data.get("reason", "")}),
        "done": lambda data, res: f"✅ Абонемент #{data['subscription_id']} действует до {str(res.get('expires_at'))[:10]}.",
    },
    "puppy": {
        "fields": [("name", "Кличка щенка:"),
                   ("litter", "Помёт, или «-»:"),
                   ("birth_date", "Дата рождения (ГГГГ-ММ-ДД), или «-»:"),
                   ("sex", "Пол (мальчик/девочка):"),
                   ("color", "Окрас:"),
                   ("price", "Цена, ₽ (число), или «-»:")],
        "submit": lambda data: api("POST", "/admin/puppies", {
            **data, "price": int(data["price"]) if data.get("price", "-") != "-" else None,
        }),
        "done": lambda data, res: f"✅ Щенок «{data['name']}» добавлен в amoCRM (сделка {res.get('amocrm_lead_id', '?')}).",
    },
}


# Однополевые «спросить и показать» — без submit/done, сразу рендер списка.
ASK_AND_SHOW = {
    "bookings_date": ("На какую дату показать записи (ГГГГ-ММ-ДД)?", lambda cid, v: show_bookings(cid, v)),
    "subcheck": ("Телефон клиента (как в записи, например +79991234567):", lambda cid, v: show_subscriptions(cid, v)),
}


def start_flow(chat_id: int, flow_name: str) -> None:
    if flow_name in ASK_AND_SHOW:
        st = user_state(chat_id)
        st["flow"] = flow_name
        st["step"] = 0
        st["data"] = {}
        save_state()
        send(chat_id, ASK_AND_SHOW[flow_name][0], cancel_kb())
        return
    st = user_state(chat_id)
    st["flow"] = flow_name
    st["step"] = 0
    st["data"] = {}
    save_state()
    field_name, prompt = FLOWS[flow_name]["fields"][0]
    send(chat_id, prompt, cancel_kb())


def continue_flow(chat_id: int, text: str) -> None:
    st = user_state(chat_id)
    flow_name = st["flow"]
    if flow_name in ASK_AND_SHOW:
        reset_flow(chat_id)
        ASK_AND_SHOW[flow_name][1](chat_id, text.strip())
        return
    flow = FLOWS[flow_name]
    fields = flow["fields"]
    step = st["step"]
    field_name = fields[step][0]
    st["data"][field_name] = text.strip()
    step += 1
    st["step"] = step
    save_state()

    if step < len(fields):
        send(chat_id, fields[step][1], cancel_kb())
        return

    try:
        status, res = flow["submit"](st["data"])
    except ValueError:
        send(chat_id, "⚠️ Где-то нужно число (сумма, цена, количество дней). Начните заново.", main_menu())
        reset_flow(chat_id)
        return
    if status and 200 <= status < 300:
        send(chat_id, flow["done"](st["data"], res if isinstance(res, dict) else {}), main_menu())
    else:
        msg = res.get("detail") if isinstance(res, dict) else str(res)
        send(chat_id, f"⚠️ Не удалось сохранить: {msg or status}. Проверьте данные и попробуйте снова.", main_menu())
    reset_flow(chat_id)


# ── Обработчики ─────────────────────────────────────────────────────────────
def is_allowed(chat_id: int) -> bool:
    return not ALLOWED_IDS or str(chat_id) in ALLOWED_IDS


def handle_start(chat_id: int, username: str = "") -> None:
    # ID пишем в лог всегда: по нему заполняется whitelist (KARINA_TELEGRAM_IDS).
    log.info("вход: chat_id=%s username=%s разрешён=%s", chat_id, username or "-", is_allowed(chat_id))
    if not is_allowed(chat_id):
        log.info("start от неразрешённого chat_id=%s", chat_id)
        send(chat_id, f"Доступ к этому боту ограничен.\nВаш Telegram ID: <code>{chat_id}</code>")
        return
    suffix = "" if ALLOWED_IDS else f"\n\n<i>Ваш Telegram ID: {chat_id} (whitelist пока не настроен — доступ открыт всем, кто знает бота)</i>"
    send(chat_id, f"🐾 <b>Keris Club — панель Карины</b>\n\nВыберите действие:{suffix}", main_menu())


def handle_callback(chat_id: int, cb_id: str, data: str) -> None:
    answer_callback(cb_id)
    if not is_allowed(chat_id):
        return
    if data == "cancel":
        reset_flow(chat_id)
        send(chat_id, "Отменено.", main_menu())
    elif data.startswith("bk:"):
        show_bookings(chat_id, data.split(":", 1)[1])
    elif data.startswith("flow:"):
        start_flow(chat_id, data.split(":", 1)[1])
    else:
        send(chat_id, "Не понял действие.", main_menu())


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    username = (msg.get("from") or {}).get("username", "")
    if not is_allowed(chat_id):
        log.info("сообщение от неразрешённого: chat_id=%s username=%s", chat_id, username or "-")
        send(chat_id, f"Доступ к этому боту ограничен.\nВаш Telegram ID: <code>{chat_id}</code>")
        return
    if text.startswith("/start") or text.startswith("/menu"):
        reset_flow(chat_id)
        handle_start(chat_id, username)
        return
    st = user_state(chat_id)
    if st.get("flow"):
        continue_flow(chat_id, text)
    else:
        handle_start(chat_id, username)


def handle_update(upd: dict) -> None:
    try:
        if "message" in upd:
            handle_message(upd["message"])
        elif "callback_query" in upd:
            cb = upd["callback_query"]
            handle_callback(cb["message"]["chat"]["id"], cb["id"], cb.get("data", ""))
    except Exception:
        log.warning("handle_update failed: %s", str(upd)[:300], exc_info=True)


ALLOWED_UPDATES = urllib.parse.quote('["message","callback_query"]')


def main() -> None:
    load_state()
    log.info("keris-admin-bot запущен (long polling), api=%s, разрешённых id=%d", API_BASE, len(ALLOWED_IDS))
    _http("POST", f"{TG_API}/deleteWebhook", {"drop_pending_updates": False})
    offset = 0
    while True:
        try:
            status, data = _http(
                "GET",
                f"{TG_API}/getUpdates?offset={offset}&timeout=25&allowed_updates={ALLOWED_UPDATES}",
                None, None, timeout=35.0,
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
