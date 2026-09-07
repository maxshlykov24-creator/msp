#!/usr/bin/env python3
"""Обёртка. Канон — study-bot/scripts/transcribe_meeting.py"""

from __future__ import annotations

import runpy
from pathlib import Path

VAULT = Path(__file__).resolve().parents[5]
CANON = (
    VAULT
    / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика"
    / "study-bot/scripts/transcribe_meeting.py"
)

if not CANON.is_file():
    raise SystemExit(f"Нет канона: {CANON}")

runpy.run_path(str(CANON), run_name="__main__")
