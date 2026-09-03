"""Фильтр «на складе + в продаже + на площадке»."""
from __future__ import annotations

from typing import Any

from app.map_row import pick

PUBLISH_FIELD_CANDIDATES: tuple[str, ...] = (
    "publicationStatus",
    "publishStatus",
    "publishedStatus",
    "published",
    "isPublished",
    "adsPublished",
    "hasPublishedAds",
    "hasAds",
    "lastPublishedAt",
    "publication",
    "ads",
)

TRUE_PUB = {
    "published",
    "true",
    "1",
    "yes",
    "on",
    "active",
    "ok",
    "online",
    "listed",
}
FALSE_PUB = {
    "unpublished",
    "not_published",
    "notpublished",
    "not-published",
    "false",
    "0",
    "no",
    "off",
    "inactive",
    "none",
    "null",
    "empty",
}


def _norm(value: Any) -> str:
    return str(value).strip().lower().replace("ё", "е")


def stock_in(car: dict[str, Any]) -> bool:
    raw = pick(car, "stockState", "stock_state", "stock.status")
    if raw in (None, ""):
        return True  # поля нет — не режем по складу
    return _norm(raw) in {"in", "на складе", "instock", "in_stock"}


def on_sale(car: dict[str, Any]) -> bool:
    raw = pick(car, "saleStatus", "sale_status", "sale.status")
    if raw in (None, ""):
        return True
    return _norm(raw) in {"onsale", "on_sale", "on-sale", "в продаже", "sale"}


def detect_publish_field(sample: list[dict[str, Any]], explicit: str = "") -> str | None:
    if explicit:
        return explicit
    if not sample:
        return None
    keys: set[str] = set()
    for car in sample[:20]:
        if isinstance(car, dict):
            keys.update(car.keys())
    for cand in PUBLISH_FIELD_CANDIDATES:
        if cand in keys:
            return cand
    return None


def _truthy_publish(value: Any) -> bool | None:
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    if isinstance(value, dict):
        for k in ("status", "state", "published", "isPublished", "name", "value"):
            if k in value:
                inner = _truthy_publish(value[k])
                if inner is not None:
                    return inner
        return len(value) > 0
    key = _norm(value)
    if key in TRUE_PUB:
        return True
    if key in FALSE_PUB:
        return False
    # неизвестная строка статуса — считаем опубликованным, если не «нет»
    if "не опублик" in key or "not publish" in key:
        return False
    if "опублик" in key or "publish" in key:
        return True
    return None


def is_published(car: dict[str, Any], field: str | None) -> bool | None:
    """None = поле не задано / не интерпретируется."""
    if not field:
        return None
    return _truthy_publish(car.get(field))


class FilterResult:
    def __init__(self) -> None:
        self.kept: list[dict[str, Any]] = []
        self.publish_field: str | None = None
        self.reason: str = ""
        self.stats: dict[str, int] = {}


def filter_stock(
    cars: list[dict[str, Any]],
    *,
    mode: str = "require",
    publish_field: str = "",
    dealer_id: str = "",
) -> FilterResult:
    out = FilterResult()
    if dealer_id:
        cars = [
            c for c in cars
            if isinstance(c, dict) and str(c.get("dealerId") or "") == str(dealer_id)
        ]
    in_stock = [c for c in cars if isinstance(c, dict) and stock_in(c)]
    onsale = [c for c in in_stock if on_sale(c)]
    field = detect_publish_field(onsale or in_stock or cars, publish_field)
    out.publish_field = field
    out.stats = {
        "total": len(cars),
        "in_stock": len(in_stock),
        "onsale": len(onsale),
        "published": 0,
    }

    if mode == "in_stock_only":
        out.kept = onsale
        return out

    if not field:
        out.reason = (
            "поле публикации в JSON не найдено; в таблицу не пишем "
            "(CME_PUBLISH_MODE=in_stock_only — только после сверки probe)"
        )
        return out

    published = [c for c in onsale if is_published(c, field)]
    out.stats["published"] = len(published)
    unknown = sum(1 for c in onsale if is_published(c, field) is None)
    if not published and onsale and unknown == len(onsale):
        out.reason = (
            f"поле {field} есть, но значения не распознаны как статус публикации"
        )
        return out
    out.kept = published
    return out
