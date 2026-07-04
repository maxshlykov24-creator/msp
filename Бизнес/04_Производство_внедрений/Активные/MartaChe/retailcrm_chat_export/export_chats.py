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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DIALOGS_DIR = DATA_DIR / "dialogs"
CHATS_LIST_PATH = DATA_DIR / "chats_list.json"

load_dotenv(ENV_PATH)

RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", "0.3"))
MAX_RETRIES = 5


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Заполните {name} в .env (запустите сперва register_bot.py)")
    return val


API_PREFIX = "/api/bot/v1"


class MgBotClient:
    def __init__(self, endpoint: str, token: str) -> None:
        base = endpoint.rstrip("/")
        if not base.endswith(API_PREFIX):
            base += API_PREFIX
        self.endpoint = base
        self.session = requests.Session()
        self.session.headers.update({"x-bot-token": token})

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        url = f"{self.endpoint}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=(15, 120))
            except requests.exceptions.RequestException as exc:
                wait = min(2 ** attempt, 30)
                print(f"  сеть ({type(exc).__name__}) retry #{attempt} через {wait}s ({path})", file=sys.stderr)
                time.sleep(wait)
                continue
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
        """Пагинация MG Bot API: since_id (>=1) возвращает записи с id > since_id
        по возрастанию, до limit штук. Идём вперёд, пока приходят новые id.
        """
        base_params = dict(base_params or {})
        seen: dict[Any, dict] = {}
        since_id = 1
        while True:
            params = dict(base_params, limit=limit, since_id=since_id)
            batch = self.get(path, params)
            if not batch:
                break
            max_id = since_id
            new_count = 0
            for item in batch:
                item_id = item.get("id")
                if item_id is None:
                    continue
                if item_id not in seen:
                    seen[item_id] = item
                    new_count += 1
                if item_id > max_id:
                    max_id = item_id
            if max_id <= since_id or new_count == 0:
                break
            since_id = max_id
            if len(batch) < limit:
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


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="перевыгрузить уже сохранённые чаты")
    parser.add_argument("--months", type=int, default=6,
                        help="брать чаты с активностью за последние N месяцев (0 = все)")
    parser.add_argument("--refresh-list", action="store_true",
                        help="перезапросить список чатов из API (иначе берём data/chats_list.json)")
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

    if CHATS_LIST_PATH.exists() and not args.refresh_list:
        chats = json.loads(CHATS_LIST_PATH.read_text(encoding="utf-8"))
        print(f"Список чатов из кэша {CHATS_LIST_PATH.name}: {len(chats)}")
    else:
        print("Тяну список чатов из API...")
        chats = client.paginate_by_time("/chats")
        CHATS_LIST_PATH.write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
        print(f"  чатов найдено: {len(chats)}")

    if args.months:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(args.months * 30.5))
        before = len(chats)
        chats = [
            c for c in chats
            if (parse_dt(c.get("last_activity")) or datetime(1970, 1, 1, tzinfo=timezone.utc)) >= cutoff
        ]
        chats.sort(key=lambda c: c.get("last_activity") or "", reverse=True)
        print(f"Фильтр по активности за {args.months} мес (с {cutoff.date()}): {len(chats)} из {before}")

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
