#!/usr/bin/env python3
"""@kerisclubadminbot: две роли в одном процессе.

Админ видит фото до/после, кнопку «Сегодня» и оперативные карточки записей
(их пишет сервер, не этот цикл). Роль Карины добавляет щенка, открывает пульс
и получает сводку в 22:15. Пустой список ролей никого не пускает.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("keris-admin-bot")

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


def _ids(name: str) -> set[str]:
    return {x.strip() for x in os.environ.get(name, "").split(",") if x.strip()}


ADMIN_IDS = _ids("ADMIN_ROLE_IDS") or _ids("ADMIN_NOTIFY_CHAT_IDS")
KARINA_IDS = _ids("KARINA_ROLE_IDS")
ALLOWED_IDS = ADMIN_IDS | KARINA_IDS

API_BASE = os.environ.get("KERIS_SERVER_URL", "http://127.0.0.1:8091").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "/root/keris-admin-bot/state.json")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

_state: dict[str, dict[str, Any]] = {}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLORS = ("gold", "ice gold", "red brown")
_SIZES = ("микро", "мини", "стандарт")


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


def user_state(chat_id: int) -> dict[str, Any]:
    return _state.setdefault(str(chat_id), {})


def reset_flow(chat_id: int) -> None:
    st = user_state(chat_id)
    st.pop("flow", None)
    st.pop("step", None)
    st.pop("data", None)
    save_state()


def role_of(chat_id: int) -> str:
    key = str(chat_id)
    st = user_state(chat_id)
    mode = st.get("mode")
    if mode == "karina" and key in KARINA_IDS:
        return "karina"
    if mode == "admin" and key in ADMIN_IDS:
        return "admin"
    if key in ADMIN_IDS:
        return "admin"
    if key in KARINA_IDS:
        return "karina"
    return ""


def _http(method: str, url: str, payload: Optional[dict] = None,
          headers: Optional[dict] = None, timeout: float = 20.0) -> tuple[int, Any]:
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
    sep = "&" if "?" in path else "?"
    return _http(
        method,
        f"{API_BASE}{path}",
        payload,
        headers={"X-Admin-Key": ADMIN_API_KEY},
    ) if method != "GET" else _http(
        "GET",
        f"{API_BASE}{path}",
        None,
        headers={"X-Admin-Key": ADMIN_API_KEY},
    )


def send(chat_id: int, text: str, keyboard: Optional[dict] = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    tg("sendMessage", payload)


def answer_callback(cb_id: str, text: str = "") -> None:
    tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:200]})


def detail_of(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("detail") or detail)
        if detail:
            return str(detail)
    return ""


def pulse_url(chat_id: int) -> str:
    status, data = api("POST", "/admin/pulse-link", {"chat_id": str(chat_id)})
    if status == 200 and isinstance(data, dict) and data.get("url"):
        return str(data["url"])
    return ""


def apply_menu_button(chat_id: int, mode: str) -> str:
    if mode == "karina":
        url = pulse_url(chat_id)
        if url:
            tg("setChatMenuButton", {
                "chat_id": chat_id,
                "menu_button": {"type": "web_app", "text": "Пульс", "web_app": {"url": url}},
            })
            return url
    tg("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "default"}})
    return ""


def menu_for(chat_id: int) -> dict:
    mode = role_of(chat_id)
    rows: list[list[dict]] = []
    both = str(chat_id) in ADMIN_IDS and str(chat_id) in KARINA_IDS
    if mode == "karina":
        rows.append([{"text": "Добавить щенка", "callback_data": "flow:puppy"}])
        url = user_state(chat_id).get("pulse_url") or ""
        if url:
            rows.append([{"text": "Пульс", "web_app": {"url": url}}])
        if both:
            rows.append([{"text": "Переключить на админа", "callback_data": "role:admin"}])
    else:
        rows.append([{"text": "Фото-отчёт", "callback_data": "flow:photo"}])
        rows.append([{"text": "Сегодня", "callback_data": "today"}])
        if both:
            rows.append([{"text": "Переключить на Карину", "callback_data": "role:karina"}])
    return {"inline_keyboard": rows}


def show_home(chat_id: int, text: str = "") -> None:
    mode = role_of(chat_id)
    title = "Режим Карины" if mode == "karina" else "Режим админа"
    send(chat_id, text or title, menu_for(chat_id))


def switch_role(chat_id: int, mode: str) -> None:
    reset_flow(chat_id)
    st = user_state(chat_id)
    st["mode"] = mode
    st["pulse_url"] = apply_menu_button(chat_id, mode) if mode == "karina" else ""
    if mode != "karina":
        apply_menu_button(chat_id, "admin")
    save_state()
    show_home(chat_id)


def show_today(chat_id: int) -> None:
    status, data = api("GET", "/admin/today")
    if status != 200 or not isinstance(data, dict):
        send(chat_id, f"Не удалось собрать день. {detail_of(data)}".strip(), menu_for(chat_id))
        return
    text = (
        "<b>Сегодня</b>\n"
        f"Визитов: {data.get('visits', 0)}\n"
        f"Сумма: {data.get('revenue', 0)} ₽\n"
        f"Отмены: {data.get('cancelled', 0)}\n"
        f"Не пришли: {data.get('no_show', 0)}\n"
        f"Без пары фото: {data.get('photos_missing', 0)}"
    )
    send(chat_id, text, menu_for(chat_id))


def start_photos(chat_id: int) -> None:
    st = user_state(chat_id)
    st["flow"] = "photo"
    st["step"] = "day"
    st["data"] = {}
    save_state()
    send(chat_id, "Какие визиты показать?", {"inline_keyboard": [
        [{"text": "Сегодня", "callback_data": "ph:today"},
         {"text": "Вчера", "callback_data": "ph:yesterday"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def show_photo_bookings(chat_id: int, when: str) -> None:
    status, data = api("GET", f"/admin/bookings?when={urllib.parse.quote(when)}&for_photos=1")
    if status != 200 or not isinstance(data, list):
        send(chat_id, f"Не удалось получить записи. {detail_of(data)}".strip(), menu_for(chat_id))
        reset_flow(chat_id)
        return
    rows = [row for row in data if not (row.get("photos_before") and row.get("photos_after"))]
    if not rows:
        send(chat_id, "Пар без фото нет.", menu_for(chat_id))
        reset_flow(chat_id)
        return
    keyboard = []
    for row in rows[:20]:
        label = f"{row.get('time')} {row.get('pet') or 'питомец'}"
        keyboard.append([{"text": label, "callback_data": f"phb:{row['id']}"}])
    keyboard.append([{"text": "Отменить", "callback_data": "cancel"}])
    st = user_state(chat_id)
    st["data"] = {"when": when, "rows": {row["id"]: row for row in rows}}
    st["step"] = "booking"
    save_state()
    send(chat_id, "Выбери запись", {"inline_keyboard": keyboard})


def ask_kind(chat_id: int, booking_id: str) -> None:
    st = user_state(chat_id)
    rows = (st.get("data") or {}).get("rows") or {}
    row = rows.get(booking_id) or {}
    st["data"]["booking_id"] = booking_id
    st["step"] = "kind"
    save_state()
    send(
        chat_id,
        f"{row.get('pet') or 'Питомец'}, {row.get('time') or ''}, {row.get('owner') or ''}".strip(", "),
        {"inline_keyboard": [
            [{"text": "До", "callback_data": "phd:before"},
             {"text": "После", "callback_data": "phd:after"}],
            [{"text": "Отменить", "callback_data": "cancel"}],
        ]},
    )


def ask_photo(chat_id: int, kind: str) -> None:
    st = user_state(chat_id)
    st.setdefault("data", {})["kind"] = kind
    st["step"] = "photo"
    save_state()
    label = "до" if kind == "before" else "после"
    send(chat_id, f"Пришли одно фото {label}.", {"inline_keyboard": [
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def save_photo(chat_id: int, file_id: str) -> None:
    st = user_state(chat_id)
    data = st.get("data") or {}
    booking_id = data.get("booking_id")
    kind = data.get("kind")
    if not booking_id or not kind:
        send(chat_id, "Сначала выбери запись и тип снимка.", menu_for(chat_id))
        return
    status, body = api("POST", f"/admin/bookings/{booking_id}/photos", {
        "kind": kind,
        "file_id": file_id,
        "source": "admin",
        "added_by": str(chat_id),
    })
    if status != 200:
        send(chat_id, f"Фото не сохранилось. {detail_of(body)}".strip(), menu_for(chat_id))
        return
    show_photo_card(chat_id, booking_id)


def show_photo_card(chat_id: int, booking_id: str) -> None:
    status, body = api("GET", f"/admin/bookings?when=today&for_photos=1")
    row = None
    when = (user_state(chat_id).get("data") or {}).get("when") or "today"
    status, body = api("GET", f"/admin/bookings?when={urllib.parse.quote(when)}")
    if status == 200 and isinstance(body, list):
        row = next((item for item in body if item.get("id") == booking_id), None)
    if not row:
        send(chat_id, "Запись не найдена.", menu_for(chat_id))
        reset_flow(chat_id)
        return
    pair = bool(row.get("photos_before")) and bool(row.get("photos_after"))
    text = (
        f"<b>{row.get('pet') or 'Питомец'}</b>\n"
        f"{row.get('time') or ''}\n"
        f"{row.get('owner') or ''}\n"
        f"До: {row.get('photos_before') or 0}, после: {row.get('photos_after') or 0}"
    )
    buttons = [
        [{"text": "До", "callback_data": "phd:before"},
         {"text": "После", "callback_data": "phd:after"}],
    ]
    if pair:
        buttons.append([{"text": "Отправить клиенту", "callback_data": f"phs:{booking_id}"}])
    buttons.append([{"text": "Отменить", "callback_data": "cancel"}])
    user_state(chat_id)["step"] = "kind"
    user_state(chat_id).setdefault("data", {})["booking_id"] = booking_id
    save_state()
    send(chat_id, text, {"inline_keyboard": buttons})


def send_report(chat_id: int, booking_id: str) -> None:
    status, body = api("POST", f"/admin/bookings/{booking_id}/report/send?require_pair=1")
    reset_flow(chat_id)
    if status != 200 or not isinstance(body, dict):
        send(chat_id, f"Отчёт не ушёл. {detail_of(body)}".strip(), menu_for(chat_id))
        return
    if body.get("pending"):
        links = body.get("links") or {}
        lines = ["Отчёт ждёт привязки номера. Клиенту не отправлено."]
        if links.get("telegram"):
            lines.append(str(links["telegram"]))
        if links.get("max"):
            lines.append(str(links["max"]))
        send(chat_id, "\n".join(lines), menu_for(chat_id))
        return
    channel = "Telegram" if body.get("telegram") else "MAX" if body.get("max") else "клиенту"
    send(chat_id, f"Отправлено в {channel}.", menu_for(chat_id))


def start_puppy(chat_id: int) -> None:
    st = user_state(chat_id)
    st["flow"] = "puppy"
    st["step"] = "name"
    st["data"] = {}
    save_state()
    send(chat_id, "Кличка щенка", {"inline_keyboard": [[{"text": "Отменить", "callback_data": "cancel"}]]})


def puppy_sex(chat_id: int) -> None:
    user_state(chat_id)["step"] = "sex"
    save_state()
    send(chat_id, "Пол", {"inline_keyboard": [
        [{"text": "мальчик", "callback_data": "px:мальчик"},
         {"text": "девочка", "callback_data": "px:девочка"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_color(chat_id: int) -> None:
    user_state(chat_id)["step"] = "color"
    save_state()
    send(chat_id, "Окрас. Можно нажать кнопку или написать свой.", {"inline_keyboard": [
        [{"text": "gold", "callback_data": "pc:gold"}],
        [{"text": "ice gold", "callback_data": "pc:ice gold"}],
        [{"text": "red brown", "callback_data": "pc:red brown"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_birth(chat_id: int) -> None:
    user_state(chat_id)["step"] = "birth"
    save_state()
    send(chat_id, "Дата рождения, ГГГГ-ММ-ДД. Или пропусти.", {"inline_keyboard": [
        [{"text": "Пропустить", "callback_data": "pb:skip"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_size(chat_id: int) -> None:
    user_state(chat_id)["step"] = "size"
    save_state()
    send(chat_id, "Размер", {"inline_keyboard": [
        [{"text": "микро", "callback_data": "pz:микро"},
         {"text": "мини", "callback_data": "pz:мини"},
         {"text": "стандарт", "callback_data": "pz:стандарт"}],
        [{"text": "Пропустить", "callback_data": "pz:skip"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_price(chat_id: int) -> None:
    user_state(chat_id)["step"] = "price"
    save_state()
    send(chat_id, "Цена числом. Или пропусти.", {"inline_keyboard": [
        [{"text": "Пропустить", "callback_data": "pp:skip"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_confirm(chat_id: int) -> None:
    data = user_state(chat_id).get("data") or {}
    user_state(chat_id)["step"] = "confirm"
    save_state()
    lines = [
        "<b>Запишу так</b>",
        data.get("name") or "",
        f"Пол: {data.get('sex') or ''}",
        f"Окрас: {data.get('color') or ''}",
        f"Дата рождения: {data.get('birth') or 'не указана'}",
        f"Размер: {data.get('size') or 'не указан'}",
        f"Цена: {data.get('price') if data.get('price') is not None else 'не указана'}",
    ]
    send(chat_id, "\n".join(lines), {"inline_keyboard": [
        [{"text": "Записать", "callback_data": "py:save"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def save_puppy(chat_id: int, force: bool = False) -> None:
    data = user_state(chat_id).get("data") or {}
    payload = {
        "name": data.get("name") or "",
        "sex": data.get("sex") or "",
        "color": data.get("color") or "",
        "birth_date": data.get("birth") or "",
        "size": data.get("size") or "",
        "price": data.get("price"),
        "force": force,
    }
    status, body = api("POST", "/admin/puppies", payload)
    if status == 409:
        send(chat_id, "Открытая карточка с этой кличкой уже есть. Создать вторую?", {"inline_keyboard": [
            [{"text": "Создать вторую", "callback_data": "py:force"}],
            [{"text": "Отменить", "callback_data": "cancel"}],
        ]})
        return
    if status != 200 or not isinstance(body, dict) or not body.get("amocrm_lead_id"):
        send(chat_id, f"amoCRM не записал щенка. {detail_of(body) or 'нет id сделки'}".strip(), menu_for(chat_id))
        return
    reset_flow(chat_id)
    send(chat_id, f"Щенок добавлен, сделка {body['amocrm_lead_id']}.", menu_for(chat_id))


def on_text(chat_id: int, text: str) -> None:
    st = user_state(chat_id)
    flow = st.get("flow")
    step = st.get("step")
    raw = text.strip()
    if flow == "puppy" and step == "name":
        st.setdefault("data", {})["name"] = raw
        save_state()
        puppy_sex(chat_id)
        return
    if flow == "puppy" and step == "color":
        st.setdefault("data", {})["color"] = raw
        save_state()
        puppy_birth(chat_id)
        return
    if flow == "puppy" and step == "birth":
        if not _DATE.match(raw):
            send(chat_id, "Нужен формат ГГГГ-ММ-ДД.")
            return
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            send(chat_id, "Такой даты нет.")
            return
        st.setdefault("data", {})["birth"] = raw
        save_state()
        puppy_size(chat_id)
        return
    if flow == "puppy" and step == "price":
        digits = raw.replace(" ", "")
        if not digits.isdigit():
            send(chat_id, "Нужно число.")
            return
        st.setdefault("data", {})["price"] = int(digits)
        save_state()
        puppy_confirm(chat_id)
        return
    if raw in ("/start", "/menu"):
        reset_flow(chat_id)
        if str(chat_id) in KARINA_IDS:
            user_state(chat_id)["pulse_url"] = apply_menu_button(chat_id, role_of(chat_id))
            save_state()
        show_home(chat_id)
        return
    show_home(chat_id, "Кнопка снизу.")


def on_photo(chat_id: int, message: dict) -> None:
    st = user_state(chat_id)
    if message.get("media_group_id"):
        send(chat_id, "Нужно одно фото, не альбом.")
        return
    if st.get("flow") != "photo" or st.get("step") != "photo":
        send(chat_id, "Сначала нажми «до» или «после».")
        return
    sizes = message.get("photo") or []
    if not sizes:
        send(chat_id, "Пришли одно фото.")
        return
    save_photo(chat_id, sizes[-1]["file_id"])


def on_callback(chat_id: int, data: str) -> None:
    if data == "cancel":
        reset_flow(chat_id)
        show_home(chat_id)
        return
    if data == "role:admin" and str(chat_id) in ADMIN_IDS:
        switch_role(chat_id, "admin")
        return
    if data == "role:karina" and str(chat_id) in KARINA_IDS:
        switch_role(chat_id, "karina")
        return
    mode = role_of(chat_id)
    if data == "today" and mode == "admin":
        show_today(chat_id)
        return
    if data == "flow:photo" and mode == "admin":
        start_photos(chat_id)
        return
    if data == "flow:puppy" and mode == "karina":
        start_puppy(chat_id)
        return
    if data.startswith("ph:") and mode == "admin":
        show_photo_bookings(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("phb:") and mode == "admin":
        ask_kind(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("phd:") and mode == "admin":
        ask_photo(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("phs:") and mode == "admin":
        send_report(chat_id, data.split(":", 1)[1])
        return
    if data.startswith("px:") and mode == "karina":
        user_state(chat_id).setdefault("data", {})["sex"] = data.split(":", 1)[1]
        save_state()
        puppy_color(chat_id)
        return
    if data.startswith("pc:") and mode == "karina":
        user_state(chat_id).setdefault("data", {})["color"] = data.split(":", 1)[1]
        save_state()
        puppy_birth(chat_id)
        return
    if data == "pb:skip" and mode == "karina":
        user_state(chat_id).setdefault("data", {})["birth"] = ""
        save_state()
        puppy_size(chat_id)
        return
    if data.startswith("pz:") and mode == "karina":
        value = data.split(":", 1)[1]
        user_state(chat_id).setdefault("data", {})["size"] = "" if value == "skip" else value
        save_state()
        puppy_price(chat_id)
        return
    if data == "pp:skip" and mode == "karina":
        user_state(chat_id).setdefault("data", {})["price"] = None
        save_state()
        puppy_confirm(chat_id)
        return
    if data == "py:save" and mode == "karina":
        save_puppy(chat_id, force=False)
        return
    if data == "py:force" and mode == "karina":
        save_puppy(chat_id, force=True)
        return
    show_home(chat_id)


def handle(update: dict) -> None:
    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    if callback:
        chat_id = int((callback.get("message") or {}).get("chat", {}).get("id") or 0)
        answer_callback(callback.get("id") or "")
    else:
        chat_id = int((message.get("chat") or {}).get("id") or 0)
    if chat_id <= 0 or str(chat_id) not in ALLOWED_IDS:
        if chat_id > 0:
            send(chat_id, "Нет доступа.")
        return
    if callback:
        on_callback(chat_id, callback.get("data") or "")
        return
    if message.get("photo"):
        on_photo(chat_id, message)
        return
    if message.get("sticker") or message.get("video") or message.get("animation"):
        send(chat_id, "Нужно одно фото.")
        return
    text = message.get("text") or ""
    if text:
        on_text(chat_id, text)


def poll() -> None:
    offset = 0
    log.info("admin bot started, admin=%s karina=%s", sorted(ADMIN_IDS), sorted(KARINA_IDS))
    while True:
        status, data = _http("GET", f"{TG_API}/getUpdates?timeout=25&offset={offset}", timeout=35)
        if status != 200 or not isinstance(data, dict):
            time.sleep(2)
            continue
        for update in data.get("result") or []:
            offset = int(update["update_id"]) + 1
            try:
                handle(update)
            except Exception:
                log.warning("update failed", exc_info=True)


if __name__ == "__main__":
    load_state()
    poll()
