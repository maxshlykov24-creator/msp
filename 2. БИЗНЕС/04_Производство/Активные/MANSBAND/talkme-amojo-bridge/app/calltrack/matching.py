from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calltrack.models import Assignment, CallEvent, PoolNumber, Visit
from app.calltrack.pool import utm_source_map
from app.config import get_settings
from app.talkme_parse import normalize_phone, tracking_is_empty

UTM_ATTRS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "yclid",
)


@dataclass
class CallAttribution:
    tracking: dict[str, str]
    tech_source: Optional[str]
    visit: Optional[Visit]
    disputed: bool
    kind: str


def _aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def match_call_for_lead(
    db: Session,
    phones: list[str],
    lead_created_at: dt.datetime,
) -> Optional[CallEvent]:
    """Звонок с этим телефоном в окне до сделки и 2 минуты после. Самый поздний."""
    keys = [k for k in (normalize_phone(p) for p in phones) if k]
    if not keys or lead_created_at is None:
        return None
    settings = get_settings()
    created = _aware(lead_created_at)
    start = created - timedelta(minutes=int(settings.ct_call_window_minutes))
    end = created + timedelta(minutes=2)
    return (
        db.execute(
            select(CallEvent)
            .where(
                CallEvent.caller.in_(keys),
                CallEvent.started_at >= start,
                CallEvent.started_at <= end,
                CallEvent.lead_id.is_(None),
            )
            .order_by(CallEvent.started_at.desc())
        )
        .scalars()
        .first()
    )


def visit_for_call(db: Session, call: CallEvent) -> tuple[Optional[Visit], bool]:
    """Визит, который держал набранный номер в момент звонка."""
    if not call.called or call.started_at is None:
        return None, False
    settings = get_settings()
    hold = timedelta(minutes=int(settings.ct_hold_minutes))
    started = _aware(call.started_at)
    rows = db.execute(
        select(Assignment, Visit)
        .join(PoolNumber, PoolNumber.id == Assignment.number_id)
        .join(Visit, Visit.id == Assignment.visit_id)
        .where(PoolNumber.number == call.called, Assignment.started_at <= started)
        .order_by(Assignment.started_at.desc())
    ).all()
    for assignment, visit in rows:
        seen = _aware(assignment.last_seen_at)
        if started <= seen + hold:
            return visit, bool(visit.disputed)
    return None, False


def _visit_tracking(visit: Visit) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in UTM_ATTRS:
        val = getattr(visit, key, None)
        if val:
            out[key] = str(val)
    if visit.referrer:
        out["utm_referrer"] = visit.referrer
    return out


def attribute_call(db: Session, call: CallEvent) -> CallAttribution:
    number = db.execute(select(PoolNumber).where(PoolNumber.number == call.called)).scalar_one_or_none()
    kind = number.kind if number is not None else "unknown"
    if kind == "channel":
        return CallAttribution({}, number.source if number else None, None, False, "channel")
    if kind == "organic":
        return CallAttribution({}, "Сайт (органика)", None, False, "organic")
    if kind != "pool":
        return CallAttribution({}, None, None, False, "unknown")
    visit, disputed = visit_for_call(db, call)
    if visit is None:
        return CallAttribution({}, None, None, False, "pool")
    source: Optional[str] = None
    if visit.yclid and not visit.utm_source:
        source = "Яндекс Директ"
    elif visit.utm_source:
        source = utm_source_map(get_settings()).get(visit.utm_source.lower())
    return CallAttribution(
        _visit_tracking(visit),
        source,
        visit,
        disputed or bool(visit.disputed),
        "pool",
    )


def resolve_our_visit(db: Session, tracking: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Наш id визита разворачивается в UTM и не пишется в поле ROISTAT.

    Чужой id Roistat, меньше ct_visit_id_base или отсутствующий в ct_visit, не трогаем.
    """
    out: dict[str, Any] = dict(tracking or {})
    raw = out.get("roistat")
    if raw in (None, ""):
        return out
    try:
        vid = int(str(raw).strip())
    except (TypeError, ValueError):
        return out
    if vid < int(get_settings().ct_visit_id_base):
        return out
    visit = db.get(Visit, vid)
    if visit is None:
        return out
    for key in UTM_ATTRS:
        if not out.get(key):
            val = getattr(visit, key, None)
            if val:
                out[key] = str(val)
    if not out.get("utm_referrer") and visit.referrer:
        out["utm_referrer"] = visit.referrer
    out.pop("roistat", None)
    return out


def plan_lead_patch(
    *,
    tracking: dict[str, Any],
    tech_source: Optional[str],
    lead: dict[str, Any],
    id_by_key: dict[str, int],
    select_info: Optional[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Пустые поля меток и пустой список «Источник (техническое)». Занятое не трогаем."""
    from app import amo_v4

    busy = set(amo_v4.existing_tracking_values(lead=lead, id_by_key=id_by_key))
    values = amo_v4.build_custom_fields_payload(
        tracking=tracking, id_by_key=id_by_key, skip_keys=busy
    )
    written = {
        k: v
        for k, v in tracking.items()
        if k in id_by_key and k not in busy and v not in (None, "")
    }
    tech_written = False
    if select_info and select_info.get("field_id"):
        field_id = int(select_info["field_id"])
        if amo_v4.lead_field_filled(lead, field_id):
            tech_written = True
        elif tech_source and tech_source in (select_info.get("enums") or {}):
            values.append(
                {
                    "field_id": field_id,
                    "values": [{"enum_id": int(select_info["enums"][tech_source])}],
                }
            )
            tech_written = True
    return values, {"written": written, "tech_written": tech_written}


def call_note(call: CallEvent, attr: CallAttribution) -> str:
    lines = ["Звонок на номер коллтрекинга", f"Номер набора: +7{call.called}"]
    if attr.visit is not None:
        lines.append(f"Визит: {attr.visit.id}")
        if attr.visit.landing_url:
            lines.append(f"Страница входа: {attr.visit.landing_url}")
    if attr.disputed:
        lines.append("спорный")
    return "\n".join(lines)


def decide(
    db: Session,
    *,
    phones: list[str],
    names: list[str],
    lead_created_at: dt.datetime,
) -> dict[str, Any]:
    """Сначала звонок, потом беседа. Если оба, ближе по времени к созданию сделки."""
    from app.main import _match_conversation

    call = None
    try:
        call = match_call_for_lead(db, phones, lead_created_at)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("ct: поиск звонка не удался, остаётся путь чата")
        call = None
    cm = _match_conversation(db, phones, names)
    if cm is not None and tracking_is_empty(cm.tracking):
        cm = None
    if call is not None and cm is not None:
        created = _aware(lead_created_at)
        call_delta = abs((created - _aware(call.started_at)).total_seconds())
        if cm.last_message_at is None:
            chat_delta = float("inf")
        else:
            chat_delta = abs((created - _aware(cm.last_message_at)).total_seconds())
        if call_delta <= chat_delta:
            cm = None
        else:
            call = None
    if call is not None:
        attr = attribute_call(db, call)
        return {"kind": "call", "call": call, "attr": attr}
    if cm is not None:
        return {"kind": "chat", "conversation": cm}
    return {"kind": "none"}


def apply_call(
    db: Session,
    call: CallEvent,
    lead_id: int,
    lead: dict[str, Any],
    access_token: str,
) -> dict[str, Any]:
    """Записать метки звонка в пустые поля и примечание. Чужое не затирает."""
    from app import amo_v4

    settings = get_settings()
    attr = attribute_call(db, call)
    id_by_key = amo_v4.field_id_by_key(access_token=access_token, settings=settings)
    select_info = amo_v4.select_field(
        name=settings.ct_tech_source_field, access_token=access_token, settings=settings
    )
    values, meta = plan_lead_patch(
        tracking=attr.tracking,
        tech_source=attr.tech_source,
        lead=lead,
        id_by_key=id_by_key,
        select_info=select_info,
    )
    if values:
        r = amo_v4.patch_lead_fields(
            lead_id=lead_id,
            custom_fields_values=values,
            access_token=access_token,
            settings=settings,
        )
        if r.status_code >= 400:
            return {"ok": False, "reason": f"amo {r.status_code}"}
    amo_v4.add_lead_note(
        lead_id=lead_id,
        text=call_note(call, attr),
        access_token=access_token,
        settings=settings,
    )
    call.visit_id = attr.visit.id if attr.visit is not None else None
    call.lead_id = lead_id
    call.number_kind = attr.kind
    has_source = bool(meta["tech_written"] or meta["written"])
    call.status = "matched" if has_source else "new"
    call.result = {
        "written": meta["written"],
        "tech_source": attr.tech_source,
        "tech_written": meta["tech_written"],
        "disputed": attr.disputed,
    }
    db.commit()
    if not has_source:
        from app.calltrack.telegram import send

        send(
            f"Сделка {lead_id} по звонку без источника. Набор +7{call.called}, id {call.call_id}"
        )
        call.alerted_at = dt.datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "status": call.status, "result": call.result}
