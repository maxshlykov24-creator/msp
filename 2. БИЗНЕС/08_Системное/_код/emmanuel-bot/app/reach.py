"""Личка бота: кто не открывал и кто заблокировал. Проверено 2026-09-29."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "seed" / "private_reach.json"


@lru_cache
def _ids(key: str) -> frozenset[int]:
    if not SEED.is_file():
        return frozenset()
    data = json.loads(SEED.read_text(encoding="utf-8"))
    return frozenset(int(x) for x in (data.get(key) or {}))


def not_started_ids() -> frozenset[int]:
    return _ids("not_started")


def blocked_ids() -> frozenset[int]:
    return _ids("blocked")
