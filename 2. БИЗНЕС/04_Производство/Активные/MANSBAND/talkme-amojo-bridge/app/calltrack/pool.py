from __future__ import annotations

import datetime as dt
from datetime import timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.calltrack.models import Assignment, PoolNumber, Visit
from app.calltrack.phones import digits10
from app.config import Settings, get_settings

MSK = timezone(timedelta(hours=3))


def _pairs(raw: str, sep: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in (raw or "").split(sep):
        if "=" not in part:
            continue
        left, right = part.split("=", 1)
        if left.strip() and right.strip():
            out.append((left.strip(), right.strip()))
    return out


def pool_numbers(settings: Settings) -> list[str]:
    out: list[str] = []
    for part in (settings.ct_pool_numbers or "").split(","):
        n = digits10(part.strip())
        if n and n not in out:
            out.append(n)
    return out


def channel_map(settings: Settings) -> dict[str, str]:
    out: dict[str, str] = {}
    for num, source in _pairs(settings.ct_channel_numbers, ";"):
        n = digits10(num)
        if n:
            out[n] = source
    return out


def utm_source_map(settings: Settings) -> dict[str, str]:
    mapping = {k.lower(): v for k, v in _pairs(settings.ct_utm_source_map, ";")}
    mapping.setdefault("ya", "Яндекс Директ")
    return mapping


def record_fallback(db: Session, visit: Visit, now: dt.datetime, *, recovered: bool = False) -> None:
    """One small row per affected visit/day, without URL, IP, ClientID or phone."""
    import json
    from app.models import KvState
    key = f"ct_fallback:{now.astimezone(MSK).date()}:{visit.id}"
    if recovered:
        # A recovery can cross midnight, so update any unresolved row for this visit.
        rows = db.scalars(select(KvState).where(KvState.key.like(f"ct_fallback:%:{visit.id}"))).all()
        for row in rows:
            data = json.loads(row.value)
            if not data.get("recovered_at"):
                data["recovered_at"] = now.isoformat()
                row.value = json.dumps(data)
                row.updated_at = now
        return
    db.execute(text("""
        INSERT INTO kv_state (key, value, updated_at) VALUES (:key, :value, :now)
        ON CONFLICT (key) DO UPDATE SET value =
          (kv_state.value::jsonb || jsonb_build_object(
            'last_at', :stamp, 'responses', (kv_state.value::jsonb->>'responses')::int + 1))::text,
          updated_at = :now
    """), {"key": key, "now": now, "stamp": now.isoformat(), "value": json.dumps({
        "visit": visit.id, "first_at": now.isoformat(), "last_at": now.isoformat(),
        "responses": 1, "source": (visit.utm_source or "")[:255], "paid": visit.is_paid,
        "recovered_at": None})})


def wanted_numbers(settings: Settings) -> dict[str, tuple[str, Optional[str]]]:
    """Не допускаем общий номер в динамический пул."""
    wanted: dict[str, tuple[str, Optional[str]]] = {}
    for n in pool_numbers(settings):
        wanted[n] = ("pool", None)
    organic = digits10(settings.ct_organic_number)
    if organic and organic in wanted:
        raise ValueError("Основной номер должен быть вне пула")
    if organic and organic not in wanted:
        wanted[organic] = ("organic", "Сайт (органика)")
    for n, source in channel_map(settings).items():
        if n not in wanted:
            wanted[n] = ("channel", source)
    return wanted


def sync_numbers(db: Session, settings: Optional[Settings] = None) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    settings = settings or get_settings()
    wanted = wanted_numbers(settings)
    if wanted:
        rows = [
            {"number": n, "kind": kind, "source": source, "active": True}
            for n, (kind, source) in wanted.items()
        ]
        stmt = pg_insert(PoolNumber).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={"kind": stmt.excluded.kind, "source": stmt.excluded.source, "active": True},
        )
        db.execute(stmt)
    seen = list(wanted)
    if seen:
        db.execute(update(PoolNumber).where(PoolNumber.number.not_in(seen)).values(active=False))
    elif db.execute(select(PoolNumber.id).limit(1)).first():
        db.execute(update(PoolNumber).values(active=False))
    db.flush()


def _bump_exhausted(db: Session, now: dt.datetime) -> None:
    day = now.astimezone(MSK).date().isoformat()
    db.execute(
        text(
            """
            INSERT INTO kv_state (key, value, updated_at)
            VALUES (:key, '1', now())
            ON CONFLICT (key) DO UPDATE
            SET value = (kv_state.value::int + 1)::text,
                updated_at = now()
            """
        ),
        {"key": f"ct_pool_exhausted:{day}"},
    )


def _touch_assignment(db: Session, number: PoolNumber, visit: Visit, now: dt.datetime) -> None:
    row = (
        db.execute(
            select(Assignment)
            .where(Assignment.number_id == number.id, Assignment.visit_id == visit.id)
            .order_by(Assignment.id.desc())
        )
        .scalars()
        .first()
    )
    if row is not None:
        row.last_seen_at = now
        row.expires_at = number.held_until


def allocate_pool(db: Session, visit: Visit, now: Optional[dt.datetime] = None) -> Optional[PoolNumber]:
    """Выдать номер пула. Одна транзакция, строка номера блокируется.

    Свободный номер берётся с SKIP LOCKED. Занятые номера не отбираются.
    При нехватке возвращается None, вызывающий код показывает резерв.
    """
    settings = get_settings()
    now = now or dt.datetime.now(timezone.utc)
    hold = timedelta(minutes=int(settings.ct_hold_minutes))

    own = (
        db.execute(
            select(PoolNumber)
            .where(
                PoolNumber.kind == "pool",
                PoolNumber.active.is_(True),
                PoolNumber.held_by_visit_id == visit.id,
            )
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if own is not None and own.held_until is not None and own.held_until > now:
        own.held_until = max(own.held_until, now + hold)
        visit.last_seen_at = now
        _touch_assignment(db, own, visit, now)
        return own

    free = (
        db.execute(
            select(PoolNumber)
            .where(
                PoolNumber.kind == "pool",
                PoolNumber.active.is_(True),
                (PoolNumber.held_until.is_(None)) | (PoolNumber.held_until <= now),
            )
            .order_by(PoolNumber.held_until.asc().nullsfirst())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .first()
    )
    if free is None:
        _bump_exhausted(db, now)
        record_fallback(db, visit, now)
        return None

    free.held_by_visit_id = visit.id
    free.held_until = now + hold
    visit.last_seen_at = now
    db.add(
        Assignment(
            number_id=free.id,
            visit_id=visit.id,
            started_at=now,
            last_seen_at=now,
            expires_at=free.held_until,
        )
    )
    db.flush()
    record_fallback(db, visit, now, recovered=True)
    return free


def pool_snapshot(db: Session, now: Optional[dt.datetime] = None) -> dict[str, int]:
    now = now or dt.datetime.now(timezone.utc)
    total = db.scalar(select(func.count()).select_from(PoolNumber).where(
        PoolNumber.kind == "pool", PoolNumber.active.is_(True))) or 0
    busy = db.scalar(select(func.count()).select_from(PoolNumber).where(
        PoolNumber.kind == "pool", PoolNumber.active.is_(True), PoolNumber.held_until > now)) or 0
    from app.models import KvState

    reset = db.get(KvState, "ct_visit_reset_max_id")
    cutoff = int(reset.value) if reset else 0
    demand = db.scalar(select(func.count()).select_from(Visit).where(
        Visit.id > cutoff,
        Visit.is_paid.is_(True), Visit.last_seen_at > now - timedelta(minutes=get_settings().ct_hold_minutes))) or 0
    return {"total": total, "busy": busy, "free": total - busy, "demand": demand}


def record_load(db: Session, now: dt.datetime) -> None:
    db.flush()
    state = pool_snapshot(db, now)
    for metric in ("busy", "demand"):
        db.execute(text("""
            INSERT INTO kv_state (key, value, updated_at) VALUES (:key, :value, now())
            ON CONFLICT (key) DO UPDATE SET
              value = GREATEST(kv_state.value::int, :number)::text, updated_at = now()
        """), {"key": f"ct_peak_{metric}:{now.astimezone(MSK).date()}",
               "value": str(state[metric]), "number": state[metric]})
