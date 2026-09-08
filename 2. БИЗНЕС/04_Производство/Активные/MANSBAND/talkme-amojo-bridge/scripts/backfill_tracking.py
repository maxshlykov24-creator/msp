"""Бэкфилл рекламных меток в сделки amoCRM по истории бесед Talk-me.

Идёт по conversation_map, достаёт метки из сохранённого payload, ищет сделку клиента
по телефону и заполняет пустые поля utm_*, roistat, yclid. Занятые поля не трогает.

Запуск на сервере (по умолчанию — только отчёт, без записи):

    docker compose exec -T api python -m scripts.backfill_tracking
    docker compose exec -T api python -m scripts.backfill_tracking --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.main import apply_tracking_to_lead
from app.models import ConversationMap
from app.oauth_amocrm import api_base
from app.talkme_parse import normalize_phone, parse_talkme_tracking, tracking_is_empty
from app.tokens import get_valid_access_token


def visit_dt(payload: dict[str, Any]) -> Optional[datetime]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    client = data.get("client") if isinstance(data.get("client"), dict) else {}
    last = client.get("lastVisit") if isinstance(client.get("lastVisit"), dict) else {}
    raw = last.get("dateTime")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def find_leads_by_phone(phone: str, token: str, settings) -> list[dict[str, Any]]:
    url = f"{api_base(settings)}/api/v4/leads"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params={"query": phone, "with": "contacts", "limit": 10}, headers=headers)
        if r.status_code in (204, 404):
            return []
        r.raise_for_status()
        return ((r.json() or {}).get("_embedded") or {}).get("leads") or []


def pick_lead(leads: list[dict[str, Any]], visited: Optional[datetime]) -> Optional[dict[str, Any]]:
    """Сделка, ближайшая по времени к визиту. Без даты визита — самая свежая."""
    if not leads:
        return None
    if visited is None:
        return max(leads, key=lambda x: x.get("created_at") or 0)
    target = visited.timestamp()
    return min(leads, key=lambda x: abs((x.get("created_at") or 0) - target))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="писать в amoCRM (иначе только отчёт)")
    ap.add_argument("--limit", type=int, default=0, help="обработать не больше N бесед")
    ap.add_argument("--report", default="", help="путь для JSON-отчёта")
    args = ap.parse_args()

    init_db()
    settings = get_settings()
    Sess = get_session_factory()
    db = Sess()
    stats = {
        "бесед": 0,
        "без_меток": 0,
        "без_телефона": 0,
        "сделка_не_найдена": 0,
        "уже_заполнено": 0,
        "обновлено": 0,
        "ошибок": 0,
    }
    rows: list[dict[str, Any]] = []
    try:
        token = get_valid_access_token(db)
        conversations = db.execute(select(ConversationMap).order_by(ConversationMap.id)).scalars().all()
        for cm in conversations:
            if args.limit and stats["бесед"] >= args.limit:
                break
            stats["бесед"] += 1
            payload = (cm.talkme_context or {}).get("last") or {}

            # Достраиваем поля, которых не было у старых записей.
            if tracking_is_empty(cm.tracking):
                parsed = parse_talkme_tracking(payload)
                if not tracking_is_empty(parsed):
                    cm.tracking = parsed
            if not cm.client_phone:
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                client = data.get("client") if isinstance(data.get("client"), dict) else {}
                cm.client_phone = normalize_phone(client.get("phone"))
            if args.apply:
                db.commit()

            if tracking_is_empty(cm.tracking):
                stats["без_меток"] += 1
                continue
            if cm.amo_lead_id:
                stats["уже_заполнено"] += 1
                continue
            if not cm.client_phone:
                stats["без_телефона"] += 1
                continue

            try:
                leads = find_leads_by_phone(cm.client_phone, token, settings)
                lead = pick_lead(leads, visit_dt(payload))
                if lead is None:
                    stats["сделка_не_найдена"] += 1
                    continue
                res = apply_tracking_to_lead(
                    db, cm, int(lead["id"]), token, dry_run=not args.apply
                )
                if not res.get("ok"):
                    stats["ошибок"] += 1
                elif res.get("written"):
                    stats["обновлено"] += 1
                    rows.append(
                        {
                            "conversation": cm.id,
                            "lead": lead["id"],
                            "phone": "***" + (cm.client_phone or "")[-4:],
                            "written": res["written"],
                            "skipped": res.get("skipped") or {},
                        }
                    )
                else:
                    stats["уже_заполнено"] += 1
            except Exception as e:  # noqa: BLE001
                stats["ошибок"] += 1
                print(f"беседа {cm.id}: {e}", file=sys.stderr)
    finally:
        db.close()

    mode = "ЗАПИСЬ В amoCRM" if args.apply else "ТОЛЬКО ОТЧЁТ (dry-run)"
    print(f"\n=== Бэкфилл меток — {mode} ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\nсделок к обновлению: {len(rows)}")
    for r in rows[:20]:
        print(f"  беседа {r['conversation']} → сделка {r['lead']}: {', '.join(r['written'])}")
    if len(rows) > 20:
        print(f"  ... ещё {len(rows) - 20}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"stats": stats, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"\nотчёт: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
