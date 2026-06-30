#!/usr/bin/env python3
"""
Подтягивает price_input / price_output в models_cache.json из openrouter_cache.json
по совпадению cursor_id с ключом модели OpenRouter.

Запуск после: python3 scripts/fetch_openrouter.py

Алиасы: если slug в OpenRouter переименован, задайте в CURSOR_ID_ALIASES.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Локальный ключ models_cache → актуальный id в OpenRouter (если переименовали)
CURSOR_ID_ALIASES: dict[str, str] = {
    # В выдаче OpenRouter (2026-05): google/gemini-3-pro отсутствует, превью — 3.1
    "google/gemini-3-pro": "google/gemini-3.1-pro-preview",
}


def fmt_price(x: float) -> float:
    if x < 1:
        return round(x, 4)
    return round(x, 2)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    data = root / "_ДАННЫЕ"
    mc_path = data / "models_cache.json"
    or_path = data / "openrouter_cache.json"

    if not or_path.is_file():
        print("Нет openrouter_cache.json — сначала fetch_openrouter.py", file=sys.stderr)
        return 1

    mc: dict[str, Any] = json.loads(mc_path.read_text(encoding="utf-8"))
    or_doc: dict[str, Any] = json.loads(or_path.read_text(encoding="utf-8"))
    om: dict[str, Any] = or_doc.get("models") or {}

    missing: list[tuple[str, str]] = []
    for mk, model in mc.get("models", {}).items():
        if not isinstance(model, dict):
            continue
        cid: str | None = model.get("cursor_id")
        if not cid:
            continue
        or_id = CURSOR_ID_ALIASES.get(cid, cid)
        if or_id != cid:
            model["cursor_id"] = or_id
            cid = or_id
        if cid not in om:
            missing.append((mk, cid))
            continue
        pr = om[cid]
        pin = pr.get("price_input_per_1m")
        pout = pr.get("price_output_per_1m")
        if pin is not None:
            model["price_input"] = fmt_price(float(pin))
        if pout is not None:
            model["price_output"] = fmt_price(float(pout))
        ctx = pr.get("context_length")
        if isinstance(ctx, int) and ctx > 0:
            model["context_window"] = ctx

    if missing:
        print(
            "Нет в OpenRouter (цены не тронуты, см. refresh_note):",
            missing,
            file=sys.stderr,
        )

    mc_path.write_text(json.dumps(mc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK {mc_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
