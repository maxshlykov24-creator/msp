#!/usr/bin/env python3
"""@kerisclubadminbot: две роли в одном процессе.

Админ видит фото до/после, кнопку «Статистика» и оперативные карточки записей
(их пишет сервер, не этот цикл). Роль Карины добавляет щенка, открывает пульс
и получает сводку в 22:15. Пустой список ролей никого не пускает.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from puppy_parse import parse_birth, parse_price, price_label

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
_ready: dict[str, Any] = {"today": None, "stats": None, "bookings": {}}
_state_lock = threading.Lock()


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
        with _state_lock:
            snapshot = json.dumps(_state, ensure_ascii=False)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(snapshot)
    except Exception:
        log.warning("save_state failed", exc_info=True)


def user_state(chat_id: int) -> dict[str, Any]:
    return _state.setdefault(str(chat_id), {})


def reset_flow(chat_id: int) -> None:
    st = user_state(chat_id)
    st.pop("flow", None)
    st.pop("step", None)
    st.pop("data", None)
    st.pop("editing", None)
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


def tg(method: str, payload: dict, timeout: float = 8) -> Any:
    status, data = _http("POST", f"{TG_API}/{method}", payload, timeout=timeout)
    if status != 200:
        log.warning("tg %s -> %s %s", method, status, str(data)[:300])
    return data


def api(method: str, path: str, payload: Optional[dict] = None, timeout: float = 4) -> tuple[int, Any]:
    return _http(
        method if method != "GET" else "GET",
        f"{API_BASE}{path}",
        payload if method != "GET" else None,
        headers={"X-Admin-Key": ADMIN_API_KEY},
        timeout=timeout,
    )


def present(chat_id: int, text: str, keyboard: Optional[dict] = None) -> None:
    """Один экран на диалог: кнопка меняет это сообщение, а не пишет следующее."""
    st = user_state(chat_id)
    body: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}
    if keyboard is not None:
        body["reply_markup"] = keyboard
    mid = st.get("screen_id")
    if mid:
        edit = dict(body)
        edit["message_id"] = mid
        status, data = _http("POST", f"{TG_API}/editMessageText", edit, timeout=8)
        desc = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        if status == 200 or "message is not modified" in desc:
            return
    status, data = _http("POST", f"{TG_API}/sendMessage", body, timeout=8)
    result = data.get("result") if isinstance(data, dict) else None
    new_id = (result or {}).get("message_id") if isinstance(result, dict) else None
    if new_id:
        st["screen_id"] = new_id
        save_state()


def send(chat_id: int, text: str, keyboard: Optional[dict] = None) -> None:
    present(chat_id, text, keyboard)


def reply_below(chat_id: int) -> None:
    """Ответ на текст или фото — новым сообщением под ним, а не правкой экрана выше."""
    st = user_state(chat_id)
    mid = st.get("screen_id")
    if mid:
        tg("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": mid,
            "reply_markup": {"inline_keyboard": []},
        })
    st["screen_id"] = None


def answer_callback(cb_id: str, text: str = "") -> None:
    tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:200]}, timeout=3)


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
        url = user_state(chat_id).get("pulse_url") or pulse_url(chat_id)
        if url:
            tg("setChatMenuButton", {
                "chat_id": chat_id,
                "menu_button": {"type": "web_app", "text": "💗 Пульс", "web_app": {"url": url}},
            })
            return url
    tg("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "default"}})
    return ""


def menu_for(chat_id: int) -> dict:
    mode = role_of(chat_id)
    rows: list[list[dict]] = []
    both = str(chat_id) in ADMIN_IDS and str(chat_id) in KARINA_IDS
    if mode == "karina":
        rows.append([{"text": "🐶 Добавить щенка", "callback_data": "flow:puppy"}])
        url = user_state(chat_id).get("pulse_url") or ""
        if url:
            rows.append([{"text": "💗 Пульс", "web_app": {"url": url}}])
        if both:
            rows.append([{"text": "Переключить на админа", "callback_data": "role:admin"}])
    else:
        rows.append([{"text": "📸 Фото-отчёт", "callback_data": "flow:photo"}])
        rows.append([{"text": "📊 Статистика", "callback_data": "stats"}])
        if both:
            rows.append([{"text": "Переключить на Карину", "callback_data": "role:karina"}])
    return {"inline_keyboard": rows}


def show_home(chat_id: int, text: str = "") -> None:
    mode = role_of(chat_id)
    title = "Режим Карины" if mode == "karina" else "Режим админа"
    send(chat_id, text or title, menu_for(chat_id))


def switch_role(chat_id: int, mode: str) -> None:
    reset_flow(chat_id)
    user_state(chat_id)["mode"] = mode
    save_state()
    show_home(chat_id)


def stats_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "Сегодня", "callback_data": "st:today"},
         {"text": "Вчера", "callback_data": "st:yesterday"}],
        [{"text": "Эта неделя", "callback_data": "st:week"},
         {"text": "Прошлая неделя", "callback_data": "st:prev_week"}],
        [{"text": "В меню", "callback_data": "home"}],
    ]}


def _money(value: Any) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    return f"{number:,}".replace(",", " ")


def _load_stats() -> dict | None:
    cached = _ready.get("stats")
    if isinstance(cached, dict) and isinstance(cached.get("today"), dict):
        return cached
    status, data = api("GET", "/admin/stats", timeout=8)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("today"), dict):
        return None
    _ready["stats"] = data
    _ready["today"] = data["today"]
    return data


def show_stats_pick(chat_id: int) -> None:
    send(chat_id, "🐾 <b>Статистика</b>\nСегодня, вчера или неделя.", stats_keyboard())


def _photo_line(block: dict) -> str:
    """Строка про фото только по визитам, которые уже начались. Будущие в это число не входят."""
    started = block.get("visits_started")
    if started is None:
        return ""
    started = int(started or 0)
    ready = int(block.get("photos_ready") or 0)
    if started <= 0:
        return "📷 Прошедших визитов пока нет, фото не к чему приложить."
    if ready <= 0:
        return f"📷 Фото до и после нет ни у одного из {started} прошедших."
    if ready >= started:
        return f"📷 Фото до и после есть у всех {started} прошедших."
    return f"📷 Фото до и после есть у {ready} из {started} прошедших."


def show_stat(chat_id: int, key: str) -> None:
    stats = _load_stats()
    block = (stats or {}).get(key) if isinstance(stats, dict) else None
    if not isinstance(block, dict):
        send(chat_id, "Срез ещё собирается. Нажми ещё раз через минуту.", stats_keyboard())
        return
    title = block.get("title") or "Статистика"
    lines = [f"🐾 <b>{title}</b>"]
    if key in ("week", "prev_week") and block.get("span"):
        lines.append(str(block["span"]))
    lines.append("")
    lines.extend([
        f"🗓 Визитов: {block.get('visits', 0)}",
        f"💰 Сумма: {_money(block.get('revenue', 0))} ₽",
        f"↩️ Отмены: {block.get('cancelled', 0)}",
        f"🚫 Не пришли: {block.get('no_show', 0)}",
    ])
    photo = _photo_line(block)
    if photo:
        lines.append(photo)
    send(chat_id, "\n".join(lines), stats_keyboard())


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


def _visit_label(row: dict) -> str:
    before = int(row.get("photos_before") or 0)
    after = int(row.get("photos_after") or 0)
    if before and after:
        photo = "пара есть"
    elif before:
        photo = "есть до"
    elif after:
        photo = "есть после"
    else:
        photo = "без фото"
    chat = {"telegram": "Telegram", "max": "MAX"}.get(row.get("client_chat") or "", "нет бота")
    label = f"{row.get('time') or ''} {row.get('pet') or 'питомец'} · {photo} · {chat}"
    return label[:60]


def show_photo_bookings(chat_id: int, when: str, note: str = "", skip_id: str = "") -> None:
    day = {"today": "сегодня", "yesterday": "вчера"}.get(when, "")
    status, data = api("GET", f"/admin/bookings?when={urllib.parse.quote(when)}&for_photos=1", timeout=8)
    if status == 200 and isinstance(data, list):
        _ready.setdefault("bookings", {})[when] = data
    else:
        cached = (_ready.get("bookings") or {}).get(when)
        data = cached if isinstance(cached, list) else None
    if not isinstance(data, list):
        send(chat_id, "Список визитов ещё собирается. Нажми ещё раз через минуту.", menu_for(chat_id))
        reset_flow(chat_id)
        return
    rows = [row for row in data if row.get("id") != skip_id]
    head = note.strip()
    if not rows:
        if skip_id:
            tail = f"За {day} других визитов нет." if day else "Других визитов нет."
        else:
            tail = f"За {day} таких визитов нет." if day else "Таких визитов нет."
        send(chat_id, f"{head}\n\n{tail}".strip(), menu_for(chat_id))
        reset_flow(chat_id)
        return
    keyboard = []
    for row in rows[:20]:
        label = _visit_label(row)
        keyboard.append([{"text": label, "callback_data": f"phb:{row['id']}"}])
    keyboard.append([{"text": "Отменить", "callback_data": "cancel"}])
    st = user_state(chat_id)
    st["flow"] = "photo"
    st["data"] = {"when": when, "rows": {row["id"]: row for row in rows}}
    st["step"] = "booking"
    save_state()
    title = f"Выбери запись за {day}." if day else "Выбери запись."
    if head:
        title = f"{head}\n\n{title}"
    send(chat_id, title, {"inline_keyboard": keyboard})


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
    }, timeout=40)
    if status != 200:
        send(chat_id, f"Фото не сохранилось. {detail_of(body)}".strip(), menu_for(chat_id))
        return
    _ready["stats"] = None
    _ready["bookings"] = {}
    report = body.get("report") if isinstance(body, dict) else None
    report_error = str(body.get("report_error") or "") if isinstance(body, dict) else ""
    if _photos_went(report):
        show_photo_bookings(
            chat_id,
            str(data.get("when") or "today"),
            note=_delivery_line(report, ""),
            skip_id=str(booking_id),
        )
        return
    show_photo_card(
        chat_id,
        booking_id,
        shots=body.get("photos") if isinstance(body, dict) else None,
        report=report,
        report_error=report_error,
    )


def _photos_went(report: Optional[dict]) -> bool:
    return isinstance(report, dict) and any(
        item in ("telegram", "max") for item in (report.get("channels") or [])
    )


def _delivery_line(report: Optional[dict], report_error: str) -> str:
    if report_error:
        return f"Фото сохранены. Клиенту не ушло: {report_error}"
    if not isinstance(report, dict):
        return ""
    channels = report.get("channels") or []
    dest = report.get("destination") or ""
    if "telegram" in channels:
        return "Клиенту ушло в Telegram. Первое фото до, второе после."
    if "max" in channels:
        return "В Telegram клиент не писал. Ушло в MAX. Первое фото до, второе после."
    if "max_text" in channels:
        return "В MAX ушёл текст без фото. Картинки не прикрепились."
    if dest == "telegram":
        return "Клиент в Telegram, но сообщение не ушло. Можно отправить ещё раз."
    if dest == "max":
        return "В Telegram клиент не писал. В MAX сообщение не ушло. Можно отправить ещё раз."
    lines = ["Клиент ещё не открывал бота. Фото сохранены и уйдут сами, когда он поделится номером."]
    links = report.get("links") or {}
    if links.get("telegram"):
        lines.append(str(links["telegram"]))
    if links.get("max"):
        lines.append(str(links["max"]))
    return "\n".join(lines)


def show_photo_card(
    chat_id: int,
    booking_id: str,
    shots: Optional[list] = None,
    report: Optional[dict] = None,
    report_error: str = "",
) -> None:
    st = user_state(chat_id)
    rows = (st.get("data") or {}).get("rows") or {}
    row = rows.get(booking_id)
    if shots is not None and row is not None:
        row["photos_before"] = sum(1 for item in shots if item.get("kind") == "before")
        row["photos_after"] = sum(1 for item in shots if item.get("kind") == "after")
        save_state()
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
    delivery = _delivery_line(report, report_error)
    if delivery:
        text += "\n\n" + delivery
    elif not pair:
        text += "\n\nКлиенту уйдёт само, когда будут оба фото."
    sent_ok = _photos_went(report)
    nowhere = isinstance(report, dict) and report.get("pending") and not report.get("destination")
    buttons = [
        [{"text": "До", "callback_data": "phd:before"},
         {"text": "После", "callback_data": "phd:after"}],
    ]
    if pair and not sent_ok and not nowhere:
        buttons.append([{"text": "Отправить клиенту", "callback_data": f"phs:{booking_id}"}])
    buttons.append([{"text": "Отменить", "callback_data": "cancel"}])
    user_state(chat_id)["step"] = "kind"
    user_state(chat_id).setdefault("data", {})["booking_id"] = booking_id
    save_state()
    send(chat_id, text, {"inline_keyboard": buttons})


def send_report(chat_id: int, booking_id: str) -> None:
    when = str((user_state(chat_id).get("data") or {}).get("when") or "today")
    status, body = api("POST", f"/admin/bookings/{booking_id}/report/send?require_pair=1", timeout=40)
    if status != 200 or not isinstance(body, dict):
        send(chat_id, f"Отчёт не ушёл. {detail_of(body)}".strip(), menu_for(chat_id))
        reset_flow(chat_id)
        return
    if _photos_went(body):
        user_state(chat_id)["flow"] = "photo"
        show_photo_bookings(chat_id, when, note=_delivery_line(body, ""), skip_id=booking_id)
        return
    reset_flow(chat_id)
    send(chat_id, _delivery_line(body, ""), menu_for(chat_id))


def start_puppy(chat_id: int) -> None:
    st = user_state(chat_id)
    st["flow"] = "puppy"
    st["step"] = "name"
    st["data"] = {}
    save_state()
    send(chat_id, "Кличка щенка", {"inline_keyboard": [[{"text": "Отменить", "callback_data": "cancel"}]]})


def _ask_name(chat_id: int) -> None:
    user_state(chat_id)["step"] = "name"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Кличка щенка"), {"inline_keyboard": [
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_sex(chat_id: int) -> None:
    user_state(chat_id)["step"] = "sex"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Пол"), {"inline_keyboard": [
        [{"text": "мальчик", "callback_data": "px:мальчик"},
         {"text": "девочка", "callback_data": "px:девочка"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_color(chat_id: int) -> None:
    user_state(chat_id)["step"] = "color"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Окрас. Можно нажать кнопку или написать свой."), {"inline_keyboard": [
        [{"text": "gold", "callback_data": "pc:gold"}],
        [{"text": "ice gold", "callback_data": "pc:ice gold"}],
        [{"text": "red brown", "callback_data": "pc:red brown"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_birth(chat_id: int) -> None:
    user_state(chat_id)["step"] = "birth"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Дата рождения, например 2.08. Год текущий."), {"inline_keyboard": [
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_size(chat_id: int) -> None:
    user_state(chat_id)["step"] = "size"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Размер"), {"inline_keyboard": [
        [{"text": "микро", "callback_data": "pz:микро"},
         {"text": "мини", "callback_data": "pz:мини"},
         {"text": "стандарт", "callback_data": "pz:стандарт"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_price(chat_id: int) -> None:
    user_state(chat_id)["step"] = "price"
    save_state()
    send(chat_id, _puppy_prompt(chat_id, "Цена: 350 000, 350.000 или 350."), {"inline_keyboard": [
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_card(chat_id: int) -> str:
    data = user_state(chat_id).get("data") or {}
    return "\n".join([
        "<b>Запишу так</b>",
        data.get("name") or "",
        f"Пол: {data.get('sex') or ''}",
        f"Окрас: {data.get('color') or ''}",
        f"Дата рождения: {data.get('birth_label') or 'не указана'}",
        f"Размер: {data.get('size') or 'не указан'}",
        f"Цена: {price_label(data.get('price'))}",
    ])


def _puppy_prompt(chat_id: int, hint: str) -> str:
    if user_state(chat_id).get("editing"):
        return puppy_card(chat_id) + "\n\n" + hint
    return hint


def _after_puppy_field(chat_id: int, nxt) -> None:
    if user_state(chat_id).pop("editing", None):
        save_state()
        puppy_confirm(chat_id)
        return
    nxt(chat_id)


def _puppy_gap(chat_id: int) -> str:
    data = user_state(chat_id).get("data") or {}
    if not data.get("birth"):
        return "birth"
    if data.get("size") not in ("микро", "мини", "стандарт"):
        return "size"
    if not data.get("price"):
        return "price"
    return ""


def puppy_confirm(chat_id: int) -> None:
    gap = _puppy_gap(chat_id)
    if gap == "birth":
        puppy_birth(chat_id)
        return
    if gap == "size":
        puppy_size(chat_id)
        return
    if gap == "price":
        puppy_price(chat_id)
        return
    user_state(chat_id)["step"] = "confirm"
    user_state(chat_id)["editing"] = False
    save_state()
    send(chat_id, puppy_card(chat_id), {"inline_keyboard": [
        [{"text": "Записать", "callback_data": "py:save"}],
        [{"text": "Изменить", "callback_data": "py:edit"}],
        [{"text": "Отменить", "callback_data": "cancel"}],
    ]})


def puppy_edit_menu(chat_id: int) -> None:
    user_state(chat_id)["step"] = "editmenu"
    save_state()
    send(chat_id, puppy_card(chat_id), {"inline_keyboard": [
        [{"text": "Кличка", "callback_data": "py:part:name"},
         {"text": "Пол", "callback_data": "py:part:sex"}],
        [{"text": "Окрас", "callback_data": "py:part:color"},
         {"text": "Дата", "callback_data": "py:part:birth"}],
        [{"text": "Размер", "callback_data": "py:part:size"},
         {"text": "Цена", "callback_data": "py:part:price"}],
        [{"text": "Назад", "callback_data": "py:back"}],
    ]})


def save_puppy(chat_id: int, force: bool = False) -> None:
    if _puppy_gap(chat_id):
        puppy_confirm(chat_id)
        return
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
    status, body = api("POST", "/admin/puppies", payload, timeout=20)
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
        _after_puppy_field(chat_id, puppy_sex)
        return
    if flow == "puppy" and step == "color":
        st.setdefault("data", {})["color"] = raw
        save_state()
        _after_puppy_field(chat_id, puppy_birth)
        return
    if flow == "puppy" and step == "birth":
        parsed = parse_birth(raw)
        if not parsed:
            puppy_birth(chat_id)
            return
        iso, label = parsed
        st.setdefault("data", {})["birth"] = iso
        st["data"]["birth_label"] = label
        save_state()
        _after_puppy_field(chat_id, puppy_size)
        return
    if flow == "puppy" and step == "price":
        price = parse_price(raw)
        if price is None:
            puppy_price(chat_id)
            return
        st.setdefault("data", {})["price"] = price
        save_state()
        puppy_confirm(chat_id)
        return
    if raw in ("/start", "/menu"):
        reset_flow(chat_id)
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
    if data == "menu:new":
        reset_flow(chat_id)
        user_state(chat_id)["screen_id"] = None
        save_state()
        show_home(chat_id)
        return
    if data in ("cancel", "home"):
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
    if data == "stats" and mode == "admin":
        show_stats_pick(chat_id)
        return
    if data.startswith("st:") and mode == "admin":
        show_stat(chat_id, data.split(":", 1)[1])
        return
    if data == "today" and mode == "admin":
        show_stat(chat_id, "today")
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
        _after_puppy_field(chat_id, puppy_color)
        return
    if data.startswith("pc:") and mode == "karina":
        user_state(chat_id).setdefault("data", {})["color"] = data.split(":", 1)[1]
        save_state()
        _after_puppy_field(chat_id, puppy_birth)
        return
    if data.startswith("pz:") and mode == "karina":
        value = data.split(":", 1)[1]
        if value not in ("микро", "мини", "стандарт"):
            puppy_size(chat_id)
            return
        user_state(chat_id).setdefault("data", {})["size"] = value
        save_state()
        _after_puppy_field(chat_id, puppy_price)
        return
    if data == "py:edit" and mode == "karina":
        puppy_edit_menu(chat_id)
        return
    if data == "py:back" and mode == "karina":
        puppy_confirm(chat_id)
        return
    if data.startswith("py:part:") and mode == "karina":
        part = data.split(":", 2)[2]
        user_state(chat_id)["editing"] = True
        save_state()
        {
            "name": lambda: _ask_name(chat_id),
            "sex": lambda: puppy_sex(chat_id),
            "color": lambda: puppy_color(chat_id),
            "birth": lambda: puppy_birth(chat_id),
            "size": lambda: puppy_size(chat_id),
            "price": lambda: puppy_price(chat_id),
        }.get(part, puppy_edit_menu)()
        return
    if data == "py:save" and mode == "karina":
        save_puppy(chat_id, force=False)
        return
    if data == "py:force" and mode == "karina":
        save_puppy(chat_id, force=True)
        return
    show_home(chat_id)


def warm_ready() -> None:
    """Срезы и ссылка пульса обновляются в фоне, кнопка их не ждёт."""
    status, data = api("GET", "/admin/stats", timeout=8)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("today"), dict):
        _ready["stats"] = data
        _ready["today"] = data["today"]
    bookings: dict[str, list] = {}
    for when in ("today", "yesterday"):
        status, rows = api("GET", f"/admin/bookings?when={when}&for_photos=1", timeout=4)
        if status == 200 and isinstance(rows, list):
            bookings[when] = rows
    if bookings:
        _ready["bookings"] = bookings
    for raw_id in KARINA_IDS:
        chat_id = int(raw_id)
        if role_of(chat_id) != "karina":
            if user_state(chat_id).get("menu_button") != "admin":
                apply_menu_button(chat_id, "admin")
                user_state(chat_id)["menu_button"] = "admin"
            continue
        url = pulse_url(chat_id)
        if not url:
            continue
        user_state(chat_id)["pulse_url"] = url
        if user_state(chat_id).get("menu_button") != "pulse":
            apply_menu_button(chat_id, "karina")
            user_state(chat_id)["menu_button"] = "pulse"
    save_state()


def warm_loop() -> None:
    while True:
        try:
            warm_ready()
        except Exception:
            log.warning("фоновый срез не обновился", exc_info=True)
        time.sleep(600)


def handle(update: dict) -> None:
    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    if callback:
        source = callback.get("message") or {}
        chat_id = int((source.get("chat") or {}).get("id") or 0)
        answer_callback(callback.get("id") or "")
        mid = source.get("message_id")
        data = callback.get("data") or ""
        # Кнопка на уведомлении не делает эту карточку экраном: меню уйдёт новым сообщением.
        if chat_id > 0 and mid and data != "menu:new":
            user_state(chat_id)["screen_id"] = mid
    else:
        chat_id = int((message.get("chat") or {}).get("id") or 0)
        if chat_id > 0:
            reply_below(chat_id)
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
    threading.Thread(target=warm_loop, name="keris-warm", daemon=True).start()
    log.info("admin bot started, admin=%s karina=%s", sorted(ADMIN_IDS), sorted(KARINA_IDS))
    while True:
        status, data = _http("GET", f"{TG_API}/getUpdates?timeout=20&offset={offset}", timeout=28)
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
