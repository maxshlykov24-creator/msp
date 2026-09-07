#!/usr/bin/env python3
"""Независимый read-only аудит DKAcademy напрямую из amoCRM.

Не читает PostgreSQL и не импортирует collector/api. Результат содержит
агрегаты, lead_id/event id, промежуточные суммы и автоматические инварианты.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.amo_client import AmoClient  # noqa: E402
from app.config import settings  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
EVENT_HISTORY_START = date(2026, 3, 16)
SOURCE_FIELD_ID = 2816115
OPERATIONAL_METRICS = {
    "invoice_sum", "messages_in", "messages_out", "dialogs", "missed",
    "rt_avg", "first_rt_avg",
}


def ts(day: date, *, end: bool = False) -> int:
    moment = datetime.combine(day, datetime.max.time() if end else datetime.min.time(), MSK)
    return int(moment.timestamp())


def msk_day(value: int | None) -> str | None:
    return datetime.fromtimestamp(value, MSK).date().isoformat() if value else None


def source_of(lead: dict) -> str | None:
    for field in lead.get("custom_fields_values") or []:
        if field.get("field_id") == SOURCE_FIELD_ID:
            values = field.get("values") or []
            if values:
                value = str(values[0].get("value") or "").strip()
                return value or None
    return None


def status_after(event: dict) -> tuple[int | None, int | None]:
    raw = event.get("value_after")
    for value in raw if isinstance(raw, list) else [raw]:
        if not isinstance(value, dict):
            continue
        candidate = value.get("lead_status") or value.get("status") or value
        if not isinstance(candidate, dict):
            continue
        status_id = candidate.get("id") or candidate.get("status_id")
        pipeline_id = candidate.get("pipeline_id")
        try:
            return (
                int(status_id) if status_id is not None else None,
                int(pipeline_id) if pipeline_id is not None else None,
            )
        except (TypeError, ValueError):
            continue
    return None, None


def events(amo: AmoClient, types: list[str], start: int, end: int) -> list[dict]:
    params: dict[str, int | str] = {
        "filter[created_at][from]": start,
        "filter[created_at][to]": end,
        "limit": 100,
    }
    for index, event_type in enumerate(types):
        params[f"filter[type][{index}]"] = event_type
    return list(amo.paginate("/api/v4/events", "events", params, limit=100))


def leads(amo: AmoClient, field: str, start: int, end: int) -> list[dict]:
    params = {
        f"filter[{field}][from]": start,
        f"filter[{field}][to]": end,
        "with": "contacts",
        "limit": 250,
    }
    return list(amo.paginate("/api/v4/leads", "leads", params))


def leads_by_ids(amo: AmoClient, ids: set[int]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    clean = sorted(ids)
    for offset in range(0, len(clean), 100):
        params: dict[str, int | str] = {"with": "contacts", "limit": 250}
        for index, lead_id in enumerate(clean[offset:offset + 100]):
            params[f"filter[id][{index}]"] = lead_id
        for lead in amo.paginate("/api/v4/leads", "leads", params):
            result[lead["id"]] = lead
    return result


def call_notes(amo: AmoClient, start: int, end: int) -> list[dict]:
    params = {
        "filter[note_type][0]": "call_in",
        "filter[note_type][1]": "call_out",
        "filter[updated_at][from]": start,
        "filter[updated_at][to]": int(datetime.now(MSK).timestamp()),
        "limit": 250,
    }
    seen: set[int] = set()
    result = []
    for note in amo.paginate("/api/v4/leads/notes", "notes", params):
        created_at = int(note.get("created_at") or 0)
        note_id = note.get("id")
        if not start <= created_at <= end or note_id in seen:
            continue
        if isinstance(note_id, int):
            seen.add(note_id)
        result.append(note)
    return result


def response_pairs(chat_events: list[dict]) -> list[dict]:
    by_lead: dict[int, list[dict]] = defaultdict(list)
    for event in chat_events:
        lead_id = event.get("entity_id")
        if isinstance(lead_id, int):
            by_lead[lead_id].append(event)
    pairs: list[dict] = []
    for lead_id, items in by_lead.items():
        pending: dict | None = None
        first = True
        for event in sorted(items, key=lambda item: item.get("created_at") or 0):
            if event.get("type") == "incoming_chat_message" and pending is None:
                pending = event
            elif event.get("type") == "outgoing_chat_message" and pending:
                delta = max(
                    0.0,
                    (int(event.get("created_at") or 0) - int(pending.get("created_at") or 0)) / 60,
                )
                pairs.append({
                    "lead_id": lead_id,
                    "incoming_event_id": pending.get("id"),
                    "outgoing_event_id": event.get("id"),
                    "incoming_at": pending.get("created_at"),
                    "minutes": round(delta, 2),
                    "is_first": first,
                })
                first = False
                pending = None
    return pairs


def dashboard_values(base_url: str, start: date, end: date) -> dict | None:
    login = os.getenv("DKA_DASH_LOGIN")
    password = os.getenv("DKA_DASH_PASSWORD")
    if not base_url or not login or not password:
        return None
    with httpx.Client(base_url=base_url.rstrip("/"), follow_redirects=True, timeout=30) as client:
        response = client.post("/login", data={"login": login, "password": password})
        response.raise_for_status()
        params = {"from": start.isoformat(), "to": end.isoformat()}
        overview = client.get(
            "/api/overview",
            params=params,
        )
        overview.raise_for_status()
        table = client.get("/api/table", params={**params, "scope": "all"})
        table.raise_for_status()
        data = overview.json().get("data") or {}
        totals = (table.json().get("data") or {}).get("totals") or {}
        return {
            "new_leads": data.get("new_leads", 0),
            "cohort_won": data.get("converted", 0),
            "conv_lead_deal": data.get("conv_lead_deal", 0),
            "paid": data.get("paid_count", 0),
            "revenue": data.get("revenue", 0),
            "avg_check": data.get("avg_check", 0),
            "invoice_count": data.get("invoice_count", 0),
            "invoice_sum": data.get("invoice_sum", 0),
            "calls_out": data.get("calls_out", 0),
            "call_minutes": data.get("call_minutes", 0),
            "messages_in": data.get("messages_in", 0),
            "messages_out": data.get("messages_out", 0),
            "dialogs": totals.get("dialogs", 0),
            "missed": data.get("missed_dialogs", 0),
            "rt_avg": data.get("rt_avg", 0),
            "first_rt_avg": data.get("first_rt_avg", 0),
        }


def database_values(start: date, end: date) -> dict:
    """Прочитать агрегаты дашборда только для сравнения с независимым расчётом."""
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import MetricDaily

    db = SessionLocal()
    try:
        rows = db.execute(
            select(MetricDaily.metric, func.sum(MetricDaily.fact))
            .where(
                MetricDaily.day >= start.isoformat(),
                MetricDaily.day <= end.isoformat(),
                MetricDaily.scope == "all",
            )
            .group_by(MetricDaily.metric)
        ).all()
    finally:
        db.close()
    values = {metric: float(value or 0) for metric, value in rows}
    paid, leads_count = values.get("paid", 0), values.get("new_leads", 0)
    return {
        "new_leads": values.get("new_leads", 0),
        "cohort_won": values.get("cohort_won", 0),
        "paid": paid,
        "revenue": values.get("revenue", 0),
        "avg_check": round(values.get("revenue", 0) / paid) if paid else 0,
        "invoice_count": values.get("invoice_count", 0),
        "invoice_sum": values.get("invoice_sum", 0),
        "calls_out": values.get("calls_out", 0),
        "call_minutes": round(values.get("call_minutes", 0)),
        "messages_in": values.get("msg_in", 0),
        "messages_out": values.get("msg_out", 0),
        "dialogs": values.get("dialogs", 0),
        "missed": values.get("missed", 0),
        "rt_avg": round(
            values.get("rt_sum", 0) / values.get("rt_cnt", 0)
        ) if values.get("rt_cnt", 0) else 0,
        "first_rt_avg": round(
            values.get("first_rt_sum", 0) / values.get("first_rt_cnt", 0)
        ) if values.get("first_rt_cnt", 0) else 0,
        "conv_lead_deal": round(
            values.get("cohort_won", 0) / leads_count * 100, 2
        ) if leads_count else 0,
    }


def audit_period(
    amo: AmoClient,
    start: date,
    end: date,
    dashboard_url: str | None = None,
    status_history: list[dict] | None = None,
    compare_db: bool = False,
    include_communications: bool = True,
) -> dict:
    start_ts, end_ts = ts(start), ts(end, end=True)
    created = [
        lead for lead in leads(amo, "created_at", start_ts, end_ts)
        if lead.get("pipeline_id") == settings.pipeline_sales
    ]
    closed = [
        lead for lead in leads(amo, "closed_at", start_ts, end_ts)
        if lead.get("pipeline_id") == settings.pipeline_sales
        and lead.get("status_id") == settings.status_won
    ]
    created_ids = {lead["id"] for lead in created}
    cohort_won_ids = {
        lead["id"] for lead in created if lead.get("status_id") == settings.status_won
    }
    paid_ids = {lead["id"] for lead in closed}
    revenue_rows = [
        {"lead_id": lead["id"], "price": int(lead.get("price") or 0)}
        for lead in closed
    ]
    revenue = sum(row["price"] for row in revenue_rows)

    status_events = status_history if status_history is not None else events(
        amo, ["lead_status_changed"], ts(EVENT_HISTORY_START), end_ts
    )
    first_invoice: dict[int, dict] = {}
    for event in sorted(status_events, key=lambda item: item.get("created_at") or 0):
        status_id, pipeline_id = status_after(event)
        lead_id = event.get("entity_id")
        if (
            status_id == settings.status_invoice
            and pipeline_id in (None, settings.pipeline_sales)
            and isinstance(lead_id, int)
        ):
            first_invoice.setdefault(lead_id, event)
    period_invoice = {
        lead_id: event
        for lead_id, event in first_invoice.items()
        if start_ts <= int(event.get("created_at") or 0) <= end_ts
    }
    invoice_leads = leads_by_ids(amo, set(period_invoice))
    invoice_rows = [
        {
            "lead_id": lead_id,
            "event_id": event.get("id"),
            "event_at": event.get("created_at"),
            "price": int((invoice_leads.get(lead_id) or {}).get("price") or 0),
        }
        for lead_id, event in period_invoice.items()
        if (invoice_leads.get(lead_id) or {}).get("pipeline_id") == settings.pipeline_sales
    ]
    manager_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    source_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def add_dimension(lead: dict, metric: str, value: int) -> None:
        manager = str(lead.get("responsible_user_id") or "Не указан")
        source = source_of(lead) or "Не определён"
        manager_totals[manager][metric] += value
        source_totals[source][metric] += value

    for lead in created:
        add_dimension(lead, "new_leads", 1)
        if lead.get("status_id") == settings.status_won:
            add_dimension(lead, "cohort_won", 1)
    for lead in closed:
        add_dimension(lead, "paid", 1)
        add_dimension(lead, "revenue", int(lead.get("price") or 0))
    for row in invoice_rows:
        lead = invoice_leads[row["lead_id"]]
        add_dimension(lead, "invoice_count", 1)
        add_dimension(lead, "invoice_sum", row["price"])

    notes = call_notes(amo, start_ts, end_ts)
    chat = events(
        amo,
        ["incoming_chat_message", "outgoing_chat_message"],
        start_ts,
        end_ts,
    ) if include_communications else []
    unique_chat: dict[object, dict] = {}
    for event in chat:
        event_id = event.get("id")
        unique_chat.setdefault(event_id if event_id is not None else id(event), event)
    chat = list(unique_chat.values())
    talks = events(amo, ["talk_created"], start_ts, end_ts) if include_communications else []
    missed = (
        events(amo, ["talk_missed_event"], start_ts, end_ts)
        if include_communications else []
    )
    pairs = response_pairs(chat)
    avg_response = (
        sum(min(row["minutes"], 480) for row in pairs) / len(pairs) if pairs else 0
    )
    first_pairs = [row for row in pairs if row["is_first"]]
    first_avg = (
        sum(min(row["minutes"], 480) for row in first_pairs) / len(first_pairs)
        if first_pairs else 0
    )

    metrics = {
        "new_leads": len(created_ids),
        "cohort_won": len(cohort_won_ids),
        "conv_lead_deal": round(len(cohort_won_ids) / len(created_ids) * 100, 2)
        if created_ids else 0,
        "paid": len(paid_ids),
        "revenue": revenue,
        "avg_check": round(revenue / len(paid_ids)) if paid_ids else 0,
        "invoice_count": len(invoice_rows),
        "invoice_sum": sum(row["price"] for row in invoice_rows),
        "calls_out": sum(note.get("note_type") == "call_out" for note in notes),
        "call_minutes": round(
            sum(float((note.get("params") or {}).get("duration") or 0) for note in notes) / 60,
        ),
        "messages_in": (
            sum(event.get("type") == "incoming_chat_message" for event in chat)
            if include_communications else None
        ),
        "messages_out": (
            sum(event.get("type") == "outgoing_chat_message" for event in chat)
            if include_communications else None
        ),
        "dialogs": len({event.get("id") for event in talks}) if include_communications else None,
        "missed": len({event.get("id") for event in missed}) if include_communications else None,
        "rt_avg": round(avg_response) if include_communications else None,
        "first_rt_avg": round(first_avg) if include_communications else None,
    }
    details = {
        "new_lead_ids": sorted(created_ids),
        "cohort_won_ids": sorted(cohort_won_ids),
        "paid_ids": sorted(paid_ids),
        "revenue_rows": revenue_rows,
        "invoice_rows": invoice_rows,
        "call_note_ids": sorted(note["id"] for note in notes if note.get("id")),
        "chat_event_ids": sorted(event["id"] for event in chat if event.get("id")),
        "talk_event_ids": sorted(event["id"] for event in talks if event.get("id")),
        "missed_event_ids": sorted(event["id"] for event in missed if event.get("id")),
        "response_pairs": pairs,
    }
    quality_ids = {
        "zero_price": [
            lead["id"] for lead in closed if int(lead.get("price") or 0) == 0
        ],
        "no_contact": [
            lead["id"] for lead in closed
            if not ((lead.get("_embedded") or {}).get("contacts") or [])
        ],
        "no_source": [lead["id"] for lead in closed if not source_of(lead)],
        "no_responsible": [
            lead["id"] for lead in closed if not lead.get("responsible_user_id")
        ],
        "invalid_closed_at": [
            lead["id"] for lead in closed
            if not lead.get("closed_at")
            or int(lead.get("closed_at") or 0) < int(lead.get("created_at") or 0)
        ],
    }
    invariants = {
        "conversion_lte_100": metrics["conv_lead_deal"] <= 100,
        "avg_check_identity": metrics["avg_check"] == round(
            metrics["revenue"] / metrics["paid"] if metrics["paid"] else 0
        ),
        "unique_new_leads": len(created_ids) == len(created),
        "unique_paid_leads": len(paid_ids) == len(closed),
        "unique_invoice_leads": len({row["lead_id"] for row in invoice_rows})
        == len(invoice_rows),
        "unique_call_notes": len({note.get("id") for note in notes}) == len(notes),
        "unique_chat_events": len({event.get("id") for event in chat}) == len(chat),
        "unique_talk_events": len({event.get("id") for event in talks}) == len(talks),
        "unique_missed_events": len({event.get("id") for event in missed}) == len(missed),
        "cohort_won_subset": cohort_won_ids <= created_ids,
        "manager_sum_equals_department": all(
            sum(values.get(metric, 0) for values in manager_totals.values())
            == metrics[metric]
            for metric in (
                "new_leads", "cohort_won", "paid", "revenue",
                "invoice_count", "invoice_sum",
            )
        ),
        "source_sum_equals_department": all(
            sum(values.get(metric, 0) for values in source_totals.values())
            == metrics[metric]
            for metric in (
                "new_leads", "cohort_won", "paid", "revenue",
                "invoice_count", "invoice_sum",
            )
        ),
    }

    dashboard = (
        database_values(start, end)
        if compare_db
        else dashboard_values(dashboard_url or "", start, end)
    )
    differences = None
    if dashboard:
        differences = {
            metric: round(float(dashboard.get(metric, 0)) - float(value), 2)
            for metric, value in metrics.items()
            if value is not None
        }

    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "metrics": metrics,
        "details": details,
        "dimensions": {
            "managers": {key: dict(value) for key, value in manager_totals.items()},
            "sources": {key: dict(value) for key, value in source_totals.items()},
        },
        "data_quality": {
            "checked_won": len(closed),
            "counts": {key: len(ids) for key, ids in quality_ids.items()},
            "lead_ids": quality_ids,
        },
        "invariants": invariants,
        "dashboard": dashboard,
        "dashboard_minus_audit": differences,
        "operational_metrics": sorted(OPERATIONAL_METRICS),
        "ok": all(invariants.values())
        and (
            differences is None
            or all(
                abs(value) <= 0.01
                for metric, value in differences.items()
                if metric not in OPERATIONAL_METRICS
            )
        ),
    }


def parse_period(value: str) -> tuple[date, date]:
    try:
        start_raw, end_raw = value.split(":", 1)
        start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("период: YYYY-MM-DD:YYYY-MM-DD") from exc
    if start > end:
        start, end = end, start
    return start, end


def default_periods() -> list[tuple[date, date]]:
    today = datetime.now(MSK).date()
    current_start = today.replace(day=1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    last_full_day = today - timedelta(days=1)
    return [
        (previous_start, previous_end),
        (last_full_day - timedelta(days=6), last_full_day),
        (max(EVENT_HISTORY_START, today - timedelta(days=20)), today - timedelta(days=10)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", action="append", type=parse_period)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "audit_latest.json")
    parser.add_argument("--dashboard-url", default="https://dkacademy-analytics.ru")
    parser.add_argument(
        "--compare-db",
        action="store_true",
        help="сравнить прямой расчёт с metric_daily (не используется в формулах аудита)",
    )
    parser.add_argument(
        "--skip-communications",
        action="store_true",
        help="не читать тяжёлые чаты/SLA; они останутся операционными",
    )
    args = parser.parse_args()
    periods = args.period or default_periods()

    amo = AmoClient()
    try:
        latest_end = max(end for _, end in periods)
        status_history = events(
            amo,
            ["lead_status_changed"],
            ts(EVENT_HISTORY_START),
            ts(latest_end, end=True),
        )
        results = [
            audit_period(
                amo,
                start,
                end,
                dashboard_url=args.dashboard_url,
                status_history=status_history,
                compare_db=args.compare_db,
                include_communications=not args.skip_communications,
            )
            for start, end in periods
        ]
    finally:
        amo.close()
    payload = {
        "generated_at": datetime.now(MSK).isoformat(),
        "formula_version": "2026-07-20",
        "source": settings.amo_base_url,
        "periods": results,
        "ok": all(result["ok"] for result in results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
