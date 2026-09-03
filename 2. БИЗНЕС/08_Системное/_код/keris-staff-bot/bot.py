#!/usr/bin/env python3
"""Бот администратора Keris Club: фото до/после (@kerisclubphotobot).

Один сценарий: выбрать запись на сегодня → прислать фото «до» → фото «после» →
отправить клиенту. Фото уходят в keris-server (`POST /admin/bookings/{id}/photos`),
он скачивает файл себе и хранит его: постоянное место отчёта — кабинет «Мой Keris»,
а сообщение в боте клиента — дубль-уведомление.

Бот Карины (keris-admin-bot) не трогаем: у неё управление и уведомления, здесь —
только фото. Доступ — свой whitelist STAFF_TELEGRAM_IDS.

Только stdlib (urllib), long polling — тот же стиль, что keris-admin-bot.
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
log = logging.getLogger("keris-staff-bot")

# IPv6 к api.telegram.org, IPv4 — к keris-server: та же логика, что в боте Карины.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6_first(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    if family == 0:
        try:
            return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
        except socket.gaierror:
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv6_first

BOT_TOKEN = os.environ.get("STAFF_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("STAFF_BOT_TOKEN не задан — см. .env")

ALLOWED_IDS = {x.strip() for x in os.environ.get("STAFF_TELEGRAM_IDS", "").split(",") if x.strip()}
if not ALLOWED_IDS:
    log.warning("STAFF_TELEGRAM_IDS пуст — доступ открыт всем, кто знает бота")

# Свободные места самозаписи: первые N новых людей (Карина, администратор салона)
# получают доступ сами, без правки .env и рестарта. Дальше — только whitelist.
AUTO_ENROLL_LIMIT = int(os.environ.get("STAFF_AUTO_ENROLL_LIMIT", "3"))

API_BASE = os.environ.get("KERIS_SERVER_URL", "http://127.0.0.1:8091").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-staff-bot/state.json")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

KIND_LABEL = {"before": "до", "after": "после"}

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
    st.pop("booking_id", None)
    st.pop("kind", None)
    save_state()


# ── HTTP helpers ────────────────────────────────────────────────────────────
def _http(method: str, url: str, payload: Optional[dict] = None,
          headers: Optional[dict] = None, timeout: float = 30.0) -> tuple[int, Any]:
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
        [{"text": "📸 Фото-отчёт: записи сегодня", "callback_data": "day:today"}],
        [{"text": "🗓 Записи за вчера", "callback_data": "day:yesterday"}],
    ]}


def bookings_kb(rows: list[dict]) -> dict:
    keyboard = [
        [{"text": f"{b['time']} · {b.get('pet') or b['owner']} · {b['service'][:22]}",
          "callback_data": f"pick:{b['id']}"}]
        for b in rows[:20]
    ]
    keyboard.append([{"text": "✖️ Закрыть", "callback_data": "cancel"}])
    return {"inline_keyboard": keyboard}


def booking_kb(booking_id: str, counts: dict[str, int], when: str = "today") -> dict:
    before = f"📷 Фото ДО ({counts.get('before', 0)})"
    after = f"📷 Фото ПОСЛЕ ({counts.get('after', 0)})"
    keyboard = [
        [{"text": before, "callback_data": f"kind:before:{booking_id}"}],
        [{"text": after, "callback_data": f"kind:after:{booking_id}"}],
    ]
    if counts.get("before") or counts.get("after"):
        keyboard.append([{"text": "✅ Отправить клиенту", "callback_data": f"send:{booking_id}"}])
    keyboard.append([{"text": "⬅️ К записям", "callback_data": f"day:{when}"}])
    return {"inline_keyboard": keyboard}


# ── Экраны ──────────────────────────────────────────────────────────────────
DAY_LABEL = {"today": "сегодня", "yesterday": "вчера"}
ALL_DONE = {
    "today": "🎉 Все супер: по всем записям на сегодня фото до и после уже загружены.",
    "yesterday": "🎉 Все супер: по всем вчерашним записям фото до и после уже загружены.",
}


def needs_photos(b: dict) -> bool:
    """Показываем только незакрытые визиты: нет фото до или нет фото после."""
    return not (b.get("photos_before") and b.get("photos_after"))


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
    pending = [b for b in data if needs_photos(b)]
    if not pending:
        send(chat_id, ALL_DONE.get(when, "🎉 Все супер: фото загружены по всем записям."), main_menu())
        return
    st = user_state(chat_id)
    st["when"] = when
    save_state()
    reset_flow(chat_id)
    send(chat_id, f"<b>Записи за {label} без фото — {len(pending)}.</b>\n"
                  "Выберите визит, к которому добавляем фото:",
         bookings_kb(pending))


def photo_counts(booking_id: str) -> dict[str, int]:
    status, data = api("GET", f"/admin/bookings/{booking_id}/photos")
    counts = {"before": 0, "after": 0}
    if status == 200 and isinstance(data, dict):
        for p in data.get("photos", []):
            kind = p.get("kind")
            if kind in counts:
                counts[kind] += 1
    return counts


def show_booking(chat_id: int, booking_id: str, prefix: str = "") -> None:
    st = user_state(chat_id)
    st["booking_id"] = booking_id
    st.pop("kind", None)
    save_state()
    counts = photo_counts(booking_id)
    text = (
        f"{prefix}<b>Запись {booking_id}</b>\n\n"
        f"Загружено: до — {counts['before']}, после — {counts['after']}.\n\n"
        "Нажмите «Фото ДО» или «Фото ПОСЛЕ» и пришлите снимки: можно несколько подряд."
    )
    send(chat_id, text, booking_kb(booking_id, counts, st.get("when", "today")))


def ask_photo(chat_id: int, kind: str, booking_id: str) -> None:
    st = user_state(chat_id)
    st["booking_id"] = booking_id
    st["kind"] = kind
    save_state()
    send(
        chat_id,
        f"Пришлите фото <b>{KIND_LABEL[kind]}</b> для записи {booking_id}.\n"
        "Можно несколько подряд — каждое сохраню.",
        {"inline_keyboard": [[{"text": "⬅️ Назад к записи", "callback_data": f"pick:{booking_id}"}]]},
    )


def upload_photo(chat_id: int, file_id: str) -> None:
    st = user_state(chat_id)
    booking_id = st.get("booking_id")
    kind = st.get("kind")
    if not booking_id or not kind:
        send(chat_id, "Сначала выберите запись и нажмите «Фото ДО» или «Фото ПОСЛЕ».", main_menu())
        return
    status, res = api(
        "POST", f"/admin/bookings/{booking_id}/photos",
        {"kind": kind, "file_id": file_id, "added_by": str(chat_id)},
    )
    if not (status and 200 <= status < 300):
        msg = res.get("detail") if isinstance(res, dict) else str(res)
        send(chat_id, f"⚠️ Фото не сохранилось: {msg or status}. Попробуйте прислать ещё раз.")
        return
    counts = photo_counts(booking_id)
    send(
        chat_id,
        f"✅ Фото <b>{KIND_LABEL[kind]}</b> сохранено (до — {counts['before']}, после — {counts['after']}).\n"
        "Можно прислать ещё или отправить отчёт клиенту.",
        booking_kb(booking_id, counts, st.get("when", "today")),
    )


def send_report(chat_id: int, booking_id: str) -> None:
    status, res = api("POST", f"/admin/bookings/{booking_id}/report/send")
    if not (status and 200 <= status < 300) or not isinstance(res, dict):
        msg = res.get("detail") if isinstance(res, dict) else str(res)
        send(chat_id, f"⚠️ Отчёт не отправлен: {msg or status}", main_menu())
        return
    if res.get("pending"):
        links = res.get("links") or {}
        # Фото уже в кабинете, но клиент ещё не в боте: отчёт догонит его сам
        # сразу после привязки номера — администратору остаётся дать ссылку.
        send(
            chat_id,
            "📌 Фото сохранены, но клиента ещё нет в боте.\n\n"
            "Дайте ему ссылку — после «Поделиться номером» отчёт придёт автоматически:\n"
            f"Telegram: {links.get('telegram', '')}\nMAX: {links.get('max', '')}",
            main_menu(),
        )
        return
    channels = ", ".join(res.get("channels", [])) or "—"
    send(
        chat_id,
        f"✅ Отчёт по {booking_id} отправлен клиенту ({channels}).\n"
        "Фото остались в кабинете «Мой Keris» — клиент сможет открыть их и через месяц.",
        main_menu(),
    )
    reset_flow(chat_id)


# ── Обработчики ─────────────────────────────────────────────────────────────
def enrolled_ids() -> dict[str, str]:
    return _state.setdefault("_enrolled", {})


def is_allowed(chat_id: int) -> bool:
    return not ALLOWED_IDS or str(chat_id) in ALLOWED_IDS or str(chat_id) in enrolled_ids()


def try_enroll(chat_id: int, username: str = "") -> bool:
    """Занять свободное место. Возвращает True, если доступ теперь есть."""
    if is_allowed(chat_id):
        return True
    seats = enrolled_ids()
    if len(seats) >= AUTO_ENROLL_LIMIT:
        return False
    seats[str(chat_id)] = username or ""
    save_state()
    log.info("самозапись: chat_id=%s username=%s занято %d из %d",
             chat_id, username or "-", len(seats), AUTO_ENROLL_LIMIT)
    return True


def handle_start(chat_id: int, username: str = "") -> None:
    allowed = try_enroll(chat_id, username)
    log.info("вход: chat_id=%s username=%s разрешён=%s", chat_id, username or "-", allowed)
    if not allowed:
        send(chat_id, f"Доступ к этому боту ограничен.\nВаш Telegram ID: <code>{chat_id}</code>")
        return
    suffix = "" if ALLOWED_IDS else f"\n\n<i>Ваш Telegram ID: {chat_id} (whitelist пока не настроен)</i>"
    send(
        chat_id,
        "🐾 <b>Keris Club — фото до и после</b>\n\n"
        "Выберите запись, пришлите фото до и после, отправьте клиенту.\n"
        f"Фото сохраняются в кабинете клиента навсегда.{suffix}",
        main_menu(),
    )


def handle_callback(chat_id: int, cb_id: str, data: str) -> None:
    answer_callback(cb_id)
    if not is_allowed(chat_id):
        return
    if data == "cancel":
        reset_flow(chat_id)
        send(chat_id, "Закрыл.", main_menu())
    elif data.startswith("day:"):
        show_bookings(chat_id, data.split(":", 1)[1])
    elif data.startswith("pick:"):
        show_booking(chat_id, data.split(":", 1)[1])
    elif data.startswith("kind:"):
        _, kind, booking_id = data.split(":", 2)
        ask_photo(chat_id, kind, booking_id)
    elif data.startswith("send:"):
        send_report(chat_id, data.split(":", 1)[1])
    else:
        send(chat_id, "Не понял действие.", main_menu())


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    username = (msg.get("from") or {}).get("username", "")
    if not try_enroll(chat_id, username):
        log.info("сообщение от неразрешённого: chat_id=%s username=%s", chat_id, username or "-")
        send(chat_id, f"Доступ к этому боту ограничен.\nВаш Telegram ID: <code>{chat_id}</code>")
        return

    photos = msg.get("photo") or []
    if photos:
        # Telegram присылает несколько размеров: берём самый большой (последний).
        upload_photo(chat_id, photos[-1]["file_id"])
        return
    document = msg.get("document") or {}
    if str(document.get("mime_type", "")).startswith("image/"):
        upload_photo(chat_id, document["file_id"])
        return

    if text.startswith("/start") or text.startswith("/menu"):
        reset_flow(chat_id)
        handle_start(chat_id, username)
        return
    # Номер записи текстом — короткий путь без списка (например из уведомления).
    if text.upper().startswith("KERIS-"):
        show_booking(chat_id, text.upper())
        return
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
    log.info("keris-staff-bot запущен (long polling), api=%s, разрешённых id=%d", API_BASE, len(ALLOWED_IDS))
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
