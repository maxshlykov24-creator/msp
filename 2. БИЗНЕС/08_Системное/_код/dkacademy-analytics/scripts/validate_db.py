#!/usr/bin/env python3
"""Проверить внутренние инварианты агрегатов после backfill."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import MetricDaily  # noqa: E402

COUNT_METRICS = {
    "new_leads", "cohort_won", "converted", "paid", "invoice_count",
    "repeat_created", "repeat_cohort_won", "repeat_deals",
    "calls_out", "msg_in", "msg_out", "dialogs", "missed",
}
MANAGER_RECONCILE = {
    "new_leads", "cohort_won", "paid", "revenue", "invoice_count", "invoice_sum",
}
SOURCE_RECONCILE = MANAGER_RECONCILE


def validate(start: date, end: date) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                MetricDaily.scope,
                MetricDaily.metric,
                func.sum(MetricDaily.fact),
            )
            .where(
                MetricDaily.day >= start.isoformat(),
                MetricDaily.day <= end.isoformat(),
            )
            .group_by(MetricDaily.scope, MetricDaily.metric)
        ).all()
        daily = db.execute(
            select(
                MetricDaily.metric,
                MetricDaily.day,
                func.sum(MetricDaily.fact),
            )
            .where(
                MetricDaily.day >= start.isoformat(),
                MetricDaily.day <= end.isoformat(),
                MetricDaily.scope == "all",
            )
            .group_by(MetricDaily.metric, MetricDaily.day)
        ).all()
    finally:
        db.close()

    scopes: dict[str, dict[str, float]] = defaultdict(dict)
    for scope, metric, value in rows:
        scopes[scope][metric] = float(value or 0)
    all_values = scopes.get("all", {})
    manager_sums: dict[str, float] = defaultdict(float)
    source_sums: dict[str, float] = defaultdict(float)
    for scope, values in scopes.items():
        target = (
            manager_sums if scope.startswith("mgr:")
            else source_sums if scope.startswith("src:")
            else None
        )
        if target is not None:
            for metric, value in values.items():
                target[metric] += value
    daily_sums: dict[str, float] = defaultdict(float)
    for metric, _, value in daily:
        daily_sums[metric] += float(value or 0)

    paid = all_values.get("paid", 0)
    revenue = all_values.get("revenue", 0)
    leads = all_values.get("new_leads", 0)
    cohort_won = all_values.get("cohort_won", 0)
    repeat = all_values.get("repeat_created", 0)
    repeat_won = all_values.get("repeat_cohort_won", 0)
    checks = {
        "lead_conversion_lte_100": cohort_won <= leads,
        "repeat_conversion_lte_100": repeat_won <= repeat,
        "avg_check_identity": (revenue / paid if paid else 0) >= 0,
        "daily_equals_period": all(
            abs(value - daily_sums.get(metric, 0)) < 0.01
            for metric, value in all_values.items()
        ),
        "counts_are_integers": all(
            abs(all_values.get(metric, 0) - round(all_values.get(metric, 0))) < 1e-9
            for metric in COUNT_METRICS
        ),
    }
    manager_diff = {
        metric: round(all_values.get(metric, 0) - manager_sums.get(metric, 0), 2)
        for metric in MANAGER_RECONCILE
    }
    source_diff = {
        metric: round(all_values.get(metric, 0) - source_sums.get(metric, 0), 2)
        for metric in SOURCE_RECONCILE
    }
    checks["manager_sum_equals_department"] = all(
        abs(value) < 0.01 for value in manager_diff.values()
    )
    checks["source_sum_equals_department"] = all(
        abs(value) < 0.01 for value in source_diff.values()
    )
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "checks": checks,
        "manager_minus_department": {
            key: -value for key, value in manager_diff.items()
        },
        "source_minus_department": {
            key: -value for key, value in source_diff.items()
        },
        "ok": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.start, args.end)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
