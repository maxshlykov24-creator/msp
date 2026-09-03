"""Проба: список call-notes за 2026 и скачивается ли запись Mango."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from amo_client import NIKITA_USER_ID, TOKEN, items, request
import urllib.request

SINCE = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())


def main() -> int:
    code, data = request(
        "/api/v4/leads/notes",
        {
            "filter[note_type][0]": "call_in",
            "filter[note_type][1]": "call_out",
            "filter[updated_at][from]": str(SINCE),
            "limit": "50",
            "page": "1",
        },
    )
    print("notes page1", code)
    notes = items(data, "notes")
    print("count", len(notes), "next", bool((data.get("_links") or {}).get("next")))
    nikita_with_link = 0
    sample = None
    for n in notes:
        p = n.get("params") or {}
        if int(n.get("created_by") or 0) != NIKITA_USER_ID:
            continue
        if int(n.get("created_at") or 0) < SINCE:
            continue
        link = (p.get("link") or "").strip()
        dur = int(p.get("duration") or 0)
        if link and dur > 0:
            nikita_with_link += 1
            if sample is None:
                sample = n
    print("nikita_with_link_on_page", nikita_with_link)
    if not sample:
        print("нет образца на первой странице, ключи первого note:")
        if notes:
            print(json.dumps({k: notes[0].get(k) for k in notes[0]}, ensure_ascii=False)[:800])
        return 0

    p = sample.get("params") or {}
    print("sample note_id", sample.get("id"), "lead", (sample.get("entity_id")), "dur", p.get("duration"))
    print("link", (p.get("link") or "")[:80], "...")

    link = p["link"]
    for label, headers in (
        ("bare", {"User-Agent": "divo-ai-manager/1.0"}),
        ("amo-bearer", {"User-Agent": "divo-ai-manager/1.0", "Authorization": f"Bearer {TOKEN}"}),
    ):
        req = urllib.request.Request(link, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read(64)
                ctype = resp.headers.get("Content-Type", "")
                clen = resp.headers.get("Content-Length", "")
                print(f"{label}: {resp.status} ctype={ctype} len={clen} magic={raw[:12]!r}")
        except Exception as exc:
            print(f"{label}: FAIL {type(exc).__name__} {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
