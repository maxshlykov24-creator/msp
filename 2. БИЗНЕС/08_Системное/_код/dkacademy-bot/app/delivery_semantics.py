"""Смысловые статусы доставки для уведомлений клиенту.

LiveInform отдаёт грубый код (в пути / ПВЗ / вручен / возврат) и текстовые
события трека (формулировки СДЭК ≠ Почта). Клиенту шлём только whitelist
смысловых id; UX-тексты — в delivery_notify_templates.py.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Whitelist смысловых событий (без «заказ собран» — отдельный контур amoCRM).
ACCEPTED_ORIGIN = "accepted_origin"
SORTING = "sorting"
TO_DEST_CITY = "to_dest_city"
READY_PICKUP = "ready_pickup"
WITH_COURIER = "with_courier"
DELIVERED = "delivered"
DELIVERY_FAILED = "delivery_failed"
STORAGE_EXPIRING = "storage_expiring"

NOTIFY_WHITELIST: frozenset[str] = frozenset(
    {
        ACCEPTED_ORIGIN,
        SORTING,
        TO_DEST_CITY,
        READY_PICKUP,
        WITH_COURIER,
        DELIVERED,
        DELIVERY_FAILED,
        STORAGE_EXPIRING,
    }
)

# Порядок важен: более специфичные фразы раньше.
# (подстрока после нормализации, semantic_id)
_ALIAS_RULES: tuple[tuple[str, str], ...] = (
    # срок хранения
    ("истекает срок хранения", STORAGE_EXPIRING),
    ("истек срок хранения", STORAGE_EXPIRING),
    ("истёк срок хранения", STORAGE_EXPIRING),
    ("срок хранения истекает", STORAGE_EXPIRING),
    ("срок хранения истек", STORAGE_EXPIRING),
    ("заканчивается срок хранения", STORAGE_EXPIRING),
    # не вручен
    ("неудачная попытка", DELIVERY_FAILED),
    ("неудачн попытка", DELIVERY_FAILED),
    ("не вручен", DELIVERY_FAILED),
    ("не вручён", DELIVERY_FAILED),
    ("не удалось вручить", DELIVERY_FAILED),
    ("адресат не доступен", DELIVERY_FAILED),
    ("адресат недоступен", DELIVERY_FAILED),
    ("отказ в получении", DELIVERY_FAILED),
    # курьер
    ("передано курьеру", WITH_COURIER),
    ("передан курьеру", WITH_COURIER),
    ("у курьера", WITH_COURIER),
    ("на доставке у курьера", WITH_COURIER),
    ("выдан курьеру", WITH_COURIER),
    ("курьер направился", WITH_COURIER),
    ("выехал к получателю", WITH_COURIER),
    # готов к выдаче / ПВЗ
    ("прибыло в место вручения", READY_PICKUP),
    ("прибыл в место вручения", READY_PICKUP),
    ("прибыла в место вручения", READY_PICKUP),
    ("готов к выдаче", READY_PICKUP),
    ("готова к выдаче", READY_PICKUP),
    ("готово к выдаче", READY_PICKUP),
    ("до востребования", READY_PICKUP),
    ("в пункте выдачи", READY_PICKUP),
    ("прибыл в пвз", READY_PICKUP),
    ("прибыло в пвз", READY_PICKUP),
    ("прибыла в пвз", READY_PICKUP),
    ("ожидает в пункте", READY_PICKUP),
    ("ожидает адресата", READY_PICKUP),
    # вручен (после «не вручен» и «место вручения», без голого «вручен»)
    ("вручен получателю", DELIVERED),
    ("вручено получателю", DELIVERED),
    ("вручена получателю", DELIVERED),
    ("получено адресатом", DELIVERED),
    ("получен адресатом", DELIVERED),
    ("успешно вручен", DELIVERED),
    ("успешно вручено", DELIVERED),
    # отправлен в город получателя
    ("отправлен в город получателя", TO_DEST_CITY),
    ("отправлено в город получателя", TO_DEST_CITY),
    ("отправлена в город получателя", TO_DEST_CITY),
    ("прибыл в город получателя", TO_DEST_CITY),
    ("прибыло в город получателя", TO_DEST_CITY),
    ("прибыла в город получателя", TO_DEST_CITY),
    ("в городе получателя", TO_DEST_CITY),
    ("отправлено в город назначения", TO_DEST_CITY),
    ("отправлен в город назначения", TO_DEST_CITY),
    # сортировка
    ("сортировочн", SORTING),
    ("на сортировке", SORTING),
    ("сортировка", SORTING),
    # принят у отправителя
    ("принят в городе отправителя", ACCEPTED_ORIGIN),
    ("принято в городе отправителя", ACCEPTED_ORIGIN),
    ("принята в городе отправителя", ACCEPTED_ORIGIN),
    ("принят в отделении связи", ACCEPTED_ORIGIN),
    ("принято в отделении связи", ACCEPTED_ORIGIN),
    ("принята в отделении связи", ACCEPTED_ORIGIN),
    ("принято в отделении", ACCEPTED_ORIGIN),
    ("принят в отделении", ACCEPTED_ORIGIN),
    ("принято отправителем", ACCEPTED_ORIGIN),
    ("принято перевозчиком", ACCEPTED_ORIGIN),
    ("принят перевозчиком", ACCEPTED_ORIGIN),
    ("поступило в сортировочный центр", ACCEPTED_ORIGIN),
    ("поступило на склад", ACCEPTED_ORIGIN),
    ("принят на склад", ACCEPTED_ORIGIN),
    ("принято на склад", ACCEPTED_ORIGIN),
)


def normalize_status_text(raw: str) -> str:
    s = (raw or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def delivery_status_code(payload: dict[str, Any]) -> str:
    """Код статуса доставки (0–4), не код индексации.

    В callback LiveInform: status = индексация, track_status = доставка.
    В ответе track/v2: status = доставка. Берём track_status, если есть.
    """
    if not payload:
        return ""
    ts = payload.get("track_status")
    if ts is not None and str(ts).strip() != "":
        return str(ts).strip()
    st = payload.get("status")
    if st is not None and str(st).strip() != "":
        return str(st).strip()
    return ""


def _latest_track_blob(payload: dict[str, Any]) -> str:
    """Текст последнего события трека (text + operation)."""
    parts: list[str] = []
    tl = payload.get("track") or payload.get("tracking")
    if isinstance(tl, list) and tl:
        # Обычно первое = самое свежее в LI; если нет — берём [0] и [-1]
        candidates = [tl[0]]
        if len(tl) > 1:
            candidates.append(tl[-1])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in ("text", "operation", "status_text", "status_reason"):
                v = item.get(key)
                if v is not None and str(v).strip():
                    parts.append(str(v).strip())
    for key in ("status_text", "status_reason", "comment"):
        v = payload.get(key)
        if v is not None and str(v).strip():
            parts.append(str(v).strip())
    return " ".join(parts)


def match_alias(blob: str) -> Optional[str]:
    norm = normalize_status_text(blob)
    if not norm:
        return None
    for needle, semantic_id in _ALIAS_RULES:
        if normalize_status_text(needle) in norm:
            return semantic_id
    return None


def classify_delivery_event(
    payload: dict[str, Any],
    *,
    status_line: str = "",
) -> Optional[str]:
    """Вернуть semantic_id из whitelist или None (не уведомлять).

    Порядок:
    1) код доставки 1 → ready_pickup; 2/3/4 → None
    2) алиасы по тексту трека + status_line (для code 0 и уточнений)
    """
    code = delivery_status_code(payload)

    if code == "1":
        return READY_PICKUP
    if code == "2":
        return DELIVERED
    if code in {"3", "4"}:
        log.info(
            "delivery_semantics: code=%s вне whitelist (возврат/стоп)",
            code,
        )
        return None

    blob = " ".join(
        p for p in (_latest_track_blob(payload), status_line or "") if p
    )
    matched = match_alias(blob)
    if matched and matched in NOTIFY_WHITELIST:
        return matched

    log.warning(
        "delivery_semantics: unclassified code=%r status=%r track_status=%r "
        "status_line=%r track_blob=%r",
        code,
        payload.get("status"),
        payload.get("track_status"),
        (status_line or "")[:120],
        blob[:200],
    )
    return None


def is_intentional_no_notify(payload: dict[str, Any]) -> bool:
    """True = точно не шлём клиенту (возврат/стоп), это не «серый» статус."""
    return delivery_status_code(payload) in {"3", "4"}


def collect_classify_debug(
    payload: dict[str, Any],
    *,
    status_line: str = "",
) -> dict[str, str]:
    """Данные для ops-алерта / логов при unclassified."""
    return {
        "code": delivery_status_code(payload),
        "status": str(payload.get("status") or ""),
        "track_status": str(payload.get("track_status") or ""),
        "status_line": (status_line or "")[:500],
        "track_blob": _latest_track_blob(payload)[:500],
        "delivery": str(payload.get("delivery") or ""),
        "phone": str(payload.get("phone") or "")[:32],
    }
