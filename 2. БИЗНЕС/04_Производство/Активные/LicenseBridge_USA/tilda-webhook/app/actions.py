"""Общие действия hub с учётом shadow-режима.

Все мутации Kommo идут отсюда, чтобы единообразно:
- уважать shadow_mode (в shadow ничего не меняем в Kommo, только пишем решение);
- снимать JSON-снимок перед удалением;
- вести machine-readable журнал решений.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.kommo.client import KommoClient
from app.merge_tags import allocate_slot
from app.models import Decision, DupManual, EntitySnapshot
from app.rollout import FlagSet, flags_from_settings

log = logging.getLogger("actions")


class Ctx:
    """Контекст обработки одного события.

    `flags` — действующий набор feature-flags (этап авто-раскатки либо ручные
    настройки). Логика спрашивает ctx.flags, а не settings, чтобы этап можно было
    менять на живом сервисе без передеплоя."""

    def __init__(self, client: KommoClient, session: Session, inbox_id: int | None,
                 phone: str | None, shadow: bool, flags: FlagSet | None = None) -> None:
        self.client = client
        self.s = session
        self.inbox_id = inbox_id
        self.phone = phone
        self.shadow = shadow
        self.flags = flags or flags_from_settings()


def log_decision(ctx: Ctx, action: str, **detail: Any) -> None:
    ctx.s.add(Decision(
        inbox_id=ctx.inbox_id, phone=ctx.phone, action=action,
        shadow=ctx.shadow, detail=detail,
    ))
    ctx.s.flush()
    log.info("decision action=%s shadow=%s %s", action, ctx.shadow, detail)


def snapshot_entity(ctx: Ctx, entity_type: str, entity_id: int, reason: str) -> None:
    """JSON-снимок сущности перед удалением (для восстановления)."""
    try:
        if entity_type == "contact":
            data = ctx.client.get_contact(entity_id, with_="leads")
        else:
            data = ctx.client.get_lead(entity_id, with_="contacts")
    except Exception as exc:  # снимок не должен ронять обработку — но без него не удаляем
        log.warning("snapshot failed %s %s: %s", entity_type, entity_id, exc)
        raise
    ctx.s.add(EntitySnapshot(
        entity_type=entity_type, entity_id=entity_id, reason=reason, snapshot=data or {},
    ))
    ctx.s.flush()


def current_tags(entity: dict[str, Any]) -> list[str]:
    tags = ((entity.get("_embedded") or {}).get("tags")) or []
    return [t.get("name", "") for t in tags if t.get("name")]


def add_tags(ctx: Ctx, entity_type: str, entity_id: int, entity: dict[str, Any],
             new_tags: list[str]) -> None:
    """Добавляет теги, сохраняя существующие (Kommo PATCH заменяет весь набор)."""
    existing = set(current_tags(entity))
    merged = sorted(existing | {t for t in new_tags if t})
    if merged == sorted(existing):
        return
    kommo_entity = "contacts" if entity_type == "contact" else "leads"
    if ctx.shadow:
        log_decision(ctx, "set_tags.shadow", entity=entity_type, id=entity_id, tags=merged)
        return
    ctx.client.set_tags(kommo_entity, entity_id, merged)


def tag_manual_pair(ctx: Ctx, kind: str, entities: list[dict[str, Any]], reason: str) -> str:
    """Маркер очереди + уникальный тег пары из пула на ВСЕ сущности группы.
    kind: 'contact' | 'deal'. Возвращает имя тега пары. Задачи не создаём."""
    ids = sorted(int(e["id"]) for e in entities)
    pair_ref = f"{kind}:{'-'.join(map(str, ids))}"
    marker = settings.tag_marker_contact if kind == "contact" else settings.tag_marker_deal

    slot = allocate_slot(ctx.s, kind, pair_ref)
    pair_tag = slot.tag

    entity_type = "contact" if kind == "contact" else "deal"
    for e in entities:
        add_tags(ctx, entity_type, int(e["id"]), e, [marker, pair_tag])

    # запись в очередь ручной склейки (идемпотентно по pair_ref/tag)
    existing = ctx.s.query(DupManual).filter(
        DupManual.tag == pair_tag, DupManual.status == "open"
    ).first()
    if not existing:
        ctx.s.add(DupManual(kind=kind, tag=pair_tag, entity_ids=ids,
                            reason=reason, status="open"))
        ctx.s.flush()
    log_decision(ctx, "manual_merge_tag", kind=kind, tag=pair_tag, ids=ids, reason=reason)
    return pair_tag


def _dup_lead_payload() -> dict[str, Any]:
    """Куда уводим дубль-сделку. Архивная воронка — если она задана, иначе «Провал»
    с причиной (`DUP_LOST_REASON_ID`). Пустой словарь = оставить на месте."""
    if settings.archive_pipeline_id:
        payload: dict[str, Any] = {"pipeline_id": settings.archive_pipeline_id}
        if settings.archive_status_id:
            payload["status_id"] = settings.archive_status_id
        return payload
    if settings.dup_lost_reason_id:
        return {"status_id": settings.status_lost,
                "loss_reason_id": settings.dup_lost_reason_id}
    return {}


def mark_duplicate(ctx: Ctx, entity_type: str, entity: dict[str, Any], reason: str) -> None:
    """Пометить обезвреженный дубль (работа с него уже снята).

    Удалить через API нельзя — Kommo отдаёт 405 на DELETE сделок/контактов. Поэтому:
    - контакт: тег `дубль_удалить`, физически удаляет человек пакетно в UI;
    - сделка: тег `дубль` + перевод в «Провал» с причиной (см. `_dup_lead_payload`),
      чтобы дубль не висел в рабочих этапах, но история звонков/примечаний осталась."""
    entity_id = int(entity["id"])
    is_deal = entity_type == "deal"
    tag = settings.tag_dup_deal if is_deal else settings.tag_dup_to_delete
    add_tags(ctx, entity_type, entity_id, entity, [tag])
    payload = _dup_lead_payload() if is_deal else {}
    if payload:
        if ctx.shadow:
            log_decision(ctx, "dup.move.shadow", id=entity_id, payload=payload)
            payload = {}
        else:
            ctx.client.update_lead(entity_id, payload)
    log_decision(ctx, "dup.marked", entity=entity_type, id=entity_id,
                 reason=reason, moved=payload or None, shadow=ctx.shadow)


def add_note(ctx: Ctx, entity_type: str, entity_id: int, text: str) -> None:
    kommo_entity = "contacts" if entity_type == "contact" else "leads"
    if ctx.shadow:
        log_decision(ctx, "note.shadow", entity=entity_type, id=entity_id, text=text[:200])
        return
    ctx.client.add_note(kommo_entity, entity_id, text)
