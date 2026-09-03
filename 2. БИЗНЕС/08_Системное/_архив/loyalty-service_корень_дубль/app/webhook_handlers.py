from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bonus_log import log_bonus
from app.cashback_engine import compute_cashback_for_order
from app.config import Settings, get_settings
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent, ProcessedEventStatus
from app.moysklad_client import MoySkladClient
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru


def _native_id_from_meta_href(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


def _order_state_name(order: dict[str, Any]) -> str:
    st = order.get("state")
    if isinstance(st, dict):
        return str(st.get("name") or "")
    return ""


def _agent_meta(order: dict[str, Any]) -> dict[str, Any]:
    agent = order.get("agent") or {}
    meta = agent.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("В заказе нет agent.meta")
    return meta


def _fmt_ms_moment(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _get_or_create_member(db: Session, agent_id: str) -> LoyaltyMember:
    m = db.get(LoyaltyMember, agent_id)
    if m:
        return m
    m = LoyaltyMember(agent_id=agent_id, tier=0, annual_sum_rub=0, tier_locked=False)
    db.add(m)
    db.flush()
    return m


def _build_attribute_patch(settings: Settings, attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Если в .env заданы UUID доп. полей контрагента — собираем массив attributes для PUT."""
    out: list[dict[str, Any]] = []
    base = settings.api_base.rstrip("/")

    def ameta(attr_uuid: str) -> dict[str, Any]:
        return {
            "href": f"{base}/entity/counterparty/metadata/attributes/{attr_uuid}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        }

    mapping = {
        settings.attr_loyalty_tier: attrs.get("tier_name"),
        settings.attr_annual_sum_rub: attrs.get("annual_sum_rub"),
        settings.attr_enrolled_at: attrs.get("enrolled_at"),
        settings.attr_last_tier_review: attrs.get("last_tier_review"),
        settings.attr_tier_locked: attrs.get("tier_locked"),
    }
    for attr_id, value in mapping.items():
        if not attr_id or value is None:
            continue
        out.append({"meta": ameta(str(attr_id)), "value": value})
    return out


def _attr_key(meta_href: str) -> str:
    return meta_href.rstrip("/").split("/")[-1]


def _safe_sync_counterparty_attributes(
    client: MoySkladClient,
    settings: Settings,
    *,
    agent_id: str,
    attrs: dict[str, Any],
) -> None:
    try:
        _sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=attrs)
    except Exception:
        # Не блокируем начисление, если доп. поля ещё не настроены / типы не совпали
        return


def _sync_counterparty_attributes(
    client: MoySkladClient,
    settings: Settings,
    *,
    agent_id: str,
    attrs: dict[str, Any],
) -> None:
    patch = _build_attribute_patch(settings, attrs)
    if not patch:
        return
    body = client.get(f"/entity/counterparty/{agent_id}")
    by_key: dict[str, dict[str, Any]] = {}
    for a in body.get("attributes") or []:
        mh = str((a.get("meta") or {}).get("href") or "")
        if mh:
            by_key[_attr_key(mh)] = a
    for p in patch:
        mh = str((p.get("meta") or {}).get("href") or "")
        if mh:
            by_key[_attr_key(mh)] = p
    body["attributes"] = list(by_key.values())
    client.put(f"/entity/counterparty/{agent_id}", body, disable_webhook=True)


def handle_customerorder_update(db: Session, client: MoySkladClient, order_id: str) -> None:
    settings = get_settings()
    order = client.fetch_customerorder(order_id)
    state = _order_state_name(order)
    agent_meta = _agent_meta(order)
    agent_id = str(agent_meta.get("id"))
    agent_name = str((order.get("agent") or {}).get("name") or "")

    if state == settings.status_delivered:
        _handle_delivered(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)
        return
    if state == settings.status_return_full:
        _handle_return_full(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)
        return
    if state == settings.status_return_partial:
        _handle_return_partial(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)
        return


def _handle_delivered(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
    order_id = str(order.get("id"))
    order_name = str(order.get("name") or "")

    existing = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id))
    if existing and existing.status == ProcessedEventStatus.active.value:
        return

    member = _get_or_create_member(db, agent_id)
    is_first_enrollment = member.enrolled_at is None
    if is_first_enrollment:
        member.enrolled_at = date.today()

    cb = compute_cashback_for_order(
        order=order,
        settings=settings,
        current_annual_rub=int(member.annual_sum_rub),
        current_tier_locked=bool(member.tier_locked),
        current_tier_index=int(member.tier),
    )

    old_tier = int(member.tier)
    member.annual_sum_rub = int(cb.provisional_annual_rub)
    if not member.tier_locked:
        member.tier = int(cb.tier_used)

    bonus_program_meta = client.fetch_bonus_program_meta()
    exec_dt = datetime.now() + timedelta(days=int(settings.bonus_delay_days))

    bt_body: dict[str, Any] = {
        "bonusProgram": {"meta": bonus_program_meta},
        "agent": {"meta": _agent_meta(order)},
        "transactionType": "EARNING",
        "bonusValue": int(cb.total_bonus_points),
        "executionDate": _fmt_ms_moment(exec_dt),
        "externalCode": settings.loyalty_external_code,
        "name": f"Кэшбэк заказ {order_name}",
    }
    bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
    bt_id = str(bt.get("id"))

    if existing:
        existing.entity_type = "customerorder"
        existing.bonustransaction_id = bt_id
        existing.bonus_value = int(cb.total_bonus_points)
        existing.cashable_total_rub = int(cb.details.get("cashable_total_rub") or 0)
        existing.status = ProcessedEventStatus.active.value
    else:
        db.add(
            ProcessedEvent(
                entity_type="customerorder",
                entity_id=order_id,
                bonustransaction_id=bt_id,
                bonus_value=int(cb.total_bonus_points),
                cashable_total_rub=int(cb.details.get("cashable_total_rub") or 0),
                status=ProcessedEventStatus.active.value,
            )
        )

    db.add(
        BonusBatch(
            agent_id=agent_id,
            original_amount=int(cb.total_bonus_points),
            remaining=int(cb.total_bonus_points),
            expires_at=(exec_dt.date() + timedelta(days=365)),
            source_order_id=order_id,
            bonustransaction_id=bt_id,
            batch_type="earn",
        )
    )

    log_bonus(
        db,
        action="EARN",
        customerorder_id=order_id,
        customerorder_name=order_name,
        agent_id=agent_id,
        agent_name=agent_name,
        tier_at_moment=int(cb.tier_used),
        bonus_amount=int(cb.total_bonus_points),
        bonustransaction_id=bt_id,
        details=cb.details,
    )
    w_points = int(settings.welcome_bonus_points)
    if is_first_enrollment and w_points > 0:
        w_exec = datetime.now() + timedelta(days=int(settings.bonus_delay_days))
        wbt_body: dict[str, Any] = {
            "bonusProgram": {"meta": bonus_program_meta},
            "agent": {"meta": _agent_meta(order)},
            "transactionType": "EARNING",
            "bonusValue": w_points,
            "executionDate": _fmt_ms_moment(w_exec),
            "externalCode": settings.loyalty_external_code,
            "name": "Приветственные баллы (первая доставка)",
        }
        wbt = client.post("/entity/bonustransaction", wbt_body, disable_webhook=True)
        wbt_id = str(wbt.get("id"))
        db.add(
            BonusBatch(
                agent_id=agent_id,
                original_amount=w_points,
                remaining=w_points,
                expires_at=(w_exec.date() + timedelta(days=365)),
                source_order_id=order_id,
                bonustransaction_id=wbt_id,
                batch_type="welcome",
            )
        )
        log_bonus(
            db,
            action="WELCOME",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_at_moment=int(member.tier),
            bonus_amount=w_points,
            bonustransaction_id=wbt_id,
            details={},
        )
    if not member.tier_locked and int(cb.tier_used) > old_tier:
        log_bonus(
            db,
            action="TIER_UP",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_at_moment=int(cb.tier_used),
            details={"from": old_tier, "to": int(cb.tier_used)},
        )

    _safe_sync_counterparty_attributes(
        client,
        settings,
        agent_id=agent_id,
        attrs={
            "tier_name": tier_name_ru(int(member.tier)),
            "annual_sum_rub": int(member.annual_sum_rub),
            "enrolled_at": member.enrolled_at.isoformat() if member.enrolled_at else None,
            "last_tier_review": member.last_tier_review_at.isoformat() if member.last_tier_review_at else None,
            "tier_locked": bool(member.tier_locked),
        },
    )


def _handle_return_full(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
    order_id = str(order.get("id"))
    order_name = str(order.get("name") or "")
    ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id))
    if not ev or ev.status != ProcessedEventStatus.active.value:
        return

    bonus_program_meta = client.fetch_bonus_program_meta()
    if ev.bonustransaction_id:
        try:
            client.delete(f"/entity/bonustransaction/{ev.bonustransaction_id}", disable_webhook=True)
        except Exception:
            # уже исполнена — откатываем списанием
            client.post(
                "/entity/bonustransaction",
                {
                    "bonusProgram": {"meta": bonus_program_meta},
                    "agent": {"meta": {"href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}", "type": "counterparty", "mediaType": "application/json"}},
                    "transactionType": "SPENDING",
                    "bonusValue": int(ev.bonus_value or 0),
                    "externalCode": settings.loyalty_external_code,
                    "name": f"Отмена кэшбэка {order_name}",
                },
                disable_webhook=True,
            )

    member = _get_or_create_member(db, agent_id)
    cashable = int(ev.cashable_total_rub or 0)
    member.annual_sum_rub = max(0, int(member.annual_sum_rub) - cashable)
    if not member.tier_locked:
        member.tier = tier_index_from_annual_sum(int(member.annual_sum_rub))

    for batch in db.scalars(select(BonusBatch).where(BonusBatch.source_order_id == order_id)).all():
        batch.remaining = 0

    ev.status = ProcessedEventStatus.cancelled.value
    log_bonus(
        db,
        action="CANCEL",
        customerorder_id=order_id,
        customerorder_name=order_name,
        agent_id=agent_id,
        agent_name=agent_name,
        bonus_amount=int(ev.bonus_value or 0),
        bonustransaction_id=ev.bonustransaction_id,
        details={"reason": "full_return"},
    )
    _safe_sync_counterparty_attributes(
        client,
        settings,
        agent_id=agent_id,
        attrs={
            "tier_name": tier_name_ru(int(member.tier)),
            "annual_sum_rub": int(member.annual_sum_rub),
            "enrolled_at": member.enrolled_at.isoformat() if member.enrolled_at else None,
            "last_tier_review": member.last_tier_review_at.isoformat() if member.last_tier_review_at else None,
            "tier_locked": bool(member.tier_locked),
        },
    )


def _sum_salesreturn_eligible_rub(sr: dict[str, Any], settings: Settings) -> int:
    pos_block = sr.get("positions") or {}
    rows = list(pos_block.get("rows") or []) if isinstance(pos_block, dict) else []
    total = 0
    for pos in rows:
        assortment = pos.get("assortment") or {}
        if not assortment:
            continue
        from app.cashback_engine import _is_in_folders

        if _is_in_folders(assortment, settings.gift_folder_ids_set):
            continue
        if _is_in_folders(assortment, settings.delivery_folder_ids_set):
            continue
        if _is_in_folders(assortment, settings.outlet_folder_ids_set):
            continue
        price = pos.get("price")
        qty = float(pos.get("quantity") or 0)
        if price is None:
            continue
        kop = int(round(float(price) * qty))
        total += kop // 100
    return int(total)


def _handle_return_partial(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
    order_id = str(order.get("id"))
    order_name = str(order.get("name") or "")
    ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id))
    if not ev or ev.status != ProcessedEventStatus.active.value:
        return

    order_href = str((order.get("meta") or {}).get("href"))
    srs = client.fetch_salesreturns_for_order(order_href)
    if not srs:
        return
    srs_sorted = sorted(srs, key=lambda x: str(x.get("updated") or x.get("moment") or ""))
    latest = srs_sorted[-1]
    latest_id = str(latest.get("id") or "")
    if not latest_id:
        return
    sr_full = client.fetch_salesreturn(latest_id)
    returned_rub = _sum_salesreturn_eligible_rub(sr_full, settings)
    cashable_orig = int(ev.cashable_total_rub or 0)
    if cashable_orig <= 0 or returned_rub <= 0:
        return
    ratio = min(1.0, returned_rub / max(1, cashable_orig))
    claw = math.ceil(int(ev.bonus_value or 0) * ratio)

    bonus_program_meta = client.fetch_bonus_program_meta()
    if claw > 0:
        client.post(
            "/entity/bonustransaction",
            {
                "bonusProgram": {"meta": bonus_program_meta},
                "agent": {"meta": {"href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}", "type": "counterparty", "mediaType": "application/json"}},
                "transactionType": "SPENDING",
                "bonusValue": int(claw),
                "externalCode": settings.loyalty_external_code,
                "name": f"Коррекция кэшбэка (частичный возврат) {order_name}",
            },
            disable_webhook=True,
        )

    member = _get_or_create_member(db, agent_id)
    member.annual_sum_rub = max(0, int(member.annual_sum_rub) - int(returned_rub))
    if not member.tier_locked:
        member.tier = tier_index_from_annual_sum(int(member.annual_sum_rub))

    new_bonus = max(0, int(ev.bonus_value or 0) - claw)
    ev.bonus_value = new_bonus
    ev.cashable_total_rub = max(0, cashable_orig - returned_rub)

    for batch in db.scalars(select(BonusBatch).where(BonusBatch.source_order_id == order_id)).all():
        batch.remaining = max(0, int(batch.remaining) - claw)

    log_bonus(
        db,
        action="RETURN_SPEND",
        customerorder_id=order_id,
        customerorder_name=order_name,
        agent_id=agent_id,
        agent_name=agent_name,
        bonus_amount=int(claw),
        details={"returned_rub": returned_rub, "ratio": ratio},
    )
    _safe_sync_counterparty_attributes(
        client,
        settings,
        agent_id=agent_id,
        attrs={
            "tier_name": tier_name_ru(int(member.tier)),
            "annual_sum_rub": int(member.annual_sum_rub),
            "enrolled_at": member.enrolled_at.isoformat() if member.enrolled_at else None,
            "last_tier_review": member.last_tier_review_at.isoformat() if member.last_tier_review_at else None,
            "tier_locked": bool(member.tier_locked),
        },
    )


def handle_bonustransaction_create(db: Session, client: MoySkladClient, bt_id: str) -> None:
    settings = get_settings()
    bt = client.get(f"/entity/bonustransaction/{bt_id}")
    if str(bt.get("externalCode") or "") == settings.loyalty_external_code:
        return
    agent = bt.get("agent") or {}
    agent_meta = agent.get("meta") or {}
    agent_id = str(agent_meta.get("id") or "")
    if not agent_id:
        return
    v = int(float(bt.get("bonusValue") or 0))
    t = str(bt.get("transactionType") or "")
    if t == "EARNING" and v > 0:
        db.add(
            BonusBatch(
                agent_id=agent_id,
                original_amount=v,
                remaining=v,
                expires_at=date.today() + timedelta(days=365),
                source_order_id=None,
                bonustransaction_id=str(bt.get("id")),
                batch_type="manual_earn",
            )
        )
        log_bonus(db, action="MANUAL_SYNC", agent_id=agent_id, bonus_amount=v, bonustransaction_id=str(bt.get("id")), details={"transactionType": t})
    elif t == "SPENDING" and v > 0:
        remaining = v
        batches = list(
            db.scalars(
                select(BonusBatch).where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0).order_by(BonusBatch.created_at.asc())
            ).all()
        )
        for b in batches:
            if remaining <= 0:
                break
            take = min(int(b.remaining), remaining)
            b.remaining = int(b.remaining) - take
            remaining -= take
        log_bonus(db, action="MANUAL_SYNC", agent_id=agent_id, bonus_amount=v, bonustransaction_id=str(bt.get("id")), details={"transactionType": t})


def dispatch_webhook_event(db: Session, client: MoySkladClient, meta: dict[str, Any]) -> None:
    etype = str(meta.get("type") or "")
    href = str(meta.get("href") or "")
    if not href:
        return
    eid = _native_id_from_meta_href(href)
    if etype == "customerorder":
        handle_customerorder_update(db, client, eid)
        return
    if etype == "bonustransaction":
        handle_bonustransaction_create(db, client, eid)
        return
