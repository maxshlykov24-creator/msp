#!/usr/bin/env python3
"""Настройка бота алертов: проверить токен, найти chat_id, послать тест.

Зачем скрипт, а не «зайди в Telegram»: chat_id группы руками не увидеть, а
ошибиться в нём легко — у групп он отрицательный, у супергрупп начинается с
`-100`. Скрипт показывает ровно те строки, которые надо положить в `.env`.

    python3 scripts/alert_setup.py --token 123456:AA...           # шаг 1: кто я и куда писать
    python3 scripts/alert_setup.py --token 123456:AA... --chat -1001234567890   # шаг 2: тест

Порядок целиком:
1. @BotFather → /newbot → имя и username (см. RUNBOOK, раздел 6б).
2. Создать группу, добавить бота, написать в неё любое сообщение.
   Без сообщения Telegram не отдаёт чат в getUpdates: бот не видит истории до
   своего появления.
3. Прогнать этот скрипт, положить TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env
   хаба, задеплоить.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, payload: dict | None = None) -> dict:
    url = API.format(token=token, method=method)
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Telegram отклонил {method}: {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"нет связи с api.telegram.org: {exc}") from exc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="токен от @BotFather")
    ap.add_argument("--chat", default="", help="chat_id: если задан, шлём тест")
    args = ap.parse_args()

    me = call(args.token, "getMe").get("result", {})
    print(f"бот: @{me.get('username')} ({me.get('first_name')}), id={me.get('id')}")

    if not args.chat:
        updates = call(args.token, "getUpdates").get("result", [])
        chats: dict[int, str] = {}
        for upd in updates:
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if chat.get("id"):
                chats[chat["id"]] = f"{chat.get('type')} {chat.get('title') or chat.get('username') or ''}".strip()
        if not chats:
            print("\nчатов не видно. Напиши любое сообщение в группу, где есть бот,\n"
                  "и запусти снова: до первого сообщения Telegram чат не отдаёт.")
            return 1
        print("\nнайденные чаты:")
        for cid, title in chats.items():
            print(f"  {cid}  {title}")
        print("\nдальше: тот же вызов с --chat <id>")
        return 0

    call(args.token, "sendMessage", {
        "chat_id": args.chat,
        "text": ("✅ LicenseBridge: канал алертов подключён.\n"
                 "Сюда придут: отвал добавочного, упавший транк, застрявшие события, "
                 "тишина у менеджера, всплеск заявок, утренняя сводка."),
        "disable_web_page_preview": True,
    })
    print("тест отправлен. В .env хаба положить:\n"
          f"TELEGRAM_BOT_TOKEN={args.token}\n"
          f"TELEGRAM_CHAT_ID={args.chat}\n"
          "затем: python3 scripts/deploy.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
