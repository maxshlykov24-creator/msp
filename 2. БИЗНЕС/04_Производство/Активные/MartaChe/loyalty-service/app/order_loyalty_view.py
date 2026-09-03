"""
Сборка attributes заказа покупателя (ПЛ-блок) и PUT в МойСклад.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import Settings
from app.customentity_values import customentity_value_meta, status_value_name
from app.models import LoyaltyMember
from app.moysklad_client import MoySkladClient
from app.tier_manager import tier_name_ru


def _attr_meta(settings: Settings, attr_uuid: str) -> dict[str, Any]:
    base = settings.api_base.rstrip("/")
    return {
        "href": f"{base}/entity/customerorder/metadata/attributes/{attr_uuid}",
        "type": "attributemetadata",
        "mediaType": "application/json",
    }


def _now_msk(settings: Settings) -> datetime:
    tz = timezone(timedelta(minutes=int(settings.loyalty_comment_tz_offset_minutes)))
    return datetime.now(tz)


def status_str(member: LoyaltyMember) -> str:
    return status_value_name(bool(member.is_blocked))


def make_comment_line(settings: Settings, message: str) -> str:
    """Префикс с MSK-штампом + сообщение."""
    ts = _now_msk(settings).strftime("%d.%m %H:%M")
    return f"[{ts}] {message}"


def append_comment_to_log(settings: Settings, prev_log: str, new_line: str) -> str:
    """Дописывает строку в начало (свежие сверху). Обрезает по `loyalty_comment_max_lines`."""
    prev = (prev_log or "").strip()
    lines = [new_line] + [ln for ln in prev.splitlines() if ln.strip()]
    keep = max(1, int(settings.loyalty_comment_max_lines))
    return "\n".join(lines[:keep])


def build_order_attributes(
    settings: Settings,
    *,
    client: MoySkladClient | None = None,
    member: LoyaltyMember,
    spent_amount: int,
    active_balance: int,
    comment_log: str,
) -> list[dict[str, Any]]:
    """Собирает массив attributes для PUT по заказу. Пустые UUID — пропускаются."""
    out: list[dict[str, Any]] = []
    if settings.attr_order_loyalty_status:
        value: Any = status_str(member)
        if client:
            meta = customentity_value_meta(client, settings.attr_status_customentity_id, value)
            if meta:
                value = {"meta": meta}
        out.append({"meta": _attr_meta(settings, settings.attr_order_loyalty_status), "value": value})
    if settings.attr_order_loyalty_tier:
        value = tier_name_ru(int(member.tier))
        if client:
            meta = customentity_value_meta(client, settings.attr_tier_customentity_id, value)
            if meta:
                value = {"meta": meta}
        out.append({"meta": _attr_meta(settings, settings.attr_order_loyalty_tier), "value": value})
    if settings.attr_order_active_bonuses:
        out.append({"meta": _attr_meta(settings, settings.attr_order_active_bonuses), "value": int(active_balance)})
    if settings.attr_order_spend_bonuses:
        out.append({"meta": _attr_meta(settings, settings.attr_order_spend_bonuses), "value": int(spent_amount)})
    if settings.attr_order_loyalty_comment and comment_log:
        out.append({"meta": _attr_meta(settings, settings.attr_order_loyalty_comment), "value": comment_log})
    return out


def system_view_for_order(
    settings: Settings,
    *,
    member: LoyaltyMember,
    spent_amount: int,
    active_balance: int,
) -> dict[str, Any]:
    """Снимок «как должно быть» — для сохранения в processed_events.last_synced_attrs."""
    return {
        settings.attr_order_loyalty_status: status_str(member) if settings.attr_order_loyalty_status else None,
        settings.attr_order_loyalty_tier: tier_name_ru(int(member.tier)) if settings.attr_order_loyalty_tier else None,
        settings.attr_order_active_bonuses: int(active_balance) if settings.attr_order_active_bonuses else None,
        settings.attr_order_spend_bonuses: int(spent_amount) if settings.attr_order_spend_bonuses else None,
    }


def write_order_loyalty_attributes(
    client: MoySkladClient,
    settings: Settings,
    *,
    order_id: str,
    patch_attrs: list[dict[str, Any]],
) -> None:
    """Объединяет наши attributes с существующими в заказе и шлёт PUT с disable_webhook."""
    if not patch_attrs:
        return
    body = client.get(f"/entity/customerorder/{order_id}")
    by_key: dict[str, dict[str, Any]] = {}
    for a in body.get("attributes") or []:
        mh = str((a.get("meta") or {}).get("href") or "")
        if mh:
            by_key[mh.rstrip("/").split("/")[-1]] = a
    for p in patch_attrs:
        mh = str((p.get("meta") or {}).get("href") or "")
        if mh:
            by_key[mh.rstrip("/").split("/")[-1]] = p
    body["attributes"] = list(by_key.values())
    client.put(f"/entity/customerorder/{order_id}", body, disable_webhook=True)


def get_order_attribute_value(order: dict[str, Any], attr_uuid: str) -> Optional[Any]:
    """Возвращает текущее значение attribute по UUID (или None)."""
    if not attr_uuid:
        return None
    for a in order.get("attributes") or []:
        mh = str((a.get("meta") or {}).get("href") or "")
        if mh.rstrip("/").split("/")[-1] == attr_uuid:
            return a.get("value")
    return None


def get_order_loyalty_comment(order: dict[str, Any], settings: Settings) -> str:
    if not settings.attr_order_loyalty_comment:
        return ""
    v = get_order_attribute_value(order, settings.attr_order_loyalty_comment)
    return str(v or "")
