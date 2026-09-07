"""История диалогов, пауза по диалогу, лог реплик. Всё файлами — переживает рестарт."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bot.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _history_path(chat_id: int | str) -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / ("%s.json" % chat_id)


def _paused_path(chat_id: int | str) -> Path:
    settings.paused_dir.mkdir(parents=True, exist_ok=True)
    return settings.paused_dir / str(chat_id)


def load_doc(chat_id: int | str) -> dict:
    path = _history_path(chat_id)
    empty = {"chat_id": str(chat_id), "messages": [], "nudge": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("chat_id", str(chat_id))
    data.setdefault("messages", [])
    data.setdefault("nudge", {})
    return data


def save_doc(chat_id: int | str, doc: dict) -> None:
    messages = list(doc.get("messages") or [])[-settings.history_turns:]
    payload = {
        "chat_id": str(chat_id),
        "updated_at": _now(),
        "messages": messages,
        "nudge": doc.get("nudge") or {},
    }
    _history_path(chat_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_history(chat_id: int | str) -> list[dict]:
    return list(load_doc(chat_id).get("messages") or [])


def save_history(chat_id: int | str, messages: list[dict]) -> None:
    doc = load_doc(chat_id)
    doc["messages"] = messages
    save_doc(chat_id, doc)


def reset_history(chat_id: int | str) -> None:
    _history_path(chat_id).unlink(missing_ok=True)


def chat_ids() -> list[int]:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    out: list[int] = []
    for path in settings.state_dir.glob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            out.append(int(path.stem))
        except ValueError:
            continue
    return out


def is_paused(chat_id: int | str) -> bool:
    return _paused_path(chat_id).exists()


def pause(chat_id: int | str, reason: str = "handoff") -> None:
    _paused_path(chat_id).write_text(
        "paused_at=%s\nreason=%s\n" % (_now(), reason), encoding="utf-8"
    )


def resume(chat_id: int | str) -> None:
    _paused_path(chat_id).unlink(missing_ok=True)


def log_line(chat_id: int | str, who: str, text: str) -> None:
    """Плоский лог всех диалогов, для разбора вечером."""
    log_dir = settings.state_dir / "_логи"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flat = text.replace("\n", " ⏎ ")
    with (log_dir / ("%s.log" % day)).open("a", encoding="utf-8") as fh:
        fh.write("%s\t%s\t%s\t%s\n" % (_now(), chat_id, who, flat))
