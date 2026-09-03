"""Проба 4: что полностью лежит в примечаниях звонков Никиты.

talks/messages закрыт (403 Invalid scope), авито-чаты без ответственного.
Значит эталон общения Никиты может лежать в аналитике его звонков.
Смотрим полный текст common-примечаний и структуру call_in.
"""
from __future__ import annotations

import json
import sys

from amo_client import NIKITA_USER_ID, items, request


def main() -> int:
    code, data = request(
        "/api/v4/leads",
        {
            "filter[responsible_user_id]": str(NIKITA_USER_ID),
            "limit": "15",
            "order[updated_at]": "desc",
        },
    )
    if code >= 400:
        print(f"leads -> {code} {data}")
        return 1

    shown_common = 0
    shown_call = 0
    for lead in items(data, "leads"):
        lead_id = lead.get("id")
        ncode, ndata = request(f"/api/v4/leads/{lead_id}/notes", {"limit": "100"})
        if ncode >= 400:
            continue
        for note in items(ndata, "notes"):
            ntype = note.get("note_type")
            params = note.get("params") or {}
            if ntype == "common" and shown_common < 4:
                text = params.get("text") or ""
                if len(text) > 200:
                    print("=" * 62)
                    print(f"COMMON на сделке {lead_id}, длина {len(text)}")
                    print(text[:2500])
                    shown_common += 1
            if ntype == "call_in" and shown_call < 2:
                print("=" * 62)
                print(f"CALL_IN на сделке {lead_id}: params =")
                print(json.dumps(params, ensure_ascii=False, indent=2)[:1200])
                shown_call += 1
        if shown_common >= 4 and shown_call >= 2:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
