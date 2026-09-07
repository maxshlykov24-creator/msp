"""Ищем текст сообщения по id из события.

Событие знает message.id / talk_id / created_by, но текста нет.
Пробуем все известные точки, где текст мог лежать.
"""
from __future__ import annotations

import json
import sys

from amo_client import items, request

LEAD = 45271017  # исходящее Евгения есть
TALK = 5561
MSG = "1deac4b2-07ad-4332-9f2a-abc0afaadfc8"


def try_get(path: str, params: dict | None = None) -> None:
    code, data = request(path, params)
    preview = str(data)[:220]
    print(f"  {code} {path} {params or ''}")
    print(f"     {preview}")


def main() -> int:
    print("1) talks/messages (тот же 403?)")
    try_get(f"/api/v4/talks/{TALK}/messages", {"limit": "20"})

    print("2) notes сделки с исходящим чатом")
    code, data = request(f"/api/v4/leads/{LEAD}/notes", {"limit": "50"})
    print(f"  {code} notes count={len(items(data, 'notes')) if code < 400 else 0}")
    if code < 400:
        for note in items(data, "notes"):
            params = note.get("params") or {}
            print(f"     type={note.get('note_type')} keys={list(params.keys())} created_by={note.get('created_by')}")
            text = params.get("text") or params.get("message") or ""
            if text:
                print(f"       text={str(text)[:120]!r}")

    print("3) одиночное событие с embed")
    code, events = request(
        "/api/v4/events",
        {"filter[entity]": "lead", "filter[entity_id]": str(LEAD), "limit": "10"},
    )
    print(f"  events by lead -> {code} count={len(items(events, 'events')) if code < 400 else 0}")
    if code < 400:
        for ev in items(events, "events")[:5]:
            print(f"     {ev.get('type')} created_by={ev.get('created_by')} va={json.dumps(ev.get('value_after'), ensure_ascii=False)[:160]}")

    print("4) account: какие scopes у токена")
    code, data = request("/api/v4/account", {"with": "datetime,drive_url"})
    print(f"  account {code} keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
    if isinstance(data, dict):
        print(f"     {json.dumps({k: data[k] for k in data if k != '_links'}, ensure_ascii=False)[:500]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
