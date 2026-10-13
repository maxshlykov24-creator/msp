"""Задачи в Kommo. Единственное место, где hub их создаёт."""
from __future__ import annotations

import logging
import time

from app.actions import Ctx, log_decision
from app.config import settings

log = logging.getLogger("tasks")

TASK_TYPE_CALL = 1  # «Связаться» — базовый тип задачи Kommo


def create_task(ctx: Ctx, entity_type: str, entity_id: int, text: str,
                owner_id: int, minutes: int | None = None,
                task_type_id: int = TASK_TYPE_CALL) -> int | None:
    """Задача на ответственного. entity_type: 'contact' | 'deal'."""
    kommo_entity = "contacts" if entity_type == "contact" else "leads"
    complete_till = int(time.time()) + (minutes or settings.telephony_task_minutes) * 60
    if ctx.shadow and not settings.enable_telephony:
        log_decision(ctx, "task.shadow", entity=entity_type, id=entity_id,
                     owner=owner_id, text=text[:120])
        return None
    payload = [{
        "text": text,
        "complete_till": complete_till,
        "entity_id": entity_id,
        "entity_type": kommo_entity,
        "responsible_user_id": owner_id,
        "task_type_id": task_type_id,
    }]
    data = ctx.client.post("/tasks", payload)
    task_id = None
    if data:
        items = (data.get("_embedded") or {}).get("tasks") or []
        task_id = items[0]["id"] if items else None
    log_decision(ctx, "task.created", entity=entity_type, id=entity_id,
                 owner=owner_id, task=task_id, text=text[:120])
    return task_id


def create_pleep_handoff_task(ctx: Ctx, lead_id: int, text: str,
                              request_key: str) -> int | None:
    """One open Pleep handoff task per deal, preserving its current owner.

    Persist intent BEFORE POST in a separate transaction. After a timeout or
    crash we only search by marker; absence requires manual reconciliation,
    never a second blind POST. This favors a visible failed event over duplicates.
    """
    import hashlib
    import uuid
    from app.db import phone_lock, session_scope
    from app.models import PleepTaskDelivery

    if ctx.shadow:
        log_decision(ctx, "pleep.task_shadow", lead=lead_id)
        return None
    request_digest = hashlib.sha256(request_key.encode()).hexdigest()
    # The intent transaction is separate from the worker inbox transaction.
    # Do not use the phone lock: worker already holds it on another connection.
    with session_scope() as delivery_session:
        with phone_lock(delivery_session, f"pleep-task:{lead_id}"):
            row = delivery_session.get(PleepTaskDelivery, lead_id)
            tasks = list(ctx.client.paginate("/tasks", "tasks", {
                "filter[entity_type]": "leads", "filter[entity_id][]": lead_id,
            }))
            tasks = [t for t in tasks if t.get("entity_type") == "leads"
                     and int(t.get("entity_id") or 0) == lead_id]
            if row:
                matched = next((t for t in tasks if row.marker in (t.get("text") or "")), None)
                if matched:
                    row.task_id, row.state = int(matched["id"]), "done"
                    if row.request_key == request_digest or not matched.get("is_completed"):
                        return row.task_id
                elif row.state == "sending":
                    raise RuntimeError("Pleep task outcome uncertain; reconcile CRM marker before retrying")
                elif row.request_key == request_digest:
                    # Even a deleted task must not be recreated by event redelivery.
                    return row.task_id
            opened = next((t for t in tasks if not t.get("is_completed")
                           and (t.get("text") or "").startswith("[Pleep handoff:")), None)
            if opened:
                return int(opened["id"])
            lead = ctx.client.get_lead(lead_id)
            if not lead:
                raise RuntimeError("Pleep handoff deal not found")
            owner = int(lead.get("responsible_user_id") or settings.default_sales_owner_id)
            active = {int(u["id"]) for u in ctx.client.users()
                      if u.get("rights", {}).get("is_active")}
            if owner not in active:
                raise RuntimeError("Pleep handoff owner inactive; explicit reassignment required")
            marker = f"[Pleep handoff:{uuid.uuid4().hex}]"
            if row is None:
                row = PleepTaskDelivery(lead_id=lead_id, request_key=request_digest, marker=marker)
                delivery_session.add(row)
            row.request_key, row.marker, row.state, row.task_id = request_digest, marker, "sending", None
    # No auto retry here, including 5xx/timeout: Kommo may have accepted the write.
    data = ctx.client.post_once("/tasks", [{
        "entity_id": lead_id, "entity_type": "leads",
        "responsible_user_id": owner, "task_type_id": TASK_TYPE_CALL,
        "complete_till": int(time.time()),
        "text": f"{marker}\nОтветить клиенту в диалоге Pleep.\n{text[:4000]}",
    }])
    items = (data or {}).get("_embedded", {}).get("tasks", [])
    if not items:
        raise RuntimeError("Pleep task POST returned no id; reconciliation required")
    task_id = int(items[0]["id"])
    verified = ctx.client.get(f"/tasks/{task_id}") or {}
    if (int(verified.get("entity_id") or 0) != lead_id
            or int(verified.get("responsible_user_id") or 0) != owner
            or marker not in (verified.get("text") or "")):
        raise RuntimeError("Pleep task readback failed; reconciliation required")
    with session_scope() as delivery_session:
        row = delivery_session.get(PleepTaskDelivery, lead_id)
        if row and row.marker == marker:
            row.task_id, row.state = task_id, "done"
    log_decision(ctx, "pleep.task_created", lead=lead_id, task=task_id, owner=owner)
    return task_id
