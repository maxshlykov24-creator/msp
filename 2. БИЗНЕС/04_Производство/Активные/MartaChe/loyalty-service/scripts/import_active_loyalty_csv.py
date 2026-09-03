#!/usr/bin/env python3
"""
Актуализация ПЛ MartaChe из выгрузки вида:

  Клиент;Уровень;Дата регистрации;Сумма покупок;Баланс бонусов;Телефон;Статус;Магазины

Скрипт берёт только строки со статусом "Активен", сопоставляет контрагента
МойСклад по телефону и обновляет локальную модель ПЛ + доп. поля контрагента.

По умолчанию это dry-run. Запись в БД/МойСклад только с --apply.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, timedelta
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


IMPORT_BATCH_TYPE = "csv_active_reset"


def norm_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("8") and len(digits) >= 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def mask_phone(value: str) -> str:
    digits = norm_phone(value)
    if len(digits) < 7:
        return ""
    return f"{digits[:2]}***{digits[-4:]}"


def parse_int(value: Any) -> int:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    return int(round(float(text)))


def parse_tier(value: str) -> int:
    text = str(value or "").strip().lower()
    if "знаком" in text:
        return 0
    if "друж" in text:
        return 1
    if "люб" in text:
        return 2
    if text in {"0", "1", "2"}:
        return int(text)
    raise ValueError(f"unknown tier: {value!r}")


def build_counterparty_phone_index(client: MoySkladClient) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    offset = 0
    while True:
        data = client.get("/entity/counterparty", params={"limit": 100, "offset": offset})
        rows = list(data.get("rows") or [])
        for row in rows:
            phone = norm_phone(str(row.get("phone") or ""))
            cp_id = str(row.get("id") or "")
            if len(phone) < 10 or not cp_id:
                continue
            index.setdefault(phone, []).append((cp_id, str(row.get("name") or "")))
            index.setdefault(phone[-10:], []).append((cp_id, str(row.get("name") or "")))
        offset += len(rows)
        if len(rows) < 100:
            break
    return index


def find_counterparty(
    client: MoySkladClient,
    phone: str,
    phone_index: dict[str, list[tuple[str, str]]] | None = None,
) -> tuple[str, str, str]:
    """Возвращает (status, id, name): ok / not_found / ambiguous."""
    digits = norm_phone(phone)
    if len(digits) < 10:
        return ("not_found", "", "")

    candidates: dict[str, str] = {}
    if phone_index is not None:
        for key in (digits, digits[-10:]):
            for cp_id, cp_name in phone_index.get(key, []):
                candidates[cp_id] = cp_name
        if not candidates:
            return ("not_found", "", "")
        if len(candidates) > 1:
            return ("ambiguous", "", "; ".join(sorted(candidates.values())[:5]))
        cp_id, cp_name = next(iter(candidates.items()))
        return ("ok", cp_id, cp_name)

    for query in (digits[-10:], digits):
        for row in client.search_counterparties(query, limit=20):
            cp_id = str(row.get("id") or "")
            if cp_id:
                candidates[cp_id] = str(row.get("name") or "")

    if not candidates:
        return ("not_found", "", "")
    if len(candidates) > 1:
        return ("ambiguous", "", "; ".join(sorted(candidates.values())[:5]))
    cp_id, cp_name = next(iter(candidates.items()))
    return ("ok", cp_id, cp_name)


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
        "executionDate": f"{date.today().isoformat()} 00:00:00",
        "externalCode": settings.loyalty_external_code,
        "name": note[:200],
    }
    result = client.post("/entity/bonustransaction", body, disable_webhook=True)
    return str(result.get("id") or "")


def read_active_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        reader = csv.DictReader(f, dialect=dialect)
        rows: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            if normalized.get("статус", "").casefold() != "активен":
                continue
            normalized["_line"] = str(line_no)
            rows.append(normalized)
        return rows


def apply_member_update(
    *,
    db: Session,
    client: MoySkladClient,
    agent_id: str,
    agent_name: str,
    tier: int,
    target_bonuses: int,
    current_active: int,
    post_transactions: bool,
) -> tuple[str, str]:
    settings = get_settings()
    today = date.today()
    expires_at = today + timedelta(days=365)

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        member = LoyaltyMember(agent_id=agent_id, tier=tier, annual_sum_rub=0, tier_locked=False)
        db.add(member)

    member.tier = int(tier)
    member.tier_floor = max(int(member.tier_floor or 0), int(tier))
    member.is_blocked = False
    member.annual_sum_rub = max(int(member.annual_sum_rub or 0), (0, 20_000, 60_000)[int(tier)])
    member.last_tier_review_at = today

    spend_bt = ""
    earn_bt = ""
    if post_transactions and current_active > 0:
        spend_bt = post_bonus_transaction(
            client,
            agent_id,
            current_active,
            "SPENDING",
            f"Обнуление старого активного баланса перед импортом ПЛ {agent_id[:8]}",
        )

    for batch in db.scalars(select(BonusBatch).where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0)).all():
        batch.remaining = 0

    if target_bonuses > 0:
        if post_transactions:
            earn_bt = post_bonus_transaction(
                client,
                agent_id,
                target_bonuses,
                "EARNING",
                f"Актуализация активных бонусов ПЛ из CSV {agent_id[:8]}",
            )
        db.add(
            BonusBatch(
                agent_id=agent_id,
                original_amount=target_bonuses,
                remaining=target_bonuses,
                expires_at=expires_at,
                activates_at=None,
                source_order_id=f"active-csv-{today.isoformat()}",
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
        tier_at_moment=int(tier),
        bonus_amount=int(target_bonuses),
        bonustransaction_id=earn_bt or None,
        details={
            "source": "active_loyalty_csv",
            "current_active_before": int(current_active),
            "target_active": int(target_bonuses),
            "expires_at": expires_at.isoformat(),
            "spend_bt": spend_bt or None,
            "earn_bt": earn_bt or None,
        },
    )
    return spend_bt, earn_bt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--apply", action="store_true", help="Записать изменения в БД и МойСклад")
    parser.add_argument(
        "--skip-bonustransactions",
        action="store_true",
        help="Не создавать корректирующие bonustransaction в МойСклад; обновить только локальные батчи и доп. поля",
    )
    parser.add_argument(
        "--with-db-dry-run",
        action="store_true",
        help="В dry-run подключиться к БД и показать текущий локальный active balance",
    )
    parser.add_argument("--no-prefetch-counterparties", action="store_true", help="Искать контрагентов по API search для каждой строки")
    parser.add_argument("--report", type=Path, default=Path("active_loyalty_import_report.csv"))
    args = parser.parse_args()

    if not args.csv_path.is_file():
        raise SystemExit(f"CSV not found: {args.csv_path}")

    rows = read_active_rows(args.csv_path)
    client = MoySkladClient()
    phone_index = None if args.no_prefetch_counterparties else build_counterparty_phone_index(client)
    db: Session | None = None
    if args.apply or args.with_db_dry_run:
        init_db()
        SessionLocal = get_session_factory()
        db = SessionLocal()
    report: list[dict[str, Any]] = []
    processed_agents: set[str] = set()
    try:
        for row in rows:
            csv_name = row.get("клиент", "")
            phone = row.get("телефон", "")
            rec: dict[str, Any] = {
                "line": row.get("_line", ""),
                "csv_name": csv_name,
                "phone_masked": mask_phone(phone),
                "match_status": "",
                "agent_id": "",
                "agent_name": "",
                "tier": "",
                "target_bonuses": "",
                "current_active": "",
                "action": "dry_run" if not args.apply else "apply",
                "note": "",
            }
            try:
                tier = parse_tier(row.get("уровень", ""))
                target_bonuses = max(0, parse_int(row.get("баланс бонусов", "0")))
            except Exception as exc:
                rec["match_status"] = "error"
                rec["note"] = str(exc)
                report.append(rec)
                continue

            status, agent_id, agent_name = find_counterparty(client, phone, phone_index)
            rec["match_status"] = status
            rec["agent_id"] = agent_id
            rec["agent_name"] = agent_name
            rec["tier"] = tier_name_ru(tier)
            rec["target_bonuses"] = target_bonuses
            if status != "ok":
                rec["note"] = agent_name if status == "ambiguous" else "counterparty not found by phone"
                report.append(rec)
                continue
            if agent_id in processed_agents:
                rec["match_status"] = "duplicate_agent"
                rec["note"] = "same counterparty already processed from another active CSV row"
                report.append(rec)
                continue
            processed_agents.add(agent_id)

            current_active = compute_active_balance(db, agent_id) if db is not None else 0
            rec["current_active"] = current_active
            if args.apply:
                if db is None:
                    raise RuntimeError("db is required for --apply")
                spend_bt, earn_bt = apply_member_update(
                    db=db,
                    client=client,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    tier=tier,
                    target_bonuses=target_bonuses,
                    current_active=current_active,
                    post_transactions=not args.skip_bonustransactions,
                )
                rec["note"] = f"updated; spend_bt={spend_bt or '-'}; earn_bt={earn_bt or '-'}"
            else:
                rec["note"] = "would update level/status/active bonuses; expiry=today+365"
            report.append(rec)

        if args.apply and db is not None:
            db.commit()
        elif db is not None:
            db.rollback()
    except Exception:
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            db.close()

    with args.report.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "line",
            "csv_name",
            "phone_masked",
            "match_status",
            "agent_id",
            "agent_name",
            "tier",
            "target_bonuses",
            "current_active",
            "action",
            "note",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report)

    total = len(report)
    ok = sum(1 for row in report if row["match_status"] == "ok")
    not_found = sum(1 for row in report if row["match_status"] == "not_found")
    ambiguous = sum(1 for row in report if row["match_status"] == "ambiguous")
    duplicate = sum(1 for row in report if row["match_status"] == "duplicate_agent")
    errors = sum(1 for row in report if row["match_status"] == "error")
    print(
        f"active_rows={total} matched={ok} not_found={not_found} "
        f"ambiguous={ambiguous} duplicate_agent={duplicate} errors={errors} report={args.report.resolve()}"
    )


if __name__ == "__main__":
    main()
