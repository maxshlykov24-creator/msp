from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import BonusLog


def log_bonus(
    db: Session,
    *,
    action: str,
    customerorder_id: str | None = None,
    customerorder_name: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    tier_at_moment: int | None = None,
    cashback_percent: int | None = None,
    order_sum_kop: int | None = None,
    bonus_amount: int | None = None,
    bonustransaction_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        BonusLog(
            customerorder_id=customerorder_id,
            customerorder_name=customerorder_name,
            agent_id=agent_id,
            agent_name=agent_name,
            action=action,
            tier_at_moment=tier_at_moment,
            cashback_percent=cashback_percent,
            order_sum_kop=order_sum_kop,
            bonus_amount=bonus_amount,
            bonustransaction_id=bonustransaction_id,
            details=details,
        )
    )
