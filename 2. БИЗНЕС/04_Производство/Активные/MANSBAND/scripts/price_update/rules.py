"""Правила прайса как данные (созвон 09.09): строка = условие по имени вида
(`product.name` в МойСклад) + целевая цена в рублях. Матчинг по имени, не по
догадке — что не совпало ни с одним правилом, идёт блокером в отчёт `--scan`,
а не тихо остаётся со старой ценой без предупреждения.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from config import RULES_FILE


def load_rules(path: Path = RULES_FILE) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules") or []
    seen_ids = set()
    for r in rules:
        if r["id"] in seen_ids:
            raise ValueError(f"Дублирующийся id правила в {path.name}: {r['id']}")
        seen_ids.add(r["id"])
        if "match" not in r or not r["match"]:
            raise ValueError(f"Правило {r['id']} без match — нечем матчить")
    return rules


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split())


def rule_matches(rule: Dict[str, Any], product_name: str) -> bool:
    """AND между разными видами условий, OR внутри списка одного вида —
    так же, как `suitPriceOf` в кассе (packages/shared/src/suitPrices.ts)."""
    name = _norm(product_name)
    m = rule.get("match") or {}
    if "nameEquals" in m:
        if not any(name == _norm(v) for v in m["nameEquals"]):
            return False
    if "nameIncludesAll" in m:
        if not all(_norm(v) in name for v in m["nameIncludesAll"]):
            return False
    if "nameIncludesAny" in m:
        if not any(_norm(v) in name for v in m["nameIncludesAny"]):
            return False
    return True


def match_active_rules(rules: List[Dict[str, Any]], product_name: str) -> List[Dict[str, Any]]:
    return [r for r in rules if r.get("active", True) and rule_matches(r, product_name)]
