#!/usr/bin/env python3
"""Read-only smoke-test всех API-разделов без HTTP-авторизации."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import api  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    start, end = args.start.isoformat(), args.end.isoformat()

    overview = api.overview(None, frm=start, to=end)
    series = api.series(None, frm=start, to=end)
    managers = api.managers(None, frm=start, to=end)
    speed = api.speed(None, frm=start, to=end)
    sources = api.sources(None, frm=start, to=end)
    activity = api.activity(None, frm=start, to=end)
    table = api.table(None, frm=start, to=end, scope="all")
    plans = api.get_plans(None, frm=start, to=end)
    state = {
        "funnels": api.funnels(None),
        "stuck": api.stuck(None),
        "loss_reasons": api.loss_reasons(None),
        "meta": api.meta(None),
        "data_quality": api.data_quality(None),
        "status": api.status(None),
    }

    od = overview["data"]
    totals = table["data"]["totals"]
    checks = {
        "overview_table_new_leads": od["new_leads"] == totals["new_leads"],
        "overview_table_paid": od["paid_count"] == totals["paid"],
        "overview_table_revenue": od["revenue"] == totals["revenue"],
        "overview_table_invoice_count": od["invoice_count"] == totals["invoice_count"],
        "overview_table_invoice_sum": od["invoice_sum"] == totals["invoice_sum"],
        "overview_table_avg_check": od["avg_check"] == totals["avg_check"],
        "overview_table_lead_conversion":
            abs(od["conv_lead_deal"] - totals["conv_lead_deal"]) < 0.01,
        "overview_table_repeat_conversion":
            abs(od["conv_repeat"] - totals["conv_repeat"]) < 0.01,
        "series_days_match": series["days"] == table["data"]["days"],
        "manager_rows": isinstance(managers["data"]["rows"], list),
        "source_rows": isinstance(sources["data"]["rows"], list),
        "speed_payload": "buckets" in speed["data"],
        "activity_payload": "daily" in activity["data"],
        "plans_payload": "period" in plans,
        "state_payloads": all(isinstance(value, dict) for value in state.values()),
    }
    result = {
        "range": {"from": start, "to": end},
        "checks": checks,
        "counts": {
            "days": len(series["days"]),
            "managers": len(managers["data"]["rows"]),
            "sources": len(sources["data"]["rows"]),
        },
        "ok": all(checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
