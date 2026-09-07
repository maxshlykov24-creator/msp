"""Проба 3: настоящий talk_id + кому достаются авито-чаты.

Проба 2 бралa talk['id'], а поле называется talk_id — отсюда были 403 на /talks/None.
Здесь: реальный talk_id, привязка беседы к сделке и её ответственному,
и попытка достать текст сообщений всеми известными путями.
"""
from __future__ import annotations

import sys
from collections import Counter

from amo_client import NIKITA_USER_ID, items, request


def section(title: str) -> None:
    print("=" * 62)
    print(title)


def collect_talks(pages: int = 6) -> list[dict]:
    section("1) собираем беседы")
    rows: list[dict] = []
    for page in range(1, pages + 1):
        code, data = request("/api/v4/talks", {"limit": "50", "page": str(page)})
        if code >= 400 or not data:
            break
        chunk = items(data, "talks")
        if not chunk:
            break
        rows.extend(chunk)
        if not (data.get("_links") or {}).get("next"):
            break
    print(f"   бесед всего: {len(rows)}")
    origins: Counter = Counter(t.get("origin", "?") for t in rows)
    print(f"   origin: {dict(origins)}")
    return rows


def messages_by_talk(talks: list[dict]) -> None:
    section("2) talks/{talk_id}/messages с настоящим id")
    verdict: Counter = Counter()
    for talk in talks[:8]:
        talk_id = talk.get("talk_id")
        code, data = request(f"/api/v4/talks/{talk_id}/messages", {"limit": "50"})
        if code >= 400:
            print(f"   talk {talk_id} -> {code} {str(data)[:130]}")
            verdict[f"http_{code}"] += 1
            continue
        msgs = items(data, "messages")
        verdict["ok" if msgs else "empty"] += 1
        print(f"   talk {talk_id} origin={talk.get('origin')} -> {code}, сообщений {len(msgs)}")
        if msgs:
            print(f"      поля: {list(msgs[0].keys())}")
            for msg in msgs[:4]:
                author = (msg.get("author") or {}).get("type", "?")
                print(f"      [{author}] {str(msg.get('text'))[:90]!r}")
    print(f"   итог: {dict(verdict)}")


def who_owns_chats(talks: list[dict]) -> None:
    section("3) кому принадлежат авито-чаты (через сделку)")
    users = {}
    code, data = request("/api/v4/users", {"limit": "50"})
    if code < 400:
        users = {u["id"]: u.get("name", "") for u in items(data, "users")}

    owners: Counter = Counter()
    nikita_leads: list[int] = []
    avito = [t for t in talks if t.get("origin") == "avito"]
    print(f"   авито-бесед: {len(avito)}, проверяем до 60")
    for talk in avito[:60]:
        if talk.get("entity_type") != "lead":
            continue
        lead_id = talk.get("entity_id")
        code, lead = request(f"/api/v4/leads/{lead_id}")
        if code >= 400:
            continue
        resp = lead.get("responsible_user_id")
        owners[f"{resp} {users.get(resp, '')}"] += 1
        if resp == NIKITA_USER_ID:
            nikita_leads.append(lead_id)
    print("   ответственные по авито-чатам:")
    for key, count in owners.most_common():
        print(f"      {key}: {count}")
    print(f"   авито-сделки Никиты: {nikita_leads or 'нет в этой выборке'}")

    if nikita_leads:
        section("4) notes на авито-сделке Никиты")
        for lead_id in nikita_leads[:3]:
            code, data = request(f"/api/v4/leads/{lead_id}/notes", {"limit": "100"})
            if code >= 400:
                continue
            notes = items(data, "notes")
            types = Counter(n.get("note_type") for n in notes)
            print(f"   сделка {lead_id}: notes {len(notes)} {dict(types)}")
            for note in notes:
                params = note.get("params") or {}
                text = params.get("text") or params.get("comment")
                if text:
                    print(f"      [{note.get('note_type')}] {str(text)[:100]!r}")


def main() -> int:
    talks = collect_talks()
    if not talks:
        print("бесед нет")
        return 1
    messages_by_talk(talks)
    who_owns_chats(talks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
