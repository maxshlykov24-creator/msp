"""Проба: каким путём amo отдаёт текст переписки менеджера Никиты.

Лестница вариантов из плана:
  1. GET /api/v4/talks/{talk_id}/messages
  2. GET /api/v4/leads/{lead_id}/notes
  3. (если оба пустые) остаётся Wazzup CSV-дамп

Запуск: python3 tools/probe_amo.py [--leads 10]
Ничего не пишет на диск, только печатает отчёт.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from amo_client import NIKITA_USER_ID, AmoError, get, items, request


def probe_account() -> None:
    print("=" * 60)
    print("1. Аккаунт и права")
    code, data = request("/api/v4/account")
    print(f"   GET /api/v4/account -> {code} {data.get('name', '')} id={data.get('id', '')}")
    code, data = request("/api/v4/users", {"limit": 50})
    if code < 400:
        for user in items(data, "users"):
            mark = "  <-- НИКИТА" if user.get("id") == NIKITA_USER_ID else ""
            print(f"   user {user.get('id'):<12} {user.get('name', '')}{mark}")
    else:
        print(f"   GET /api/v4/users -> {code} {data}")


def probe_pipelines() -> dict[int, str]:
    print("=" * 60)
    print("2. Воронка: ищем этапы визита")
    names: dict[int, str] = {}
    try:
        data = get("/api/v4/leads/pipelines")
    except AmoError as exc:
        print(f"   не смог: {exc}")
        return names
    for pipe in items(data, "pipelines"):
        print(f"   воронка {pipe.get('id')} {pipe.get('name')}")
        for st in items(pipe.get("_embedded") or {}, "statuses"):
            names[st["id"]] = st.get("name", "")
            print(f"      этап {st.get('id'):<12} {st.get('name')}")
    return names


def probe_leads(limit: int) -> list[dict]:
    print("=" * 60)
    print(f"3. Сделки Никиты (responsible_user_id={NIKITA_USER_ID})")
    params = {
        "filter[responsible_user_id]": str(NIKITA_USER_ID),
        "with": "contacts",
        "limit": str(limit),
        "order[updated_at]": "desc",
    }
    code, data = request("/api/v4/leads", params)
    if code >= 400:
        print(f"   GET /api/v4/leads -> {code} {data}")
        return []
    if code == 204:
        print("   204: сделок по фильтру нет")
        return []
    leads = items(data, "leads")
    print(f"   получено {len(leads)} сделок")
    return leads


def probe_talks(leads: list[dict], statuses: dict[int, str]) -> Counter:
    print("=" * 60)
    print("4. Путь 1: talks -> messages")
    verdict: Counter = Counter()
    code, data = request("/api/v4/talks", {"limit": 5})
    print(f"   GET /api/v4/talks -> {code}")
    if code >= 400:
        print(f"      {data}")
        verdict["talks_list_denied"] += 1
        return verdict

    for lead in leads[:10]:
        lead_id = lead.get("id")
        contacts = items(lead.get("_embedded") or {}, "contacts")
        stage = statuses.get(lead.get("status_id"), lead.get("status_id"))
        if not contacts:
            print(f"   сделка {lead_id} [{stage}]: контактов нет")
            verdict["no_contact"] += 1
            continue
        contact_id = contacts[0].get("id")
        code, data = request(
            "/api/v4/talks", {"filter[contact_id]": str(contact_id), "limit": "20"}
        )
        if code >= 400:
            print(f"   сделка {lead_id}: talks по контакту -> {code}")
            verdict["talks_filter_denied"] += 1
            continue
        talks = items(data, "talks")
        if not talks:
            print(f"   сделка {lead_id} [{stage}]: беседа не найдена")
            verdict["no_talk"] += 1
            continue
        talk_id = talks[0].get("id")
        code, data = request(f"/api/v4/talks/{talk_id}/messages", {"limit": "50"})
        if code >= 400:
            print(f"   сделка {lead_id}: messages -> {code} {str(data)[:120]}")
            verdict[f"messages_{code}"] += 1
            continue
        msgs = items(data, "messages")
        print(f"   сделка {lead_id} [{stage}]: talk {talk_id}, сообщений {len(msgs)}")
        if msgs:
            verdict["messages_ok"] += 1
            sample = msgs[0]
            print(f"      пример: {str(sample.get('text'))[:90]!r}")
        else:
            verdict["messages_empty"] += 1
    return verdict


def probe_notes(leads: list[dict], statuses: dict[int, str]) -> Counter:
    print("=" * 60)
    print("5. Путь 2: leads/{id}/notes")
    verdict: Counter = Counter()
    types: Counter = Counter()
    for lead in leads[:10]:
        lead_id = lead.get("id")
        stage = statuses.get(lead.get("status_id"), lead.get("status_id"))
        code, data = request(f"/api/v4/leads/{lead_id}/notes", {"limit": "100"})
        if code >= 400:
            print(f"   сделка {lead_id}: notes -> {code} {str(data)[:120]}")
            verdict[f"notes_{code}"] += 1
            continue
        notes = items(data, "notes")
        with_text = []
        for note in notes:
            note_type = note.get("note_type", "?")
            types[note_type] += 1
            params = note.get("params") or {}
            text = params.get("text") or params.get("comment")
            if text and note_type not in ("call_in", "call_out", "service_message"):
                with_text.append((note_type, text))
        print(f"   сделка {lead_id} [{stage}]: notes {len(notes)}, с текстом {len(with_text)}")
        if with_text:
            verdict["notes_text_ok"] += 1
            ntype, text = with_text[0]
            print(f"      пример [{ntype}]: {str(text)[:90]!r}")
        else:
            verdict["notes_no_text"] += 1
    print(f"   встреченные note_type: {dict(types)}")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leads", type=int, default=10)
    args = parser.parse_args()

    try:
        probe_account()
        statuses = probe_pipelines()
        leads = probe_leads(args.leads)
    except AmoError as exc:
        print(f"ОСТАНОВ: {exc}")
        return 1

    if not leads:
        print("Сделок Никиты не получили - дальше идти не с чем.")
        return 1

    talks_verdict = probe_talks(leads, statuses)
    notes_verdict = probe_notes(leads, statuses)

    print("=" * 60)
    print("ИТОГ")
    print(f"   путь talks:  {dict(talks_verdict)}")
    print(f"   путь notes:  {dict(notes_verdict)}")
    if talks_verdict.get("messages_ok"):
        print("   ВЫБОР: путь 1 (talks/messages) живой, экспортёр строим на нём")
    elif notes_verdict.get("notes_text_ok"):
        print("   ВЫБОР: путь 2 (notes) живой, экспортёр строим на нём")
    else:
        print("   ВЫБОР: amo текст не отдаёт, остаётся Wazzup CSV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
