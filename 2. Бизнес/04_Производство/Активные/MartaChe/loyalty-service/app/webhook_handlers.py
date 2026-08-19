from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.bonus_log import log_bonus
from app.cashback_engine import compute_cashback_for_order
from app.config import Settings, get_settings
from app.customentity_values import customentity_value_meta
from app.field_guard import (
    BonusFieldRevert,
    TierStatusChange,
    parse_status,
    parse_tier,
    read_attr_value,
    reconcile_counterparty_bonus_fields,
    reconcile_order_active_bonuses,
    reconcile_tier_status,
    status_str_for,
)
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent, ProcessedEventStatus
from app.moysklad_client import MoySkladClient
from app.order_loyalty_view import (
    append_comment_to_log,
    build_order_attributes,
    get_order_loyalty_comment,
    make_comment_line,
    status_str,
    system_view_for_order,
    write_order_loyalty_attributes,
)
from app.spend_engine import (
    SpendResult,
    apply_spend_intent,
    compute_active_balance,
    compute_pending_balance,
    rollback_all_spend,
    unique_bt_name,
)
from app.tier_manager import tier_index_from_annual_sum, tier_name_ru


# ================= helpers =================


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


def _try_agent_id(order: dict[str, Any]) -> Optional[str]:
    agent = order.get("agent") or {}
    meta = agent.get("meta") or {}
    aid = meta.get("id") or _native_id_from_meta_href(str(meta.get("href") or ""))
    return str(aid) if aid else None


def _fmt_ms_moment(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _agent_lock(db: Session, agent_id: str) -> None:
    """PG advisory transaction lock на конкретного клиента."""
    if not agent_id:
        return
    # 32-bit signed range для pg_advisory_xact_lock(int4)
    h = abs(hash(("loyalty:" + agent_id))) % (2**31 - 1)
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:h)"), {"h": int(h)})
    except Exception:
        pass


def _get_or_create_member(db: Session, agent_id: str) -> LoyaltyMember:
    m = db.get(LoyaltyMember, agent_id)
    if m:
        return m
    m = LoyaltyMember(agent_id=agent_id, tier=0, annual_sum_rub=0, tier_locked=False, tier_floor=0, is_blocked=False)
    db.add(m)
    db.flush()
    return m


def _registration_tier_from_order(settings: Settings, order: dict[str, Any]) -> Optional[int]:
    """Новый участник регистрируется только явным выбором менеджера в заказе."""
    attrs = order.get("attributes") or []
    status_v = read_attr_value(attrs, settings.attr_order_loyalty_status)
    tier_v = read_attr_value(attrs, settings.attr_order_loyalty_tier)
    is_blocked = parse_status(status_v)
    tier_idx = parse_tier(tier_v)
    if is_blocked is False and tier_idx is not None:
        return int(tier_idx)
    return None


def _tier_floor_annual_sum(tier_idx: int) -> int:
    return (0, 20_000, 60_000)[max(0, min(2, int(tier_idx)))]


def _create_registration_welcome(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    agent_id: str,
    agent_name: str,
    tier_idx: int,
) -> None:
    points = int(settings.registration_welcome_bonus_points)
    if points <= 0:
        return
    now = datetime.now()
    bonus_program_meta = client.fetch_bonus_program_meta()
    order_name = str(order.get("name") or "")
    bt_body: dict[str, Any] = {
        "bonusProgram": {"meta": bonus_program_meta},
        "agent": {"meta": _agent_meta(order)},
        "transactionType": "EARNING",
        "bonusValue": points,
        "executionDate": _fmt_ms_moment(now),
        "externalCode": settings.loyalty_external_code,
        "name": unique_bt_name(f"Приветственные баллы ПЛ {agent_id[:8]}"),
    }
    bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
    bt_id = str(bt.get("id") or "")
    db.add(
        BonusBatch(
            agent_id=agent_id,
            original_amount=points,
            remaining=points,
            expires_at=now.date() + timedelta(days=365),
            activates_at=None,
            source_order_id=str(order.get("id") or ""),
            bonustransaction_id=bt_id or None,
            batch_type="registration_welcome",
        )
    )
    log_bonus(
        db,
        action="WELCOME",
        customerorder_id=str(order.get("id") or ""),
        customerorder_name=order_name,
        agent_id=agent_id,
        agent_name=agent_name,
        tier_at_moment=int(tier_idx),
        bonus_amount=points,
        bonustransaction_id=bt_id or None,
        details={"source": "manual_order_registration"},
    )


def _register_member_from_order(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    agent_id: str,
    agent_name: str,
    tier_idx: int,
) -> LoyaltyMember:
    today = date.today()
    m = LoyaltyMember(
        agent_id=agent_id,
        tier=int(tier_idx),
        annual_sum_rub=_tier_floor_annual_sum(int(tier_idx)),
        enrolled_at=today,
        last_tier_review_at=today,
        tier_locked=False,
        tier_floor=int(tier_idx),
        is_blocked=False,
    )
    db.add(m)
    db.flush()
    log_bonus(
        db,
        action="MANUAL_SYNC",
        customerorder_id=str(order.get("id") or ""),
        customerorder_name=str(order.get("name") or ""),
        agent_id=agent_id,
        agent_name=agent_name,
        tier_at_moment=int(tier_idx),
        details={"source": "manual_order_registration", "status": "Активен"},
    )
    _create_registration_welcome(
        db,
        client,
        settings,
        order=order,
        agent_id=agent_id,
        agent_name=agent_name,
        tier_idx=int(tier_idx),
    )
    return m


# ================= Counterparty attribute sync =================


def _build_attribute_patch(client: MoySkladClient, settings: Settings, attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Если в .env заданы UUID доп. полей контрагента — собираем массив attributes для PUT."""
    out: list[dict[str, Any]] = []
    base = settings.api_base.rstrip("/")

    def ameta(attr_uuid: str) -> dict[str, Any]:
        return {
            "href": f"{base}/entity/counterparty/metadata/attributes/{attr_uuid}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        }

    mapping: dict[str, Any] = {
        settings.attr_loyalty_tier: attrs.get("tier_name"),
        settings.attr_loyalty_status: attrs.get("status"),
        settings.attr_active_bonuses: attrs.get("active_bonuses"),
        settings.attr_pending_bonuses: attrs.get("pending_bonuses"),
        # legacy:
        settings.attr_annual_sum_rub: attrs.get("annual_sum_rub"),
        settings.attr_enrolled_at: attrs.get("enrolled_at"),
        settings.attr_last_tier_review: attrs.get("last_tier_review"),
        settings.attr_tier_locked: attrs.get("tier_locked"),
    }
    if settings.attr_loyalty_tier and mapping.get(settings.attr_loyalty_tier):
        meta = customentity_value_meta(client, settings.attr_tier_customentity_id, str(mapping[settings.attr_loyalty_tier]))
        if meta:
            mapping[settings.attr_loyalty_tier] = {"meta": meta}
    if settings.attr_loyalty_status and mapping.get(settings.attr_loyalty_status):
        meta = customentity_value_meta(client, settings.attr_status_customentity_id, str(mapping[settings.attr_loyalty_status]))
        if meta:
            mapping[settings.attr_loyalty_status] = {"meta": meta}
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
        # Не блокируем основной поток, если доп. поля ещё не настроены / типы не совпали
        return


def _sync_counterparty_attributes(
    client: MoySkladClient,
    settings: Settings,
    *,
    agent_id: str,
    attrs: dict[str, Any],
) -> None:
    patch = _build_attribute_patch(client, settings, attrs)
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


def _build_member_attrs_for_cp(
    db: Session,
    settings: Settings,
    member: LoyaltyMember,
) -> dict[str, Any]:
    """Сборка значений для синка карточки контрагента."""
    active = compute_active_balance(db, member.agent_id)
    pending = compute_pending_balance(db, member.agent_id)
    return {
        "tier_name": tier_name_ru(int(member.tier)),
        "status": status_str_for(bool(member.is_blocked)),
        "active_bonuses": int(active),
        "pending_bonuses": int(pending),
        "annual_sum_rub": int(member.annual_sum_rub),
        "enrolled_at": member.enrolled_at.isoformat() if member.enrolled_at else None,
        "last_tier_review": member.last_tier_review_at.isoformat() if member.last_tier_review_at else None,
        "tier_locked": bool(member.tier_locked),
    }


def _save_last_synced_cp_attrs(member: LoyaltyMember, settings: Settings, attrs: dict[str, Any]) -> None:
    member.last_synced_cp_attrs = {
        settings.attr_loyalty_tier: attrs.get("tier_name") if settings.attr_loyalty_tier else None,
        settings.attr_loyalty_status: attrs.get("status") if settings.attr_loyalty_status else None,
        settings.attr_active_bonuses: attrs.get("active_bonuses") if settings.attr_active_bonuses else None,
        settings.attr_pending_bonuses: attrs.get("pending_bonuses") if settings.attr_pending_bonuses else None,
    }


# ================= Customer order: единый upsert =================


def handle_customerorder_event(
    db: Session,
    client: MoySkladClient,
    order_id: str,
    action: str = "UPDATE",
) -> None:
    """Диспетчер CREATE/UPDATE/DELETE."""
    a = (action or "").upper()
    if a == "DELETE":
        handle_customerorder_delete(db, client, order_id)
        return
    handle_customerorder_upsert(db, client, order_id)


def handle_customerorder_upsert(db: Session, client: MoySkladClient, order_id: str) -> None:
    settings = get_settings()
    try:
        order = client.fetch_customerorder(order_id)
    except Exception:
        return
    agent_id = _try_agent_id(order)
    if not agent_id:
        # розница без контрагента — пропускаем
        return
    _agent_lock(db, agent_id)

    agent_name = str((order.get("agent") or {}).get("name") or "")
    member = db.get(LoyaltyMember, agent_id)
    comment_lines: list[str] = []
    if not member:
        registration_tier = _registration_tier_from_order(settings, order)
        if registration_tier is None:
            # Новый клиент не включается в ПЛ автоматически.
            return
        member = _register_member_from_order(
            db,
            client,
            settings,
            order=order,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_idx=int(registration_tier),
        )
        comment_lines.append(
            f"Клиент зарегистрирован в ПЛ: Активен / {tier_name_ru(int(registration_tier))}; "
            f"+{int(settings.registration_welcome_bonus_points)} приветственных бонусов"
        )
    order_name = str(order.get("name") or "")

    # 1) Reconcile Уровень/Статус (что менеджер мог поменять в заказе)
    inc_tier_v = read_attr_value(order.get("attributes") or [], settings.attr_order_loyalty_tier)
    inc_status_v = read_attr_value(order.get("attributes") or [], settings.attr_order_loyalty_status)
    tch = reconcile_tier_status(member=member, incoming_tier_value=inc_tier_v, incoming_status_value=inc_status_v)

    if tch.accept_tier_idx is not None:
        from_idx = int(member.tier)
        member.tier = int(tch.accept_tier_idx)
        member.tier_floor = max(int(member.tier_floor or 0), int(tch.accept_tier_idx))
        log_bonus(
            db,
            action="MANUAL_TIER_UP",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            tier_at_moment=int(member.tier),
            details={"from": from_idx, "to": int(member.tier), "source": "order"},
        )
        comment_lines.append(f"Уровень повышен вручную: {tier_name_ru(from_idx)} → {tier_name_ru(int(member.tier))}")

    if tch.revert_tier:
        log_bonus(
            db,
            action="REVERT_TIER_DOWN",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            tier_at_moment=int(member.tier),
            details={"reason": tch.revert_reason, "source": "order"},
        )
        comment_lines.append(tch.revert_reason or "Понижение уровня вручную запрещено")

    if tch.accept_is_blocked is not None:
        was = bool(member.is_blocked)
        member.is_blocked = bool(tch.accept_is_blocked)
        log_bonus(
            db,
            action="BLOCK" if member.is_blocked else "UNBLOCK",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            details={"from": was, "to": bool(member.is_blocked), "source": "order"},
        )
        comment_lines.append("Клиент заблокирован" if member.is_blocked else "Клиент активирован")

    # 2) Основной статус заказа (доставка / возврат)
    state = _order_state_name(order)
    if state == settings.status_delivered:
        _handle_delivered(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)
    elif state == settings.status_return_full:
        _handle_return_full(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)
    elif state == settings.status_return_partial:
        _handle_return_partial(db, client, settings, order=order, agent_id=agent_id, agent_name=agent_name)

    # 3) ProcessedEvent для трекинга spend (создаём всегда)
    ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id))
    if not ev:
        ev = ProcessedEvent(
            entity_type="customerorder",
            entity_id=order_id,
            status=ProcessedEventStatus.active.value,
            spent_amount=0,
            last_spend_intent=0,
            spend_pending=False,
        )
        db.add(ev)
        db.flush()

    # 4) Apply spend intent (manual SPEND)
    try:
        spend_res = apply_spend_intent(db, client, settings, order=order, member=member, ev=ev)
    except Exception as e:
        comment_lines.append(f"Ошибка списания: {e!s}"[:200])
        spend_res = SpendResult(intent=0, cap=int(ev.spent_amount or 0), capped_by=None, delta=0, spent_before=int(ev.spent_amount or 0), spent_after=int(ev.spent_amount or 0))

    if spend_res.delta != 0:
        if spend_res.cap > 0 and spend_res.delta > 0:
            line = f"Списано: {spend_res.cap} ₽ (intent {spend_res.intent})"
        elif spend_res.cap == 0 and spend_res.spent_before > 0:
            line = f"Списание отменено (intent {spend_res.intent})"
        elif spend_res.delta < 0:
            line = f"Списано: {spend_res.cap} ₽ (было {spend_res.spent_before})"
        else:
            line = f"Списано: {spend_res.cap} ₽"
        if spend_res.capped_by == "limit_30":
            line += f"; обрезано до {settings.loyalty_spend_percent_limit}% от заказа"
        elif spend_res.capped_by == "balance":
            line += "; обрезано по балансу"
        elif spend_res.capped_by == "blocked":
            line += "; клиент заблокирован"
        comment_lines.append(line)
    elif spend_res.intent != int(ev.last_spend_intent or 0):
        # intent сохранили, но cap не изменился (например, повторно тот же)
        pass

    # 5) Active balance + защита поля «Активно бонусов»
    active_balance = compute_active_balance(db, agent_id)
    revert_active_to = reconcile_order_active_bonuses(
        settings=settings,
        order_attrs=order.get("attributes") or [],
        expected_active=int(active_balance),
    )
    if revert_active_to is not None and ev.last_synced_attrs:
        prev = ev.last_synced_attrs.get(settings.attr_order_active_bonuses)
        # если значение в МС отличается и от prev — менеджер тронул
        cur = read_attr_value(order.get("attributes") or [], settings.attr_order_active_bonuses)
        if cur is not None and prev is not None and int(cur) != int(prev or 0):
            log_bonus(
                db,
                action="REVERT_BALANCE_FIELD",
                customerorder_id=order_id,
                customerorder_name=order_name,
                agent_id=agent_id,
                details={"field": "order.active_bonuses", "from": cur, "to": int(active_balance)},
            )
            comment_lines.append(f"Активно бонусов: ручная правка отклонена ({cur} → {int(active_balance)})")

    # 6) Compose комментарий + write attributes
    prev_log = get_order_loyalty_comment(order, settings)
    log_text = prev_log
    for ln in comment_lines:
        log_text = append_comment_to_log(settings, log_text, make_comment_line(settings, ln))

    patch_attrs = build_order_attributes(
        settings,
        client=client,
        member=member,
        spent_amount=int(ev.spent_amount or 0),
        active_balance=int(active_balance),
        comment_log=log_text,
    )
    try:
        write_order_loyalty_attributes(client, settings, order_id=order_id, patch_attrs=patch_attrs)
        ev.last_synced_attrs = system_view_for_order(
            settings,
            member=member,
            spent_amount=int(ev.spent_amount or 0),
            active_balance=int(active_balance),
        )
    except Exception:
        pass

    # 7) Sync контрагента (включая Активные / Pending / Уровень / Статус)
    cp_attrs = _build_member_attrs_for_cp(db, settings, member)
    _safe_sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=cp_attrs)
    _save_last_synced_cp_attrs(member, settings, cp_attrs)


# ===== обратная совместимость для scheduler.py / тестов =====


def handle_customerorder_update(db: Session, client: MoySkladClient, order_id: str) -> None:
    handle_customerorder_upsert(db, client, order_id)


# ================= DELETE =================


def handle_customerorder_delete(db: Session, client: MoySkladClient, order_id: str) -> None:
    settings = get_settings()
    ev = db.scalar(select(ProcessedEvent).where(ProcessedEvent.entity_id == order_id))
    if not ev:
        return
    agent_id = ""
    # пытаемся достать agent через batches (чтобы не дёргать API заказа, которого уже нет)
    any_batch = db.scalar(select(BonusBatch).where(BonusBatch.source_order_id == order_id))
    if any_batch:
        agent_id = str(any_batch.agent_id)
    if not agent_id:
        # без agent_id корректно откатить не сможем
        ev.status = ProcessedEventStatus.cancelled.value
        log_bonus(db, action="CANCEL", customerorder_id=order_id, details={"reason": "deleted_no_agent"})
        return

    _agent_lock(db, agent_id)
    member = db.get(LoyaltyMember, agent_id)
    if not member:
        return

    # 1) Откат manual SPEND (если был)
    try:
        rollback_all_spend(db, client, settings, member=member, ev=ev)
    except Exception:
        pass

    # 2) Откат EARNING (cashback) — как полный возврат
    if ev.bonustransaction_id and ev.status == ProcessedEventStatus.active.value:
        bonus_program_meta = client.fetch_bonus_program_meta()
        try:
            client.delete(f"/entity/bonustransaction/{ev.bonustransaction_id}", disable_webhook=True)
        except Exception:
            try:
                client.post(
                    "/entity/bonustransaction",
                    {
                        "bonusProgram": {"meta": bonus_program_meta},
                        "agent": {"meta": {"href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}", "type": "counterparty", "mediaType": "application/json"}},
                        "transactionType": "SPENDING",
                        "bonusValue": int(ev.bonus_value or 0),
                        "externalCode": settings.loyalty_external_code,
                        "name": unique_bt_name("Отмена кэшбэка (удалён заказ)"),
                    },
                    disable_webhook=True,
                )
            except Exception:
                pass

        cashable = int(ev.cashable_total_rub or 0)
        member.annual_sum_rub = max(0, int(member.annual_sum_rub) - cashable)
        if not member.tier_locked:
            member.tier = max(tier_index_from_annual_sum(int(member.annual_sum_rub)), int(member.tier_floor or 0))

        for batch in db.scalars(select(BonusBatch).where(BonusBatch.source_order_id == order_id)).all():
            batch.remaining = 0

    ev.status = ProcessedEventStatus.cancelled.value
    log_bonus(
        db,
        action="CANCEL",
        customerorder_id=order_id,
        agent_id=agent_id,
        bonus_amount=int(ev.bonus_value or 0),
        bonustransaction_id=ev.bonustransaction_id,
        details={"reason": "order_deleted"},
    )

    cp_attrs = _build_member_attrs_for_cp(db, settings, member)
    _safe_sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=cp_attrs)
    _save_last_synced_cp_attrs(member, settings, cp_attrs)


# ================= Доставка / возвраты (внутренние) =================


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
    if existing and existing.status == ProcessedEventStatus.active.value and existing.bonustransaction_id:
        return

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        return
    if member.is_blocked:
        # Клиент заблокирован — не начисляем кэшбэк, но запись о доставке создадим
        if existing:
            existing.status = ProcessedEventStatus.active.value
            existing.bonus_value = 0
            existing.cashable_total_rub = 0
        else:
            db.add(
                ProcessedEvent(
                    entity_type="customerorder",
                    entity_id=order_id,
                    bonustransaction_id=None,
                    bonus_value=0,
                    cashable_total_rub=0,
                    status=ProcessedEventStatus.active.value,
                )
            )
        log_bonus(
            db,
            action="EARN",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            agent_name=agent_name,
            bonus_amount=0,
            details={"skipped": "client_blocked"},
        )
        return

    is_first_enrollment = member.enrolled_at is None
    if is_first_enrollment:
        member.enrolled_at = date.today()

    # Учитываем ручное списание бонусов как «оплачено бонусами» (в нашем МС нет
    # штатного поля payedBonus), и не считаем нашу надбавку discount как реальную скидку.
    manually_spent = int((existing.spent_amount or 0) if existing else 0)
    extra_disc = (existing.spend_extra_discounts if existing else None) or {}
    cb = compute_cashback_for_order(
        order=order,
        settings=settings,
        current_annual_rub=int(member.annual_sum_rub),
        current_tier_locked=bool(member.tier_locked),
        current_tier_index=int(member.tier),
        manually_spent_rub=manually_spent,
        extra_discount_per_pos=extra_disc,
    )

    old_tier = int(member.tier)
    member.annual_sum_rub = int(cb.provisional_annual_rub)
    if not member.tier_locked:
        member.tier = max(int(cb.tier_used), int(member.tier_floor or 0))

    bonus_program_meta = client.fetch_bonus_program_meta()
    exec_dt = datetime.now() + timedelta(days=int(settings.bonus_delay_days))

    bt_body: dict[str, Any] = {
        "bonusProgram": {"meta": bonus_program_meta},
        "agent": {"meta": _agent_meta(order)},
        "transactionType": "EARNING",
        "bonusValue": int(cb.total_bonus_points),
        "executionDate": _fmt_ms_moment(exec_dt),
        "externalCode": settings.loyalty_external_code,
        "name": unique_bt_name(f"Кэшбэк заказ {order_name}"),
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
            activates_at=exec_dt,
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
    if not member.tier_locked and int(member.tier) > old_tier:
        log_bonus(
            db,
            action="TIER_UP",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_at_moment=int(member.tier),
            details={"from": old_tier, "to": int(member.tier)},
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
            client.post(
                "/entity/bonustransaction",
                {
                    "bonusProgram": {"meta": bonus_program_meta},
                    "agent": {"meta": {"href": f"{settings.api_base.rstrip('/')}/entity/counterparty/{agent_id}", "type": "counterparty", "mediaType": "application/json"}},
                    "transactionType": "SPENDING",
                    "bonusValue": int(ev.bonus_value or 0),
                    "externalCode": settings.loyalty_external_code,
                    "name": unique_bt_name(f"Отмена кэшбэка {order_name}"),
                },
                disable_webhook=True,
            )

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        return

    # ВОЗВРАТ SPEND (manual): если на заказе было списание — вернуть бонусы клиенту
    try:
        rollback_all_spend(db, client, settings, member=member, ev=ev, order_name=order_name)
    except Exception:
        pass

    cashable = int(ev.cashable_total_rub or 0)
    member.annual_sum_rub = max(0, int(member.annual_sum_rub) - cashable)
    if not member.tier_locked:
        member.tier = max(tier_index_from_annual_sum(int(member.annual_sum_rub)), int(member.tier_floor or 0))

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
                "name": unique_bt_name(f"Коррекция кэшбэка (частичный возврат) {order_name}"),
            },
            disable_webhook=True,
        )

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        return
    member.annual_sum_rub = max(0, int(member.annual_sum_rub) - int(returned_rub))
    if not member.tier_locked:
        member.tier = max(tier_index_from_annual_sum(int(member.annual_sum_rub)), int(member.tier_floor or 0))

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


# ================= Counterparty UPDATE =================


def handle_counterparty_update(db: Session, client: MoySkladClient, agent_id: str) -> None:
    settings = get_settings()
    if not agent_id:
        return
    _agent_lock(db, agent_id)
    try:
        cp = client.fetch_counterparty(agent_id)
    except Exception:
        return
    cp_attrs = cp.get("attributes") or []

    member = db.get(LoyaltyMember, agent_id)
    if not member:
        # Карточка контрагента сама по себе не регистрирует клиента в ПЛ.
        return

    # 1) Уровень / Статус
    inc_tier_v = read_attr_value(cp_attrs, settings.attr_loyalty_tier)
    inc_status_v = read_attr_value(cp_attrs, settings.attr_loyalty_status)
    tch = reconcile_tier_status(member=member, incoming_tier_value=inc_tier_v, incoming_status_value=inc_status_v)

    if tch.accept_tier_idx is not None:
        from_idx = int(member.tier)
        member.tier = int(tch.accept_tier_idx)
        member.tier_floor = max(int(member.tier_floor or 0), int(tch.accept_tier_idx))
        log_bonus(
            db,
            action="MANUAL_TIER_UP",
            agent_id=agent_id,
            tier_at_moment=int(member.tier),
            details={"from": from_idx, "to": int(member.tier), "source": "counterparty"},
        )
    if tch.revert_tier:
        log_bonus(
            db,
            action="REVERT_TIER_DOWN",
            agent_id=agent_id,
            tier_at_moment=int(member.tier),
            details={"reason": tch.revert_reason, "source": "counterparty"},
        )
    if tch.accept_is_blocked is not None:
        was = bool(member.is_blocked)
        member.is_blocked = bool(tch.accept_is_blocked)
        log_bonus(
            db,
            action="BLOCK" if member.is_blocked else "UNBLOCK",
            agent_id=agent_id,
            details={"from": was, "to": bool(member.is_blocked), "source": "counterparty"},
        )

    # 2) Бонусные поля — откат, если менеджер тронул
    expected_active = compute_active_balance(db, agent_id)
    expected_pending = compute_pending_balance(db, agent_id)
    bf = reconcile_counterparty_bonus_fields(
        settings=settings,
        cp_attrs=cp_attrs,
        expected_active=expected_active,
        expected_pending=expected_pending,
    )
    if bf.notes:
        for note in bf.notes:
            log_bonus(
                db,
                action="REVERT_BALANCE_FIELD",
                agent_id=agent_id,
                details={"note": note},
            )

    # 3) Полный sync (источник истины — наш расчёт)
    cp_target = _build_member_attrs_for_cp(db, settings, member)
    _safe_sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=cp_target)
    _save_last_synced_cp_attrs(member, settings, cp_target)


# ================= bonustransaction CREATE (как было) =================


def handle_bonustransaction_create(db: Session, client: MoySkladClient, bt_id: str) -> None:
    settings = get_settings()
    bt = client.get(f"/entity/bonustransaction/{bt_id}")
    if str(bt.get("externalCode") or "") == settings.loyalty_external_code:
        return
    agent = bt.get("agent") or {}
    agent_meta = agent.get("meta") or {}
    agent_id = str(agent_meta.get("id") or _native_id_from_meta_href(str(agent_meta.get("href") or "")))
    if not agent_id:
        return
    _agent_lock(db, agent_id)
    member = db.get(LoyaltyMember, agent_id)
    if not member:
        # Ручная бонусная операция не является регистрацией в ПЛ.
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
                activates_at=None,
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

    # Синкаем attributes контрагента, чтобы новый баланс отразился сразу
    cp_target = _build_member_attrs_for_cp(db, settings, member)
    _safe_sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=cp_target)
    _save_last_synced_cp_attrs(member, settings, cp_target)


# ================= dispatcher =================


def dispatch_webhook_event(db: Session, client: MoySkladClient, meta: dict[str, Any], action: str = "UPDATE") -> None:
    etype = str(meta.get("type") or "")
    href = str(meta.get("href") or "")
    if not href:
        return
    eid = _native_id_from_meta_href(href)
    if etype == "customerorder":
        handle_customerorder_event(db, client, eid, action=action)
        return
    if etype == "bonustransaction":
        handle_bonustransaction_create(db, client, eid)
        return
    if etype == "counterparty":
        handle_counterparty_update(db, client, eid)
        return
