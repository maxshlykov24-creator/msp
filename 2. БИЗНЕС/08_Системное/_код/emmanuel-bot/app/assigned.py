"""Имена, заданные владельцем: хэштег без сдачи отчёта и кто вне круга."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "seed" / "assigned_hashtags.json"


@lru_cache
def _config() -> tuple[dict[int, str], frozenset[int]]:
    if not SEED.is_file():
        return {}, frozenset()
    data = json.loads(SEED.read_text(encoding="utf-8"))
    tags = {int(k): str(v).strip() for k, v in (data.get("hashtags") or {}).items() if str(v).strip()}
    oos = frozenset(int(x) for x in (data.get("out_of_scope") or []))
    return tags, oos


def assigned_hashtag(tg_user_id: int) -> str | None:
    tags, _ = _config()
    return tags.get(int(tg_user_id))


def is_out_of_scope(tg_user_id: int) -> bool:
    _, oos = _config()
    return int(tg_user_id) in oos
