#!/usr/bin/env python3
"""Пересинхронизация ПЛ-полей в заказах клиента по текущему состоянию БД.

Поле «Активно бонусов» в заказе — это баланс клиента, который сервис перезаписывает
при каждой обработке заказа. После коррекции леджера в заказах остаются снимки старого
баланса, и менеджер видит в карточке заказа число, которого уже нет. Скрипт приводит
ПЛ-блок заказов к актуальному состоянию и обновляет `last_synced_attrs`, чтобы
field_guard не считал это ручной правкой менеджера.

Запуск:
    python scripts/resync_order_loyalty_fields.py <agent_id>            # dry-run
    python scripts/resync_order_loyalty_fields.py <agent_id> --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import LoyaltyMember, ProcessedEvent
from app.moysklad_client import MoySkladClient
from app.order_loyalty_view import (
    build_order_attributes,
    get_order_loyalty_comment,
    make_comment_line,
    append_comment_to_log,
    system_view_for_order,
    write_order_loyalty_attributes,
)
from app.spend_engine import compute_active_balance
from app.tier_manager import tier_name_ru


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_id")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--comment", default="", help="строка в «Комментарии ПЛ» (опционально)")
    args = ap.parse_args()
    dry = not args.apply

    settings = get_settings()
    init_db()
    db = get_session_factory()()
    client = MoySkladClient()

    member = db.get(LoyaltyMember, args.agent_id)
    if member is None:
        print("Участник ПЛ не найден")
        return

    balance = compute_active_balance(db, args.agent_id)
    print(f"{'DRY-RUN' if dry else 'APPLY'} · баланс={balance} уровень={tier_name_ru(int(member.tier))}")

    href = f"{settings.api_base.rstrip('/')}/entity/counterparty/{args.agent_id}"
    orders = client.get("/entity/customerorder", params={"filter": f"agent={href}", "limit": 100})
    rows = sorted(orders.get("rows") or [], key=lambda o: o.get("moment") or "")

    for o in rows:
        order_id = o["id"]
        name = o.get("name") or ""
        ev = db.scalars(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id)).first()
        spent = int(ev.spent_amount or 0) if ev else 0

        cur = None
        for a in o.get("attributes") or []:
            if a.get("id") == settings.attr_order_active_bonuses:
                cur = a.get("value")
        if cur is not None and int(float(cur)) == balance:
            print(f"  {name:<20} уже {balance} — пропуск")
            continue

        print(f"  {name:<20} «Активно бонусов» {cur} -> {balance}  (списано {spent})")
        if dry:
            continue

        log_text = get_order_loyalty_comment(o, settings)
        if args.comment:
            log_text = append_comment_to_log(settings, log_text, make_comment_line(settings, args.comment))
        patch = build_order_attributes(
            settings,
            client=client,
            member=member,
            spent_amount=spent,
            active_balance=int(balance),
            comment_log=log_text,
        )
        write_order_loyalty_attributes(client, settings, order_id=order_id, patch_attrs=patch)
        if ev is not None:
            ev.last_synced_attrs = system_view_for_order(
                settings, member=member, spent_amount=spent, active_balance=int(balance)
            )

    if not dry:
        db.commit()
        print("Готово.")


if __name__ == "__main__":
    main()
