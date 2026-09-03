#!/usr/bin/env python3
"""Проверка JSON кэша агента @модель. Код выхода 0 при успехе, иначе 1."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def require_keys(obj: dict[str, Any], keys: set[str], ctx: str) -> None:
    missing = keys - obj.keys()
    if missing:
        raise ValueError(f"{ctx}: отсутствуют ключи: {sorted(missing)}")


def validate_models_cache(data: dict[str, Any]) -> None:
    require_keys(data, {"version", "last_updated", "models"}, "models_cache.json")
    models = data["models"]
    if not isinstance(models, dict) or not models:
        raise ValueError("models_cache.json: поле models должно быть непустым объектом")

    required_model = {
        "name",
        "provider",
        "price_input",
        "price_output",
        "context_window",
        "available_in_cursor",
        "features",
        "strengths",
        "use_cases",
    }
    for key, m in models.items():
        if not isinstance(m, dict):
            raise ValueError(f"models[{key!r}]: ожидается объект")
        require_keys(m, required_model, f"models[{key!r}]")
        if not isinstance(m["price_input"], (int, float)) or m["price_input"] < 0:
            raise ValueError(f"models[{key!r}].price_input: ожидается число >= 0")
        if not isinstance(m["price_output"], (int, float)) or m["price_output"] < 0:
            raise ValueError(f"models[{key!r}].price_output: ожидается число >= 0")
        if not isinstance(m["context_window"], int) or m["context_window"] <= 0:
            raise ValueError(f"models[{key!r}].context_window: ожидается положительное целое")
        if not isinstance(m["available_in_cursor"], bool):
            raise ValueError(f"models[{key!r}].available_in_cursor: ожидается boolean")
        for list_key in ("features", "strengths", "use_cases"):
            if not isinstance(m[list_key], list) or not m[list_key]:
                raise ValueError(f"models[{key!r}].{list_key}: ожидается непустой массив")
        cid = m.get("cursor_id")
        if cid is not None and not isinstance(cid, str):
            raise ValueError(f"models[{key!r}].cursor_id: null или строка")


def validate_fast_track(data: dict[str, Any]) -> None:
    require_keys(data, {"version", "last_updated", "rules"}, "fast_track_rules.json")
    rules = data["rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("fast_track_rules.json: rules должен быть непустым массивом")
    required_rule = {"id", "keywords", "model", "confidence", "reason"}
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError(f"rules[{i}]: ожидается объект")
        require_keys(r, required_rule, f"rules[{i}]")
        if not isinstance(r["keywords"], list) or not r["keywords"]:
            raise ValueError(f"rules[{i}].keywords: непустой массив строк")
        if not isinstance(r["confidence"], int):
            raise ValueError(f"rules[{i}].confidence: ожидается int")


def validate_metadata(data: dict[str, Any]) -> None:
    require_keys(data, {"version", "last_updated"}, "metadata.json")


def validate_openrouter_cache(data: dict[str, Any]) -> None:
    require_keys(
        data,
        {"version", "fetched_at", "source_url", "model_count", "models"},
        "openrouter_cache.json",
    )
    models = data["models"]
    if not isinstance(models, dict) or not models:
        raise ValueError("openrouter_cache.json: models должен быть непустым объектом")
    for mid, m in models.items():
        if not isinstance(m, dict):
            raise ValueError(f"openrouter.models[{mid!r}]: ожидается объект")
        require_keys(m, {"name", "context_length"}, f"openrouter.models[{mid!r}]")
        if not isinstance(m["name"], str):
            raise ValueError(f"openrouter.models[{mid!r}].name: ожидается строка")
        if not isinstance(m["context_length"], int) or m["context_length"] < 0:
            raise ValueError(f"openrouter.models[{mid!r}].context_length: ожидается int >= 0")
        pin, pout = m.get("price_input_per_1m"), m.get("price_output_per_1m")
        if pin is None and pout is None:
            raise ValueError(f"openrouter.models[{mid!r}]: нужен хотя бы один из price_*_per_1m")
        for key, val in (("price_input_per_1m", pin), ("price_output_per_1m", pout)):
            if val is not None and (not isinstance(val, (int, float)) or val < 0):
                raise ValueError(f"openrouter.models[{mid!r}].{key}: ожидается число >= 0")


def load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError(f"{path.name}: корень должен быть объектом")
    return obj


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "_ДАННЫЕ"
    files = [
        (data_dir / "models_cache.json", validate_models_cache),
        (data_dir / "fast_track_rules.json", validate_fast_track),
        (data_dir / "metadata.json", validate_metadata),
    ]
    try:
        for path, validator in files:
            if not path.is_file():
                err(f"Нет файла: {path}")
                return 1
            data = load_json(path)
            validator(data)
            print(f"OK {path.name}")
        opt = data_dir / "openrouter_cache.json"
        if opt.is_file():
            data = load_json(opt)
            validate_openrouter_cache(data)
            print(f"OK {opt.name}")
        print("Все проверки пройдены.")
        return 0
    except (json.JSONDecodeError, ValueError) as e:
        err(str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
