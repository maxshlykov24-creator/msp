"""Антидубль КОНТАКТОВ (гибрид, без NOVA).

Ключ различия — сохраним ли историю и возможность ответить в канале при удалении
контакта (чаты теперь в Kommo).

- Чата нет → soft-merge (перелинковать сделки, скопировать поля/примечания, снимок, удалить младший).
- Лёгкий читаемый чат + можно ответить в канале (есть номер) → копия сообщений в примечание, soft-merge.
- Насыщенный/вложения/диалог/нечитаемый/канал без ответа → маркер + тег пары, ручная native-merge.
- Instagram-ник без телефона → всегда только тег (удаление рвёт нить).
- Общий номер (>N сделок разными именами) или тег notmerge → только тег.
"""
from __future__ import annotations

import logging
from typing import Any

from app.actions import (
    Ctx,
    add_note,
    current_tags,
    log_decision,
    mark_duplicate,
    snapshot_entity,
    tag_manual_pair,
)
from app.chat import can_reply_in_channel, note_text, read_contact_chat
from app.config import settings
from app.identity import same_person_name

log = logging.getLogger("dedup_contacts")

# Примечания с текстом, который нельзя потерять вместе с карточкой дубля:
# комментарии менеджеров и данные рекламных заявок.
_CARRY_NOTE_TYPES = {"common", "amomessage", "chat_message",
                     "targeting_in", "targeting_out", "service_message"}


def _oldest(contacts: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(contacts, key=lambda c: c.get("created_at") or 0)[0]


def _contact_lead_ids(contact: dict[str, Any]) -> list[int]:
    leads = ((contact.get("_embedded") or {}).get("leads")) or []
    return [int(l["id"]) for l in leads if l.get("id")]


def _different_people(contacts: list[dict[str, Any]]) -> bool:
    """Есть ли в группе карточки, которые не сводятся к одному имени.

    Транслитерация и отсутствие фамилии за разных людей не считаются — иначе
    «Евгений Коека» из Facebook и «Evgeniy Koeka» из Tilda навсегда остаются
    двумя клиентами (боевой случай 11.08, сделки 29755601 / 29807229)."""
    names = [c.get("name") for c in contacts]
    return any(not same_person_name(a, b)
               for i, a in enumerate(names) for b in names[i + 1:])


def _is_shared_number(client, contacts: list[dict[str, Any]]) -> bool:
    """Разные имена на одном номере → семья/офис, авто-склейка запрещена.

    Раньше требовалось ещё и «больше N сделок», из-за чего пара карточек с явно
    разными людьми могла склеиться. Порог убран: решает только имя, зато оно
    теперь сравнивается с учётом транслитерации (см. `_different_people`)."""
    return _different_people(contacts)


def _copy_missing_fields(ctx: Ctx, base: dict[str, Any], dup: dict[str, Any]) -> None:
    base_fids = {cf.get("field_id") for cf in base.get("custom_fields_values") or []}
    to_add = [cf for cf in dup.get("custom_fields_values") or []
              if cf.get("field_id") not in base_fids]
    if not to_add:
        return
    if ctx.shadow:
        log_decision(ctx, "contact.copy_fields.shadow", base=base["id"],
                     dup=dup["id"], fields=[cf.get("field_id") for cf in to_add])
        return
    ctx.client.update_contact(int(base["id"]), {"custom_fields_values": to_add})


def _relink_leads(ctx: Ctx, base_id: int, dup: dict[str, Any]) -> bool:
    """True, если все сделки дубля переехали на основной контакт.

    Провал критичен: карточку дубля потом удаляет человек, и непереехавшая сделка
    осталась бы без контакта. Поэтому при ошибке склейка отменяется."""
    ok = True
    for lead_id in _contact_lead_ids(dup):
        if ctx.shadow:
            log_decision(ctx, "contact.relink.shadow", lead=lead_id, to=base_id)
            continue
        try:
            ctx.client.link_lead_contact(lead_id, base_id, main=True)
            ctx.client.unlink_lead_contact(lead_id, int(dup["id"]))
        except Exception as exc:
            log.warning("relink lead=%s failed: %s", lead_id, exc)
            log_decision(ctx, "contact.relink.failed", lead=lead_id, to=base_id,
                         error=str(exc)[:200])
            ok = False
    return ok


def _carry_notes(ctx: Ctx, base_id: int, dup_id: int) -> None:
    """Перенести текст примечаний дубля на основную карточку.

    Карточку дубля потом удаляет человек (API удалять не умеет), вместе с ней ушли
    бы комментарии менеджеров — на боевой базе они есть у каждого пятого дубля."""
    try:
        notes = ctx.client.get_notes("contacts", dup_id)
    except Exception as exc:
        log.warning("notes read failed contact=%s: %s", dup_id, exc)
        return
    texts = [t for n in notes
             if n.get("note_type") in _CARRY_NOTE_TYPES and (t := note_text(n).strip())]
    if not texts:
        return
    body = f"Примечания из объединённого контакта #{dup_id}:\n" + \
           "\n".join(f"— {t}" for t in texts)
    add_note(ctx, "contact", base_id, body)


def _soft_merge(ctx: Ctx, base: dict[str, Any], dup: dict[str, Any],
                copy_messages: list[str] | None, reason: str) -> None:
    base_id, dup_id = int(base["id"]), int(dup["id"])
    _carry_notes(ctx, base_id, dup_id)
    if copy_messages:
        body = f"Сообщения из объединённого контакта #{dup_id}:\n" + \
               "\n".join(f"— {m}" for m in copy_messages)
        add_note(ctx, "contact", base_id, body)
    _copy_missing_fields(ctx, base, dup)
    if not _relink_leads(ctx, base_id, dup):
        tag_manual_pair(ctx, "contact", [base, dup], reason="relink_failed")
        log_decision(ctx, "contact_merge.aborted", base=base_id, dup=dup_id,
                     reason="сделки не переехали")
        return
    add_note(ctx, "contact", base_id, f"Контакт #{dup_id} объединён ({reason}).")
    if not ctx.flags.contact_soft_merge or ctx.shadow:
        log_decision(ctx, "contact_merge.skip", base=base_id, dup=dup_id,
                     reason=reason, shadow=ctx.shadow, enabled=ctx.flags.contact_soft_merge)
        return
    snapshot_entity(ctx, "contact", dup_id, reason=f"contact_merge:{reason}")
    # сделки и поля уже перенесены на base — дубль пустой; удалить его через API
    # нельзя, помечаем тегом для пакетного удаления в UI
    mark_duplicate(ctx, "contact", dup, reason=f"объединён с #{base_id} ({reason})")
    log_decision(ctx, "contact_merge.resolved", base=base_id, dup=dup_id, reason=reason)


def resolve(ctx: Ctx, contacts: list[dict[str, Any]], has_phone: bool) -> dict[str, Any]:
    """Возвращает итоговый контакт и всю группу карточек одного номера.

    `group` нужен антидублю сделок: если карточки склеить не удалось, сделки всё
    равно надо сравнивать по всей группе, иначе дубль на «чужой» карточке никто
    не увидит."""
    if not contacts:
        return {"contact_id": None, "action": "no_contact", "group": []}
    # уже объединённые дубли ждут ручного удаления — второй раз их не трогаем
    contacts = [c for c in contacts
                if settings.tag_dup_to_delete not in current_tags(c)] or contacts
    group = sorted(int(c["id"]) for c in contacts)
    if len(contacts) == 1:
        return {"contact_id": int(contacts[0]["id"]), "action": "single", "group": group}

    base = _oldest(contacts)
    base_id = int(base["id"])
    dups = [c for c in contacts if int(c["id"]) != base_id]

    # notmerge / общий номер → только тег на всю группу
    all_tags = set()
    for c in contacts:
        all_tags |= set(current_tags(c))
    if settings.tag_notmerge in all_tags or _is_shared_number(ctx.client, contacts):
        tag_manual_pair(ctx, "contact", contacts, reason="shared_or_notmerge")
        return {"contact_id": base_id, "action": "manual_shared", "group": group}

    # Instagram-ник / без телефона → всегда только тег
    if not has_phone:
        tag_manual_pair(ctx, "contact", contacts, reason="no_phone_instagram")
        return {"contact_id": base_id, "action": "manual_no_phone", "group": group}

    manual_group: list[dict[str, Any]] = []
    for dup in dups:
        chat = read_contact_chat(ctx.client, int(dup["id"]))
        cls = chat.classify()
        if cls == "none":
            _soft_merge(ctx, base, dup, None, reason="no_chat")
        elif cls == "light" and ctx.flags.light_chat_merge and can_reply_in_channel(base):
            _soft_merge(ctx, base, dup, chat.messages, reason="light_chat")
        else:
            manual_group.append(dup)

    if manual_group:
        tag_manual_pair(ctx, "contact", [base] + manual_group, reason="rich_or_attachments")
        return {"contact_id": base_id, "action": "manual_rich", "group": group,
                "manual": [c["id"] for c in manual_group]}

    return {"contact_id": base_id, "action": "contact_merged", "group": group}
