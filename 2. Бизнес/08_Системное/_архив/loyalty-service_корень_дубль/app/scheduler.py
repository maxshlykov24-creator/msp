from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, select

from apscheduler.schedulers.background import BackgroundScheduler

from app.bonus_log import log_bonus
from app.config import get_settings
from app.database import get_session_factory
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent, ProcessedEventStatus
from app.moysklad_client import MoySkladClient
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru
from app.webhook_handlers import _safe_sync_counterparty_attributes, handle_customerorder_update

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
            if m.tier_locked:
                continue
            want = tier_index_from_annual_sum(int(m.annual_sum_rub))
            if int(m.tier) != int(want):
                old = int(m.tier)
                m.tier = int(want)
                log_bonus(
                    db,
                    action="TIER_DOWN" if want < old else "TIER_UP",
                    agent_id=m.agent_id,
                    details={"from": old, "to": want, "annual": int(m.annual_sum_rub)},
                )
                try:
                    _safe_sync_counterparty_attributes(
                        client,
                        settings,
                        agent_id=m.agent_id,
                        attrs={
                            "tier_name": tier_name_ru(int(m.tier)),
                            "annual_sum_rub": int(m.annual_sum_rub),
                            "enrolled_at": m.enrolled_at.isoformat() if m.enrolled_at else None,
                            "last_tier_review": m.last_tier_review_at.isoformat() if m.last_tier_review_at else None,
                            "tier_locked": bool(m.tier_locked),
                        },
                    )
                except Exception:
                    pass
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
        bonus_program_meta = client.fetch_bonus_program_meta()
        for m in db.scalars(
            select(LoyaltyMember).where(
                and_(LoyaltyMember.birth_month.isnot(None), LoyaltyMember.birth_day.isnot(None))
            )
        ).all():
            if m.birth_month != today.month or m.birth_day != today.day:
                continue
            if m.last_birthday_bonus_year == today.year:
                continue
            exec_dt = datetime.now() + timedelta(days=int(settings.bonus_delay_days))
            bt_body: dict = {
                "bonusProgram": {"meta": bonus_program_meta},
                "agent": _agent_meta_href(settings, m.agent_id),
                "transactionType": "EARNING",
                "bonusValue": pts,
                "executionDate": exec_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "externalCode": settings.loyalty_external_code,
                "name": "Бонус на день рождения",
            }
            try:
                bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
            except Exception:
                continue
            bt_id = str(bt.get("id") or "")
            m.last_birthday_bonus_year = today.year
            db.add(
                BonusBatch(
                    agent_id=m.agent_id,
                    original_amount=pts,
                    remaining=pts,
                    expires_at=(exec_dt.date() + timedelta(days=365)),
                    source_order_id=None,
                    bonustransaction_id=bt_id or None,
                    batch_type="birthday",
                )
            )
            log_bonus(
                db,
                action="BIRTHDAY",
                agent_id=m.agent_id,
                bonus_amount=pts,
                bonustransaction_id=bt_id or None,
                details={"year": today.year},
            )
            try:
                _safe_sync_counterparty_attributes(
                    client,
                    settings,
                    agent_id=m.agent_id,
                    attrs={
                        "tier_name": tier_name_ru(int(m.tier)),
                        "annual_sum_rub": int(m.annual_sum_rub),
                        "enrolled_at": m.enrolled_at.isoformat() if m.enrolled_at else None,
                        "last_tier_review": m.last_tier_review_at.isoformat() if m.last_tier_review_at else None,
                        "tier_locked": bool(m.tier_locked),
                    },
                )
            except Exception:
                pass
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
                    "name": "Сгорание бонусов (срок)",
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
    _scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
