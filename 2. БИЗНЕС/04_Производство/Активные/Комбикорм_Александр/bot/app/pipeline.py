"""Пайплайн разбора: сырой текст → черновик позиций с матчингом.

Общий для всех операций. Возвращает список ``DraftLine``, часть из которых может
быть помечена как неоднозначная/нераспознанная — их пользователь правит тапом.
Опционально Groq-фолбэк для строк «❓» (``LLM_FALLBACK_ENABLED=1``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .text import normalize
from .text.matcher import MatchCandidate, Matcher

if TYPE_CHECKING:
    from .llm.groq_fallback import GroqFallback

log = logging.getLogger(__name__)


@dataclass
class DraftLine:
    index: int
    raw: str
    status: str  # ok | ambiguous | unmatched
    product_id: Optional[int] = None
    name: Optional[str] = None
    qty: Optional[float] = None
    unit: str = "bag"
    bag_size_kg: Optional[float] = None
    price_bag: Optional[float] = None
    line_sum: Optional[float] = None
    reason: Optional[str] = None
    candidates: list[MatchCandidate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.product_id is not None and self.qty is not None


def parse_text(matcher: Matcher, text: str, *, expect_sum: bool = False) -> list[DraftLine]:
    norm = normalize.normalize(text)
    raw_lines = normalize.split_into_lines(norm)
    drafts: list[DraftLine] = []
    for idx, rl in enumerate(raw_lines, start=1):
        parsed = normalize.parse_line(rl, expect_sum=expect_sum)
        result = matcher.match(parsed.name_text or rl)

        draft = DraftLine(
            index=idx,
            raw=rl,
            status="unmatched",
            qty=parsed.qty,
            unit=parsed.unit,
            line_sum=parsed.line_sum,
            candidates=result.candidates,
        )
        if result.best and not result.ambiguous:
            draft.status = "ok" if parsed.qty is not None else "ambiguous"
            draft.product_id = result.best.product_id
            draft.name = result.best.name
            draft.bag_size_kg = result.best.bag_size_kg
            draft.price_bag = result.best.price_bag
            # если сумма не сказана, но цена мешка известна — оценим
            if expect_sum and draft.line_sum is None and parsed.qty is not None and result.best.price_bag:
                if parsed.unit == "bag":
                    draft.line_sum = parsed.qty * result.best.price_bag
        elif result.candidates:
            draft.status = "ambiguous"
        drafts.append(draft)
    return drafts


async def apply_llm_fallback(
    drafts: list[DraftLine],
    groq: GroqFallback,
    *,
    product_lookup,
) -> list[DraftLine]:
    """Пробует Groq только для unmatched/ambiguous без product_id."""
    if not groq or not groq.available:
        return drafts

    for draft in drafts:
        if draft.status == "ok" and draft.product_id is not None:
            continue
        llm = await groq.resolve_line(
            draft.raw,
            draft.candidates,
            parsed_qty=draft.qty,
            parsed_unit=draft.unit,
        )
        if not llm or not llm.confident or llm.product_id is None:
            continue
        prod = product_lookup(llm.product_id)
        if not prod:
            continue
        draft.product_id = llm.product_id
        draft.name = prod["canonical_name"]
        draft.bag_size_kg = prod["bag_size_kg"]
        draft.price_bag = prod["price_bag"]
        if llm.qty is not None:
            draft.qty = llm.qty
        if llm.unit in ("bag", "kg"):
            draft.unit = llm.unit
        draft.status = "ok" if draft.qty is not None else "ambiguous"
        log.info("LLM fallback: #%s -> %s", draft.index, draft.name)
    return drafts


async def parse_text_with_fallback(
    matcher: Matcher,
    text: str,
    *,
    expect_sum: bool = False,
    groq: GroqFallback | None = None,
    product_lookup=None,
) -> list[DraftLine]:
    drafts = parse_text(matcher, text, expect_sum=expect_sum)
    if groq and groq.available and product_lookup:
        drafts = await apply_llm_fallback(drafts, groq, product_lookup=product_lookup)
    return drafts
