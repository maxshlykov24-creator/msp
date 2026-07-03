#!/usr/bin/env python3
"""
Выгружает всю историю чатов из RetailCRM Message Gateway (MG Bot API) и сохраняет:

  data/raw/chat_<id>.json          — сырой дамп чата + всех сообщений (для повторной обработки/миграции)
  data/dialogs/<id>_<phone>.html   — читаемая переписка
  data/index.csv                   — сводная таблица по всем чатам

Документация MG Bot API: https://139810.selcdn.ru/download/doc/mg-bot-api/bot.v1.en.html
Авторизация: заголовок x-bot-token (получить через register_bot.py).

Пагинация /chats и /messages в доке описана нечётко («до 1000 записей, при since/until —
в обратном порядке»), поэтому обходим на дедупликации по id и по времени в обе стороны —
это чуть больше запросов, зато без риска пропустить сообщения.

Запуск:
    python export_chats.py            # выгрузить всё, пропуская уже готовые чаты
    python export_chats.py --force    # перевыгрузить всё заново
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DIALOGS_DIR = DATA_DIR / "dialogs"

load_dotenv(ENV_PATH)

RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", "0.3"))
MAX_RETRIES = 5


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Заполните {name} в .env (запустите сперва register_bot.py)")
    return val


class MgBotClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"x-bot-token": token})

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        url = f"{self.endpoint}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                time.sleep(RATE_LIMIT_DELAY)
                data = resp.json()
                return data if isinstance(data, list) else data.get("items", []) or []
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 30)
                print(f"  [{resp.status_code}] retry #{attempt} через {wait}s ({path})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"Ошибка {resp.status_code} на {path}: {resp.text[:300]}", file=sys.stderr)
            resp.raise_for_status()
        raise RuntimeError(f"Не удалось получить {path} после {MAX_RETRIES} попыток")

    def paginate_by_time(self, path: str, base_params: dict[str, Any] | None = None, limit: int = 1000) -> list[dict]:
        """Обходит список назад по времени (until), дедуплицируя по id.
        Затем на всякий случай добавляет обход вперёд (since) от начала эпохи —
        подстраховка на случай другого порядка сортировки на стороне API.
        """
        base_params = dict(base_params or {})
        seen: dict[Any, dict] = {}

        # обход назад от "сейчас"
        until: str | None = None
        while True:
            params = dict(base_params, limit=limit)
            if until:
                params["until"] = until
            batch = self.get(path, params)
            if not batch:
                break
            new_count = 0
            oldest_time = None
            for item in batch:
                item_id = item.get("id")
                if item_id not in seen:
                    seen[item_id] = item
                    new_count += 1
                t = item.get("time") or item.get("created_at")
                if t and (oldest_time is None or t < oldest_time):
                    oldest_time = t
            if new_count == 0 or oldest_time is None or oldest_time == until:
                break
            until = oldest_time
            if len(batch) < limit:
                break

        # обход вперёд с самого начала — подстраховка от иной семантики сортировки
        since: str | None = "2000-01-01T00:00:00.000000"
        while True:
            params = dict(base_params, limit=limit, since=since)
            batch = self.get(path, params)
            if not batch:
                break
            new_count = 0
            newest_time = None
            for item in batch:
                item_id = item.get("id")
                if item_id not in seen:
                    seen[item_id] = item
                    new_count += 1
                t = item.get("time") or item.get("created_at")
                if t and (newest_time is None or t > newest_time):
                    newest_time = t
            if newest_time is None or newest_time == since:
                break
            since = newest_time
            if len(batch) < limit and new_count == 0:
                break

        return list(seen.values())


def sender_label(message: dict, chat: dict, managers_by_id: dict[int, dict]) -> str:
    from_id = (message.get("from") or {}).get("id")
    customer_id = (chat.get("customer") or {}).get("id")
    if from_id is not None and customer_id is not None and from_id == customer_id:
        return "Клиент"
    manager = managers_by_id.get(from_id)
    if manager:
        name = " ".join(filter(None, [manager.get("first_name"), manager.get("last_name")])).strip()
        return f"Менеджер {name}".strip() if name else "Менеджер"
    return "Бот/система"


def render_dialog_html(chat: dict, messages: list[dict], managers_by_id: dict[int, dict]) -> str:
    customer = chat.get("customer") or {}
    title = " ".join(filter(None, [customer.get("first_name"), customer.get("last_name")])).strip()
    title = title or customer.get("username") or customer.get("phone") or f"chat_{chat.get('id')}"

    rows = []
    for m in sorted(messages, key=lambda x: x.get("time") or x.get("created_at") or ""):
        who = html.escape(sender_label(m, chat, managers_by_id))
        when = html.escape(str(m.get("time") or m.get("created_at") or ""))
        text = html.escape(m.get("content") or "")
        attachments = []
        for item in m.get("items") or []:
            cap = item.get("caption") or item.get("type") or "вложение"
            attachments.append(f"[{html.escape(str(cap))}]")
        att_html = (" " + " ".join(attachments)) if attachments else ""
        rows.append(
            f'<div class="msg"><span class="time">{when}</span> '
            f'<b class="who">{who}:</b> <span class="text">{text}{att_html}</span></div>'
        )

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Переписка — {html.escape(title)}</title>
<style>
body{{font-family:sans-serif;max-width:800px;margin:20px auto;padding:0 16px;}}
.msg{{padding:6px 0;border-bottom:1px solid #eee;}}
.time{{color:#888;font-size:12px;margin-right:8px;}}
.who{{margin-right:6px;}}
h1{{font-size:18px;}}
</style></head>
<body>
<h1>Переписка: {html.escape(title)}</h1>
<p>Телефон: {html.escape(customer.get('phone') or '—')} · Канал: {html.escape((chat.get('channel') or {}).get('type') or '—')} ·
Сообщений: {len(messages)}</p>
{''.join(rows)}
</body></html>"""


def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:80]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="перевыгрузить уже сохранённые чаты")
    args = parser.parse_args()

    endpoint = _require("RETAILCRM_MG_BOT_ENDPOINT")
    token = _require("RETAILCRM_MG_BOT_TOKEN")
    client = MgBotClient(endpoint, token)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DIALOGS_DIR.mkdir(parents=True, exist_ok=True)

    print("Тяну список менеджеров/ботов...")
    managers = client.paginate_by_time("/users")
    managers_by_id = {m["id"]: m for m in managers if "id" in m}
    print(f"  менеджеров/ботов: {len(managers_by_id)}")

    print("Тяну список чатов...")
    chats = client.paginate_by_time("/chats")
    print(f"  чатов найдено: {len(chats)}")

    if not chats:
        print(
            "\nЧатов не найдено. Похоже, у bot-модуля нет доступа к истории — "
            "проверьте в RetailCRM: Настройки -> Чат-центр -> Боты (доступ к чатам), "
            "см. README.md, раздел «Важное допущение».",
            file=sys.stderr,
        )
        sys.exit(2)

    index_rows = []
    for i, chat in enumerate(chats, start=1):
        chat_id = chat["id"]
        raw_path = RAW_DIR / f"chat_{chat_id}.json"
        if raw_path.exists() and not args.force:
            print(f"[{i}/{len(chats)}] чат {chat_id} уже выгружен, пропуск")
            with raw_path.open(encoding="utf-8") as f:
                dump = json.load(f)
            messages = dump.get("messages", [])
        else:
            print(f"[{i}/{len(chats)}] чат {chat_id}: тяну сообщения...")
            messages = client.paginate_by_time("/messages", {"chat_id": chat_id})
            raw_path.write_text(
                json.dumps({"chat": chat, "messages": messages}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        customer = chat.get("customer") or {}
        phone = customer.get("phone") or customer.get("username") or f"id{customer.get('id', chat_id)}"
        name = " ".join(filter(None, [customer.get("first_name"), customer.get("last_name")])).strip()
        fname = safe_filename(f"{chat_id}_{phone}")
        dialog_path = DIALOGS_DIR / f"{fname}.html"
        dialog_path.write_text(render_dialog_html(chat, messages, managers_by_id), encoding="utf-8")

        times = [m.get("time") or m.get("created_at") for m in messages if m.get("time") or m.get("created_at")]
        index_rows.append(
            {
                "chat_id": chat_id,
                "customer_name": name,
                "phone": customer.get("phone") or "",
                "username": customer.get("username") or "",
                "channel_type": (chat.get("channel") or {}).get("type") or "",
                "messages_count": len(messages),
                "first_message_at": min(times) if times else "",
                "last_message_at": max(times) if times else "",
                "file": str(dialog_path.relative_to(ROOT)),
            }
        )

    index_path = DATA_DIR / "index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "chat_id", "customer_name", "phone", "username", "channel_type",
                "messages_count", "first_message_at", "last_message_at", "file",
            ],
        )
        w.writeheader()
        w.writerows(index_rows)

    total_messages = sum(r["messages_count"] for r in index_rows)
    print(f"\nГотово. Чатов: {len(index_rows)}, сообщений: {total_messages}")
    print(f"Индекс: {index_path}")
    print(f"Переписка: {DIALOGS_DIR}")
    print(f"Сырые данные: {RAW_DIR}")


if __name__ == "__main__":
    main()
