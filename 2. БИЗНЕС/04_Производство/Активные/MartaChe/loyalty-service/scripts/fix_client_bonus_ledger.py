#!/usr/bin/env python3
"""Коррекция бонусного леджера клиента до значений, подтверждённых заказами МойСклад.

Контекст (2026-09-07, Ирина Хайкичева): массовый прогон начислил кэшбэк по историческим
заказам с текущим уровнем клиента вместо уровня на дату заказа, плюс в леджере остался
импорт баланса из RetailCRM, не подтверждённый заказами. Вариант А — признаём только то,
что считается по заказам МС.

Что делает (только для одного agent_id, по явному плану ниже):
  1. Аннулирует импортный батч (original/remaining -> 0) и удаляет его bonustransaction.
  2. Пересоздаёт завышенные EARNING-транзакции на корректные суммы.
  3. Приводит bonus_batches / processed_events к корректным суммам.
  4. Перераспределяет уже сделанное списание по FIFO заново.
  5. Ставит annual_sum_rub по фактическим заказам и синхронизирует карточку МС.

Запуск:
    python scripts/fix_client_bonus_ledger.py            # dry-run, ничего не меняет
    python scripts/fix_client_bonus_ledger.py --apply    # применить
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.bonus_log import log_bonus
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent
from app.moysklad_client import MoySkladClient
from app.spend_engine import compute_active_balance, compute_pending_balance, unique_bt_name
from app.tier_manager import tier_name_ru
from app.webhook_handlers import _build_member_attrs_for_cp, _sync_member_card

AGENT_ID = "437d4f4b-bed6-11ee-0a80-0bb00077ce33"
AGENT_NAME = "Ирина Хайкичева"

# Импортный батч из RetailCRM — аннулируем целиком.
CSV_BATCH_ID = "b6e3f847-4800-4e13-a9ac-d816203fb378"
CSV_BT_ID = "15774eba-4729-11f1-0a80-0413001aee4e"

# Заказы с завышенным кэшбэком: order_id -> (имя, было, стало, cashable_было, cashable_стало)
CORRECTIONS = {
    "9931d2c3-f813-11ef-0a80-10480054d738": ("102354", 536, 234, 7450, 7450),
    "80c35e22-d36f-11f0-0a80-02b0005ee78e": ("103444", 2504, 1776, 31938, 31938),
    "03a09bf5-d74c-11f0-0a80-059e0018d407": ("103483", 622, 445, 8663, 8663),
    "0567bb6d-5820-11f1-0a80-1ade003182d5": ("site_1207652465", 654, 498, 6540, 4979),
}

# Годовой оборот строго по доставленным заказам МС.
TARGET_ANNUAL_RUB = 118_620
# Уже проведённое списание — остаётся в силе, но переразложится по батчам.
KNOWN_SPENT = 1_560
TARGET_BALANCE = 7_306

REASON = "коррекция аудита 07.09: уровень на дату заказа, база без бонусной оплаты"


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="применить изменения (иначе dry-run)")
    args = ap.parse_args()
    dry = not args.apply

    settings = get_settings()
    init_db()
    db = get_session_factory()()
    client = MoySkladClient()

    member = db.get(LoyaltyMember, AGENT_ID)
    if member is None:
        print("Участник ПЛ не найден")
        return

    print(f"{'РЕЖИМ: DRY-RUN (ничего не пишем)' if dry else 'РЕЖИМ: APPLY'}")
    print(f"Клиент: {AGENT_NAME}  ({AGENT_ID})")
    print(f"До:  баланс={fmt(compute_active_balance(db, AGENT_ID))}  "
          f"pending={fmt(compute_pending_balance(db, AGENT_ID))}  "
          f"annual={fmt(int(member.annual_sum_rub))}  уровень={tier_name_ru(int(member.tier))}")

    batches = db.scalars(
        select(BonusBatch).where(BonusBatch.agent_id == AGENT_ID).order_by(BonusBatch.created_at.asc())
    ).all()
    by_order = {b.source_order_id: b for b in batches}

    # --- 1. Аннулируем импортный батч RetailCRM --------------------------------
    csv_batch = next((b for b in batches if str(b.id) == CSV_BATCH_ID), None)
    if csv_batch is None:
        print(f"! Импортный батч {CSV_BATCH_ID} не найден — прерываю")
        return
    print(f"\n1. Импорт RetailCRM: батч orig={fmt(csv_batch.original_amount)} "
          f"rem={fmt(csv_batch.remaining)} -> 0/0, транзакция {CSV_BT_ID[:8]} удаляется")
    if not dry:
        try:
            client.delete(f"/entity/bonustransaction/{CSV_BT_ID}", disable_webhook=True)
            print("   транзакция удалена в МС")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! удалить не вышло ({exc}) — ставлю компенсирующий SPENDING")
            bonus_program_meta = client.fetch_bonus_program_meta()
            client.post(
                "/entity/bonustransaction",
                {
                    "bonusProgram": {"meta": bonus_program_meta},
                    "agent": {"meta": {
                        "href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{AGENT_ID}",
                        "type": "counterparty",
                        "mediaType": "application/json",
                    }},
                    "transactionType": "SPENDING",
                    "bonusValue": int(csv_batch.original_amount),
                    "externalCode": settings.loyalty_external_code,
                    "name": unique_bt_name(f"Аннулирование импорта RetailCRM ({REASON})"),
                },
                disable_webhook=True,
            )
        csv_batch.original_amount = 0
        csv_batch.remaining = 0
        csv_batch.bonustransaction_id = None
        log_bonus(
            db,
            action="RECONCILIATION",
            agent_id=AGENT_ID,
            agent_name=AGENT_NAME,
            bonus_amount=-5158,
            bonustransaction_id=CSV_BT_ID,
            details={"reason": REASON, "source": "retailcrm_csv_import", "action": "annulled"},
        )

    # --- 2. Пересоздаём завышенные начисления ---------------------------------
    print("\n2. Коррекция кэшбэка по заказам:")
    bonus_program_meta = client.fetch_bonus_program_meta() if not dry else None
    agent_meta = {
        "href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{AGENT_ID}",
        "type": "counterparty",
        "mediaType": "application/json",
    }

    for order_id, (order_name, was, now_val, cash_was, cash_now) in CORRECTIONS.items():
        batch = by_order.get(order_id)
        ev = db.scalars(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id)).first()
        cash_note = "" if cash_was == cash_now else f", база {fmt(cash_was)} -> {fmt(cash_now)} ₽"
        print(f"   {order_name:<18} {fmt(was):>6} -> {fmt(now_val):<6}{cash_note}")
        if dry:
            continue

        old_bt = (ev.bonustransaction_id if ev else None) or (batch.bonustransaction_id if batch else None)
        if old_bt:
            try:
                client.delete(f"/entity/bonustransaction/{old_bt}", disable_webhook=True)
            except Exception as exc:  # noqa: BLE001
                print(f"     ! старую транзакцию не удалить ({exc})")
        new_bt = client.post(
            "/entity/bonustransaction",
            {
                "bonusProgram": {"meta": bonus_program_meta},
                "agent": {"meta": agent_meta},
                "transactionType": "EARNING",
                "bonusValue": int(now_val),
                "externalCode": settings.loyalty_external_code,
                "name": unique_bt_name(f"Кэшбэк заказ {order_name} ({REASON})"),
            },
            disable_webhook=True,
        )
        new_bt_id = str(new_bt.get("id") or "") or None

        if batch is not None:
            batch.original_amount = int(now_val)
            batch.remaining = int(now_val)
            batch.bonustransaction_id = new_bt_id
        if ev is not None:
            ev.bonus_value = int(now_val)
            ev.cashable_total_rub = int(cash_now)
            ev.bonustransaction_id = new_bt_id
        log_bonus(
            db,
            action="RECONCILIATION",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=AGENT_ID,
            agent_name=AGENT_NAME,
            bonus_amount=int(now_val) - int(was),
            bonustransaction_id=new_bt_id,
            details={"reason": REASON, "was": was, "now": now_val,
                     "cashable_was": cash_was, "cashable_now": cash_now},
        )

    # --- 3. Перераскладываем списание по FIFO ---------------------------------
    print(f"\n3. Списание {fmt(KNOWN_SPENT)} перераскладывается по FIFO заново:")
    if not dry:
        db.flush()
    fresh = db.scalars(
        select(BonusBatch).where(BonusBatch.agent_id == AGENT_ID).order_by(BonusBatch.created_at.asc())
    ).all()
    left = KNOWN_SPENT
    total_after = 0
    for b in fresh:
        # В dry-run берём целевые суммы: в БД ещё лежат старые, и план по ним был бы ложным.
        if str(b.id) == CSV_BATCH_ID:
            orig = 0
        elif b.source_order_id in CORRECTIONS:
            orig = CORRECTIONS[b.source_order_id][2]
        else:
            orig = int(b.original_amount)
        take = min(orig, left)
        left -= take
        total_after += orig - take
        label = b.source_order_id or "—"
        name = CORRECTIONS.get(b.source_order_id, (None,))[0] or label
        if orig > 0 or take > 0:
            print(f"   батч {str(name)[:22]:<22} orig={fmt(orig):>6} списано={fmt(take):>6} остаток={fmt(orig - take):>6}")
        if not dry:
            b.original_amount = orig
            b.remaining = orig - take
    if left > 0:
        print(f"   ! не удалось разложить {left} — прерываю без записи")
        db.rollback()
        return
    print(f"   итого остаток по батчам: {fmt(total_after)}")
    if total_after != TARGET_BALANCE:
        print(f"   ! расчётный остаток {total_after} != целевого {TARGET_BALANCE} — прерываю без записи")
        db.rollback()
        return

    # --- 4. Годовой оборот и карточка -----------------------------------------
    print(f"\n4. Годовой оборот: {fmt(int(member.annual_sum_rub))} -> {fmt(TARGET_ANNUAL_RUB)} ₽ "
          f"(уровень {tier_name_ru(int(member.tier))} сохраняется, порог 60 000 пройден)")
    if not dry:
        old_annual = int(member.annual_sum_rub)
        member.annual_sum_rub = TARGET_ANNUAL_RUB
        log_bonus(
            db,
            action="RECONCILIATION",
            agent_id=AGENT_ID,
            agent_name=AGENT_NAME,
            tier_at_moment=int(member.tier),
            details={"reason": REASON, "annual_was": old_annual, "annual_now": TARGET_ANNUAL_RUB},
        )
        db.flush()

        balance = compute_active_balance(db, AGENT_ID)
        if balance != TARGET_BALANCE:
            print(f"   ! баланс после правок {balance} != целевого {TARGET_BALANCE} — откат")
            db.rollback()
            return
        attrs = _build_member_attrs_for_cp(db, settings, member)
        ok = _sync_member_card(client, settings, member, attrs)
        print(f"   карточка МС: {'обновлена' if ok else 'НЕ обновлена (см. лог сервиса)'}")
        db.commit()

    print("\n=== ИТОГ ===")
    if dry:
        print(f"  Ожидаемый баланс после применения: {fmt(TARGET_BALANCE)}")
        print("  Запусти с --apply, чтобы применить.")
    else:
        print(f"  Активные бонусы: {fmt(compute_active_balance(db, AGENT_ID))}")
        print(f"  Ожидают активации: {fmt(compute_pending_balance(db, AGENT_ID))}")
        print(f"  Годовой оборот: {fmt(int(member.annual_sum_rub))} ₽, уровень {tier_name_ru(int(member.tier))}")


if __name__ == "__main__":
    main()
