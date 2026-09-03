"""Проба: читаются ли чаты через /events, и видно ли автора (как Евгений на скрине).

talks/{id}/messages на DIVO даёт 403 Invalid scope.
LicenseBridge уже читает переписку общим потоком:
  GET /events?filter[type]=incoming_chat_message|outgoing_chat_message
created_by > 0 = кто из amo отправил.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter

from amo_client import NIKITA_USER_ID, items, request


def dump_event(event: dict) -> None:
    print("-" * 60)
    print(f"type={event.get('type')} entity={event.get('entity_type')}:{event.get('entity_id')}")
    print(f"created_by={event.get('created_by')} created_at={event.get('created_at')}")
    va = event.get("value_after")
    print("value_after:")
    print(json.dumps(va, ensure_ascii=False, indent=2)[:1800])
    vb = event.get("value_before")
    if vb:
        print("value_before keys:", list(vb[0].keys()) if isinstance(vb, list) and vb else type(vb))


def fetch_type(event_type: str, since: int, pages: int = 3) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        code, data = request(
            "/api/v4/events",
            {
                "filter[type]": event_type,
                "filter[created_at][from]": str(since),
                "limit": "50",
                "page": str(page),
            },
        )
        print(f"  {event_type} page {page} -> {code}")
        if code >= 400:
            print(f"    {str(data)[:200]}")
            break
        chunk = items(data, "events")
        if not chunk:
            break
        rows.extend(chunk)
        if not (data.get("_links") or {}).get("next"):
            break
    return rows


def main() -> int:
    since = int(time.time()) - 14 * 86400
    print("=" * 62)
    print("1) incoming_chat_message за 14 дней")
    incoming = fetch_type("incoming_chat_message", since)
    print(f"   событий: {len(incoming)}")
    if incoming:
        dump_event(incoming[0])
        if len(incoming) > 1:
            dump_event(incoming[1])

    print("=" * 62)
    print("2) outgoing_chat_message за 14 дней")
    outgoing = fetch_type("outgoing_chat_message", since)
    print(f"   событий: {len(outgoing)}")
    if outgoing:
        dump_event(outgoing[0])
        if len(outgoing) > 1:
            dump_event(outgoing[1])

    authors = Counter(int(e.get("created_by") or 0) for e in outgoing)
    print("=" * 62)
    print("3) кто писал исходящие (created_by)")
    users = {}
    code, data = request("/api/v4/users", {"limit": "50"})
    if code < 400:
        users = {int(u["id"]): u.get("name", "") for u in items(data, "users")}
    for uid, count in authors.most_common():
        mark = "  <-- НИКИТА" if uid == NIKITA_USER_ID else ""
        print(f"   {uid} {users.get(uid, '?')}: {count}{mark}")

    nikita_out = [e for e in outgoing if int(e.get("created_by") or 0) == NIKITA_USER_ID]
    print(f"   исходящих от Никиты: {len(nikita_out)}")
    if nikita_out:
        dump_event(nikita_out[0])

    # есть ли текст
    def extract_text(event: dict) -> str:
        for block in event.get("value_after") or []:
            if not isinstance(block, dict):
                continue
            msg = block.get("message") or block
            for key in ("text", "message", "body", "content"):
                if isinstance(msg, dict) and msg.get(key):
                    return str(msg[key])
            if block.get("text"):
                return str(block["text"])
        return ""

    in_with_text = sum(1 for e in incoming if extract_text(e))
    out_with_text = sum(1 for e in outgoing if extract_text(e))
    print("=" * 62)
    print("4) есть ли текст в событии")
    print(f"   incoming с текстом: {in_with_text}/{len(incoming)}")
    print(f"   outgoing с текстом: {out_with_text}/{len(outgoing)}")
    if incoming:
        print("   сырые ключи первого incoming:", list(incoming[0].keys()))
    if outgoing:
        print("   сырые ключи первого outgoing:", list(outgoing[0].keys()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
