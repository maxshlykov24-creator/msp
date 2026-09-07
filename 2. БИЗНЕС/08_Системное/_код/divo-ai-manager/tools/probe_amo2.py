"""Проба 2: где у DIVO живут чаты и как достать текст.

Проверяем:
  a) отдаёт ли `with=contacts` контакты у одиночной сделки
  b) что возвращает /api/v4/talks списком: origin, responsible, contact
  c) есть ли сообщения в talks/{id}/messages по этим беседам
  d) какие note_type встречаются на сделках, пришедших из чатов (не звонки)
"""
from __future__ import annotations

import sys
from collections import Counter

from amo_client import NIKITA_USER_ID, get, items, request


def section(title: str) -> None:
    print("=" * 62)
    print(title)


def a_single_lead() -> None:
    section("a) одиночная сделка с with=contacts")
    code, data = request("/api/v4/leads/45269007", {"with": "contacts,source_id"})
    print(f"   -> {code}")
    if code >= 400:
        print(f"   {data}")
        return
    emb = data.get("_embedded") or {}
    print(f"   ключи _embedded: {list(emb.keys())}")
    for contact in items(data, "contacts"):
        print(f"   контакт {contact.get('id')}")
    print(f"   source_id={data.get('source_id')} status_id={data.get('status_id')}")


def b_talks() -> list[dict]:
    section("b) /api/v4/talks списком")
    code, data = request("/api/v4/talks", {"limit": "50"})
    print(f"   -> {code}")
    if code >= 400:
        print(f"   {data}")
        return []
    talks = items(data, "talks")
    print(f"   бесед: {len(talks)}")
    if talks:
        print(f"   поля беседы: {list(talks[0].keys())}")
    origins: Counter = Counter()
    for talk in talks:
        origins[talk.get("origin", "?")] += 1
    print(f"   origin: {dict(origins)}")
    nikita = [t for t in talks if t.get("responsible_user_id") == NIKITA_USER_ID]
    print(f"   из них у Никиты: {len(nikita)}")
    for talk in talks[:8]:
        print(
            f"   talk {talk.get('id')} origin={talk.get('origin')} "
            f"resp={talk.get('responsible_user_id')} "
            f"entity={talk.get('entity_type')}:{talk.get('entity_id')} "
            f"contact={talk.get('contact_id')}"
        )
    return talks


def c_messages(talks: list[dict]) -> None:
    section("c) talks/{id}/messages")
    if not talks:
        print("   бесед нет")
        return
    verdict: Counter = Counter()
    for talk in talks[:10]:
        talk_id = talk.get("id")
        code, data = request(f"/api/v4/talks/{talk_id}/messages", {"limit": "50"})
        if code >= 400:
            print(f"   talk {talk_id} -> {code} {str(data)[:140]}")
            verdict[f"http_{code}"] += 1
            continue
        msgs = items(data, "messages")
        verdict["ok" if msgs else "empty"] += 1
        print(f"   talk {talk_id} -> {code}, сообщений {len(msgs)}")
        if msgs:
            print(f"      поля: {list(msgs[0].keys())}")
            for msg in msgs[:3]:
                print(f"      [{msg.get('author', {}).get('type', '?')}] {str(msg.get('text'))[:80]!r}")
    print(f"   итог: {dict(verdict)}")


def d_chat_leads() -> None:
    section("d) сделки Никиты из чатов, а не из звонков")
    # берём шире и ищем те, где есть note_type от мессенджеров
    seen: Counter = Counter()
    checked = 0
    code, data = request(
        "/api/v4/leads",
        {
            "filter[responsible_user_id]": str(NIKITA_USER_ID),
            "limit": "50",
            "order[updated_at]": "desc",
        },
    )
    if code >= 400:
        print(f"   -> {code} {data}")
        return
    leads = items(data, "leads")
    print(f"   сделок к проверке: {len(leads)}")
    chat_leads = []
    for lead in leads:
        lead_id = lead.get("id")
        ncode, ndata = request(f"/api/v4/leads/{lead_id}/notes", {"limit": "100"})
        if ncode >= 400:
            continue
        checked += 1
        types = {n.get("note_type") for n in items(ndata, "notes")}
        for t in types:
            seen[t] += 1
        if types & {"amomessage", "amomessage_in", "amomessage_out", "chat_message"}:
            chat_leads.append(lead_id)
    print(f"   проверено сделок: {checked}")
    print(f"   все note_type по выборке: {dict(seen)}")
    print(f"   сделки с признаком чата: {chat_leads or 'нет'}")


def e_sources() -> None:
    section("e) источники сделок (откуда приходят)")
    try:
        data = get("/api/v4/sources", {"limit": 50})
    except Exception as exc:  # noqa: BLE001
        print(f"   /api/v4/sources: {exc}")
        return
    for src in items(data, "sources"):
        print(f"   source {src.get('id')} {src.get('name')} external={src.get('external_id')}")


def main() -> int:
    a_single_lead()
    talks = b_talks()
    c_messages(talks)
    d_chat_leads()
    e_sources()
    return 0


if __name__ == "__main__":
    sys.exit(main())
