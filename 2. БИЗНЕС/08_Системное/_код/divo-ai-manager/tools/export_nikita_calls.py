"""Реестр звонков Никиты за год: события amo + ссылки Mango из примечаний.

Запуск:
  python3 tools/export_nikita_calls.py --since 2026-01-01
Пишет _ЭТАЛОН/звонки/_индекс.json (телефоны маскируются).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from amo_client import NIKITA_USER_ID, items, request

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_ЭТАЛОН" / "звонки"
INDEX = OUT_DIR / "_индекс.json"

PHONE_RE = re.compile(r"(?:\+7|8|7)?[\s\-()]*(?:\d[\s\-()]*){10}")


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 10:
        return ""
    return f"+7***{digits[-4:]}"


def ts(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_events(event_type: str, since: int) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 80:
        code, data = request(
            "/api/v4/events",
            {
                "filter[type]": event_type,
                "filter[created_at][from]": str(since),
                "limit": "100",
                "page": str(page),
            },
        )
        if code >= 400:
            print(f"  {event_type} page {page} -> {code} {str(data)[:160]}")
            break
        chunk = items(data, "events")
        if not chunk:
            break
        rows.extend(chunk)
        print(f"  {event_type} page {page}: +{len(chunk)} (всего {len(rows)})")
        if not (data.get("_links") or {}).get("next"):
            break
        page += 1
        time.sleep(0.15)
    return rows


def notes_for_lead(lead_id: int) -> list[dict]:
    code, data = request(f"/api/v4/leads/{lead_id}/notes", {"limit": "250"})
    if code >= 400:
        return []
    return items(data, "notes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-01-01")
    parser.add_argument("--probe", type=int, default=0, help="сколько сделок снять notes")
    args = parser.parse_args()
    since = ts(args.since)

    print("1) события звонков")
    incoming = fetch_events("incoming_call", since)
    outgoing = fetch_events("outgoing_call", since)
    all_events = incoming + outgoing
    by_author = Counter(int(e.get("created_by") or 0) for e in all_events)
    print("   авторы:", dict(by_author.most_common(8)))

    nikita = [e for e in all_events if int(e.get("created_by") or 0) == NIKITA_USER_ID]
    print(f"   звонков Никиты в событиях: {len(nikita)} из {len(all_events)}")

    lead_ids = sorted({int(e["entity_id"]) for e in nikita if e.get("entity_type") == "lead"})
    print(f"   сделок Никиты со звонками: {len(lead_ids)}")

    # Если created_by на звонках пустой — запасной путь: сделки Никиты
    if not nikita:
        print("2) события без автора Никиты — берём сделки responsible=Никита")
        lead_ids = []
        for lead in __import__("amo_client", fromlist=["paged"]).paged(
            "/api/v4/leads",
            {
                "filter[responsible_user_id]": str(NIKITA_USER_ID),
                "filter[created_at][from]": str(since),
                "limit": "50",
            },
            "leads",
            max_pages=80,
        ):
            lead_ids.append(int(lead["id"]))
        print(f"   сделок: {len(lead_ids)}")

    sample_n = args.probe or min(len(lead_ids), 40)
    print(f"3) примечания на {sample_n} сделках")
    calls: list[dict] = []
    seen: set[str] = set()
    with_link = 0
    durations = 0
    for i, lead_id in enumerate(lead_ids[:sample_n]):
        for note in notes_for_lead(lead_id):
            ntype = note.get("note_type")
            if ntype not in ("call_in", "call_out"):
                continue
            created = int(note.get("created_at") or 0)
            if created < since:
                continue
            params = note.get("params") or {}
            uniq = str(params.get("uniq") or note.get("id") or "")
            if uniq and uniq in seen:
                continue
            if uniq:
                seen.add(uniq)
            duration = int(params.get("duration") or 0)
            link = (params.get("link") or "").strip()
            if link:
                with_link += 1
            durations += duration
            calls.append(
                {
                    "lead_id": lead_id,
                    "note_id": note.get("id"),
                    "type": ntype,
                    "created_at": created,
                    "duration": duration,
                    "link": link,
                    "phone": mask_phone(str(params.get("phone") or "")),
                    "created_by": note.get("created_by"),
                }
            )
        if (i + 1) % 10 == 0:
            print(f"   ... {i + 1}/{sample_n}, звонков {len(calls)}, с записью {with_link}")
        time.sleep(0.12)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "since": args.since,
        "nikita_user_id": NIKITA_USER_ID,
        "events_nikita": len(nikita),
        "leads_scanned": sample_n,
        "leads_total_with_calls": len(lead_ids),
        "calls": calls,
        "with_recording": with_link,
        "duration_sec": durations,
        "duration_min": round(durations / 60, 1),
        "nexara_est_rub": round((durations / 60) * 0.36, 1),
    }
    INDEX.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 50)
    print(f"звонков в выборке: {len(calls)}")
    print(f"с записью: {with_link}")
    print(f"минут: {payload['duration_min']}  оценка Nexara: {payload['nexara_est_rub']} ₽")
    print(f"индекс: {INDEX}")
    if calls:
        print("пример:", {k: calls[0][k] for k in ("lead_id", "type", "duration", "link", "phone")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
