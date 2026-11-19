"""Durable stage-event intake and task execution. Disabled unless explicitly configured.

The activation boundary is explicit. Existing obligations are imported separately;
turning this worker on must never replay the account's history.
"""
from __future__ import annotations

import asyncio
import logging
import httpx
from datetime import date, datetime, time, timedelta, timezone
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
    store_fields_live_from: int = 0
    ad_call_live_from: int = 0
    visit_task_live_from: int = 0
    typed_tasks_live_from: int = 0
    promise_users: dict[str, int] = {}
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

    def kassa_state(self, lead_id, *, include_promise=False):
        key = self.settings.service_key.get_secret_value()
        if not key: raise ValueError("kassa_binding_service_required")
        with httpx.Client(base_url=self.settings.kassa_url, timeout=15,
                          headers={"X-Automation-Key": key}) as client:
            response = client.get(f"/api/internal/crm-automation/leads/{lead_id}", params={"include":"promise"} if include_promise else {})
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
                    if settings.store_fields_live_from:
                        try:
                            process_store_updates(db, c, catalog, settings.store_fields_live_from, through)
                        except Exception as exc:
                            db.rollback()
                            log.error("Store field update: %s", type(exc).__name__)
                    if settings.fields_enabled:
                        try:
                            process_fields(db, c, catalog, preview=settings.mode != "live", sources_enabled=settings.sources_enabled, ad_call_live_from=settings.ad_call_live_from, store_scoped=bool(settings.store_fields_live_from))
                        except Exception as exc:
                            db.rollback()
                            log.error("CRM field processing: %s",type(exc).__name__)
                    if settings.mode == "live":
                        reconcile_pending(db, c, ids)
                        dispatch(db, c, catalog, ids, cutover_at=settings.cutover_at)
                        handoff_completed_couriers(db, c, catalog, ids)
                    elif settings.typed_tasks_live_from > 0:
                        reconcile_pending(db, c, ids)
                        dispatch(db, c, catalog, ids, cutover_at=settings.typed_tasks_live_from,
                                 pipelines={a.OFFLINE, a.ONLINE, a.RENTAL, a.COMPANY})
                        handoff_completed_couriers(db, c, catalog, ids)
                    if settings.visit_task_live_from > 0:
                        visit_tasks(db, c, datetime.now(timezone.utc), settings.visit_task_live_from)
                    if settings.messages_enabled:
                        for direction in ('incoming','outgoing'):
                            messages.collect_chats(db,raw,settings.activated_at,through,direction)
                        messages.plan_night(db,raw,datetime.now(timezone.utc),preview=settings.mode!='live',cutover_at=settings.cutover_at)
                finally:
                    raw.close()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})


STORE_FIELDS = {3383324, 3390831}
STORE_JOB_PREFIX = "store-details-v1:"


def apply_store_details(db, c, catalog, lead):
    """Only maintain the address and metro; never reclaim a manually changed value."""
    lid = lead["id"]
    if (lead.get("pipeline_id") not in a.PIPELINES or catalog.stage(lead) is None or
            a.is_test(lead) or migration_blocked(lead) or lead.get("is_deleted")):
        return
    migration = db.get(a.MigrationEntry, lid)
    if migration and migration.state not in ("preview", "verified", "rolled_back"):
        return
    jobs = list(db.scalars(select(f.FieldExecution).where(f.FieldExecution.lead_id == lid)
                           .order_by(f.FieldExecution.updated_at, f.FieldExecution.id)))
    owned = {}
    generation = "initial"
    for job in jobs:
        if job.state == "applied":
            for field in job.payload:
                if field["field_id"] in STORE_FIELDS:
                    owned[field["field_id"]] = field["values"]
                    generation = job.id

    def project(current):
        projection = f.project_fields(current, catalog, owned_values=owned)
        fields = [item for item in projection.fields if item["field_id"] in STORE_FIELDS and
                  (item["field_id"] not in owned or f.fields_match(current, [
                      {"field_id": item["field_id"], "values": owned[item["field_id"]]}]))]
        return f.FieldProjection(fields=fields)

    for job in jobs:
        if job.event_id.startswith(STORE_JOB_PREFIX) and job.state in ("inflight", "uncertain"):
            # Never send again on an ambiguous response; refresh ownership next pass.
            f.execute_fields(db, c, job, project, preview=False)
            return
    projection = project(lead)
    if not projection.fields:
        return
    if binding_issue(lead, c.kassa_state(lid)):
        return
    job = f.plan_fields(db, lead, STORE_JOB_PREFIX + str(lead.get("updated_at")) + ":" + generation, projection)
    db.commit()
    return f.execute_fields(db, c, job, project, preview=False)


def process_store_updates(db, c, catalog, boundary, through):
    from app.message_templates import all_pages
    cursor = db.get(Cursor, "store-details-v1")
    if boundary <= 0 or (cursor and boundary != cursor.activated_at):
        raise ValueError("invalid_store_field_boundary")
    start = max(boundary, cursor.through - 30 if cursor else boundary)
    if through < start:
        return
    outstanding = db.scalars(select(f.FieldExecution).where(
        f.FieldExecution.event_id.startswith(STORE_JOB_PREFIX),
        f.FieldExecution.state.in_(("planned", "uncertain", "inflight")))
        .order_by(f.FieldExecution.updated_at).limit(20)).all()
    for job in outstanding:
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        current = c.get(f"/api/v4/leads/{job.lead_id}")
        if job.state == "planned" and current.get("updated_at") != int(job.event_id.split(":")[1]):
            job.state, job.reason = "cancelled", "snapshot_changed"
            db.commit()
        else:
            apply_store_details(db, c, catalog, current)
    # A short fixed window and overlap keep paginated reads stable during live edits.
    end = min(through, start + 600)
    leads = all_pages(c, "/api/v4/leads", "leads", **{
        "filter[updated_at][from]": start, "filter[updated_at][to]": end, "order[id]": "asc"})
    for lead in leads:
        if not isinstance(lead.get("updated_at"), int) or not start <= lead["updated_at"] <= end:
            raise ValueError("store_field_filter_not_honored")
        apply_store_details(db, c, catalog, lead)
    if not cursor:
        cursor = Cursor(id="store-details-v1", activated_at=boundary, through=end)
        db.add(cursor)
    else:
        cursor.through = max(cursor.through, end)
    db.commit()


def process_fields(db, c, catalog, *, preview=True, sources_enabled=False, ad_call_live_from=0, store_scoped=False):
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
        # Reconcile a lost response even if both fields are already present.
        # The scoped job must never enter the broad field dispatch path.
        if ad_call_live_from:
            ad_pending = db.scalars(select(f.FieldExecution).where(
                f.FieldExecution.event_id == "shared-ad-call-v1",
                f.FieldExecution.lead_id == watch.lead_id,
                f.FieldExecution.state.in_(("inflight", "uncertain"))))
            for attempt in ad_pending:
                f.execute_fields(db, c, attempt, lambda _: f.FieldProjection(), preview=False)
        source=f.FieldProjection()
        if sources_enabled:
            creations=[e for e in db.scalars(select(Event).where(Event.lead_id==watch.lead_id))
                if e.before.get('kind')=='lead_added' and not migration_event(db,e)]
            observed_creation=creations[0].at if len(creations)==1 else None
            source=f.verified_source_projection(c,lead,fields,created_at=observed_creation,through=cursor.through)
            if ad_call_live_from > 0 and observed_creation and observed_creation >= ad_call_live_from:
                apply_shared_ad_source(db, c, lead, fields, source)
        def project(current):
            past=f.project_stage_history(current,catalog,history)
            present=f.project_fields(current,catalog,owned_values=owned)
            attribution=source if not sources_enabled or (current.get('updated_at')==lead.get('updated_at') and current.get('custom_fields_values')==lead.get('custom_fields_values')) else f.FieldProjection(issues=['source_snapshot_changed'])
            combined=past.fields+present.fields+attribution.fields
            if store_scoped:
                combined = [item for item in combined if item["field_id"] not in STORE_FIELDS]
            protected=[field['field_id'] for field in combined if field['field_id'] in owned
                and not f.fields_match(current,[{'field_id':field['field_id'],'values':owned[field['field_id']]}])]
            return f.FieldProjection(fields=[field for field in combined if field['field_id'] not in protected],
                issues=past.issues+present.issues+attribution.issues+[f'manual_change_preserved:{fid}' for fid in protected])
        pending = list(db.scalars(select(f.FieldExecution).where(f.FieldExecution.lead_id==watch.lead_id,
            f.FieldExecution.event_id != "shared-ad-call-v1",
            ~f.FieldExecution.event_id.startswith(STORE_JOB_PREFIX),
            f.FieldExecution.state.in_(("planned","inflight","uncertain")))))
        for job in pending:
            f.execute_fields(db,c,job,project,preview=preview)
        fresh=c.get(f"/api/v4/leads/{watch.lead_id}") if pending and not preview else lead
        projection=project(fresh)
        watch.issues=projection.issues
        job=f.plan_fields(db,fresh,"fields-v1:"+generation,projection)
        db.commit()
        if job: f.execute_fields(db,c,job,project,preview=preview)


def apply_shared_ad_source(db, c, lead, fields, source):
    """Independent cutover for newly observed calls. Never replay older leads."""
    if not f.shared_ad_call(lead) or source.issues or not source.fields:
        return
    if any(a.field_values(lead, fid) for fid in (3781223, 3134043)):
        return
    # A more specific UTM or another source stays with its own attribution rule.
    ids = {item["id"]: {opt["id"] for opt in item.get("enums") or [] if opt["value"] == "Другое"}
           for item in fields if item["id"] in (3781223, 3134043)}
    if len(source.fields) != 2 or any(
        field["values"][0]["enum_id"] not in ids.get(field["field_id"], set()) for field in source.fields
    ):
        return
    if binding_issue(lead, c.kassa_state(lead["id"])):
        return
    job = f.plan_fields(db, lead, "shared-ad-call-v1", source)
    db.commit()

    def still_current(current):
        if (current.get("updated_at") == lead.get("updated_at")
                and current.get("custom_fields_values") == lead.get("custom_fields_values")
                and f.shared_ad_call(current) and not a.is_test(current)
                and current.get("pipeline_id") in a.PIPELINES and not current.get("is_deleted")):
            return source
        return f.FieldProjection()
    f.execute_fields(db, c, job, still_current, preview=False)


VISIT_TASK = "approximate_store_visit"
VISIT_TEXT = "Уточнить, планирует ли клиент сегодня приехать в магазин, и зафиксировать договорённость"
OLD_VISIT_TEXT = "Наступила примерная дата встречи. Выяснить планирует ли клиент сегодня приехать в магазин"


def visit_tasks(db, c, now, live_from):
    from app.stage_notifications import CalendarSettings, calendar_date, calendar_scope
    from app.message_templates import Job, all_pages
    from app.pipeline_guard import messaging_allowed
    if live_from <= 0 or now.timestamp() < live_from:
        return
    settings = CalendarSettings()
    def visit_current(lead, target):
        stores = [v.get("enum_id") for field in lead.get("custom_fields_values") or []
                  if field["field_id"] == 3158921 for v in field.get("values") or []]
        return (messaging_allowed(lead) and not lead.get("is_deleted")
                and (lead.get("pipeline_id"), lead.get("status_id")) in calendar_scope(settings, "wants_visit_status")
                and calendar_date(lead, settings.approximate_date_field) == target
                and stores in ([4765787], [4765789]))
    ids = frozenset({VISIT_TASK})
    reconcile_pending(db, c, ids)
    today = now.astimezone(a.MSK).date()
    # Only the server's own tasks are revised on a changed date/stage.
    for task in db.scalars(select(a.TaskExecution).where(a.TaskExecution.rule_id == VISIT_TASK,
            a.TaskExecution.state == "created").order_by(a.TaskExecution.updated_at).limit(25)):
        task.updated_at = now
        db.commit()
        lead = c.get(f"/api/v4/leads/{task.lead_id}")
        if a.is_test(lead) or lead.get("is_deleted"):
            a.observe_excluded_task(db, task, c)
            continue
        if migration_blocked(lead) or binding_issue(lead, c.kassa_state(task.lead_id)):
            continue
        target = date.fromisoformat(task.occurrence.removeprefix("visit:"))
        actual = c.get(f"/api/v4/tasks/{task.task_id}")
        if actual.get("entity_id") != task.lead_id or actual.get("entity_type") != "leads":
            continue
        if actual.get("is_completed"):
            task.state, task.reason = "completed", "completed_externally"
        elif any(actual.get(k) != task.payload.get(k) for k in ("text","complete_till","responsible_user_id","task_type_id")):
            task.reason = "manual_edit_conflict"
        elif not visit_current(lead, target):
            a.complete_owned_task(db, task, c, "Изменилась дата или этап визита", preview=False)
        task.updated_at = now
        db.commit()
    rows = db.scalars(select(Job).where(Job.rule_id == "legacy-wants_visit",
        Job.occurrence == today.isoformat(), Job.due_at <= now,
        Job.due_at >= datetime.fromtimestamp(live_from, timezone.utc)))
    for row in rows:
        key = "visit:" + today.isoformat()
        prior = db.scalar(select(a.TaskExecution).where(a.TaskExecution.rule_id == VISIT_TASK,
            a.TaskExecution.lead_id == row.lead_id, a.TaskExecution.occurrence == key))
        if prior and prior.state != "planned":
            continue
        def current(lead):
            return visit_current(lead, today)
        lead = c.get(f"/api/v4/leads/{row.lead_id}")
        if not current(lead) or binding_issue(lead, c.kassa_state(row.lead_id)):
            continue
        migration = db.get(a.MigrationEntry, row.lead_id)
        if migration and migration.state not in ("preview", "verified", "rolled_back"):
            continue
        # A migrated deal needs its old calendar obligation reconciled explicitly.
        if migration and migration.state == "verified" and migration.target.get("moved_on") in (None, today.isoformat()):
            continue
        existing = all_pages(c, "/api/v4/tasks", "tasks", **{
            "filter[entity_id]":row.lead_id, "filter[entity_type]":"leads"})
        if any(t.get("entity_id") != row.lead_id or t.get("entity_type") != "leads" for t in existing):
            raise ValueError("unexpected_visit_task_entity")
        legacy = any(t.get("task_type_id") == 2 and t.get("text","").strip() == OLD_VISIT_TEXT
                     and (not t.get("is_completed") or datetime.fromtimestamp(t["created_at"], a.MSK).date()==today)
                     for t in existing)
        job = prior or a.plan_task(db, VISIT_TASK, row.lead_id, key, text=VISIT_TEXT,
            due=now+timedelta(hours=3), responsible=lead["responsible_user_id"], task_type=2,
            available_at=now)
        if legacy:
            job.state, job.reason = "preserved", "matching_legacy_visit_obligation"
            db.commit()
        else:
            a.execute_task(db, job, c, enabled_rules=ids, current=current, preview=False, now=now)


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
        a.TaskExecution.rule_id.in_(ids),
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


def execution_hold(rule_id, snapshot):
    """A pre-cutover cash-desk obligation stays with its original executor."""
    if rule_id not in ("promise", "courier") or not snapshot.get("bindings"):
        return None
    if snapshot.get("execution_owners", {}).get(rule_id) != "amocrm":
        return "execution_owner_not_switched"
    kind = "promise_follow" if rule_id == "promise" else "call_courier"
    if not isinstance(snapshot.get("tasks"), list):
        return "invalid_kassa_snapshot"
    if any(t.get("kind") == kind and t.get("status") == "pending" for t in snapshot["tasks"]):
        return f"existing_kassa_{rule_id}_requires_reconciliation"
    return None


def promise_details(snapshot, lead, users):
    details = snapshot.get("promise") or {}
    author = (details.get("author") or "").strip()
    comment = (details.get("comment") or "").strip()
    if not details.get("author_id") or not author or not comment:
        raise ValueError("missing_kassa_promise_details")
    try:
        day = date.fromisoformat(details.get("due_date") or "")
    except ValueError:
        raise ValueError("invalid_kassa_promise_date") from None
    # Explicit null from the cash desk also supersedes a legacy environment mapping.
    mapped = details.get("amo_user_id", users.get(details["author_id"]))
    assignee = lead["responsible_user_id"] if mapped is None else mapped
    if not isinstance(assignee, int) or assignee <= 0:
        raise ValueError("invalid_promise_assignee")
    body = f"Выполнить обещание.\nАвтор: {author}\nКомментарий: {comment}\nСрок: {day:%d.%m.%Y}"
    return body, datetime.combine(day, time(23, 59), a.MSK), assignee


def dispatch(db, c, catalog, ids, *, cutover_at, pipelines=None):
    if cutover_at <= 0: raise ValueError("explicit_cutover_boundary_required")
    rules = {r.id: r for r in RULES}
    query = select(a.TaskExecution).where(a.TaskExecution.state.in_(("planned", "created")),
        a.TaskExecution.rule_id.in_(ids))
    if pipelines is not None:
        query = query.join(Event, a.TaskExecution.occurrence == "entry:" + Event.id).where(
            Event.after["pipeline_id"].as_integer().in_(pipelines))
    jobs = list(db.scalars(query.order_by(a.TaskExecution.updated_at).limit(25)))
    for job in jobs:
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        if job.rule_id not in ids: continue
        rule = rules[job.rule_id]
        event = db.get(Event, job.occurrence.removeprefix("entry:"))
        if event is None: continue  # Imported obligations need their explicit origin.
        if pipelines is not None and job.task_id is None and event.before.get("pipeline_id") not in (
                None, event.after["pipeline_id"]):
            job.reason = "pipeline_move_requires_reconciliation"
            db.commit()
            continue
        if event.at < cutover_at and job.task_id is None:
            # Preview observations overlap old Digital/F5 obligations. They need
            # explicit adoption, never a replay when the mode changes to live.
            job.reason = "pre_cutover_obligation_requires_reconciliation"
            db.commit()
            continue
        lead = c.get(f"/api/v4/leads/{job.lead_id}")
        if migration_blocked(lead): continue
        if a.is_test(lead) or lead.get("is_deleted"):
            if job.state == "created":
                a.observe_excluded_task(db, job, c)
            else:
                job.state, job.reason = "cancelled", "test_or_deleted"
                db.commit()
            continue
        snapshot = (c.kassa_state(job.lead_id, include_promise=True) if rule.id == "promise"
                    else c.kassa_state(job.lead_id))
        problem = binding_issue(lead, snapshot, required=rule.id in SHOP_TASK_KINDS)
        problem = problem or execution_hold(rule.id, snapshot)
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
        current = lambda value: value.get("pipeline_id") == event.after["pipeline_id"] and applicable(rule, value, catalog)
        responsible = int(lead.get("responsible_user_id") or 0)
        task_text = None
        if rule.id == "promise" and snapshot.get("bindings"):
            try:
                task_text, due, responsible = promise_details(snapshot, lead, c.settings.promise_users)
            except ValueError as exc:
                job.reason = str(exc)
                db.commit()
                continue
        result = a.revise_task(db, job, c, due=due, responsible=responsible,
                              available_at=available, current=current, preview=False, task_text=task_text)
        if result == "replanned":
            if rule.id in ("promise", "courier"):
                fresh = (c.kassa_state(job.lead_id, include_promise=True) if rule.id == "promise"
                         else c.kassa_state(job.lead_id))
                if (binding_issue(lead, fresh) or execution_hold(rule.id, fresh)
                        or fresh["bindings"] != snapshot["bindings"]
                        or (rule.id == "promise" and fresh.get("promise") != snapshot.get("promise"))):
                    job.reason = f"kassa_{rule.id}_changed"
                    db.commit()
                    continue
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
