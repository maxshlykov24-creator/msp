"""Synchronize the MANSBAND managers' shared amoCRM queue using the existing roster."""
import argparse
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
import json
import time

import httpx
from sqlalchemy import text

from app.campaign import OWNERS, duty, now
from app.database import get_engine, get_session_factory
from app.pipeline_guard import migration_blocked
from app.reservation_reminders import Clients, ReminderSettings

MANAGERS = frozenset(OWNERS.values())
CLOSED = {142, 143}
BATCH = 40
INTERVAL = 300
LOCK_ID = 809815724


def chunks(values):
    values = sorted(set(values))
    for start in range(0, len(values), BATCH):
        yield values[start:start + BATCH]


def ids_filter(key, values):
    return {f"filter[{key}][{i}]": v for i, v in enumerate(values)}


def rows(client, kind, params):
    seen = set()
    for page in range(1, 2001):
        time.sleep(.22)
        response = client.amo.get(f"/api/v4/{kind}", params={**params, "limit": 250, "page": page})
        response.raise_for_status()
        if response.status_code == 204:
            return
        body = response.json()
        batch = body["_embedded"][kind]
        for item in batch:
            key = item["talk_id" if kind == "talks" else "id"]
            if key in seen:
                raise RuntimeError(f"{kind}: unstable pagination")
            seen.add(key)
            yield item
        if not body.get("_links", {}).get("next") and len(batch) < 250:
            return
    raise RuntimeError(f"{kind}: incomplete pagination")


def lookup(client, kind, ids, **params):
    found = {}
    for group in chunks(ids):
        for item in rows(client, kind, {**params, **ids_filter("id", group)}):
            if item["id"] not in group:
                raise RuntimeError(f"{kind}: ignored ID filter")
            found[item["id"]] = item
    return found


def in_pool(lead):
    return (lead["responsible_user_id"] in MANAGERS and lead["status_id"] not in CLOSED
            and not lead.get("is_deleted") and not migration_blocked(lead))


def open_portfolio(client):
    stages = client.get("/api/v4/leads/pipelines")["_embedded"]["pipelines"]
    result = {}
    for pipeline in stages:
        stage_ids = [s["id"] for s in pipeline["_embedded"]["statuses"]
                     if s["id"] not in CLOSED and s["type"] != 1]
        if not stage_ids:
            continue
        params = {"with": "contacts", **ids_filter("responsible_user_id", sorted(MANAGERS))}
        for i, sid in enumerate(stage_ids):
            params[f"filter[statuses][{i}][pipeline_id]"] = pipeline["id"]
            params[f"filter[statuses][{i}][status_id]"] = sid
        for lead in rows(client, "leads", params):
            if (lead["pipeline_id"] != pipeline["id"] or lead["status_id"] not in stage_ids
                    or lead["responsible_user_id"] not in MANAGERS):
                raise RuntimeError("unexpected portfolio filter result")
            result[lead["id"]] = lead
    return result


@dataclass
class Plan:
    targets: dict = field(default_factory=lambda: {k: {} for k in ("contacts", "companies", "leads", "tasks")})
    held: dict = field(default_factory=lambda: defaultdict(set))
    task_parents: dict = field(default_factory=dict)
    closed_work: set = field(default_factory=set)
    talks: int = 0
    leads: int = 0

    def summary(self):
        return {"portfolio": self.leads, "talks": self.talks,
                "pending": {k: len(v) for k, v in self.targets.items()},
                "exceptions": {k: len(v) for k, v in self.held.items() if v}}


def collect(client, outgoing):
    plan = Plan()
    portfolio = open_portfolio(client)
    open_tasks = list(rows(client, "tasks", {"filter[is_completed]": 0,
                                            **ids_filter("responsible_user_id", sorted(MANAGERS))}))
    task_leads = {t["entity_id"] for t in open_tasks if t["entity_type"] == "leads"}
    for lid, lead in lookup(client, "leads", task_leads - portfolio.keys(), **{"with": "contacts"}).items():
        # Follow-up work survives closing the deal. Keep its historical owner.
        if lead["status_id"] in CLOSED and lead["responsible_user_id"] in MANAGERS and not migration_blocked(lead):
            plan.closed_work.add(lid)
            portfolio[lid] = lead
    linked = {k: defaultdict(set) for k in ("contacts", "companies")}
    for lid, lead in portfolio.items():
        for kind in linked:
            for item in lead["_embedded"][kind]:
                linked[kind][item["id"]].add(lid)
        if lid in plan.closed_work:
            continue
        if not in_pool(lead):
            plan.held["migration_leads"].add(lid)
            continue
        plan.leads += 1
        if lead["responsible_user_id"] == outgoing:
            plan.targets["leads"][lid] = {lid}

    for group in chunks(portfolio):
        query = {"filter[entity_type]": "lead", "filter[only_in_work]": 1, **ids_filter("entity_id", group)}
        for talk in rows(client, "talks", query):
            if talk["entity_type"] != "lead" or talk["entity_id"] not in group:
                raise RuntimeError("unexpected talk filter result")
            if talk["is_in_work"]:
                linked["contacts"][talk["contact_id"]].add(talk["entity_id"])
                plan.talks += 1

    # An open task can be attached directly to a contact or company outside an active deal.
    for task in open_tasks:
        if task["entity_type"] in linked:
            linked[task["entity_type"]].setdefault(task["entity_id"], set())
    parents = {}
    for kind, links in linked.items():
        parents[kind] = lookup(client, kind, links, **{"with": "leads"})
        outside = {r["id"] for item in parents[kind].values() for r in item["_embedded"]["leads"]} - portfolio.keys()
        extra_leads = lookup(client, "leads", outside)
        for entity_id, anchors in links.items():
            entity = parents[kind].get(entity_id)
            if entity is None:
                plan.held["unavailable_" + kind].add(entity_id)
                continue
            owner = entity["responsible_user_id"]
            refs = {r["id"] for r in entity["_embedded"]["leads"]}
            if owner not in MANAGERS:
                plan.held["other_owner_" + kind].add(entity_id)
                continue
            # A shared customer may also have a live deal outside this team's portfolio.
            if (anchors & plan.held["migration_leads"] or
                    any(r not in extra_leads or migration_blocked(extra_leads[r]) or extra_leads[r]["status_id"] not in CLOSED
                        for r in refs - portfolio.keys())):
                plan.held["shared_or_migrating_" + kind].add(entity_id)
                continue
            if owner == outgoing:
                plan.targets[kind][entity_id] = set(anchors) | (refs & portfolio.keys())

    for task in open_tasks:
        if task["is_completed"] or task["responsible_user_id"] != outgoing:
            continue
        tid, parent, eid = task["id"], task.get("entity_type"), task.get("entity_id")
        if "[MB-AUTO:" in (task.get("text") or "") and task["task_type_id"] in (3946574, 3946594):
            plan.held["personal_or_courier_tasks"].add(tid)
            continue
        if parent == "leads":
            if eid not in portfolio or (eid not in plan.closed_work and not in_pool(portfolio[eid])):
                plan.held["outside_portfolio_tasks"].add(tid)
                continue
            anchors = {eid}
        elif parent in parents:
            if (eid not in parents[parent] or parents[parent][eid]["responsible_user_id"] not in MANAGERS
                    or eid in plan.held["shared_or_migrating_" + parent]):
                plan.held["outside_portfolio_tasks"].add(tid)
                continue
            anchors = linked[parent][eid]
        elif not eid:
            anchors = set()
        else:
            plan.held["outside_portfolio_tasks"].add(tid)
            continue
        plan.targets["tasks"][tid] = anchors
        plan.task_parents[tid] = (parent, eid)
    return plan


def apply_batch(client, kind, group, plan, current, outgoing):
    targets = plan.targets[kind]
    refs = set().union(*(targets[i] for i in group))
    guards = lookup(client, "leads", refs, **{"with": "contacts"})
    related = kind in ("contacts", "companies")
    params = {"with": "leads"} if related else {}
    entities = guards if kind == "leads" else lookup(client, kind, group, **params)
    if related:
        linked = {r["id"] for item in entities.values() for r in item["_embedded"]["leads"]}
        guards.update(lookup(client, "leads", linked - guards.keys()))
    updates = []
    for eid in group:
        item = entities.get(eid)
        if not item or item["responsible_user_id"] != outgoing:
            continue
        if any(ref not in guards or migration_blocked(guards[ref])
               or guards[ref]["responsible_user_id"] not in MANAGERS
               or (guards[ref]["status_id"] in CLOSED and ref not in plan.closed_work)
               for ref in targets[eid]):
            continue
        if related:
            fresh_links = [r["id"] for r in item["_embedded"]["leads"]]
            if any(ref not in guards or migration_blocked(guards[ref])
                   or (guards[ref]["status_id"] not in CLOSED and not in_pool(guards[ref]))
                   for ref in fresh_links):
                continue
        if kind == "tasks" and (item["is_completed"] or
                (item.get("entity_type"), item.get("entity_id")) != plan.task_parents[eid]):
            continue
        updates.append({"id": eid, "responsible_user_id": current})
    if not updates:
        return
    if duty(now()) != current:
        raise RuntimeError("shift_changed")
    # Log the before/after pair and exact IDs before sending, including ambiguous attempts.
    print(json.dumps({"before": outgoing, "after": current, "kind": kind, "ids": [x["id"] for x in updates]}), flush=True)
    response = client.amo.patch(f"/api/v4/{kind}", json=updates)
    response.raise_for_status()
    accepted = {item["id"] for item in response.json()["_embedded"][kind]}
    if accepted != {item["id"] for item in updates}:
        raise RuntimeError(f"{kind}: incomplete PATCH acknowledgement")
    actual = lookup(client, kind, accepted)
    if any(actual.get(i, {}).get("responsible_user_id") != current for i in accepted):
        raise RuntimeError(f"{kind}: owner readback failed")


def reconcile(client, apply=False):
    owner = duty(now())
    outgoing, = MANAGERS - {owner}
    account = client.get("/api/v4/account")
    if account.get("subdomain") != "mansband":
        raise RuntimeError("unexpected amoCRM account")
    if not client.get(f"/api/v4/users/{owner}")["rights"]["is_active"]:
        raise RuntimeError("inactive_shift_owner")
    plan = collect(client, outgoing)
    print(json.dumps({"shift": owner, **plan.summary()}), flush=True)
    if not apply:
        return plan
    for kind, targets in plan.targets.items():
        for group in chunks(targets):
            apply_batch(client, kind, group, plan, owner, outgoing)
    if any(plan.targets.values()):
        remaining = collect(client, outgoing)
        print(json.dumps({"verified": remaining.summary()}), flush=True)
        if any(remaining.targets.values()):
            raise RuntimeError("pending_changes_after_verification")
    if duty(now()) != owner:
        raise RuntimeError("shift_changed")


def run_once(apply):
    with get_engine().connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}):
            raise RuntimeError("shift_worker_busy")
        try:
            with get_session_factory()() as db, closing(Clients(db, ReminderSettings())) as client:
                return reconcile(client, apply)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            run_once(args.apply)
        except Exception as exc:
            if not args.loop:
                raise
            reason = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else str(exc)[:120] if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            print("shift_handoff_failed", reason, flush=True)
        if not args.loop:
            break
        time.sleep(INTERVAL)
