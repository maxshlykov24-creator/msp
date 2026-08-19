from __future__ import annotations

from typing import Any

from app.moysklad_client import MoySkladClient

_VALUE_META_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def customentity_value_meta(client: MoySkladClient, entity_id: str, value_name: str) -> dict[str, Any] | None:
    """Returns meta for a custom entity row by human-readable name."""
    if not entity_id or not value_name:
        return None
    key = (entity_id, value_name.strip().lower())
    if key in _VALUE_META_CACHE:
        return _VALUE_META_CACHE[key]

    data = client.get(f"/entity/customentity/{entity_id}")
    rows = list(data.get("rows") or []) if isinstance(data, dict) else []
    for row in rows:
        name = str(row.get("name") or "").strip()
        meta = row.get("meta")
        if name.lower() == value_name.strip().lower() and isinstance(meta, dict):
            _VALUE_META_CACHE[key] = meta
            return meta
    return None


def status_value_name(is_blocked: bool) -> str:
    # В текущем справочнике МС значение блокировки называется «Деактивирован».
    return "Деактивирован" if is_blocked else "Активен"

