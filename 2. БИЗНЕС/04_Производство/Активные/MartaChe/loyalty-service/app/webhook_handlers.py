from __future__ import annotations

import hashlib
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger("loyalty.webhook")

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


def _ms_tz() -> timezone:
    """Часовой пояс аккаунта МойСклад (даты в API приходят без смещения)."""
    return timezone(timedelta(minutes=int(get_settings().loyalty_comment_tz_offset_minutes)))


def _ms_now() -> datetime:
    """Текущее время в поясе МойСклад — для `executionDate` и `moment`."""
    return datetime.now(_ms_tz()).replace(tzinfo=None)


def _parse_ms_moment(raw: Any) -> Optional[datetime]:
    """Дата из API МойСклад → aware datetime в UTC."""
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt, width in (("%Y-%m-%d %H:%M:%S.%f", 26), ("%Y-%m-%d %H:%M:%S", 19)):
        try:
            naive = datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=_ms_tz()).astimezone(timezone.utc)
    return None


def _cashback_activation_at(
    order: dict[str, Any], delay_days: int, now: Optional[datetime] = None
) -> Optional[datetime]:
    """Когда кэшбэк становится активным (aware UTC) или None, если уже активен.

    Отсрочку считаем от даты заказа, а не от момента обработки: иначе исторический
    «Доставлен» снова уходит в «Ожидают активации» ещё на 15 дней.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    base = _parse_ms_moment(order.get("moment") or order.get("created")) or now
    due = base + timedelta(days=max(0, int(delay_days)))
    return None if due <= now else due


def _agent_lock(db: Session, agent_id: str) -> None:
    """PG advisory transaction lock на конкретного клиента.

    Важно: нельзя использовать встроенный hash() — он рандомизируется между
    процессами/воркерами, из‑за этого параллельные webhook'и не сериализуются.
    """
    if not agent_id:
        return
    digest = hashlib.md5(f"loyalty:{agent_id}".encode("utf-8")).hexdigest()
    h = int(digest[:8], 16) % (2**31 - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:h)"), {"h": int(h)})


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


def _has_earn_batch(db: Session, order_id: str) -> bool:
    """Кэшбэк по заказу уже посчитан?

    Признак — батч `earn`, а не `bonustransaction_id` в событии: операция в МойСклад
    создаётся планировщиком только в день активации, до этого поле пустое.
    """
    row = db.scalar(
        select(BonusBatch.id).where(
            BonusBatch.source_order_id == order_id,
            BonusBatch.batch_type == "earn",
        ).limit(1)
    )
    return row is not None


def _has_registration_welcome(db: Session, agent_id: str) -> bool:
    row = db.scalar(
        select(BonusBatch.id).where(
            BonusBatch.agent_id == agent_id,
            BonusBatch.batch_type == "registration_welcome",
        ).limit(1)
    )
    return row is not None


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
    # Идемпотентность: параллельные webhook'и не должны плодить welcome BT в МС.
    if _has_registration_welcome(db, agent_id):
        log.info("welcome skip: already exists agent=%s", agent_id)
        return
    now = _ms_now()
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
    db.flush()
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
    existing = db.get(LoyaltyMember, agent_id)
    if existing:
        _create_registration_welcome(
            db,
            client,
            settings,
            order=order,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_idx=int(existing.tier),
        )
        return existing

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
    try:
        # Savepoint: при гонке PK не валим всю транзакцию webhook'а.
        with db.begin_nested():
            db.add(m)
            db.flush()
    except IntegrityError:
        existing = db.get(LoyaltyMember, agent_id)
        if not existing:
            raise
        _create_registration_welcome(
            db,
            client,
            settings,
            order=order,
            agent_id=agent_id,
            agent_name=agent_name,
            tier_idx=int(existing.tier),
        )
        return existing

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
) -> bool:
    """True — карточка в МС обновлена. Ошибку не пробрасываем, но и не прячем в тишину."""
    try:
        _sync_counterparty_attributes(client, settings, agent_id=agent_id, attrs=attrs)
        return True
    except Exception as exc:
        # Не блокируем основной поток, если доп. поля ещё не настроены / типы не совпали
        log.warning("sync cp attrs failed agent=%s: %s", agent_id, exc)
        return False


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


def _sync_member_card(
    client: MoySkladClient,
    settings: Settings,
    member: LoyaltyMember,
    attrs: dict[str, Any],
) -> bool:
    """Обновляет карточку в МС и запоминает результат.

    След пишем только при успехе: иначе упавший синк выглядел бы применённым
    и карточка залипала бы пустой навсегда.
    """
    if not _safe_sync_counterparty_attributes(client, settings, agent_id=member.agent_id, attrs=attrs):
        return False
    _save_last_synced_cp_attrs(member, settings, attrs)
    return True


def _cp_attrs_snapshot(settings: Settings, attrs: dict[str, Any]) -> dict[str, Any]:
    """Срез значимых полей карточки — по нему сверяем, отстала ли карточка в МС."""
    return {
        settings.attr_loyalty_tier: attrs.get("tier_name") if settings.attr_loyalty_tier else None,
        settings.attr_loyalty_status: attrs.get("status") if settings.attr_loyalty_status else None,
        settings.attr_active_bonuses: attrs.get("active_bonuses") if settings.attr_active_bonuses else None,
        settings.attr_pending_bonuses: attrs.get("pending_bonuses") if settings.attr_pending_bonuses else None,
    }


def _save_last_synced_cp_attrs(member: LoyaltyMember, settings: Settings, attrs: dict[str, Any]) -> None:
    member.last_synced_cp_attrs = _cp_attrs_snapshot(settings, attrs)


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
        elif spend_res.capped_by == "exempt":
            line += "; исключение: лимит 30% снят"
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
    except Exception as exc:
        # Раньше молчали — из‑за этого в заказе не появлялись «Активно бонусов»/Уровень.
        log.exception("write_order_loyalty_attributes failed order=%s: %s", order_id, exc)
        comment_lines.append(f"Ошибка записи полей ПЛ в заказ: {exc!s}"[:200])

    # 7) Sync контрагента (включая Активные / Pending / Уровень / Статус)
    cp_attrs = _build_member_attrs_for_cp(db, settings, member)
    _sync_member_card(client, settings, member, cp_attrs)


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
    _sync_member_card(client, settings, member, cp_attrs)


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
    if existing and existing.status == ProcessedEventStatus.active.value and _has_earn_batch(db, order_id):
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
            db.flush()
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

    # Тариф МойСклад не даёт executionDate в будущем (ошибка 62000).
    # Свежий заказ: батч сразу, операция в МС в день активации.
    # Исторический: срок 15 дней уже вышел — начисляем сразу активными.
    now = datetime.now(timezone.utc)
    activates_at = _cashback_activation_at(order, int(settings.bonus_delay_days), now=now)
    bt_id: Optional[str] = None
    if activates_at is None and int(cb.total_bonus_points) > 0:
        bonus_program_meta = client.fetch_bonus_program_meta()
        bt = client.post(
            "/entity/bonustransaction",
            {
                "bonusProgram": {"meta": bonus_program_meta},
                "agent": {"meta": _agent_meta(order)},
                "transactionType": "EARNING",
                "bonusValue": int(cb.total_bonus_points),
                "executionDate": _fmt_ms_moment(_ms_now()),
                "externalCode": settings.loyalty_external_code,
                "name": unique_bt_name(f"Кэшбэк заказ {order_name}"),
            },
            disable_webhook=True,
        )
        bt_id = str(bt.get("id") or "") or None

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

    expire_from = (activates_at or now).date()
    db.add(
        BonusBatch(
            agent_id=agent_id,
            original_amount=int(cb.total_bonus_points),
            remaining=int(cb.total_bonus_points),
            expires_at=expire_from + timedelta(days=365),
            activates_at=activates_at,
            source_order_id=order_id,
            bonustransaction_id=bt_id,
            batch_type="earn",
        )
    )
    # autoflush в сессии выключен: без flush следующий select не увидит эти строки и
    # вставит второе processed_events по тому же заказу (unique violation), а поля
    # «Ожидают активации» посчитаются без нового батча.
    db.flush()

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
        details={
            **cb.details,
            "activates_at": activates_at.isoformat() if activates_at else None,
            "ms_transaction": "deferred" if activates_at else "immediate",
        },
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
        from app.cashback_engine import _is_in_folders, _line_amount_rub, orig_discount_percent

        if _is_in_folders(assortment, settings.gift_folder_ids_set):
            continue
        if _is_in_folders(assortment, settings.delivery_folder_ids_set):
            continue
        if _is_in_folders(assortment, settings.outlet_folder_ids_set):
            continue
        if pos.get("price") is None:
            continue
        # Та же база, что при начислении: оплаченная сумма строки, а не цена до скидки.
        total += _line_amount_rub(pos, orig_discount_percent(pos))
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

    # Если операция в МС ещё не материализована планировщиком, отзывать в МС нечего —
    # достаточно уменьшить наши батчи, иначе уведём баланс МС в минус.
    if claw > 0 and ev.bonustransaction_id:
        bonus_program_meta = client.fetch_bonus_program_meta()
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
    _sync_member_card(client, settings, member, cp_target)


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
    _sync_member_card(client, settings, member, cp_target)


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
