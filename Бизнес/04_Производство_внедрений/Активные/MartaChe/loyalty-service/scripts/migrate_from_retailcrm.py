#!/usr/bin/env python3
"""
Импорт участников ПЛ из CSV (выгрузка из RetailCRM или свой файл).

Колонки (заголовок в первой строке):
  phone          — телефон (любой формат)
  tier           — 0 / 1 / 2 или Знакомство / Дружба / Любовь (можно пусто)
  bonuses        — целое, баллы на счёте (рубли 1:1)
  enrolled_at    — опционально, YYYY-MM-DD
  annual_sum_rub — опционально, накопленная сумма за год для уровня (если знаете)
  birth_month, birth_day — опционально (1–12 и 1–31), для ежегодного бонуса ДР

Пример:
  phone,tier,bonuses,enrolled_at
  +79161234567,Дружба,1500,2024-01-15

Запуск из каталога loyalty-service:
  python scripts/migrate_from_retailcrm.py data/import.csv
  python scripts/migrate_from_retailcrm.py data/import.csv --dry-run

Нужны в .env: MS_TOKEN, DATABASE_URL (и при необходимости API_BASE).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# корень проекта на PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import BonusBatch, LoyaltyMember
from app.moysklad_client import MoySkladClient
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru
from app.webhook_handlers import _safe_sync_counterparty_attributes


def _norm_phone(s: str) -> str:
    d = re.sub(r"\D+", "", s or "")
    if d.startswith("8") and len(d) >= 11:
        d = "7" + d[1:]
    if len(d) == 10:
        d = "7" + d
    return d


def _parse_tier(val: str) -> Optional[int]:
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip().lower()
    if s in ("0", "знакомство"):
        return 0
    if s in ("1", "дружба"):
        return 1
    if s in ("2", "любовь"):
        return 2
    try:
        i = int(s)
        if i in (0, 1, 2):
            return i
    except ValueError:
        pass
    raise ValueError(f"Неизвестный уровень: {val!r}")


def _parse_date(val: Optional[str]) -> Optional[date]:
    if not val or not str(val).strip():
        return None
    return date.fromisoformat(str(val).strip()[:10])


def _pick_counterparty(client: MoySkladClient, phone: str) -> tuple[str, str] | None:
    """Возвращает (id, name) или None."""
    digits = _norm_phone(phone)
    if len(digits) < 10:
        return None
    # поиск по последним 10 цифрам и по полному 11
    for q in (digits[-10:], digits):
        rows = client.search_counterparties(q, limit=20)
        if rows:
            r0 = rows[0]
            return str(r0.get("id")), str(r0.get("name") or "")
    return None


def _post_bonus(client: MoySkladClient, agent_id: str, amount: int, note: str) -> str:
    if amount <= 0:
        return ""
    settings = get_settings()
    meta = client.fetch_bonus_program_meta()
    body = {
        "bonusProgram": {"meta": meta},
        "agent": {
            "meta": {
                "href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}",
                "type": "counterparty",
                "mediaType": "application/json",
            }
        },
        "transactionType": "EARNING",
        "bonusValue": amount,
        "externalCode": settings.loyalty_external_code,
        "name": note[:200],
    }
    bt = client.post("/entity/bonustransaction", body, disable_webhook=True)
    return str(bt.get("id") or "")


def run_csv(path: Path, dry_run: bool, db: Session | None, client: MoySkladClient) -> list[dict]:
    settings = get_settings()
    report: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            phone = (row.get("phone") or row.get("телефон") or "").strip()
            tier_raw = row.get("tier") or row.get("уровень") or ""
            bonuses_raw = row.get("bonuses") or row.get("бонусы") or "0"
            enrolled_raw = row.get("enrolled_at") or row.get("дата_вступления") or ""
            annual_raw = row.get("annual_sum_rub") or row.get("сумма_год") or ""
            bm_raw = (row.get("birth_month") or row.get("др_мес") or "").strip()
            bd_raw = (row.get("birth_day") or row.get("др_день") or "").strip()

            rec: dict = {"row": i, "phone": phone, "status": "", "agent_id": "", "note": ""}
            try:
                tier_parsed = _parse_tier(tier_raw) if str(tier_raw).strip() else None
                bonuses = int(float(str(bonuses_raw).replace(",", ".")))
                enrolled = _parse_date(enrolled_raw) if enrolled_raw else None
                annual = int(float(str(annual_raw).replace(",", "."))) if str(annual_raw).strip() else None
                birth_m = int(bm_raw) if bm_raw else None
                birth_d = int(bd_raw) if bd_raw else None
                if birth_m is not None and not (1 <= birth_m <= 12):
                    raise ValueError("birth_month: ожидается 1–12")
                if birth_d is not None and not (1 <= birth_d <= 31):
                    raise ValueError("birth_day: ожидается 1–31")
            except Exception as e:
                rec["status"] = "error"
                rec["note"] = str(e)
                report.append(rec)
                continue

            cp = _pick_counterparty(client, phone)
            if not cp:
                rec["status"] = "not_found"
                rec["note"] = "контрагент не найден"
                report.append(rec)
                continue
            agent_id, agent_name = cp
            rec["agent_id"] = agent_id
            rec["status"] = "ok"

            if dry_run:
                rec["note"] = f"dry-run: bonuses={bonuses}, tier={tier_parsed}, name={agent_name}"
                report.append(rec)
                continue

            assert db is not None
            # Локальная карточка участника
            m = db.get(LoyaltyMember, agent_id)
            if not m:
                m = LoyaltyMember(agent_id=agent_id, tier=0, annual_sum_rub=0, tier_locked=False)
                db.add(m)
            if enrolled:
                m.enrolled_at = enrolled
            if annual is not None:
                m.annual_sum_rub = int(annual)
            elif tier_parsed is not None:
                m.annual_sum_rub = (0, 20_000, 60_000)[tier_parsed]
            if tier_parsed is not None:
                m.tier = int(tier_parsed)
            else:
                m.tier = tier_index_from_annual_sum(int(m.annual_sum_rub))
            if birth_m is not None:
                m.birth_month = birth_m
            if birth_d is not None:
                m.birth_day = birth_d

            note = f"Импорт ПЛ, строка {i}"
            bt_id = _post_bonus(client, agent_id, bonuses, note)
            if bonuses > 0 and bt_id:
                db.add(
                    BonusBatch(
                        agent_id=agent_id,
                        original_amount=bonuses,
                        remaining=bonuses,
                        expires_at=date.today() + timedelta(days=365),
                        source_order_id=None,
                        bonustransaction_id=bt_id or None,
                        batch_type="migration",
                    )
                )

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
            rec["note"] = f"imported bonuses={bonuses}, bt={bt_id}, tier={m.tier}"
            report.append(rec)

    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Импорт ПЛ из CSV (без списка «кто в RetailCRM» — просто файл).")
    p.add_argument("csv_path", type=Path, help="Путь к CSV")
    p.add_argument("--dry-run", action="store_true", help="Только отчёт, без API записи и БД")
    args = p.parse_args()

    if not args.csv_path.is_file():
        raise SystemExit(f"Файл не найден: {args.csv_path}")

    get_settings()
    client = MoySkladClient()

    if args.dry_run:
        report = run_csv(args.csv_path, True, None, client)  # type: ignore[arg-type]
    else:
        init_db()
        SessionLocal = get_session_factory()
        db = SessionLocal()
        try:
            report = run_csv(args.csv_path, False, db, client)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    out = Path(os.environ.get("MIGRATE_REPORT", "migrate_report.csv"))
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row", "phone", "status", "agent_id", "note"])
        w.writeheader()
        w.writerows(report)
    print(f"Готово. Строк: {len(report)}. Отчёт: {out.resolve()}")


if __name__ == "__main__":
    main()
