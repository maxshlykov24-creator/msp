"""Durable stage-event intake and task execution. Disabled unless explicitly configured.

The activation boundary is explicit. Existing obligations are imported separately;
turning this worker on must never replay the account's history.
"""
from __future__ import annotations

import asyncio
import logging
import httpx
from datetime import datetime, timezone
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from sqlalchemy import Integer, String, JSON, DateTime, select, text
from sqlalchemy.orm import Mapped, mapped_column

from app import crm_automation as a
from app import crm_automation_fields as f
from app import crm_automation_messages as messages
from app.crm_automation_rules import RULES, applicable, task_times
from app.database import get_engine, get_session_factory
from app.models import Base
from app.pipeline_guard import migration_blocked

log = logging.getLogger(__name__)
LOCK = 674001941


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRM_AUTOMATION_", extra="ignore")
    mode: Literal["off", "preview", "live"] = "off"
    activated_at: int = 0
    cutover_at: int = 0
    enabled_rules: str = ""
    allow_cutover: bool = False
    fields_enabled: bool = False
    sources_enabled: bool = False
    messages_enabled: bool = False
    kassa_url: str = "https://mansband-kassa.ru"
    service_key: SecretStr = SecretStr("")

    def rule_ids(self):
        ids = frozenset(x.strip() for x in self.enabled_rules.split(",") if x.strip())
        if ids - {r.id for r in RULES}:
            raise ValueError("unknown_automation_rule")
        if self.mode != "off" and self.activated_at <= 0:
            raise ValueError("explicit_activation_boundary_required")
        if self.mode != "off" and not ids:
            raise ValueError("explicit_enabled_rules_required")
        if self.mode == "live" and (not self.allow_cutover or not ids):
            raise ValueError("cutover_not_authorized")
        if self.mode == "live" and not self.service_key.get_secret_value():
            raise ValueError("kassa_binding_service_required")
        if self.mode == "live" and (self.cutover_at <= 0 or self.cutover_at < self.activated_at):
            raise ValueError("explicit_cutover_boundary_required")
        return ids


class Event(Base):
    __tablename__ = "crm_automation_event"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    at: Mapped[int] = mapped_column(Integer, index=True)
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), default="received", index=True)
    reason: Mapped[str] = mapped_column(String(160), default="")


class Cursor(Base):
    __tablename__ = "crm_automation_cursor"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    through: Mapped[int] = mapped_column(Integer)
    activated_at: Mapped[int] = mapped_column(Integer)


class EventRetry(Base):
    __tablename__ = "crm_automation_event_retry"
    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CourierHandoff(Base):
    __tablename__ = "crm_automation_courier_handoff"
    task_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    reason: Mapped[str] = mapped_column(String(100), default="")


class FieldWatch(Base):
    __tablename__ = "crm_automation_field_watch"
    lead_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    issues: Mapped[list] = mapped_column(JSON, default=list)


TABLES = [*a.TABLES, Event.__table__, Cursor.__table__, EventRetry.__table__, CourierHandoff.__table__,
          f.FieldExecution.__table__, FieldWatch.__table__, *messages.TABLES]


class Client:
    def __init__(self, wrapped, settings=None):
        self.wrapped = wrapped
        self.settings = settings or Settings()

    def get(self, path, **params):
        return self.wrapped.get(path, **params)

    def post(self, path, body):
        response = self.wrapped.amo.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def patch(self, path, body):
        response = self.wrapped.amo.patch(path, json=body)
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    def kassa_state(self, lead_id):
        key = self.settings.service_key.get_secret_value()
        if not key: raise ValueError("kassa_binding_service_required")
        with httpx.Client(base_url=self.settings.kassa_url, timeout=15,
                          headers={"X-Automation-Key": key}) as client:
            response = client.get(f"/api/internal/crm-automation/leads/{lead_id}")
            response.raise_for_status()
            return response.json()

    def courier_completed(self, job):
        key = self.settings.service_key.get_secret_value()
        if not key: raise ValueError("kassa_binding_service_required")
        with httpx.Client(base_url=self.settings.kassa_url, timeout=15,
                          headers={"X-Automation-Key": key}) as client:
            response = client.post(f"/api/internal/crm-automation/leads/{job.lead_id}/courier-completed",
                json={"task_id":job.task_id,"occurrence":job.occurrence})
            response.raise_for_status()
            result = response.json()
            if result.get("state") != "acknowledged" or result.get("task_id") != job.task_id or result.get("lead_id") != job.lead_id:
                raise ValueError("invalid_handoff_acknowledgement")


def collect(db, c, settings: Settings, until: int, *, event_type="lead_status_changed") -> int:
    """Advance only after every page is validated and durably recorded."""
    if event_type not in ("lead_status_changed", "lead_added"):
        raise ValueError("unsupported_event_type")
    cursor_id = "stage-v1" if event_type == "lead_status_changed" else "creation-v1"
    cursor = db.get(Cursor, cursor_id)
    if cursor and cursor.activated_at != settings.activated_at:
        raise ValueError("activation_boundary_changed")
    start = max(settings.activated_at, (cursor.through if cursor else settings.activated_at) - 300)
    if until < start:
        raise ValueError("clock_before_cursor")
    rows = []
    for page in range(1, 101):
        data = c.get("/api/v4/events", page=page, limit=100, **{
            "filter[entity]": "lead", "filter[type]": event_type,
            "filter[created_at][from]": start, "filter[created_at][to]": until})
        batch = data.get("_embedded", {}).get("events", [])
        rows.extend(batch)
        if not data.get("_links", {}).get("next") and len(batch) < 100:
            break
    else:
        raise ValueError("event_scan_incomplete")
    parsed = []
    for item in rows:
        if item.get("entity_type") != "lead" or item.get("type") != event_type:
            raise ValueError("unexpected_event_type")
        if event_type == "lead_status_changed":
            before, after = item["value_before"][0]["lead_status"], item["value_after"][0]["lead_status"]
            before = {k: int(before[k]) for k in ("id", "pipeline_id")}
            after = {k: int(after[k]) for k in ("id", "pipeline_id")}
        else:
            # lead_added does not carry its initial stage. Resolve only after
            # the status-event cursor covers the current lead snapshot.
            before, after = {"kind": "lead_added"}, {}
        at = int(item["created_at"])
        if not start <= at <= until:
            raise ValueError("event_outside_requested_interval")
        if not item.get("id") or int(item["entity_id"]) <= 0:
            raise ValueError("invalid_event_identity")
        parsed.append((str(item["id"]), int(item["entity_id"]), at, before, after))
    added = 0
    for key, lead_id, at, before, after in parsed:
        if db.get(Event, key) is None:
            db.add(Event(id=key, lead_id=lead_id, at=at, before=before, after=after))
            db.flush()
            added += 1
        if db.get(FieldWatch, lead_id) is None:
            db.add(FieldWatch(lead_id=lead_id))
            db.flush()
    if cursor is None:
        cursor = Cursor(id=cursor_id, through=until, activated_at=settings.activated_at)
        db.add(cursor)
    else:
        cursor.through = until
    db.commit()
    return added


def migration_event(db, event):
    if event.before.get("kind") == "lead_added": return False
    entry = db.get(a.MigrationEntry, event.lead_id)
    if entry is None or entry.target.get("transition_event_id") != event.id:
        return False
    if event.before["pipeline_id"] == event.after["pipeline_id"]:
        return False
    return (entry.before.get("pipeline_id") == event.before["pipeline_id"]
            and entry.before.get("status_id") == event.before["id"]
            and entry.target.get("pipeline_id") == event.after["pipeline_id"]
            and entry.target.get("status_id") == event.after["id"])


def process_event(db, c, event: Event, catalog: a.Catalog, enabled_rules: frozenset[str]):
    if event.state not in ("received", "held", "blocked"):
        return
    later = list(db.scalars(select(Event).where(Event.lead_id == event.lead_id, Event.at >= event.at,
                                               Event.id != event.id, Event.state != "migration")))
    later = [other for other in later if other.before.get("kind") != "lead_added"]
    creation = event.before.get("kind") == "lead_added"
    if any(other.at > event.at for other in later):
        event.state, event.reason = "superseded", "newer_stage_event"
    elif later and creation:
        event.state, event.reason = "superseded", "stage_event_at_creation"
    elif later:
        event.state, event.reason = "blocked", "ambiguous_same_second_events"
    elif migration_event(db, event):
        event.state, event.reason = "migration", "no_business_replay"
    else:
        lead = c.get(f"/api/v4/leads/{event.lead_id}")
        if creation and not event.after:
            cursor = db.get(Cursor, "stage-v1")
            updated_at = lead.get("updated_at")
            if not cursor or not isinstance(updated_at, int) or updated_at > cursor.through:
                event.state, event.reason = "held", "creation_snapshot_not_covered"
                db.commit()
                return
            if lead.get("created_at") != event.at:
                event.state, event.reason = "blocked", "creation_time_mismatch"
                db.commit()
                return
            event.after = {"pipeline_id": lead["pipeline_id"], "id": lead["status_id"]}
        migration = db.get(a.MigrationEntry, event.lead_id)
        if migration_blocked(lead) or (migration and migration.state not in ("preview", "verified", "rolled_back")):
            event.state, event.reason = "held", "migration_guard"
        elif lead.get("pipeline_id") != event.after["pipeline_id"] or lead.get("status_id") != event.after["id"]:
            event.state, event.reason = "superseded", "current_stage_differs"
        elif a.is_test(lead) or lead.get("is_deleted"):
            event.state, event.reason = "excluded", "test_or_deleted"
        else:
            for rule in RULES:
                if rule.id not in enabled_rules or not applicable(rule, lead, catalog):
                    continue
                if creation and rule.id not in ("new", "qualify", "promise"):
                    continue  # Native rules audited as transition-only.
                try:
                    available, due = task_times(rule, lead, datetime.fromtimestamp(event.at, timezone.utc))
                except ValueError:
                    event.state, event.reason = "blocked", "missing_required_date"
                    db.commit()
                    return
                # One obligation per genuine stage entry; subsequent date edits
                # revise it instead of creating a second task for the same visit.
                a.plan_task(db, rule.id, event.lead_id, "entry:" + event.id,
                    text=rule.text, due=due, responsible=int(lead.get("responsible_user_id") or 0),
                    task_type=rule.task_type, available_at=available)
            event.state, event.reason = "planned", ""
    db.commit()


def pending_events(db):
    fresh = list(db.scalars(select(Event).where(Event.state == "received")
        .order_by(Event.at, Event.id).limit(150)))
    retries = list(db.scalars(select(Event).outerjoin(EventRetry, EventRetry.event_id == Event.id)
        .where(Event.state.in_(("held", "blocked")))
        .order_by(EventRetry.checked_at.asc().nulls_first(), Event.at, Event.id).limit(50)))
    for event in retries:
        db.merge(EventRetry(event_id=event.id, checked_at=datetime.now(timezone.utc)))
    db.commit()
    return fresh + retries


def tick(settings: Settings):
    ids = settings.rule_ids()
    if settings.mode == "off": return
    from app.reservation_reminders import Clients, ReminderSettings
    with get_engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK}).scalar(): return
        try:
            with get_session_factory()() as db:
                raw = Clients(db, ReminderSettings())
                try:
                    c = Client(raw, settings)
                    through = int(datetime.now(timezone.utc).timestamp()) - 30
                    collect(db, c, settings, through)
                    collect(db, c, settings, through, event_type="lead_added")
                    catalog = a.Catalog(c.get("/api/v4/leads/pipelines").get("_embedded", {}).get("pipelines", []))
                    for event in pending_events(db):
                        process_event(db, c, event, catalog, ids)
                    if settings.fields_enabled:
                        try:
                            process_fields(db, c, catalog, preview=settings.mode != "live", sources_enabled=settings.sources_enabled)
                        except Exception as exc:
                            db.rollback()
                            log.error("CRM field processing: %s",type(exc).__name__)
                    if settings.mode == "live":
                        reconcile_pending(db, c, ids)
                        dispatch(db, c, catalog, ids, cutover_at=settings.cutover_at)
                        handoff_completed_couriers(db, c, catalog, ids)
                    if settings.messages_enabled:
                        for direction in ('incoming','outgoing'):
                            messages.collect_chats(db,raw,settings.activated_at,through,direction)
                        messages.plan_night(db,raw,datetime.now(timezone.utc),preview=settings.mode!='live',cutover_at=settings.cutover_at)
                finally:
                    raw.close()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})


def process_fields(db, c, catalog, *, preview=True, sources_enabled=False):
    """Rotate watched leads so date/manual edits are observed even without a stage change.

    Stage history is independent of task supersession. Only the collected,
    fully covered interval is eligible; no synthetic backfill of earlier history.
    """
    cursor = db.get(Cursor,"stage-v1")
    if cursor is None: return
    fields=[]
    if sources_enabled:
        from app.message_templates import all_pages
        fields=all_pages(c,'/api/v4/leads/custom_fields','custom_fields')
    watches = list(db.scalars(select(FieldWatch).order_by(FieldWatch.checked_at).limit(25)))
    for watch in watches:
        watch.checked_at = datetime.now(timezone.utc)
        db.commit()
        lead = c.get(f"/api/v4/leads/{watch.lead_id}")
        migration = db.get(a.MigrationEntry,watch.lead_id)
        if migration_blocked(lead) or (migration and migration.state not in ("preview","verified","rolled_back")):
            watch.issues=["migration_guard"];db.commit();continue
        updated = lead.get("updated_at")
        if not isinstance(updated,int) or updated > cursor.through:
            watch.issues=["history_not_caught_up"];db.commit();continue
        if not preview:
            problem = binding_issue(lead,c.kassa_state(watch.lead_id))
            if problem:
                watch.issues=[problem];db.commit();continue
        history = [{"lead_id":e.lead_id,"at":e.at,"after":e.after,"migration":migration_event(db,e)}
            for e in db.scalars(select(Event).where(Event.lead_id==watch.lead_id,
                Event.at>=cursor.activated_at,Event.at<=cursor.through))
            if e.before.get("kind") != "lead_added"]
        owned, generation = {}, "initial"
        for applied in db.scalars(select(f.FieldExecution).where(
                f.FieldExecution.lead_id==watch.lead_id,f.FieldExecution.state=="applied")
                .order_by(f.FieldExecution.updated_at,f.FieldExecution.id)):
            generation = applied.id
            for field in applied.payload:
                owned[field["field_id"]]=field["values"]
        source=f.FieldProjection()
        if sources_enabled:
            creations=[e for e in db.scalars(select(Event).where(Event.lead_id==watch.lead_id))
                if e.before.get('kind')=='lead_added' and not migration_event(db,e)]
            observed_creation=creations[0].at if len(creations)==1 else None
            source=f.verified_source_projection(c,lead,fields,created_at=observed_creation,through=cursor.through)
        def project(current):
            past=f.project_stage_history(current,catalog,history)
            present=f.project_fields(current,catalog,owned_values=owned)
            attribution=source if not sources_enabled or (current.get('updated_at')==lead.get('updated_at') and current.get('custom_fields_values')==lead.get('custom_fields_values')) else f.FieldProjection(issues=['source_snapshot_changed'])
            combined=past.fields+present.fields+attribution.fields
            protected=[field['field_id'] for field in combined if field['field_id'] in owned
                and not f.fields_match(current,[{'field_id':field['field_id'],'values':owned[field['field_id']]}])]
            return f.FieldProjection(fields=[field for field in combined if field['field_id'] not in protected],
                issues=past.issues+present.issues+attribution.issues+[f'manual_change_preserved:{fid}' for fid in protected])
        pending = list(db.scalars(select(f.FieldExecution).where(f.FieldExecution.lead_id==watch.lead_id,
            f.FieldExecution.state.in_(("planned","inflight","uncertain")))))
        for job in pending:
            f.execute_fields(db,c,job,project,preview=preview)
        fresh=c.get(f"/api/v4/leads/{watch.lead_id}") if pending and not preview else lead
        projection=project(fresh)
        watch.issues=projection.issues
        job=f.plan_fields(db,fresh,"fields-v1:"+generation,projection)
        db.commit()
        if job: f.execute_fields(db,c,job,project,preview=preview)


def handoff_completed_couriers(db, c, catalog, ids):
    if "courier" not in ids: return
    jobs = list(db.scalars(select(a.TaskExecution).outerjoin(CourierHandoff,
        CourierHandoff.task_id == a.TaskExecution.task_id).where(
        a.TaskExecution.rule_id == "courier", a.TaskExecution.state == "completed",
        a.TaskExecution.reason == "completed_externally",
        (CourierHandoff.task_id.is_(None)) | (CourierHandoff.state != "acknowledged")
    ).order_by(a.TaskExecution.updated_at).limit(25)))
    for job in jobs:
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        origin = db.get(Event, job.occurrence.removeprefix("entry:"))
        if origin is None: continue
        newer = list(db.scalars(select(Event).where(Event.lead_id == job.lead_id,
            Event.at >= origin.at, Event.id != origin.id)))
        if any(e.before.get("kind") != "lead_added" for e in newer): continue
        lead = c.get(f"/api/v4/leads/{job.lead_id}")
        migration = db.get(a.MigrationEntry,job.lead_id)
        if migration_blocked(lead) or (migration and migration.state not in ("preview","verified","rolled_back")): continue
        if a.is_test(lead) or lead.get("is_deleted") or catalog.stage(lead) != "Собран": continue
        if binding_issue(lead,c.kassa_state(job.lead_id),required=True): continue
        actual = c.get(f"/api/v4/tasks/{job.task_id}")
        if (not actual.get("is_completed") or actual.get("entity_id") != job.lead_id or
            actual.get("entity_type") != "leads" or actual.get("text") != job.payload["text"]): continue
        receipt = db.get(CourierHandoff,job.task_id)
        if receipt is None:
            receipt = CourierHandoff(task_id=job.task_id)
            db.add(receipt);db.commit()
        try:
            # This endpoint is idempotent and rechecks CRM completion. A timeout
            # may be retried with the same task ID without reopening local work.
            c.courier_completed(job)
            receipt.state,receipt.reason="acknowledged",""
        except Exception:
            receipt.state,receipt.reason="pending","handoff_not_confirmed"
        db.commit()


def reconcile_pending(db, c, ids):
    """Read both open and completed tasks; an absent match never permits POST."""
    jobs = list(db.scalars(select(a.TaskExecution).where(
        a.TaskExecution.state.in_(("inflight", "uncertain", "update_inflight", "update_uncertain"))
    ).order_by(a.TaskExecution.updated_at).limit(25)))
    for job in jobs:
        if job.rule_id not in ids: continue
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        if job.state in ("update_inflight", "update_uncertain"):
            result = a.reconcile_task_revision(db, job, c.get(f"/api/v4/tasks/{job.task_id}"))
            if result == "ownership_conflict":
                job.reason = result
                db.commit()
            continue
        tasks = []
        seen = set()
        for page in range(1, 101):
            data = c.get("/api/v4/tasks", page=page, limit=250, **{
                "filter[entity_type]": "leads", "filter[entity_id]": job.lead_id,
                "order[id]": "asc"})
            batch = data.get("_embedded", {}).get("tasks", [])
            for task in batch:
                if task.get("entity_type") != "leads" or task.get("entity_id") != job.lead_id or task.get("id") in seen:
                    raise ValueError("inconsistent_task_scan")
                seen.add(task["id"])
            tasks.extend(batch)
            if not data.get("_links", {}).get("next") and len(batch) < 250:
                a.reconcile_task(db, job, tasks, complete_scan=True)
                break
        else:
            job.reason = "task_scan_incomplete"
            db.commit()


def binding_issue(lead, snapshot, *, required=False):
    if snapshot.get("version") != 1 or snapshot.get("lead_id") != lead["id"]:
        return "invalid_kassa_snapshot"
    bindings = snapshot.get("bindings")
    if not isinstance(bindings, list): return "invalid_kassa_snapshot"
    if not bindings: return "missing_kassa_binding" if required else None
    if len(bindings) != 1: return "multiple_kassa_bindings"
    binding = bindings[0]
    try:
        if int(binding.get("lead_id", 0)) != lead["id"]: return "invalid_kassa_snapshot"
    except (ValueError, TypeError): return "invalid_kassa_snapshot"
    if binding.get("isTest"): return "test_kassa_binding"
    if binding.get("edit_pending"): return "kassa_edit_pending"
    label = a.type_from_binding(binding)
    if label is None: return "unknown_kassa_base_type"
    values = a.field_values(lead, a.TYPE_FIELD)
    if values and values != [label]: return "kassa_type_conflict"
    return None


SHOP_TASK_KINDS = {
    "transfer_control": {"movement", "movement_accept"},
    "hold_control": {"reserve"},
    "assembly_control": {"assemble_cdek"},
}


def shop_control_needed(rule_id, snapshot):
    kinds = SHOP_TASK_KINDS.get(rule_id)
    if kinds is None:
        return True
    bindings = snapshot.get("bindings") or []
    if len(bindings) != 1 or not bindings[0].get("number"):
        return False
    return any(task.get("deal_number") == bindings[0]["number"]
               and task.get("kind") in kinds and task.get("status") == "pending"
               for task in snapshot.get("tasks") or [])


def dispatch(db, c, catalog, ids, *, cutover_at):
    if cutover_at <= 0: raise ValueError("explicit_cutover_boundary_required")
    rules = {r.id: r for r in RULES}
    jobs = list(db.scalars(select(a.TaskExecution).where(a.TaskExecution.state.in_(("planned", "created")))
                          .order_by(a.TaskExecution.updated_at).limit(25)))
    for job in jobs:
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        if job.rule_id not in ids: continue
        rule = rules[job.rule_id]
        event = db.get(Event, job.occurrence.removeprefix("entry:"))
        if event is None: continue  # Imported obligations need their explicit origin.
        if event.at < cutover_at and job.task_id is None:
            # Preview observations overlap old Digital/F5 obligations. They need
            # explicit adoption, never a replay when the mode changes to live.
            job.reason = "pre_cutover_obligation_requires_reconciliation"
            db.commit()
            continue
        lead = c.get(f"/api/v4/leads/{job.lead_id}")
        if migration_blocked(lead): continue
        snapshot = c.kassa_state(job.lead_id)
        problem = binding_issue(lead, snapshot, required=rule.id in SHOP_TASK_KINDS)
        if not problem and rule.id in ("promise","courier") and snapshot.get("bindings"):
            if snapshot.get("execution_owners",{}).get(rule.id) != "amocrm":
                problem = "execution_owner_not_switched"
        if problem:
            job.reason = problem
            db.commit()
            continue
        migration = db.get(a.MigrationEntry, job.lead_id)
        if migration and migration.state not in ("preview", "verified", "rolled_back"): continue
        later = list(db.scalars(select(Event).where(Event.lead_id == job.lead_id,
            Event.at >= event.at, Event.id != event.id, Event.state != "migration")))
        later = [other for other in later if other.before.get("kind") != "lead_added"]
        creation = event.before.get("kind") == "lead_added"
        if not creation and any(other.at == event.at for other in later): continue
        if (creation and later) or any(other.at > event.at for other in later) or not applicable(rule, lead, catalog):
            if job.state == "created":
                a.complete_owned_task(db, job, c, "Условие задачи больше не действует", preview=False)
            else:
                job.state, job.reason = "cancelled", "conditions_changed"
                db.commit()
            continue
        try:
            available, due = task_times(rule, lead, datetime.fromtimestamp(event.at, timezone.utc))
        except ValueError:
            job.reason = "missing_required_date"
            db.commit()
            continue
        if job.state == "planned" and not shop_control_needed(rule.id, snapshot):
            job.reason = "no_pending_kassa_work"
            db.commit()
            continue
        current = lambda value: applicable(rule, value, catalog)
        result = a.revise_task(db, job, c, due=due, responsible=int(lead.get("responsible_user_id") or 0),
                              available_at=available, current=current, preview=False)
        if result == "replanned":
            if rule.id in SHOP_TASK_KINDS:
                fresh_snapshot = c.kassa_state(job.lead_id)
                if binding_issue(lead, fresh_snapshot, required=True) or not shop_control_needed(rule.id, fresh_snapshot):
                    job.reason = "kassa_work_changed"
                    db.commit()
                    continue
            a.execute_task(db, job, c, enabled_rules=ids, current=current, preview=False)


async def worker(settings: Settings):
    while True:
        try:
            await asyncio.to_thread(tick, settings)
        except Exception as exc:
            log.error("CRM automation worker: %s", type(exc).__name__)
        await asyncio.sleep(15)
