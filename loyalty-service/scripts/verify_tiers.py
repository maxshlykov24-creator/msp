#!/usr/bin/env python3
"""
Сверка уровня участников ПЛ с фактом заказов «Доставлен» в МойСклад за последние 365 дней.

Источник списка клиентов:
  --from-db   (по умолчанию) — все записи в таблице loyalty_members
  --csv path  — колонка agent_id или phone

Отчёт: verify_report.csv
Применение: --apply (обновляет loyalty_members и синхронизирует доп. поля контрагента, если заданы ATTR_*).

Запуск из каталога loyalty-service:
  python scripts/verify_tiers.py
  python scripts/verify_tiers.py --csv agents.csv
  python scripts/verify_tiers.py --apply
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import LoyaltyMember
from app.moysklad_client import MoySkladClient
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru
from app.webhook_handlers import _safe_sync_counterparty_attributes

from migrate_from_retailcrm import _pick_counterparty as pick_cp


def _sum_delivered_year_rub(client: MoySkladClient, agent_id: str, since: date) -> int:
    settings = get_settings()
    agent_href = f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}"
    flt = f"agent={agent_href};moment>={since.isoformat()} 00:00:00"
    total_kop = 0
    offset = 0
    while True:
        data = client.get(
            "/entity/customerorder",
            params={"filter": flt, "limit": 100, "offset": offset, "expand": "state"},
        )
        rows = data.get("rows") or []
        for row in rows:
            st = row.get("state") or {}
            if str(st.get("name") or "") != settings.status_delivered:
                continue
            total_kop += int(float(row.get("sum") or 0))
        offset += len(rows)
        if len(rows) < 100:
            break
    return total_kop // 100


def _load_agent_ids(args: argparse.Namespace, db: Session, client: MoySkladClient) -> list[tuple[str, str]]:
    """Список (agent_id, label) для отчёта."""
    out: list[tuple[str, str]] = []
    if args.csv:
        with Path(args.csv).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                aid = (row.get("agent_id") or row.get("id") or "").strip()
                phone = (row.get("phone") or row.get("телефон") or "").strip()
                if aid:
                    out.append((aid, f"csv:{aid}"))
                elif phone:
                    cp = pick_cp(client, phone)
                    if cp:
                        out.append((cp[0], f"csv:{phone}"))
        return out

    for m in db.scalars(select(LoyaltyMember)).all():
        out.append((m.agent_id, "db"))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=str, default=None, help="CSV с agent_id или phone")
    p.add_argument("--apply", action="store_true", help="Записать пересчитанный уровень и сумму")
    p.add_argument("--days", type=int, default=365, help="Глубина периода в днях")
    args = p.parse_args()

    settings = get_settings()
    client = MoySkladClient()
    since = date.today() - timedelta(days=max(1, args.days))

    init_db()
    SessionLocal = get_session_factory()
    db = SessionLocal()
    report: list[dict] = []
    try:
        targets = _load_agent_ids(args, db, client)
        if not targets:
            print("Нет участников: добавьте строки в loyalty_members или передайте --csv")
            return

        for agent_id, src in targets:
            try:
                total = _sum_delivered_year_rub(client, agent_id, since)
                want = tier_index_from_annual_sum(total)
                m = db.get(LoyaltyMember, agent_id)
                cur = int(m.tier) if m else None
                locked = bool(m.tier_locked) if m else False
                action = "ok"
                if cur is None:
                    action = "no_local_member"
                elif not locked and want != cur:
                    action = f"tier {cur}->{want}"
                rec = {
                    "agent_id": agent_id,
                    "source": src,
                    "sum_delivered_rub": total,
                    "tier_current": cur if cur is not None else "",
                    "tier_expected": want,
                    "locked": locked,
                    "action": action,
                }
                report.append(rec)

                if args.apply and m and not m.tier_locked:
                    m.annual_sum_rub = total
                    m.tier = want
                    m.last_tier_review_at = date.today()
                    _safe_sync_counterparty_attributes(
                        client,
                        settings,
                        agent_id=agent_id,
                        attrs={
                            "tier_name": tier_name_ru(int(m.tier)),
                            "annual_sum_rub": int(m.annual_sum_rub),
                            "enrolled_at": m.enrolled_at.isoformat() if m.enrolled_at else None,
                            "last_tier_review": m.last_tier_review_at.isoformat() if m.last_tier_review_at else None,
                            "tier_locked": bool(m.tier_locked),
                        },
                    )
            except Exception as e:
                report.append({"agent_id": agent_id, "source": src, "action": "error", "note": str(e)})

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    out = Path("verify_report.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        if report:
            w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
    print(f"Строк отчёта: {len(report)}. Файл: {out.resolve()}")


if __name__ == "__main__":
    main()
