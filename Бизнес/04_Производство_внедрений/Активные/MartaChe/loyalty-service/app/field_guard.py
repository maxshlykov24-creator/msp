"""
Reconcile attributes: что принять, что откатить.

Owner-правила:
  - Бонусные поля (Активно/Активные/Pending) — owner = система. Любое отличие → откат.
  - Уровень — owner = менеджер, но **только при повышении**. Понижение откатываем.
  - Статус — owner = менеджер (Активен ↔ Заблокирован).
  - Списано бонусов (только в заказе) — owner = менеджер, обрабатывается в spend_engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import Settings
from app.models import LoyaltyMember
from app.tier_manager import tier_name_ru


_TIER_NAME_TO_IDX = {
    "знакомство": 0,
    "дружба": 1,
    "любовь": 2,
}


def parse_tier(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name")
    s = str(value).strip().lower()
    if not s:
        return None
    return _TIER_NAME_TO_IDX.get(s)


_STATUS_VARIANTS_BLOCKED = {"заблокирован", "деактивирован", "blocked", "disabled", "не активен", "неактивен"}
_STATUS_VARIANTS_ACTIVE = {"активен", "active", "enabled"}


def parse_status(value: Any) -> Optional[bool]:
    """Возвращает is_blocked (True/False) или None если не распознано."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name")
    s = str(value).strip().lower()
    if not s:
        return None
    if s in _STATUS_VARIANTS_BLOCKED:
        return True
    if s in _STATUS_VARIANTS_ACTIVE:
        return False
    return None


def status_str_for(member_is_blocked: bool) -> str:
    return "Деактивирован" if member_is_blocked else "Активен"


@dataclass
class TierStatusChange:
    accept_tier_idx: Optional[int] = None
    revert_tier: bool = False                 # вернуть прежний (понижение)
    revert_reason: Optional[str] = None       # причина для лога/комментария
    accept_is_blocked: Optional[bool] = None  # True/False — принять; None — не менять


@dataclass
class BonusFieldRevert:
    revert_active: Optional[int] = None       # если не None — нужно вернуть это значение в МС
    revert_pending: Optional[int] = None
    notes: list[str] = field(default_factory=list)


def reconcile_tier_status(
    *,
    member: LoyaltyMember,
    incoming_tier_value: Any,
    incoming_status_value: Any,
) -> TierStatusChange:
    """Сверяет входящие Уровень/Статус с членом."""
    out = TierStatusChange()
    inc_tier = parse_tier(incoming_tier_value)
    if inc_tier is not None:
        cur = int(member.tier)
        if inc_tier > cur:
            out.accept_tier_idx = inc_tier
        elif inc_tier < cur:
            out.revert_tier = True
            out.revert_reason = "Понижение уровня вручную запрещено"
    inc_blocked = parse_status(incoming_status_value)
    if inc_blocked is not None and inc_blocked != bool(member.is_blocked):
        out.accept_is_blocked = inc_blocked
    return out


def reconcile_counterparty_bonus_fields(
    *,
    settings: Settings,
    cp_attrs: list[dict[str, Any]],
    expected_active: int,
    expected_pending: int,
) -> BonusFieldRevert:
    """Если значения в МС не совпадают с expected — нужно откатить."""
    out = BonusFieldRevert()
    cur_active = _read_number_attr(cp_attrs, settings.attr_active_bonuses)
    cur_pending = _read_number_attr(cp_attrs, settings.attr_pending_bonuses)
    if settings.attr_active_bonuses and cur_active is not None and int(cur_active) != int(expected_active):
        out.revert_active = int(expected_active)
        out.notes.append(f"Активные бонусы: {cur_active} → {expected_active}")
    if settings.attr_pending_bonuses and cur_pending is not None and int(cur_pending) != int(expected_pending):
        out.revert_pending = int(expected_pending)
        out.notes.append(f"Ожидают активации: {cur_pending} → {expected_pending}")
    return out


def reconcile_order_active_bonuses(
    *,
    settings: Settings,
    order_attrs: list[dict[str, Any]],
    expected_active: int,
) -> Optional[int]:
    """Возвращает значение для отката (или None если всё ок)."""
    if not settings.attr_order_active_bonuses:
        return None
    cur = _read_number_attr(order_attrs, settings.attr_order_active_bonuses)
    if cur is None:
        return int(expected_active)
    if int(cur) != int(expected_active):
        return int(expected_active)
    return None


def _read_number_attr(attrs: list[dict[str, Any]], attr_uuid: str) -> Optional[int]:
    if not attr_uuid:
        return None
    for a in attrs or []:
        mh = str((a.get("meta") or {}).get("href") or "")
        if mh.rstrip("/").split("/")[-1] == attr_uuid:
            v = a.get("value")
            if v is None:
                return None
            try:
                return int(round(float(v)))
            except (TypeError, ValueError):
                return None
    return None


def read_attr_value(attrs: list[dict[str, Any]], attr_uuid: str) -> Optional[Any]:
    if not attr_uuid:
        return None
    for a in attrs or []:
        mh = str((a.get("meta") or {}).get("href") or "")
        if mh.rstrip("/").split("/")[-1] == attr_uuid:
            return a.get("value")
    return None


def tier_text(idx: int) -> str:
    return tier_name_ru(int(idx))
