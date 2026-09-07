#!/usr/bin/env python3
"""Проверка Groq API-ключа (бесплатный tier console.groq.com)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.llm.groq_fallback import GroqFallback


async def main() -> int:
    cfg = load_config()
    groq = GroqFallback(cfg.groq_api_key, cfg.groq_model)
    if not groq.available:
        print("GROQ_API_KEY пустой. Задай в .env (https://console.groq.com → API Keys)")
        return 1
    ok, msg = await groq.ping()
    if ok:
        print(f"OK: Groq ответил — {msg!r}")
        print(f"Модель: {cfg.groq_model}")
        return 0
    print(f"FAIL: {msg}")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
