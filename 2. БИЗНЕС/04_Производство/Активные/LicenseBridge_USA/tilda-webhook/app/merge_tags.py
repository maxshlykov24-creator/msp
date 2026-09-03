"""Пул тегов ручной склейки: mc_NN (контакты) / md_NN (сделки).

- Стартовый пул фиксированного размера (settings.merge_slots_*), расширяется
  автоматически, если все заняты.
- Один тег пары вешается на ВСЕ сущности группы дублей → фильтр по mc_NN в Kommo
  показывает ровно эту группу.
- Слоты переиспользуются: после разрешения пары scanner возвращает тег в пул
  (см. app/scanner.py). Менеджеры теги руками не снимают.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MergeSlot

_KIND_PREFIX = {"contact": "mc", "deal": "md"}


def tag_name(kind: str, slot_no: int) -> str:
    return f"{_KIND_PREFIX[kind]}_{slot_no:02d}"


def ensure_pool(s: Session) -> None:
    """Создаёт стартовый пул слотов, если его ещё нет."""
    for kind, size in (
        ("contact", settings.merge_slots_contacts),
        ("deal", settings.merge_slots_deals),
    ):
        existing = s.scalar(
            select(MergeSlot).where(MergeSlot.kind == kind).limit(1)
        )
        if existing:
            continue
        for i in range(1, size + 1):
            s.add(MergeSlot(kind=kind, slot_no=i, tag=tag_name(kind, i), status="free"))
    s.flush()


def _grow_pool(s: Session, kind: str) -> MergeSlot:
    """Добавляет один новый слот сверх текущего максимума."""
    max_no = s.scalar(
        select(MergeSlot.slot_no)
        .where(MergeSlot.kind == kind)
        .order_by(MergeSlot.slot_no.desc())
        .limit(1)
    ) or 0
    slot = MergeSlot(kind=kind, slot_no=max_no + 1, tag=tag_name(kind, max_no + 1), status="free")
    s.add(slot)
    s.flush()
    return slot


def allocate_slot(s: Session, kind: str, pair_ref: str) -> MergeSlot:
    """Берёт свободный слот (или переиспользует уже выданный этой же паре).
    Блокировка строки FOR UPDATE SKIP LOCKED — от гонок между worker/scanner."""
    ensure_pool(s)
    # если паре уже выдан слот — вернуть его (идемпотентность)
    existing = s.scalar(
        select(MergeSlot).where(
            MergeSlot.kind == kind, MergeSlot.pair_ref == pair_ref, MergeSlot.status == "used"
        )
    )
    if existing:
        return existing
    stmt = (
        select(MergeSlot)
        .where(MergeSlot.kind == kind, MergeSlot.status == "free")
        .order_by(MergeSlot.slot_no.asc())
        .limit(1)
    )
    if s.bind is not None and s.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    slot = s.scalar(stmt)
    if slot is None:
        slot = _grow_pool(s, kind)
    slot.status = "used"
    slot.pair_ref = pair_ref
    s.flush()
    return slot


def free_slot(s: Session, kind: str, tag: str) -> None:
    slot = s.scalar(select(MergeSlot).where(MergeSlot.kind == kind, MergeSlot.tag == tag))
    if slot and slot.status != "free":
        slot.status = "free"
        slot.pair_ref = None
        s.flush()


def pool_stats(s: Session) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for kind in ("contact", "deal"):
        total = s.query(MergeSlot).filter(MergeSlot.kind == kind).count()
        used = s.query(MergeSlot).filter(MergeSlot.kind == kind, MergeSlot.status == "used").count()
        out[kind] = {"total": total, "used": used, "free": total - used}
    return out
