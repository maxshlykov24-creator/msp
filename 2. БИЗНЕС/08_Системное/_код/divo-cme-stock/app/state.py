"""last_ok на диске — порог 50% переживает рестарт контейнера."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _path() -> Path:
    p = Path(settings.state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {
            "last_ok_at": None,
            "last_ok_count": None,
            "fail_streak": 0,
            "last_error": "",
            "last_alert_at": None,
            "publish_field": "",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "last_ok_at": None,
            "last_ok_count": None,
            "fail_streak": 0,
            "last_error": "state.json повреждён",
            "last_alert_at": None,
            "publish_field": "",
        }


def save(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def guard_write(new_count: int, last_ok_count: int | None, allow_empty: bool, drop_ratio: float) -> str:
    """Пустая строка = можно писать, иначе причина отказа."""
    if new_count == 0 and not allow_empty:
        return "пустой сток (ALLOW_EMPTY=0)"
    if last_ok_count and last_ok_count > 0 and new_count > 0:
        floor = last_ok_count * (1.0 - drop_ratio)
        if new_count < floor:
            return (
                f"обвал {new_count} < {floor:.0f} "
                f"({drop_ratio:.0%} от прошлого last_ok={last_ok_count})"
            )
    return ""
