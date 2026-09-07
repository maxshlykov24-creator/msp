from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, select

from apscheduler.schedulers.background import BackgroundScheduler

from app.bonus_log import log_bonus
from app.config import get_settings
from app.database import get_session_factory
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent, ProcessedEventStatus
from app.moysklad_client import MoySkladClient
from app.spend_engine import unique_bt_name
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru
from app.webhook_handlers import (
    _build_member_attrs_for_cp,
    _cp_attrs_snapshot,
    _ms_now,
    _sync_member_card,
    handle_customerorder_update,
)

log = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def reconciliation_job() -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()
    db = SessionLocal()
    client = MoySkladClient()
    try:
        since = datetime.now() - timedelta(days=int(settings.reconciliation_lookback_days))
        flt = f"updated>={since.strftime('%Y-%m-%d %H:%M:%S')}"
        offset = 0
        while True:
            data = client.get(
                "/entity/customerorder",
                params={"filter": flt, "limit": 100, "offset": offset, "expand": "state"},
            )
            rows = list(data.get("rows") or [])
            if not rows:
                break
            for row in rows:
                st = row.get("state") or {}
                if str(st.get("name") or "") != settings.status_delivered:
                    continue
                oid = str(row.get("id") or "")
                if not oid:
                    continue
                ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == oid))
                if ev and ev.status == ProcessedEventStatus.active.value:
                    continue
                try:
                    handle_customerorder_update(db, client, oid)
                    log_bonus(
                        db,
                        action="RECONCILIATION",
                        customerorder_id=oid,
                        details={"reason": "missing_processed_event"},
                    )
                except Exception:
                    continue
            offset += 100
            if len(rows) < 100:
                break
        db.commit()
    finally:
        db.close()


def daily_tier_resync_job() -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()
    db = SessionLocal()
    client = MoySkladClient()
    try:
        for m in db.scalars(select(LoyaltyMember)).all():
            if not m.tier_locked:
                computed = tier_index_from_annual_sum(int(m.annual_sum_rub))
                want = max(computed, int(m.tier_floor or 0))
                if int(m.tier) != int(want):
                    old = int(m.tier)
                    m.tier = int(want)
                    log_bonus(
                        db,
                        action="TIER_DOWN" if want < old else "TIER_UP",
                        agent_id=m.agent_id,
                        details={
                            "from": old,
                            "to": want,
                            "annual": int(m.annual_sum_rub),
                            "floor": int(m.tier_floor or 0),
                        },
                    )
            # Выравниваем карточку, если она отстала от расчёта: упавший синк
            # больше не оставляет след, поэтому расхождение видно и лечится само.
            try:
                cp_target = _build_member_attrs_for_cp(db, settings, m)
                if _cp_attrs_snapshot(settings, cp_target) != (m.last_synced_cp_attrs or {}):
                    _sync_member_card(client, settings, m, cp_target)
            except Exception:
                log.warning("card resync failed agent=%s", m.agent_id, exc_info=True)
        db.commit()
    finally:
        db.close()


def _agent_meta_href(settings, agent_id: str) -> dict[str, Any]:
    base = settings.api_base.rstrip("/")
    return {
        "meta": {
            "href": f"{base}/entity/counterparty/{agent_id}",
            "type": "counterparty",
            "mediaType": "application/json",
        }
    }


def birthday_bonus_job() -> None:
    settings = get_settings()
    pts = int(settings.birthday_bonus_points)
    if pts <= 0:
        return
    SessionLocal = get_session_factory()
    db = SessionLocal()
    client = MoySkladClient()
    try:
        today = date.today()
        for m in db.scalars(
            select(LoyaltyMember).where(
                and_(LoyaltyMember.birth_month.isnot(None), LoyaltyMember.birth_day.isnot(None))
            )
        ).all():
            if m.is_blocked:
                continue
            if m.birth_month != today.month or m.birth_day != today.day:
                continue
            if m.last_birthday_bonus_year == today.year:
                continue
            # Операцию в МС создаст materialize_activated_batches_job в день активации.
            activates_at = datetime.now(timezone.utc) + timedelta(days=int(settings.bonus_delay_days))
            m.last_birthday_bonus_year = today.year
            db.add(
                BonusBatch(
                    agent_id=m.agent_id,
                    original_amount=pts,
                    remaining=pts,
                    expires_at=(activates_at.date() + timedelta(days=365)),
                    activates_at=activates_at,
                    source_order_id=None,
                    bonustransaction_id=None,
                    batch_type="birthday",
                )
            )
            log_bonus(
                db,
                action="BIRTHDAY",
                agent_id=m.agent_id,
                bonus_amount=pts,
                bonustransaction_id=None,
                details={"year": today.year, "activates_at": activates_at.isoformat(), "ms_transaction": "deferred"},
            )
            try:
                cp_target = _build_member_attrs_for_cp(db, settings, m)
                _sync_member_card(client, settings, m, cp_target)
            except Exception:
                log.warning("birthday card sync failed agent=%s", m.agent_id, exc_info=True)
        db.commit()
    finally:
        db.close()


def materialize_activated_batches_job() -> None:
    """Создаёт в МойСклад операции по батчам, у которых наступил день активации.

    Тариф МС не поддерживает `executionDate` в будущем (ошибка 62000), поэтому
    отсрочку держим у себя в `activates_at`, а операцию заводим текущей датой —
    ровно тогда, когда бонусы становятся доступны клиенту.
    """
    settings = get_settings()
    SessionLocal = get_session_factory()
    db = SessionLocal()
    client = MoySkladClient()
    try:
        now = datetime.now(timezone.utc)
        batches = list(
            db.scalars(
                select(BonusBatch).where(
                    BonusBatch.bonustransaction_id.is_(None),
                    BonusBatch.remaining > 0,
                    BonusBatch.activates_at.isnot(None),
                    BonusBatch.activates_at <= now,
                )
            ).all()
        )
        if not batches:
            return
        bonus_program_meta = client.fetch_bonus_program_meta()
        for b in batches:
            amount = int(b.original_amount)
            if amount <= 0:
                continue
            try:
                bt = client.post(
                    "/entity/bonustransaction",
                    {
                        "bonusProgram": {"meta": bonus_program_meta},
                        "agent": _agent_meta_href(settings, b.agent_id),
                        "transactionType": "EARNING",
                        "bonusValue": amount,
                        "executionDate": _ms_now().strftime("%Y-%m-%d %H:%M:%S"),
                        "externalCode": settings.loyalty_external_code,
                        "name": unique_bt_name(f"Активация бонусов ПЛ {b.agent_id[:8]}"),
                    },
                    disable_webhook=True,
                )
            except Exception:
                # следующий прогон повторит: bonustransaction_id так и остался пустым
                continue
            bt_id = str(bt.get("id") or "")
            if not bt_id:
                continue
            b.bonustransaction_id = bt_id
            if b.source_order_id:
                ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == b.source_order_id))
                if ev is not None and not ev.bonustransaction_id:
                    ev.bonustransaction_id = bt_id
            log_bonus(
                db,
                action="EARN",
                customerorder_id=b.source_order_id,
                agent_id=b.agent_id,
                bonus_amount=amount,
                bonustransaction_id=bt_id,
                details={"reason": "batch_activated", "batch_id": str(b.id), "batch_type": b.batch_type},
            )
            member = db.get(LoyaltyMember, b.agent_id)
            if member is not None:
                try:
                    cp_target = _build_member_attrs_for_cp(db, settings, member)
                    _sync_member_card(client, settings, member, cp_target)
                except Exception:
                    log.warning("card sync failed agent=%s", member.agent_id, exc_info=True)
        db.commit()
    finally:
        db.close()


def expire_bonus_batches_job() -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()
    db = SessionLocal()
    client = MoySkladClient()
    try:
        today = date.today()
        bonus_program_meta = client.fetch_bonus_program_meta()
        for b in db.scalars(select(BonusBatch).where(BonusBatch.expires_at <= today, BonusBatch.remaining > 0)).all():
            rem = int(b.remaining)
            if rem <= 0:
                continue
            if not b.bonustransaction_id:
                # Батч так и не был материализован в МС — сгорать в МС нечему.
                log_bonus(db, action="EXPIRE", agent_id=b.agent_id, bonus_amount=rem,
                          details={"batch_id": str(b.id), "ms_transaction": "never_created"})
                b.remaining = 0
                continue
            client.post(
                "/entity/bonustransaction",
                {
                    "bonusProgram": {"meta": bonus_program_meta},
                    "agent": {
                        "meta": {
                            "href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{b.agent_id}",
                            "type": "counterparty",
                            "mediaType": "application/json",
                        }
                    },
                    "transactionType": "SPENDING",
                    "bonusValue": rem,
                    "externalCode": settings.loyalty_external_code,
                    "name": unique_bt_name(f"Сгорание бонусов (срок) {b.agent_id[:8]}"),
                },
                disable_webhook=True,
            )
            log_bonus(db, action="EXPIRE", agent_id=b.agent_id, bonus_amount=rem, details={"batch_id": str(b.id)})
            b.remaining = 0
        db.commit()
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(reconciliation_job, "cron", hour=3, minute=10, id="reconciliation")
    _scheduler.add_job(daily_tier_resync_job, "cron", hour=4, minute=5, id="tier_resync")
    _scheduler.add_job(birthday_bonus_job, "cron", hour=5, minute=0, id="birthday")
    _scheduler.add_job(expire_bonus_batches_job, "cron", hour=4, minute=40, id="expire_batches")
    # Часто: батч должен стать операцией в МС в тот же день, когда активируется.
    _scheduler.add_job(materialize_activated_batches_job, "cron", minute=20, id="materialize_batches")
    _scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
