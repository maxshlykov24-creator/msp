#!/usr/bin/env python3
"""
Перенос ВСЕХ полей программы лояльности из RetailCRM в карточки МойСклад
по данным data/loyalty_matched.csv (см. pull_retailcrm_loyalty.py +
match_loyalty_to_moysklad.py).

Переносит на контрагента (agent_id = ms_agent_id из matched.csv):
  * tier               -> LoyaltyMember.tier (0/1/2), attrs.tier_name
  * bonus_balance      -> активные бонусы: баланс ВСЕГДА выставляется в целевое
                          значение (спишет текущий локальный активный остаток
                          и начислит заново target), поэтому повторный запуск
                          или предыдущий частичный перенос НЕ приводит к задвоению
  * orders_sum         -> annual_sum_rub = max(текущее, orders_sum из RetailCRM)
  * created_at         -> enrolled_at (дата регистрации в ПЛ), если ещё не задана
  * birth_day/month    -> LoyaltyMember.birth_month/birth_day (для ДР-бонуса), если ещё не заданы

По умолчанию — dry-run (только отчёт, без записи в БД/МойСклад).
Реальные изменения — только с --apply.

ВНИМАНИЕ: работает с продовой БД loyalty-service (DATABASE_URL из .env) и
реальным МойСклад (создаёт bonustransaction) — запускать на сервере/там, где
доступна БД сервиса.

Запуск (из каталога loyalty-service):
    python scripts/migrate_full_loyalty.py --dry-run          # предпросмотр
    python scripts/migrate_full_loyalty.py --apply            # перенос
    python scripts/migrate_full_loyalty.py --apply --skip-bonustransactions
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bonus_log import log_bonus
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import BonusBatch, BonusLogAction, LoyaltyMember
from app.moysklad_client import MoySkladClient
from app.spend_engine import compute_active_balance
from app.tier_manager import tier_name_ru
from app.webhook_handlers import _build_member_attrs_for_cp, _safe_sync_counterparty_attributes, _save_last_synced_cp_attrs

DATA_DIR = ROOT / "data"
IN_CSV = DATA_DIR / "loyalty_matched.csv"
IMPORT_BATCH_TYPE = "full_api_migration"
ANNUAL_SUM_BY_TIER = (0, 20_000, 60_000)


def parse_created_at(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def post_bonus_transaction(client: MoySkladClient, agent_id: str, amount: int, transaction_type: str, note: str) -> str:
    if amount <= 0:
        return ""
    settings = get_settings()
    body = {
        "bonusProgram": {"meta": client.fetch_bonus_program_meta()},
        "agent": {
            "meta": {
                "href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}",
                "type": "counterparty",
                "mediaType": "application/json",
            }
        },
        "transactionType": transaction_type,
        "bonusValue": int(amount),
        "externalCode": settings.loyalty_external_code,
        "name": note[:200],
    }
    if transaction_type == "EARNING":
        body["executionDate"] = f"{date.today().isoformat()} 00:00:00"
    result = client.post("/entity/bonustransaction", body, disable_webhook=True)
    return str(result.get("id") or "")


def apply_row(
    *,
    db: Session,
    client: MoySkladClient,
    agent_id: str,
    agent_name: str,
    tier: int,
    target_bonuses: int,
    annual_sum_rub: int,
    enrolled_at: date | None,
    birth_month: int | None,
    birth_day: int | None,
    post_transactions: bool,
) -> dict[str, Any]:
    settings = get_settings()
    today = date.today()

    current_active = compute_active_balance(db, agent_id)

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        member = LoyaltyMember(agent_id=agent_id, tier=tier, annual_sum_rub=0, tier_locked=False)
        db.add(member)

    member.tier = max(int(member.tier or 0), int(tier)) if member.tier_locked else int(tier)
    member.tier_floor = max(int(member.tier_floor or 0), int(tier))
    member.annual_sum_rub = max(int(member.annual_sum_rub or 0), int(annual_sum_rub))
    if enrolled_at and not member.enrolled_at:
        member.enrolled_at = enrolled_at
    if birth_month and not member.birth_month:
        member.birth_month = birth_month
    if birth_day and not member.birth_day:
        member.birth_day = birth_day
    member.last_tier_review_at = today

    spend_bt = ""
    earn_bt = ""
    delta = int(target_bonuses) - int(current_active)
    if delta != 0:
        if post_transactions:
            if delta < 0:
                spend_bt = post_bonus_transaction(
                    client, agent_id, -delta, "SPENDING",
                    f"Коррекция баланса при переносе ПЛ из RetailCRM ({agent_id[:8]})",
                )
            else:
                earn_bt = post_bonus_transaction(
                    client, agent_id, delta, "EARNING",
                    f"Перенос активных бонусов ПЛ из RetailCRM ({agent_id[:8]})",
                )
        for batch in db.scalars(
            select(BonusBatch).where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0)
        ).all():
            batch.remaining = 0
        if target_bonuses > 0:
            db.add(
                BonusBatch(
                    agent_id=agent_id,
                    original_amount=int(target_bonuses),
                    remaining=int(target_bonuses),
                    expires_at=today + timedelta(days=365),
                    activates_at=None,
                    source_order_id=f"full-migration-{today.isoformat()}",
                    bonustransaction_id=earn_bt or None,
                    batch_type=IMPORT_BATCH_TYPE,
                )
            )

    attrs = _build_member_attrs_for_cp(db, settings, member)
    _safe_sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=attrs)
    _save_last_synced_cp_attrs(member, settings, attrs)
    log_bonus(
        db,
        action=BonusLogAction.MANUAL_SYNC.value,
        agent_id=agent_id,
        agent_name=agent_name,
        tier_at_moment=int(member.tier),
        bonus_amount=int(target_bonuses),
        bonustransaction_id=earn_bt or spend_bt or None,
        details={
            "source": "full_api_migration",
            "current_active_before": int(current_active),
            "target_active": int(target_bonuses),
            "delta": delta,
            "spend_bt": spend_bt or None,
            "earn_bt": earn_bt or None,
        },
    )
    return {"current_active": current_active, "delta": delta, "spend_bt": spend_bt, "earn_bt": earn_bt}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Записать изменения в БД и МойСклад (иначе dry-run)")
    parser.add_argument(
        "--skip-bonustransactions", action="store_true",
        help="Не создавать bonustransaction, обновить только доп. поля/локальную БД",
    )
    parser.add_argument("--report", type=Path, default=DATA_DIR / "loyalty_migration_report.csv")
    parser.add_argument("--input", type=Path, default=IN_CSV, help="CSV для переноса (по умолчанию loyalty_matched.csv)")
    parser.add_argument("--limit", type=int, default=0, help="Ограничить число строк (0 = все), для проверки")
    args = parser.parse_args()

    in_csv = args.input
    if not in_csv.is_file():
        raise SystemExit(f"Нет {in_csv}. Сначала запустите match_loyalty_to_moysklad.py")

    rows = list(csv.DictReader(in_csv.open(encoding="utf-8")))
    if args.limit:
        rows = rows[: args.limit]
    print(f"Строк к обработке: {len(rows)} (apply={args.apply})")

    client = MoySkladClient()
    db: Session | None = None
    init_db()
    SessionLocal = get_session_factory()
    db = SessionLocal()

    report: list[dict[str, Any]] = []
    try:
        for r in rows:
            agent_id = r["ms_agent_id"]
            agent_name = r.get("ms_agent_name", "")
            rec: dict[str, Any] = {
                "account_id": r.get("account_id"),
                "phone_norm": r.get("phone_norm"),
                "ms_agent_id": agent_id,
                "ms_agent_name": agent_name,
                "tier": r.get("tier"),
                "target_bonuses": r.get("bonus_balance"),
                "action": "apply" if args.apply else "dry_run",
                "note": "",
            }
            try:
                tier = int(r.get("tier") or 0)
                target_bonuses = max(0, int(float(r.get("bonus_balance") or 0)))
                annual_sum_rub = max(int(float(r.get("orders_sum") or 0)), ANNUAL_SUM_BY_TIER[tier])
                enrolled_at = parse_created_at(r.get("created_at") or "")
                birth_month = int(r["birth_month"]) if r.get("birth_month") else None
                birth_day = int(r["birth_day"]) if r.get("birth_day") else None
            except Exception as exc:
                rec["note"] = f"error: {exc}"
                report.append(rec)
                continue

            try:
                if args.apply:
                    result = apply_row(
                        db=db, client=client, agent_id=agent_id, agent_name=agent_name,
                        tier=tier, target_bonuses=target_bonuses, annual_sum_rub=annual_sum_rub,
                        enrolled_at=enrolled_at, birth_month=birth_month, birth_day=birth_day,
                        post_transactions=not args.skip_bonustransactions,
                    )
                    rec["note"] = (
                        f"current_active_before={result['current_active']} delta={result['delta']} "
                        f"spend_bt={result['spend_bt'] or '-'} earn_bt={result['earn_bt'] or '-'}"
                    )
                    db.commit()
                else:
                    current_active = compute_active_balance(db, agent_id)
                    delta = target_bonuses - current_active
                    rec["note"] = (
                        f"would set tier={tier_name_ru(tier)}, annual_sum_rub>={annual_sum_rub}, "
                        f"active {current_active}->{target_bonuses} (delta {delta:+d}), "
                        f"enrolled_at<={enrolled_at}, birthday={birth_month}/{birth_day}"
                    )
            except Exception as exc:
                db.rollback()
                rec["note"] = f"error: {type(exc).__name__}: {exc}"
            report.append(rec)

        if args.apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    with args.report.open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["account_id", "phone_norm", "ms_agent_id", "ms_agent_name", "tier", "target_bonuses", "action", "note"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(report)

    errors = sum(1 for r in report if r["note"].startswith("error"))
    print(f"Готово. Обработано: {len(report)}, ошибок: {errors}. Отчёт: {args.report.resolve()}")


if __name__ == "__main__":
    main()
