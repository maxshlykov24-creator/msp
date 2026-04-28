#!/usr/bin/env python3
"""
Заполняет birth_month / birth_day в loyalty_members из доп. поля контрагента в МойСклад.

  В .env: MS_TOKEN, ATTR_BIRTHDATE=<uuid из discover_metadata>
  python scripts/sync_birthdays_from_moysklad.py
"""
from __future__ import annotations

import re
import sys
from datetime import date
from typing import Optional
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import LoyaltyMember
from app.moysklad_client import MoySkladClient


def _attr_key(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


def _parse_birth(s: Optional[str]) -> Optional[tuple]:
    if not s or not str(s).strip():
        return None
    t = str(s).strip()[:10]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t)
    if not m:
        m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", t)
        if not m:
            return None
        d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mon, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(y, mon, d)
    except ValueError:
        return None
    return mon, d


def main() -> None:
    settings = get_settings()
    aid = (settings.attr_birthdate or "").strip()
    if not aid:
        print("Задайте ATTR_BIRTHDATE (UUID поля даты рождения в контрагенте)", file=sys.stderr)
        sys.exit(1)
    init_db()
    SessionLocal = get_session_factory()
    db: Session = SessionLocal()
    client = MoySkladClient()
    n = 0
    try:
        for m in db.scalars(select(LoyaltyMember)).all():
            body = client.get(f"/entity/counterparty/{m.agent_id}")
            val = None
            for a in body.get("attributes") or []:
                mh = str((a.get("meta") or {}).get("href") or "")
                if _attr_key(mh) == aid or aid in mh:
                    val = a.get("value")
                    break
            if val is None:
                continue
            if isinstance(val, str):
                p = _parse_birth(val)
            elif isinstance(val, dict) and "date" in val:
                p = _parse_birth(str(val.get("date")))
            else:
                p = _parse_birth(str(val))
            if p:
                m.birth_month, m.birth_day = p
                n += 1
        db.commit()
    finally:
        db.close()
    print(f"Обновлено участников: {n}")


if __name__ == "__main__":
    main()
