"""
Idempotent движок ручного списания бонусов в заказе покупателя.

Контракт:
- Менеджер пишет в поле «Списано бонусов» желаемую сумму (intent).
- Система обрезает до cap = min(intent, active_balance, 30% * order_total).
- Если cap > spent_amount (что уже списано в БД) → создаём SPENDING на дельту, FIFO.
- Если cap < spent_amount → создаём EARNING-обратку (refund) на дельту.
- Все наши bonustransaction → с X-Lognex-WebHook-Disable=1 (без эха).

Правила-ловушки:
- Заблокированный клиент (member.is_blocked): cap = 0, в комментарий.
- Заказ без agent: ранний выход (вообще ничего не делаем).
- Идемпотентность: при повторном webhook'е с тем же intent — no-op.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bonus_log import log_bonus
from app.config import Settings
from app.models import BonusBatch, LoyaltyMember, ProcessedEvent
from app.moysklad_client import MoySkladClient


def unique_bt_name(base: str) -> str:
    """МС требует уникальный `name` у `bonustransaction` (constraint на 412/3006).
    Прилепляем к читаемому базовому имени короткий неконфликтный суффикс."""
    base = (base or "").strip() or "Бонусная операция"
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    return f"{base} [{suffix}]"


@dataclass
class SpendResult:
    intent: int                       # что менеджер вписал
    cap: int                          # эффективная сумма списания (после ограничений)
    capped_by: Optional[str]          # None | "balance" | "limit_30" | "blocked" | "no_agent"
    delta: int                        # cap - spent_before (со знаком)
    spent_before: int
    spent_after: int
    bt_id: Optional[str] = None       # id созданной нами bonustransaction (если была)
    refund_bt_id: Optional[str] = None
    comments: list[str] = field(default_factory=list)


def order_total_rub(order: dict[str, Any]) -> int:
    """sum суммы заказа в рублях (МС хранит в копейках)."""
    val = order.get("sum")
    if val is None:
        return 0
    try:
        return max(0, int(round(float(val) / 100.0)))
    except (TypeError, ValueError):
        return 0


def _order_positions(order: dict[str, Any]) -> list[dict[str, Any]]:
    pos_block = order.get("positions") or {}
    if isinstance(pos_block, dict):
        return list(pos_block.get("rows") or [])
    if isinstance(pos_block, list):
        return list(pos_block)
    return []


def _restore_orig_discount(pos: dict[str, Any], prev_extra_pct: float) -> float:
    """Возвращает «исходный» discount позиции (текущий минус ранее добавленный нами)."""
    cur = float(pos.get("discount") or 0.0)
    orig = cur - max(0.0, float(prev_extra_pct or 0.0))
    if orig < 0:
        return 0.0
    if orig > 100:
        return 100.0
    return orig


def _line_amount_kop(pos: dict[str, Any], discount_pct: float) -> float:
    price = float(pos.get("price") or 0.0)  # копейки за единицу
    qty = float(pos.get("quantity") or 0.0)
    return max(0.0, price * qty * (1.0 - max(0.0, min(100.0, discount_pct)) / 100.0))


def apply_spend_discount_to_positions(
    client: MoySkladClient,
    *,
    order: dict[str, Any],
    cap_rub: int,
    prev_per_pos: Optional[dict[str, Any]],
) -> dict[str, float]:
    """
    Применяет ровно `cap_rub` рублей скидки к позициям заказа за счёт поля `discount`.
    
    Распределяет сумму скидки пропорционально вкладу позиции в общий итог
    (после восстановления «исходного» discount без нашего вклада).
    
    Возвращает новый словарь {position_id: extra_pct} — нашу часть скидки на каждую
    позицию для последующих идемпотентных пересчётов.
    """
    order_id = str(order.get("id") or "")
    if not order_id:
        return {}
    positions = _order_positions(order)
    prev_per_pos = dict(prev_per_pos or {})

    # 1) Восстанавливаем «исходный» discount у каждой позиции.
    orig_discounts: dict[str, float] = {}
    line_amounts: dict[str, float] = {}
    total_kop = 0.0
    for pos in positions:
        pid = str(pos.get("id") or "")
        if not pid:
            continue
        prev_extra = float(prev_per_pos.get(pid) or 0.0)
        orig = _restore_orig_discount(pos, prev_extra)
        orig_discounts[pid] = orig
        amount = _line_amount_kop(pos, orig)
        line_amounts[pid] = amount
        total_kop += amount

    cap_kop = max(0, int(cap_rub)) * 100
    # Не можем дать скидку больше, чем итог по «исходному» состоянию.
    if cap_kop > total_kop:
        cap_kop = int(total_kop)

    # 2) Считаем новый discount для каждой позиции.
    new_per_pos: dict[str, float] = {}
    target_discounts: dict[str, float] = {}
    for pos in positions:
        pid = str(pos.get("id") or "")
        if not pid:
            continue
        price = float(pos.get("price") or 0.0)
        qty = float(pos.get("quantity") or 0.0)
        gross = price * qty
        if gross <= 0:
            target_discounts[pid] = orig_discounts.get(pid, 0.0)
            continue
        if cap_kop <= 0 or total_kop <= 0:
            extra_amount = 0.0
        else:
            share = line_amounts[pid] / total_kop
            extra_amount = cap_kop * share
            if extra_amount > line_amounts[pid]:
                extra_amount = line_amounts[pid]
        new_line = max(0.0, line_amounts[pid] - extra_amount)
        new_disc = (1.0 - new_line / gross) * 100.0
        new_disc = max(0.0, min(100.0, new_disc))
        target_discounts[pid] = round(new_disc, 4)
        # Наш вклад = новый - исходный (для того, чтобы при следующем upsert уметь восстановить orig).
        extra_pct = max(0.0, round(new_disc - orig_discounts.get(pid, 0.0), 4))
        if extra_pct > 0:
            new_per_pos[pid] = extra_pct

    # 3) Шлём PUT по каждой позиции, у которой discount изменился (с учётом небольшого порога).
    for pos in positions:
        pid = str(pos.get("id") or "")
        if not pid:
            continue
        cur_disc = float(pos.get("discount") or 0.0)
        new_disc = target_discounts.get(pid, cur_disc)
        if abs(new_disc - cur_disc) < 0.0005:
            continue
        try:
            client.put(
                f"/entity/customerorder/{order_id}/positions/{pid}",
                {"discount": float(new_disc)},
                disable_webhook=True,
            )
        except Exception:
            # одна позиция не должна валить весь процесс
            continue

    return new_per_pos


def compute_active_balance(db: Session, agent_id: str, *, now: Optional[datetime] = None) -> int:
    """Сумма remaining по активным (activates_at IS NULL OR activates_at <= now) батчам, не сгоревшим."""
    if now is None:
        now = datetime.now()
    today = now.date()
    total = 0
    for b in db.scalars(
        select(BonusBatch).where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0)
    ).all():
        if b.expires_at and b.expires_at <= today:
            continue
        if b.activates_at is not None and b.activates_at > now:
            continue
        total += int(b.remaining)
    return int(total)


def compute_pending_balance(db: Session, agent_id: str, *, now: Optional[datetime] = None) -> int:
    """Сумма remaining по батчам, которые ещё не активны (activates_at > now)."""
    if now is None:
        now = datetime.now()
    today = now.date()
    total = 0
    for b in db.scalars(
        select(BonusBatch).where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0)
    ).all():
        if b.expires_at and b.expires_at <= today:
            continue
        if b.activates_at is not None and b.activates_at > now:
            total += int(b.remaining)
    return int(total)


def parse_intent(value: Any) -> int:
    """Парсит ввод менеджера. Любая дрянь → 0. Отрицательные → 0."""
    if value is None:
        return 0
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def _agent_meta_href(settings: Settings, agent_id: str) -> dict[str, Any]:
    base = settings.api_base.rstrip("/")
    return {
        "href": f"{base}/entity/counterparty/{agent_id}",
        "type": "counterparty",
        "mediaType": "application/json",
    }


def _spend_fifo(db: Session, agent_id: str, amount: int, *, now: Optional[datetime] = None) -> None:
    """Списать `amount` из активных батчей FIFO (по created_at)."""
    if amount <= 0:
        return
    if now is None:
        now = datetime.now()
    today = now.date()
    remaining = amount
    batches = list(
        db.scalars(
            select(BonusBatch)
            .where(BonusBatch.agent_id == agent_id, BonusBatch.remaining > 0)
            .order_by(BonusBatch.created_at.asc())
        ).all()
    )
    for b in batches:
        if remaining <= 0:
            break
        if b.expires_at and b.expires_at <= today:
            continue
        if b.activates_at is not None and b.activates_at > now:
            continue
        take = min(int(b.remaining), remaining)
        b.remaining = int(b.remaining) - take
        remaining -= take


def _refund_fifo(db: Session, agent_id: str, amount: int, *, now: Optional[datetime] = None) -> None:
    """Вернуть `amount` обратно в самый ранний с remaining < original батч (LIFO по created_at desc)."""
    if amount <= 0:
        return
    remaining = amount
    batches = list(
        db.scalars(
            select(BonusBatch)
            .where(BonusBatch.agent_id == agent_id)
            .order_by(BonusBatch.created_at.desc())
        ).all()
    )
    for b in batches:
        if remaining <= 0:
            break
        gap = max(0, int(b.original_amount) - int(b.remaining))
        if gap <= 0:
            continue
        give = min(gap, remaining)
        b.remaining = int(b.remaining) + give
        remaining -= give
    # если не хватило (гипотетически — например, манчные SPEND через МС) — создаём «технический» батч
    if remaining > 0:
        from datetime import date as _date, timedelta as _td

        db.add(
            BonusBatch(
                agent_id=agent_id,
                original_amount=remaining,
                remaining=remaining,
                expires_at=_date.today() + _td(days=365),
                activates_at=now,
                source_order_id=None,
                bonustransaction_id=None,
                batch_type="spend_refund",
            )
        )


def apply_spend_intent(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    order: dict[str, Any],
    member: LoyaltyMember,
    ev: ProcessedEvent,
) -> SpendResult:
    """
    Идемпотентное применение ручного intent в заказе.

    `ev` — ProcessedEvent заказа (создаётся в webhook_handlers, если нет; статус — любой).
    """
    order_id = str(order.get("id") or "")
    order_name = str(order.get("name") or "")
    order_attrs = order.get("attributes") or []
    intent = 0
    if settings.attr_order_spend_bonuses:
        for a in order_attrs:
            mh = str((a.get("meta") or {}).get("href") or "")
            if mh.rstrip("/").split("/")[-1] == settings.attr_order_spend_bonuses:
                intent = parse_intent(a.get("value"))
                break

    spent_before = int(ev.spent_amount or 0)
    res = SpendResult(intent=intent, cap=0, capped_by=None, delta=-spent_before, spent_before=spent_before, spent_after=0)

    agent = order.get("agent") or {}
    if not agent or not agent.get("meta"):
        # розница без контрагента — нечего считать; если что-то уже было списано — откатим
        if spent_before > 0:
            res.cap = 0
            res.delta = -spent_before
            res.capped_by = "no_agent"
        else:
            res.capped_by = "no_agent"
            return res

    if member.is_blocked:
        cap = 0
        capped_by = "blocked"
    else:
        balance = compute_active_balance(db, member.agent_id) + spent_before
        # `balance` тут = доступно сейчас + то, что мы уже списали с этого заказа
        # (потому что spent_before уже вычтено из батчей раньше — учитываем его при оценке cap)
        order_total = order_total_rub(order)
        limit_pct = max(0, min(100, int(settings.loyalty_spend_percent_limit)))
        limit_amount = (order_total * limit_pct) // 100
        cap = max(0, intent)
        capped_by: Optional[str] = None
        if cap > limit_amount:
            cap = limit_amount
            capped_by = "limit_30"
        if cap > balance:
            cap = balance
            capped_by = "balance"

    delta = cap - spent_before
    res.cap = cap
    res.delta = delta
    res.capped_by = capped_by

    if delta == 0 and intent == int(ev.last_spend_intent or 0):
        # Ничего не изменилось. Но если на заказе уже зафиксировано списание, а скидка
        # в позициях ещё не применена (старые заказы до этой версии или сбой PUT),
        # доводим состояние позиций до целевого.
        res.spent_after = spent_before
        if cap > 0 and not ev.spend_extra_discounts:
            try:
                new_per_pos = apply_spend_discount_to_positions(
                    client,
                    order=order,
                    cap_rub=int(cap),
                    prev_per_pos=ev.spend_extra_discounts,
                )
                ev.spend_extra_discounts = new_per_pos or None
            except Exception as exc:  # noqa: BLE001
                res.comments.append(f"order discount apply error: {exc}"[:200])
        return res

    # Применяем
    if delta > 0:
        # Создаём SPENDING в МС
        bt_body: dict[str, Any] = {
            "bonusProgram": {"meta": client.fetch_bonus_program_meta()},
            "agent": {"meta": _agent_meta_href(settings, member.agent_id)},
            "transactionType": "SPENDING",
            "bonusValue": int(delta),
            "externalCode": settings.loyalty_external_code,
            "name": unique_bt_name(f"Списание бонусов (заказ {order_name})"),
        }
        ev.spend_pending = True
        db.flush()
        bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
        bt_id = str(bt.get("id") or "")
        ev.spend_pending = False
        # FIFO — вычитаем из активных батчей
        _spend_fifo(db, member.agent_id, int(delta))
        ids = list(ev.spend_bonustransaction_ids or [])
        if bt_id:
            ids.append(bt_id)
        ev.spend_bonustransaction_ids = ids
        res.bt_id = bt_id
        log_bonus(
            db,
            action="SPEND",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=member.agent_id,
            bonus_amount=int(delta),
            bonustransaction_id=bt_id,
            details={"intent": intent, "cap": cap, "capped_by": capped_by, "delta": int(delta)},
        )
    elif delta < 0:
        # Откат: возвращаем |delta| как EARNING (rollback)
        give = int(-delta)
        bt_body = {
            "bonusProgram": {"meta": client.fetch_bonus_program_meta()},
            "agent": {"meta": _agent_meta_href(settings, member.agent_id)},
            "transactionType": "EARNING",
            "bonusValue": give,
            "externalCode": settings.loyalty_external_code,
            "name": unique_bt_name(f"Возврат списания бонусов (заказ {order_name})"),
        }
        bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
        bt_id = str(bt.get("id") or "")
        _refund_fifo(db, member.agent_id, give)
        ids = list(ev.spend_bonustransaction_ids or [])
        if bt_id:
            ids.append(bt_id)
        ev.spend_bonustransaction_ids = ids
        res.refund_bt_id = bt_id
        log_bonus(
            db,
            action="SPEND_REFUND",
            customerorder_id=order_id,
            customerorder_name=order_name,
            agent_id=member.agent_id,
            bonus_amount=give,
            bonustransaction_id=bt_id,
            details={"intent": intent, "cap": cap, "capped_by": capped_by, "delta": int(delta)},
        )

    ev.spent_amount = int(cap)
    ev.last_spend_intent = int(intent)
    res.spent_after = int(cap)

    # Применяем/обновляем скидку в самих позициях заказа, чтобы итог уменьшился ровно на cap.
    try:
        new_per_pos = apply_spend_discount_to_positions(
            client,
            order=order,
            cap_rub=int(cap),
            prev_per_pos=ev.spend_extra_discounts,
        )
        ev.spend_extra_discounts = new_per_pos or None
    except Exception as exc:  # noqa: BLE001
        res.comments.append(f"order discount apply error: {exc}"[:200])

    return res


def rollback_all_spend(
    db: Session,
    client: MoySkladClient,
    settings: Settings,
    *,
    member: LoyaltyMember,
    ev: ProcessedEvent,
    order_name: str = "",
) -> Optional[str]:
    """Полный откат текущего spent_amount (используется при DELETE заказа)."""
    spent = int(ev.spent_amount or 0)
    if spent <= 0:
        return None
    bt_body = {
        "bonusProgram": {"meta": client.fetch_bonus_program_meta()},
        "agent": {"meta": _agent_meta_href(settings, member.agent_id)},
        "transactionType": "EARNING",
        "bonusValue": spent,
        "externalCode": settings.loyalty_external_code,
        "name": unique_bt_name(f"Возврат списания бонусов (отмена заказа {order_name})"),
    }
    bt = client.post("/entity/bonustransaction", bt_body, disable_webhook=True)
    bt_id = str(bt.get("id") or "")
    _refund_fifo(db, member.agent_id, spent)
    ids = list(ev.spend_bonustransaction_ids or [])
    if bt_id:
        ids.append(bt_id)
    ev.spend_bonustransaction_ids = ids
    log_bonus(
        db,
        action="SPEND_REFUND",
        customerorder_id=ev.entity_id,
        customerorder_name=order_name,
        agent_id=member.agent_id,
        bonus_amount=spent,
        bonustransaction_id=bt_id,
        details={"reason": "order_deleted"},
    )
    ev.spent_amount = 0
    ev.last_spend_intent = 0
    # После полного отката списания наша запись о скидках устаревает: заказ либо удалён,
    # либо переходит в статус возврата — discount в позициях МС оставляем как есть.
    ev.spend_extra_discounts = None
    return bt_id
