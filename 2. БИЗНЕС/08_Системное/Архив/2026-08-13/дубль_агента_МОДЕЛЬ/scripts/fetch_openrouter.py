#!/usr/bin/env python3
"""
Выгрузка публичного списка моделей OpenRouter и сохранение slim-кэша для @модель.

API: GET https://openrouter.ai/api/v1/models (ключ не обязателен для чтения списка).

Цены в ответе — USD за 1 токен (строки). Переводим в USD за 1M токенов
для сопоставления с полями price_input / price_output в models_cache.json.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OUT_NAME = "openrouter_cache.json"
USER_AGENT = "MODEL-Agent-fetch/1.0 (+https://openrouter.ai)"


def usd_per_token_to_per_1m(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value) * 1_000_000.0
    except (TypeError, ValueError):
        return None


def compact_model(m: dict[str, Any]) -> dict[str, Any] | None:
    """Оставляем только полезное для сравнения цен; id используем как ключ снаружи."""
    pr = m.get("pricing")
    if not isinstance(pr, dict):
        return None
    p_in = usd_per_token_to_per_1m(pr.get("prompt"))
    p_out = usd_per_token_to_per_1m(pr.get("completion"))
    if p_in is None and p_out is None:
        return None
    # OpenRouter иногда отдаёт отрицательные плейсхолдеры (напр. auto-router) — не кэшируем
    if (p_in is not None and p_in < 0) or (p_out is not None and p_out < 0):
        return None
    out: dict[str, Any] = {
        "name": m.get("name") or m.get("id", ""),
        "context_length": int(m.get("context_length") or 0),
    }
    if p_in is not None:
        out["price_input_per_1m"] = round(p_in, 6)
    if p_out is not None:
        out["price_output_per_1m"] = round(p_out, 6)
    return out


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    out_path = root / "_ДАННЫЕ" / OUT_NAME

    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
    except urllib.error.URLError as e:
        print(f"Ошибка HTTP: {e}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON: {e}", file=sys.stderr)
        return 1

    rows = payload.get("data")
    if not isinstance(rows, list):
        print("Ответ: ожидается ключ data — массив", file=sys.stderr)
        return 1

    models: dict[str, dict[str, Any]] = {}
    skipped = 0
    for m in rows:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid or not isinstance(mid, str):
            skipped += 1
            continue
        c = compact_model(m)
        if c is None:
            skipped += 1
            continue
        models[mid] = c

    doc = {
        "version": "1.0",
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_url": OPENROUTER_MODELS_URL,
        "note": "Цены: USD за 1M токенов (из полей prompt/completion OpenRouter, ×1e6). "
        "Сопоставляй с models_cache по cursor_id. ELO/SWE — только из models_cache.json.",
        "model_count": len(models),
        "skipped_no_pricing": skipped,
        "models": dict(sorted(models.items(), key=lambda x: x[0])),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"OK {out_path.relative_to(root)} — моделей с ценой: {len(models)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
