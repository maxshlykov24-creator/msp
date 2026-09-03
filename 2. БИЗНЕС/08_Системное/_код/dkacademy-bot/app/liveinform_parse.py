from __future__ import annotations

import hashlib
from typing import Any, Optional


def extract_liveinform_id(body: dict[str, Any]) -> Optional[str]:
    """Достаём liveinform_id из payload callback'а.

    В callback LiveInform поле называется `order_id`, но по их же комментарию это
    «ID заказа в системе LiveInform» — то есть наш liveinform_id (числовой).
    В то же время в их add-API параметр `order_id` означает «номер магазина»
    (может быть нецифровой). Поэтому из `order_id` берём только полностью числовое
    значение длиной 5+.
    """
    if not body:
        return None

    candidates: list[str] = []
    for k in ("liveinform_id", "liveinformId", "LiveInformId"):
        v = body.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()

    v_plain = body.get("id")
    if v_plain is not None and str(v_plain).strip():
        cand = str(v_plain).strip()
        if cand.isdigit():
            candidates.append(cand)

    v_order = body.get("order_id")
    if v_order is not None and str(v_order).strip():
        cand = str(v_order).strip()
        if cand.isdigit() and len(cand) >= 5:
            candidates.append(cand)

    nested = body.get("data")
    if isinstance(nested, dict):
        sid = extract_liveinform_id(nested)
        if sid:
            return sid
    nested2 = body.get("order") or body.get("result") or body.get("payload")
    if isinstance(nested2, dict):
        sid = extract_liveinform_id(nested2)
        if sid:
            return sid

    if candidates:
        return candidates[0]
    return None


def extract_tracking_hint(body: dict[str, Any]) -> Optional[str]:
    if not body:
        return None
    keys = ("tracking", "tracking_number", "track", "trakcing")
    for k in keys:
        v = body.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    nested = body.get("data") or body.get("order") or body.get("result")
    if isinstance(nested, dict):
        return extract_tracking_hint(nested)
    return None


def dedup_suffix_from_track_result(result_dict: dict[str, Any]) -> str:
    """SHA1-хэш стабилизирует длину и читаемость dedup_key."""
    status = str(result_dict.get("status") or "")
    lastcheck = str(result_dict.get("lastcheck") or "")
    first_blob = ""
    tl = result_dict.get("track")
    if isinstance(tl, list) and tl:
        first = tl[0]
        if isinstance(first, dict):
            first_blob = f"{first.get('checkdate', '')}:{first.get('text', '')}"
    raw = f"{status}|{lastcheck}|{first_blob}".encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:32]
