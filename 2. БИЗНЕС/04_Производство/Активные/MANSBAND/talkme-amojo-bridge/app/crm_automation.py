"""CRM automation domain and durable task ownership.

No network or configuration side effects on import. The caller must explicitly
enable a verified rule; preview never writes to CRM. Message delivery remains
owned by message_templates, not by this task executor.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base
from app.pipeline_guard import migration_blocked

MSK = ZoneInfo("Europe/Moscow")
SALES = 9601214
COMPLAINTS = 9601246
CAMPAIGN = 10730010
OFFLINE, ONLINE, RENTAL, COMPANY = 11482373, 11482377, 11482381, 11482385
PIPELINES = frozenset({SALES, COMPLAINTS, CAMPAIGN, OFFLINE, ONLINE, RENTAL, COMPANY})
TYPE_FIELD = 3831526
TYPE_ROUTES = {
    "Продажа офлайн": OFFLINE, "Продажа онлайн": ONLINE,
    "Сертификат пластик": OFFLINE, "Сертификат электронный": ONLINE,
    "Аренда": RENTAL, "Продажа компании": COMPANY,
    **{name: COMPLAINTS for name in (
        "Слив", "Не слив", "Дефект", "Химчистка", "Перешив",
        "Перепутан размер", "Некорректная бирка", "Возврат", "Обмен")},
}


def stamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def field_values(lead: dict, field: int) -> list:
    return [v.get("value") for f in lead.get("custom_fields_values") or []
            if f.get("field_id") == field for v in f.get("values") or []
            if v.get("value") not in (None, "")]


def field_date(lead: dict, field: int) -> datetime | None:
    values = field_values(lead, field)
    if len(values) != 1:
        return None
    try:
        return datetime.fromtimestamp(int(values[0]), MSK)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def is_test(lead: dict) -> bool:
    return bool(lead.get("isTest")) or any(
        str(t.get("name", "")).strip().upper() == "ТЕСТ НЕ ОТПРАВЛЯТЬ"
        for t in lead.get("_embedded", {}).get("tags", []))


def canonical_stage(name: str) -> str:
    # Only an observed orthographic alias; no fuzzy matching of business stages.
    return name.replace("Счёт", "Счет")


class Catalog:
    def __init__(self, pipelines: list[dict]):
        self.pipelines = {p["id"]: p for p in pipelines}
        self.by_id = {}
        self.by_name = {}
        for p in pipelines:
            for s in p.get("_embedded", {}).get("statuses", []):
                if s.get("type") == 1:
                    continue
                self.by_id[(p["id"], s["id"])] = canonical_stage(s["name"])
                key = (p["id"], canonical_stage(s["name"]))
                if key in self.by_name:
                    raise ValueError("ambiguous_stage_name")
                self.by_name[key] = s["id"]

    def stage(self, lead: dict) -> str | None:
        return self.by_id.get((lead.get("pipeline_id"), lead.get("status_id")))


@dataclass(frozen=True)
class RouteDecision:
    state: Literal["stay", "candidate", "manual", "excluded"]
    reason: str
    pipeline_id: int | None = None
    status_id: int | None = None


def route_candidate(lead: dict, catalog: Catalog, *, linked_kind: str | None = None) -> RouteDecision:
    """A candidate is not an authorization to PATCH: bindings must be reconciled."""
    if lead.get("pipeline_id") not in PIPELINES or lead.get("is_deleted") or is_test(lead):
        return RouteDecision("excluded", "outside_scope_or_test")
    if lead.get("status_id") in (142, 143):
        return RouteDecision("excluded", "closed")
    types = field_values(lead, TYPE_FIELD)
    if len(types) != 1 or types[0] not in TYPE_ROUTES:
        return RouteDecision("manual", "missing_or_ambiguous_type")
    target = TYPE_ROUTES[types[0]]
    if linked_kind is not None and linked_kind != types[0]:
        return RouteDecision("manual", "kassa_type_conflict")
    if lead.get("pipeline_id") == target:
        return RouteDecision("stay", "already_routed", target, lead.get("status_id"))
    if lead.get("pipeline_id") != SALES:
        return RouteDecision("manual", "existing_pipeline_requires_review")
    name = catalog.stage(lead)
    target_status = catalog.by_name.get((target, name))
    if not target_status:
        return RouteDecision("manual", "no_equivalent_stage")
    return RouteDecision("candidate", "binding_check_required", target, target_status)


def type_from_binding(binding: dict) -> str | None:
    known = {"company": "Продажа компании", "rental": "Аренда",
             "cert_plastic": "Сертификат пластик", "cert_digital": "Сертификат электронный",
             "delivery": "Продажа онлайн", "sliv": "Слив", "no_sliv": "Не слив",
             "defect": "Дефект", "drycleaning": "Химчистка", "resew": "Перешив",
             "wrong_size": "Перепутан размер", "wrong_label": "Некорректная бирка",
             "refund": "Возврат", "exchange": "Обмен"}
    kind = binding.get("kind")
    if kind in known:
        return known[kind]
    if kind == "sale" and binding.get("funnel") in ("offline", "online"):
        return "Продажа онлайн" if binding["funnel"] == "online" else "Продажа офлайн"
    return None


def route_with_bindings(lead: dict, catalog: Catalog, bindings: list[dict]) -> RouteDecision:
    matched = [b for b in bindings if b.get("lead_id") == lead.get("id")]
    if matched and all(b.get("isTest") is True for b in matched):
        return RouteDecision("excluded", "test_kassa_binding")
    if len(matched) > 1:
        return RouteDecision("manual", "multiple_kassa_bindings")
    if not matched:
        return route_candidate(lead, catalog)
    if matched[0].get("isTest"):
        return RouteDecision("excluded", "test_kassa_binding")
    label = type_from_binding(matched[0])
    if label is None:
        return RouteDecision("manual", "unknown_kassa_base_type")
    if not field_values(lead, TYPE_FIELD):
        # Preview only. Filling this field in production is a separately journaled
        # operation guarded together with the move, never an implicit side effect.
        fields = [f for f in lead.get("custom_fields_values") or [] if f.get("field_id") != TYPE_FIELD]
        lead = {**lead, "custom_fields_values": fields + [{"field_id": TYPE_FIELD, "values": [{"value": label}]}]}
    return route_candidate(lead, catalog, linked_kind=label)


def first_response_due(created: datetime) -> datetime:
    local = stamp(created).astimezone(MSK)
    if local.hour < 10:
        local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    elif local.hour >= 23:
        local = (local + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return stamp(local + timedelta(minutes=15))


def deadline(start: datetime, mode: str, amount: int = 0) -> datetime:
    """Native calendar policies; relative days remain 24h, not working hours.

    Calibrated against lead 62020342 on 2026-11-17. Native relative presets
    add five minutes; explicit intervals do not. Weekend +7 days was explicitly
    approved by the owner on 17 November; it is not inferred native behaviour.
    """
    local = stamp(start).astimezone(MSK)
    if mode == "first_response":
        return first_response_due(start)
    if mode in ("minutes", "hours", "days"):
        return stamp(local + timedelta(**{mode: amount}))
    if mode == "end_of_day":
        return stamp(datetime.combine(local.date(), time(23, 59), MSK))
    if mode in ("native_tomorrow", "native_three_days"):
        days = 1 if mode == "native_tomorrow" else 3
        return stamp(local + timedelta(days=days, minutes=5))
    if mode == "native_week":
        if local.weekday() >= 5:
            return stamp(local + timedelta(days=7))
        friday = local.date() + timedelta(days=4-local.weekday())
        return stamp(datetime.combine(friday, time(23, 59), MSK))
    if mode == "native_immediate":
        return stamp(local + timedelta(minutes=5))
    if mode == "immediate":
        return stamp(start)
    raise ValueError("uncalibrated_native_deadline:" + mode)


class TaskExecution(Base):
    __tablename__ = "crm_automation_task"
    __table_args__ = (UniqueConstraint("rule_id", "lead_id", "occurrence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(100))
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    occurrence: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    pending_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MigrationEntry(Base):
    __tablename__ = "crm_automation_migration"
    lead_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(30), default="preview", index=True)
    before: Mapped[dict] = mapped_column(JSON)
    target: Mapped[dict] = mapped_column(JSON)
    preserved_occurrence: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(String(160), default="")


TABLES = [TaskExecution.__table__, MigrationEntry.__table__]


def task_marker(rule_id: str, lead_id: int, occurrence: str) -> str:
    digest = hashlib.sha256(f"{rule_id}:{lead_id}:{occurrence}".encode()).hexdigest()[:24]
    return f"[MB-AUTO:{digest}]"


def plan_task(db: Session, rule_id: str, lead_id: int, occurrence: str, *,
              text: str, due: datetime, responsible: int, task_type: int,
              available_at: datetime | None = None) -> TaskExecution:
    if not occurrence or lead_id <= 0 or responsible <= 0 or task_type <= 0:
        raise ValueError("explicit_task_identity_required")
    existing = db.scalar(select(TaskExecution).where(
        TaskExecution.rule_id == rule_id, TaskExecution.lead_id == lead_id,
        TaskExecution.occurrence == occurrence))
    if existing:
        return existing
    payload = {"entity_id": lead_id, "entity_type": "leads", "responsible_user_id": responsible,
               "task_type_id": task_type, "complete_till": int(stamp(due).timestamp()),
               "text": text.rstrip() + "\n" + task_marker(rule_id, lead_id, occurrence)}
    job = TaskExecution(rule_id=rule_id, lead_id=lead_id, occurrence=occurrence, payload=payload,
                        available_at=stamp(available_at or datetime.now(timezone.utc)))
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(TaskExecution).where(
            TaskExecution.rule_id == rule_id, TaskExecution.lead_id == lead_id,
            TaskExecution.occurrence == occurrence))
        if existing is None:
            raise
        return existing
    return job


def execute_task(db: Session, job: TaskExecution, client, *, enabled_rules: frozenset[str],
                 current: Callable[[dict], bool], preview: bool = True,
                 now: datetime | None = None) -> str:
    """Reserve durably before POST. A lost response is never retried blindly.

    An atomic compare-and-set reserves the row, including across concurrent workers.
    A restarted inflight job is treated as uncertain and reconciled read-only.
    """
    if preview or job.rule_id not in enabled_rules:
        return "preview"
    if job.state != "planned":
        return job.state
    # SQLite test storage drops timezone metadata; production PostgreSQL does not.
    available = job.available_at
    if available.tzinfo is None:
        available = available.replace(tzinfo=timezone.utc)
    if available > stamp(now or datetime.now(timezone.utc)):
        return "not_due"
    lead = client.get(f"/api/v4/leads/{job.lead_id}")
    if migration_blocked(lead):
        return "migration_guard"
    migration = db.get(MigrationEntry, job.lead_id)
    if migration and migration.state not in ("preview", "verified", "rolled_back"):
        return "migration_guard"
    if is_test(lead) or not current(lead):
        db.execute(update(TaskExecution).where(TaskExecution.id == job.id,
            TaskExecution.state == "planned").values(state="cancelled", reason="conditions_changed"))
        db.commit()
        db.refresh(job)
        return job.state
    claimed = db.execute(update(TaskExecution).where(
        TaskExecution.id == job.id, TaskExecution.state == "planned"
    ).values(state="inflight"))
    db.commit()
    db.refresh(job)
    if claimed.rowcount != 1:
        return job.state
    try:
        result = client.post("/api/v4/tasks", [job.payload])
        tasks = result.get("_embedded", {}).get("tasks", [])
        if len(tasks) != 1 or not isinstance(tasks[0].get("id"), int):
            raise ValueError("ambiguous_task_response")
        job.task_id, job.state = tasks[0]["id"], "created"
    except Exception:
        # No exception text: provider bodies can contain customer data.
        job.state, job.reason = "uncertain", "post_result_unknown"
    db.commit()
    return job.state


def reconcile_task(db: Session, job: TaskExecution, tasks: list[dict], *, complete_scan: bool) -> str:
    if job.state not in ("uncertain", "inflight") or not complete_scan:
        return job.state
    marker = task_marker(job.rule_id, job.lead_id, job.occurrence)
    found = [t for t in tasks if t.get("entity_id") == job.lead_id
             and t.get("entity_type") == "leads" and marker in t.get("text", "")]
    if len(found) == 1:
        job.task_id = found[0]["id"]
        job.state = "completed" if found[0].get("is_completed") else "created"
        job.reason = "completed_externally" if job.state == "completed" else "reconciled"
    else:
        # Absence is not proof the first POST cannot still arrive.
        job.state, job.reason = "uncertain", "not_found" if not found else "multiple_matches"
    db.commit()
    return job.state


def complete_owned_task(db: Session, job: TaskExecution, client, reason: str, *, preview: bool = True) -> str:
    if preview or not job.task_id or job.state != "created":
        return "preview" if preview else job.state
    migration = db.get(MigrationEntry, job.lead_id)
    if migration and migration.state not in ("preview", "verified", "rolled_back"):
        return "migration_guard"
    if migration_blocked(client.get(f"/api/v4/leads/{job.lead_id}")):
        return "migration_guard"
    actual = client.get(f"/api/v4/tasks/{job.task_id}")
    if actual.get("entity_id") != job.lead_id or actual.get("entity_type") != "leads":
        return "ownership_conflict"
    if task_marker(job.rule_id, job.lead_id, job.occurrence) not in actual.get("text", ""):
        return "ownership_conflict"
    if not actual.get("is_completed"):
        if any(actual.get(k) != job.payload.get(k) for k in ("text", "complete_till", "responsible_user_id", "task_type_id")):
            job.reason = "manual_edit_conflict"
            db.commit()
            return job.reason
        client.patch("/api/v4/tasks", [{"id": job.task_id, "is_completed": True, "result": {"text": reason}}])
    job.state, job.reason = "completed", reason
    db.commit()
    return job.state


def source_fill_patch(lead: dict, field_id: int, enum_id: int) -> dict | None:
    """Do not replace any populated source, including unrecognized manual data."""
    if field_values(lead, field_id):
        return None
    return {"id": lead["id"], "custom_fields_values": [{"field_id": field_id, "values": [{"enum_id": enum_id}]}]}


def revise_task(db: Session, job: TaskExecution, client, *, due: datetime,
                responsible: int, available_at: datetime, current: Callable[[dict], bool],
                preview: bool = True, task_text: str | None = None) -> str:
    """Revise an existing obligation, never create another task on a date edit.

    payload is the last acknowledged server value. An uncertain PATCH retains
    both versions and must be reconciled before any subsequent mutation.
    Caller serializes each lead together with stage/date event processing.
    """
    if preview:
        return "preview"
    if responsible <= 0:
        raise ValueError("explicit_responsible_required")
    migration = db.get(MigrationEntry, job.lead_id)
    if migration and migration.state not in ("preview", "verified", "rolled_back"):
        return "migration_guard"
    lead = client.get(f"/api/v4/leads/{job.lead_id}")
    if migration_blocked(lead):
        return "migration_guard"
    if is_test(lead) or not current(lead):
        return "conditions_changed"
    desired = {**job.payload, "complete_till": int(stamp(due).timestamp()),
               "responsible_user_id": responsible}
    if task_text is not None:
        desired["text"] = task_text.rstrip() + "\n" + task_marker(job.rule_id, job.lead_id, job.occurrence)
    if job.state == "planned":
        changed = db.execute(update(TaskExecution).where(
            TaskExecution.id == job.id, TaskExecution.state == "planned",
            TaskExecution.updated_at == job.updated_at,
        ).values(payload=desired, available_at=stamp(available_at),
                 updated_at=datetime.now(timezone.utc)))
        db.commit()
        db.refresh(job)
        return "replanned" if changed.rowcount == 1 else "concurrent_change"
    if job.state != "created" or not job.task_id:
        return job.state
    actual = client.get(f"/api/v4/tasks/{job.task_id}")
    if actual.get("entity_id") != job.lead_id or actual.get("entity_type") != "leads":
        return "ownership_conflict"
    if actual.get("is_completed"):
        job.state, job.reason = "completed", "completed_externally"
        db.commit()
        return job.state
    protected = ("text", "complete_till", "responsible_user_id", "task_type_id")
    if any(actual.get(key) != job.payload.get(key) for key in protected):
        job.reason = "manual_edit_conflict"
        db.commit()
        return job.reason
    if desired == job.payload:
        return "unchanged"
    claimed = db.execute(update(TaskExecution).where(
        TaskExecution.id == job.id, TaskExecution.state == "created",
        TaskExecution.updated_at == job.updated_at,
    ).values(state="update_inflight", pending_payload=desired,
             updated_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(job)
    if claimed.rowcount != 1:
        return "concurrent_change"
    try:
        changes = {key: desired[key] for key in ("complete_till", "responsible_user_id", "text")
                   if desired[key] != job.payload[key]}
        client.patch("/api/v4/tasks", [{"id": job.task_id, **changes}])
        actual = client.get(f"/api/v4/tasks/{job.task_id}")
        return reconcile_task_revision(db, job, actual)
    except Exception:
        job.state, job.reason = "update_uncertain", "patch_result_unknown"
        db.commit()
        return job.state


def reconcile_task_revision(db: Session, job: TaskExecution, actual: dict) -> str:
    """A matching readback acknowledges PATCH; a mismatch never retries it."""
    if job.state not in ("update_inflight", "update_uncertain") or not job.pending_payload:
        return job.state
    if actual.get("id") != job.task_id or actual.get("entity_id") != job.lead_id or actual.get("entity_type") != "leads":
        return "ownership_conflict"
    keys = ("text", "complete_till", "responsible_user_id", "task_type_id")
    if all(actual.get(key) == job.pending_payload.get(key) for key in keys):
        job.payload, job.pending_payload = job.pending_payload, None
        job.state = "completed" if actual.get("is_completed") else "created"
        job.reason = "completed_externally" if job.state == "completed" else "revision_verified"
    else:
        job.state, job.reason = "update_uncertain", "revision_not_confirmed"
    db.commit()
    return job.state
