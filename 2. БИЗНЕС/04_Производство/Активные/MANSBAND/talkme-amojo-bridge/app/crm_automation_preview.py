"""Read-only live preflight. Prints no names, contacts, tokens or message bodies.

Run inside the existing reminders environment:
    python -m app.crm_automation_preview
It does not import/create automation tables and cannot enable an executor.
"""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
import argparse

from sqlalchemy import select

from app.crm_automation import Catalog, PIPELINES, field_date, route_with_bindings
from app.crm_automation_rules import RULES, STAGE_DATE_FIELDS
from app.crm_automation_fields import project_fields
from app.database import get_session_factory
from app.message_templates import Document, Job, all_pages
from app.reservation_reminders import Clients, ReminderSettings


def read_preflight(c, rules: list[dict], queues: dict, bindings: list[dict] | None = None) -> dict:
    pipelines = all_pages(c, "/api/v4/leads/pipelines", "pipelines")
    fields = all_pages(c, "/api/v4/leads/custom_fields", "custom_fields")
    catalog = Catalog(pipelines)
    field_ids = {f["id"] for f in fields}
    result = {"at": datetime.now(timezone.utc).isoformat(), "mode": "read_only",
              "pipelines": [], "routes": [], "route_counts": {}, "missing_dates": {},
              "field_projection_issues": {}, "binding_snapshot_supplied": bindings is not None,
              "policy_errors": [], "message_rules": rules, "message_queues": queues}
    absent = PIPELINES - set(catalog.pipelines)
    if absent:
        raise ValueError("Missing pipeline IDs: " + str(sorted(absent)))
    for rule in RULES:
        for pid in rule.pipelines:
            if (pid, rule.stage) not in catalog.by_name:
                result["policy_errors"].append({"rule": rule.id, "pipeline": pid, "error": "stage_missing"})
        for field in (rule.date_field, rule.condition_field):
            if field and field not in field_ids:
                result["policy_errors"].append({"rule": rule.id, "field": field, "error": "field_missing"})
    counts, missing, issues = Counter(), Counter(), Counter()
    for pid in sorted(PIPELINES):
        p = catalog.pipelines[pid]
        states = [s for s in p["_embedded"]["statuses"] if s.get("type") != 1 and s["id"] not in (142, 143)]
        params = {}
        for i, state in enumerate(states):
            params[f"filter[statuses][{i}][pipeline_id]"] = pid
            params[f"filter[statuses][{i}][status_id]"] = state["id"]
        if not states:
            raise ValueError("no_open_stages")
        leads = all_pages(c, "/api/v4/leads", "leads", **params)
        stage_counts = Counter()
        for lead in leads:
            if lead.get("pipeline_id") != pid or lead.get("status_id") in (142, 143):
                raise ValueError("unexpected_scan_result")
            stage = catalog.stage(lead)
            stage_counts[stage or "unknown"] += 1
            decision = route_with_bindings(lead, catalog, bindings or [])
            for issue in project_fields(lead, catalog).issues:
                issues[str(pid) + ":" + issue] += 1
            counts[decision.state + ":" + decision.reason] += 1
            result["routes"].append({"lead_id": lead["id"], "from_pipeline": pid,
                                     "from_status": lead["status_id"], **asdict(decision)})
            field = STAGE_DATE_FIELDS.get(stage)
            if field and field_date(lead, field) is None:
                missing[str(pid) + ":" + stage] += 1
        result["pipelines"].append({"id": pid, "name": p["name"], "open_leads": len(leads),
                                     "stages": dict(stage_counts)})
    result["route_counts"], result["missing_dates"] = dict(counts), dict(missing)
    result["field_projection_issues"] = dict(issues)
    result["cutover_ready"] = False
    result["remaining_gates"] = ["f5_block_coverage",
        "digital_cutover_manifest", "kassa_binding_reconciliation", "message_migration_guard",
        "isolated_end_to_end_acceptance"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", help="Read-only minimal kassa binding snapshot JSON")
    args = parser.parse_args()
    bindings = None
    if args.bindings:
        with open(args.bindings) as stream:
            bindings = json.load(stream)
        if not isinstance(bindings, list):
            raise ValueError("bindings_must_be_list")
    with get_session_factory()() as db:
        c = Clients(db, ReminderSettings())
        try:
            rules = [{"id": d.id, "version": d.published_version,
                      "enabled": (d.active or {}).get("enabled"),
                      "pipelines": (d.active or {}).get("pipelines"),
                      "statuses": (d.active or {}).get("statuses")}
                     for d in db.scalars(select(Document)) if d.kind == "rule"]
            queues = {state: len(list(db.scalars(select(Job.id).where(Job.state == state))))
                      for state in ("ready", "inflight", "uncertain")}
            print(json.dumps(read_preflight(c, rules, queues, bindings), ensure_ascii=False))
        finally:
            c.close()


if __name__ == "__main__":
    main()
