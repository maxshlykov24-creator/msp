#!/usr/bin/env python3
"""Личный CLI к amoCRM. Чтение через amo, отправка через Wazzup."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WAZZUP_API = "https://api.wazzup24.com/v3"
WAZZUP_API_V2 = "https://api.wazzup24.com/v2"

# transport канала Wazzup → chatType в POST /v3/message
TRANSPORT_TO_CHAT_TYPE = {
    "tgapi": "telegram",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "wapi": "whatsapp",
    "max": "max",
    "maxbot": "max",
    "instagram": "instagram",
    "vk": "vk",
    "viber": "viber",
}


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def settings() -> tuple[str, str]:
    load_env()
    raw = os.environ.get("AMOCRM_SUBDOMAIN", "").strip()
    token = os.environ.get("AMOCRM_LONG_LIVED_TOKEN", "").strip()
    if not raw or not token:
        sys.exit(
            "В .env нужны AMOCRM_SUBDOMAIN и AMOCRM_LONG_LIVED_TOKEN.\n"
            "Файл: 2. БИЗНЕС/08_Системное/_код/amo-self/.env"
        )
    raw = raw.replace("https://", "").replace("http://", "").strip("/")
    if ".amocrm." in raw or ".kommo." in raw:
        host = raw.split("/")[0]
    else:
        host = f"{raw}.amocrm.ru"
    return host, token


def wazzup_key() -> str:
    load_env()
    key = os.environ.get("WAZZUP_API_KEY", "").strip()
    if not key:
        sys.exit(
            "Нет WAZZUP_API_KEY в .env.\n"
            "Wazzup → Интеграция с CRM → API → ключ, либо вкладка Дополнительно."
        )
    return key


def api_request(path: str, params: dict[str, str] | None = None) -> tuple[int, dict]:
    host, token = settings()
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"https://{host}{path}{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            parsed = {"detail": detail[:400]}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        sys.exit(f"Сеть: {exc.reason}")


def api_get(path: str, params: dict[str, str] | None = None) -> dict:
    code, data = api_request(path, params)
    if code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        sys.exit(f"amo {code} на {path}: {detail}")
    return data


def wazzup_request(method: str, path: str, payload: dict | None = None, version: int = 3):
    base = WAZZUP_API if version == 3 else WAZZUP_API_V2
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {wazzup_key()}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        sys.exit(f"Wazzup {exc.code} на {path}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Сеть Wazzup: {exc.reason}")


def _items(data: dict, key: str) -> list:
    embedded = data.get("_embedded") or {}
    return embedded.get(key) or []


def cmd_whoami() -> None:
    data = api_get("/api/v4/account")
    name = data.get("name") or "—"
    subdomain = data.get("subdomain") or "—"
    print(f"{name}  ({subdomain})")


def cmd_leads(limit: int) -> None:
    data = api_get("/api/v4/leads", {"limit": str(limit)})
    rows = _items(data, "leads")
    if not rows:
        print("Сделок нет или нет прав.")
        return
    print(f"{'id':<12} имя")
    for row in rows:
        print(f"{row.get('id', ''):<12} {row.get('name') or '—'}")


def cmd_contacts(limit: int) -> None:
    data = api_get("/api/v4/contacts", {"limit": str(limit)})
    rows = _items(data, "contacts")
    if not rows:
        print("Контактов нет или нет прав.")
        return
    print(f"{'id':<12} имя")
    for row in rows:
        print(f"{row.get('id', ''):<12} {row.get('name') or '—'}")


def cmd_talks(limit: int) -> None:
    data = api_get("/api/v4/talks", {"limit": str(limit)})
    rows = _items(data, "talks")
    if not rows:
        print("Чатов в amo нет или нет прав.")
        return
    print(f"{'id':<8} {'origin':<22} {'status':<10} contact")
    for row in rows:
        cid = row.get("contact_id")
        name = "—"
        if cid:
            name = api_get(f"/api/v4/contacts/{cid}").get("name") or "—"
        print(
            f"{row.get('talk_id') or '':<8} "
            f"{(row.get('origin') or '—'):<22} "
            f"{(row.get('status') or '—'):<10} "
            f"{name}"
        )


def list_channels() -> list:
    data = wazzup_request("GET", "/channels")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("channels") or data.get("data") or []
    return []


def cmd_channels() -> None:
    rows = list_channels()
    if not rows:
        print("Каналов Wazzup нет.")
        return
    print(f"{'transport':<12} {'state':<16} channelId  plainId")
    for row in rows:
        print(
            f"{(row.get('transport') or '—'):<12} "
            f"{(row.get('state') or '—'):<16} "
            f"{row.get('channelId') or '—'}  "
            f"{row.get('plainId') or '—'}"
        )


def pick_channel() -> dict:
    load_env()
    wanted = os.environ.get("WAZZUP_CHANNEL_ID", "").strip()
    rows = list_channels()
    if wanted:
        for row in rows:
            if row.get("channelId") == wanted:
                return row
        sys.exit(f"Канал {wanted} в Wazzup не найден. Сначала: python3 amo.py channels")
    active = [row for row in rows if row.get("state") == "active"]
    telegram = [row for row in active if row.get("transport") in ("tgapi", "telegram")]
    if len(telegram) == 1:
        return telegram[0]
    if len(active) == 1:
        return active[0]
    print("Несколько каналов. Поставь WAZZUP_CHANNEL_ID в .env")
    cmd_channels()
    sys.exit(1)


def find_contact(query: str) -> dict:
    rows = _items(api_get("/api/v4/contacts", {"query": query, "limit": "20"}), "contacts")
    if not rows:
        sys.exit(f"Контакт «{query}» в amo не найден.")
    exact = [row for row in rows if (row.get("name") or "").lower() == query.lower()]
    chosen = exact if len(exact) == 1 else rows
    if len(chosen) != 1:
        print("Несколько контактов, уточни имя:")
        for row in rows:
            print(f"  {row.get('id')}  {row.get('name')}")
        sys.exit(1)
    return api_get(f"/api/v4/contacts/{chosen[0]['id']}")


def contact_route(contact: dict) -> tuple[str | None, str | None]:
    phone = None
    username = None
    for field in contact.get("custom_fields_values") or []:
        name = (field.get("field_name") or "").lower()
        code = (field.get("field_code") or "").lower()
        for item in field.get("values") or []:
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            if "username" in name or "username" in code:
                username = value.lstrip("@")
            elif "phone" in name or "телефон" in name or code == "phone":
                digits = re.sub(r"\D", "", value)
                if digits:
                    phone = digits
    return phone, username


def cmd_send(to: str, text: str) -> None:
    body = text.strip()
    if not body:
        sys.exit("Пустой текст.")
    contact = find_contact(to)
    phone, username = contact_route(contact)
    if not phone and not username:
        sys.exit(f"У «{contact.get('name')}» нет телефона и Telegram username.")
    channel = pick_channel()
    chat_type = TRANSPORT_TO_CHAT_TYPE.get(channel.get("transport") or "")
    if not chat_type:
        sys.exit(f"Не знаю chatType для транспорта {channel.get('transport')}.")
    payload = {
        "channelId": channel.get("channelId"),
        "chatType": chat_type,
        "text": body,
    }
    if username and chat_type == "telegram":
        payload["username"] = username
    elif phone:
        payload["phone"] = phone
    else:
        sys.exit("Для этого канала нужен телефон, в карточке его нет.")
    result = wazzup_request("POST", "/message", payload)
    print(
        f"Отправлено: {contact.get('name')}  "
        f"канал {channel.get('transport')} / {channel.get('plainId') or channel.get('channelId')}"
    )
    if isinstance(result, dict) and result:
        msg_id = result.get("messageId") or result.get("id")
        if msg_id:
            print(f"messageId {msg_id}")


def _dump_data(payload: dict) -> dict:
    block = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    return block if isinstance(block, dict) else {}


def fetch_wazzup_dump(days: int, channel_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    body = {
        "start_at": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end_at": now.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
    }
    if channel_id:
        body["channel_id"] = channel_id
    created = _dump_data(wazzup_request("POST", "/messages/messages_dump", body, version=2))
    export_id = created.get("export_id")
    if not export_id:
        sys.exit(f"Wazzup не дал export_id: {created}")
    deadline = time.time() + 90
    while time.time() < deadline:
        status = _dump_data(wazzup_request("GET", f"/messages/messages_dump/{export_id}", version=2))
        state = status.get("status")
        url = status.get("url")
        if state in ("done", "webhook_failed") and url:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=60) as resp:
                return resp.read().decode("utf-8-sig")
        if state in ("done", "webhook_failed") and not url:
            sys.exit("Выгрузка готова, но ссылки на CSV нет.")
        time.sleep(2)
    sys.exit("Выгрузка Wazzup не успела за 90 секунд.")


def row_matches_contact(row: dict, phone: str | None, username: str | None) -> bool:
    blob = " ".join(str(v) for v in row.values()).lower()
    if username and username.lower() in blob:
        return True
    if phone:
        digits = re.sub(r"\D", "", phone)
        row_digits = re.sub(r"\D", "", blob)
        if digits and digits[-10:] in row_digits:
            return True
    return False


def find_talk(contact_id: int) -> dict | None:
    rows = _items(api_get("/api/v4/talks", {"filter[contact_id]": str(contact_id), "limit": "20"}), "talks")
    open_rows = [row for row in rows if row.get("is_in_work") or row.get("status") == "in_work"]
    pool = open_rows or rows
    if not pool:
        return None
    return max(pool, key=lambda row: row.get("updated_at") or 0)


def cmd_history_from_amo(talk_id: int, limit: int) -> bool:
    code, data = api_request(f"/api/v4/talks/{talk_id}/messages", {"limit": str(min(limit, 250))})
    if code == 403:
        return False
    if code >= 400:
        sys.exit(f"amo {code} на историю чата: {data.get('detail')}")
    rows = _items(data, "messages")
    if not rows:
        print("В этой беседе amo не вернула сообщения.")
        return True
    rows = sorted(rows, key=lambda row: row.get("created_at") or 0)[-limit:]
    print(f"Беседа {talk_id}  последних {len(rows)}")
    for row in rows:
        when = datetime.fromtimestamp(int(row.get("created_at") or 0)).strftime("%Y-%m-%d %H:%M")
        direction = "я" if row.get("type") == "outgoing" else "она"
        text = (row.get("text") or "").strip() or f"[{row.get('message_type') or 'без текста'}]"
        print(f"{when}  {direction}: {text}")
    return True


def cmd_history(to: str, days: int, limit: int) -> None:
    contact = find_contact(to)
    talk = find_talk(int(contact["id"]))
    if talk and cmd_history_from_amo(int(talk["talk_id"]), limit):
        return
    if talk:
        print(
            f"Контакт «{contact.get('name')}», беседа {talk.get('talk_id')} есть. "
            "Текст amo не отдаёт: у токена нет права истории внешних чатов.\n"
            "amo → Настройки → Интеграции → Cursor → доступы → история чатов / "
            "External chat history → новый долгоживущий токен в .env"
        )
        return
    phone, username = contact_route(contact)
    channel = pick_channel()
    raw = fetch_wazzup_dump(days, channel.get("channelId"))
    reader = csv.DictReader(io.StringIO(raw))
    rows = [row for row in reader if row_matches_contact(row, phone, username)]
    if not rows:
        headers = reader.fieldnames or []
        print(
            f"В выгрузке за {days} дн. нет строк по «{contact.get('name')}». "
            f"Колонки CSV: {', '.join(headers)}"
        )
        return
    # свежие внизу как в мессенджере
    def sort_key(row: dict) -> str:
        for key in ("dateTime", "datetime", "createdAt", "created_at", "timestamp"):
            if row.get(key):
                return str(row[key])
        return ""

    rows.sort(key=sort_key)
    shown = rows[-limit:]
    print(f"{contact.get('name')}  {len(rows)} сообщ. за {days} дн.  показ последних {len(shown)}")
    for row in shown:
        when = row.get("dateTime") or row.get("datetime") or row.get("createdAt") or "—"
        text = row.get("text") or row.get("message") or row.get("body") or ""
        echo = str(row.get("isEcho") or row.get("outgoing") or "").lower()
        direction = "я" if echo in ("true", "1", "yes", "outgoing") else "она"
        if not text:
            text = row.get("type") or "[без текста]"
        print(f"{when}  {direction}: {text}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Личный CLI: amoCRM на чтение, Wazzup на отправку."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("whoami", help="проверить токен amo")
    sub.add_parser("channels", help="каналы Wazzup")
    for name, help_text in (
        ("leads", "последние сделки"),
        ("contacts", "последние контакты"),
        ("talks", "чаты, которые amo уже подтянула"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--limit", type=int, default=10)
    send = sub.add_parser("send", help="отправить в чат Wazzup")
    send.add_argument("--to", required=True, help="имя контакта в amo, точное лучше")
    send.add_argument("--text", required=True, help="текст сообщения")
    history = sub.add_parser("history", help="история чата из выгрузки Wazzup")
    history.add_argument("--to", required=True, help="имя контакта в amo")
    history.add_argument("--days", type=int, default=30)
    history.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.command == "whoami":
        cmd_whoami()
    elif args.command == "leads":
        cmd_leads(args.limit)
    elif args.command == "contacts":
        cmd_contacts(args.limit)
    elif args.command == "talks":
        cmd_talks(args.limit)
    elif args.command == "channels":
        cmd_channels()
    elif args.command == "send":
        cmd_send(args.to, args.text)
    elif args.command == "history":
        cmd_history(args.to, args.days, args.limit)


if __name__ == "__main__":
    main()
