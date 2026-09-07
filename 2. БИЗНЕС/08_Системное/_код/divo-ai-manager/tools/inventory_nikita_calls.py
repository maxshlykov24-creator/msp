"""Полный реестр звонков Никиты за год через /api/v4/leads/notes.

Пишет _ЭТАЛОН/звонки/_индекс.json. Телефоны маскирует.
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
PHONE_RE = re.compile(r"\D+")


def mask_phone(value: str) -> str:
    digits = PHONE_RE.sub("", value or "")
    if len(digits) < 10:
        return ""
    return f"+7***{digits[-4:]}"


def ts(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def fetch_call_notes(since: int) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 80:
        code, data = request(
            "/api/v4/leads/notes",
            {
                "filter[note_type][0]": "call_in",
                "filter[note_type][1]": "call_out",
                "filter[updated_at][from]": str(since),
                "limit": "250",
                "page": str(page),
            },
        )
        if code >= 400:
            print(f"  notes page {page} -> {code} {str(data)[:200]}")
            break
        chunk = items(data, "notes")
        if not chunk:
            break
        rows.extend(chunk)
        print(f"  page {page}: +{len(chunk)} (всего {len(rows)})")
        if not (data.get("_links") or {}).get("next"):
            break
        page += 1
        time.sleep(0.12)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-01-01")
    args = parser.parse_args()
    since = ts(args.since)

    print("примечания call_in/call_out с", args.since)
    notes = fetch_call_notes(since)
    by_author = Counter(int(n.get("created_by") or 0) for n in notes)
    print("авторы:", dict(by_author.most_common(8)))

    calls: list[dict] = []
    seen: set[str] = set()
    for note in notes:
        if int(note.get("created_by") or 0) != NIKITA_USER_ID:
            continue
        created = int(note.get("created_at") or 0)
        if created < since:
            continue
        ntype = note.get("note_type")
        if ntype not in ("call_in", "call_out"):
            continue
        params = note.get("params") or {}
        uniq = str(params.get("uniq") or note.get("id") or "")
        if uniq in seen:
            continue
        seen.add(uniq)
        duration = int(params.get("duration") or 0)
        link = (params.get("link") or "").strip()
        calls.append(
            {
                "lead_id": note.get("entity_id"),
                "note_id": note.get("id"),
                "type": ntype,
                "created_at": created,
                "duration": duration,
                "link": link,
                "phone": mask_phone(str(params.get("phone") or "")),
                "created_by": note.get("created_by"),
                "uniq": uniq,
            }
        )

    with_rec = [c for c in calls if c["link"] and c["duration"] > 0]
    dur = sum(c["duration"] for c in with_rec)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "since": args.since,
        "nikita_user_id": NIKITA_USER_ID,
        "notes_total": len(notes),
        "nikita_calls": len(calls),
        "with_recording": len(with_rec),
        "zero_or_no_link": len(calls) - len(with_rec),
        "duration_sec": dur,
        "duration_min": round(dur / 60, 1),
        "nexara_est_rub": round((dur / 60) * 0.36, 1),
        "calls": with_rec,
        "all_nikita_calls": calls,
    }
    INDEX.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 50)
    print(f"звонков Никиты: {len(calls)}")
    print(f"с записью (duration>0 и link): {len(with_rec)}")
    print(f"минут: {payload['duration_min']}  оценка Nexara: {payload['nexara_est_rub']} ₽")
    print(f"индекс: {INDEX}")
    if with_rec:
        c = with_rec[0]
        print("пример:", {k: c[k] for k in ("lead_id", "note_id", "duration", "phone")})
        print("link:", c["link"][:90])
    return 0


if __name__ == "__main__":
    sys.exit(main())
