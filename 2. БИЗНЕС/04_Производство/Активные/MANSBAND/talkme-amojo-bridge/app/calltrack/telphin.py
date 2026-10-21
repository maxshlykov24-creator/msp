"""Приём звонка Телфин.

Имена полей боевого вебхука сюда не подставляются, пока не разобран первый
реальный payload. Пока событие не разобрано, оно лежит в ct_call со status=new
и сырым телом. Тесты и ручная проверка шлют уже нормализованные ключи:
call_id, called, caller, started_at, incoming.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.calltrack.models import CallEvent
from app.calltrack.phones import digits10
from app.models import ProcessedEvent

log = logging.getLogger(__name__)

_DIGITS = re.compile(r"\d{7,}")


def mask_payload(raw: Any) -> str:
    try:
        text = json.dumps(raw, ensure_ascii=False, default=str)
    except Exception:
        text = str(raw)
    return _DIGITS.sub(lambda m: m.group(0)[:2] + "…" + m.group(0)[-2:], text)[:2000]


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "out", "outbound"}


def _as_time(value: Any) -> Optional[dt.datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(int(value), tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return dt.datetime.fromtimestamp(int(text), tz=timezone.utc)
    text = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_telphin(raw: Any) -> dict[str, Any]:
    """Разобрать только нормализованное тело. Чужой формат возвращается пустым."""
    if not isinstance(raw, dict):
        return {"parsed": False, "incoming": True, "call_id": None, "called": None, "caller": None, "started_at": None}
    call_id = raw.get("call_id")
    called = digits10(raw.get("called"))
    caller_raw = raw.get("caller")
    caller = digits10(caller_raw) if caller_raw not in (None, "") else None
    started = _as_time(raw.get("started_at"))
    incoming = _as_bool(raw.get("incoming"), True)
    parsed = bool(call_id) and bool(called)
    return {
        "parsed": parsed,
        "incoming": incoming,
        "call_id": str(call_id)[:128] if call_id else None,
        "called": called,
        "caller": caller,
        "started_at": started,
    }


def _key_paths(obj: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 3:
        return [prefix or "$"]
    if isinstance(obj, dict):
        out: list[str] = []
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_key_paths(v, path, depth + 1))
        return out or [prefix or "$"]
    if isinstance(obj, list):
        return _key_paths(obj[0], f"{prefix}[]", depth + 1) if obj else [prefix or "$"]
    return [prefix or "$"]


def ingest(db: Session, raw: Any) -> Optional[CallEvent]:
    """Положить событие в ct_call. Повтор того же call_id не создаёт вторую строку."""
    parsed = parse_telphin(raw)
    if parsed.get("incoming") is False:
        return None
    call_id = parsed.get("call_id")
    fully = bool(parsed.get("parsed"))
    if not call_id:
        blob = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        call_id = "raw-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
        log.info("telphin unparsed keys=%s body=%s", _key_paths(raw)[:40], mask_payload(raw))
    claim = db.execute(
        pg_insert(ProcessedEvent.__table__)
        .values(source="telphin", dedup_key=call_id)
        .on_conflict_do_nothing(index_elements=["source", "dedup_key"])
    )
    if claim.rowcount == 0:
        existing = db.execute(select(CallEvent).where(CallEvent.call_id == call_id)).scalar_one_or_none()
        if existing is not None and fully:
            if not existing.called and parsed.get("called"):
                existing.called = parsed["called"]
            if not existing.caller and parsed.get("caller"):
                existing.caller = parsed["caller"]
        return existing
    now = dt.datetime.now(timezone.utc)
    row = CallEvent(
        call_id=call_id,
        called=parsed.get("called") or "",
        caller=parsed.get("caller"),
        started_at=parsed.get("started_at") or now,
        raw=raw if isinstance(raw, dict) else {"body": mask_payload(raw)},
        status="new",
        result={},
    )
    db.add(row)
    db.flush()
    if row.caller is None and row.called:
        from app.calltrack.telegram import send

        if send(f"Звонок без номера клиента. Набор +7{row.called}, id {row.call_id}"):
            row.alerted_at = now
    return row
