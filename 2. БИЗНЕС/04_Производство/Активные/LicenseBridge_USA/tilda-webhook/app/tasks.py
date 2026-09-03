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
