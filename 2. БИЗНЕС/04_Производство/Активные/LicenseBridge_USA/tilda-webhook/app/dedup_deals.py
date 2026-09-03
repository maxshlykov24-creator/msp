"""Антидубль СДЕЛОК (без NOVA).

Основная в воронке — та, что дальше по этапам; при равных этапах старшая по дате.
Дубль = перенос примечаний/звонков на основную + уход в «Провал» с причиной «дубль»
(см. actions.mark_duplicate). Сделки на РАЗНЫХ карточках одного человека (карточки
не склеились: общий чат, notmerge) тоже считаются дублями — «один человек»
определяется по имени, см. `person_group`.
Межворонка Pipeline→Сборка сворачивается только при создании нового лида.
Ручной разбор (тег пары) остаётся для двух случаев: карточки — разные люди
(семья/офис на одном номере) и примечания дубля не читаются.
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
from app.config import CLOSED_STATUS_IDS, settings
from app.identity import same_person_name

log = logging.getLogger("dedup_deals")

# note_type, указывающие на живую переписку/talk, привязанную к сделке.
_CHAT_NOTE_TYPES = {"amomessage", "chat_message"}


def is_open(lead: dict[str, Any]) -> bool:
    return lead.get("closed_at") in (None, 0) and lead.get("status_id") not in CLOSED_STATUS_IDS


_is_open = is_open  # прежнее имя: используется в этом модуле и в тестах


def lead_contact_ids(lead: dict[str, Any]) -> set[int]:
    return {int(c["id"]) for c in ((lead.get("_embedded") or {}).get("contacts") or [])
            if c.get("id")}


def load_leads(client, contact_id: int | list[int] | tuple[int, ...]) -> list[dict[str, Any]]:
    """Полные сделки контакта (или группы контактов) с pipeline/status/closed/created."""
    ids = [contact_id] if isinstance(contact_id, int) else list(contact_id)
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for cid in ids:
        data = client.get_contact(int(cid), with_="leads")
        stubs = ((data or {}).get("_embedded", {}) or {}).get("leads", []) or []
        for st in stubs:
            lid = st.get("id")
            if not lid or int(lid) in seen:
                continue
            seen.add(int(lid))
            full = client.get_lead(lid, with_="contacts")
            if full:
                out.append(full)
    return out


def person_group(client, contact_ids: list[int], anchor_id: int) -> set[int]:
    """Карточки из группы одного номера, которые сводятся к ОДНОМУ человеку.

    Карточек-дублей у клиента бывает 3–4 (Tilda, Facebook, Wazzup, звонок), и
    склеить их автоматически удаётся не всегда (живой чат, notmerge). Для сделок
    это не повод считать их разными клиентами: имя сравнивается с учётом
    транслитерации и опечаток (`same_person_name`). Если имя явно другое —
    карточка в группу не попадает (семья/офис на одном номере)."""
    anchor_name: str | None = None
    names: dict[int, str | None] = {}
    for cid in {int(x) for x in contact_ids} | {int(anchor_id)}:
        c = client.get_contact(int(cid), with_="leads")
        if not c:
            continue
        names[int(cid)] = c.get("name")
        if int(cid) == int(anchor_id):
            anchor_name = c.get("name")
    same = {cid for cid, name in names.items() if same_person_name(anchor_name, name)}
    same.add(int(anchor_id))
    return same


def status_order(client) -> dict[int, int]:
    """{status_id: sort} по всем воронкам — чтобы понять, какая сделка дальше прошла."""
    order: dict[int, int] = {}
    try:
        pipelines = client.pipelines()
    except Exception as exc:  # без порядка этапов решаем по дате создания
        log.warning("pipelines read failed: %s", exc)
        return order
    for pl in pipelines or []:
        for st in ((pl.get("_embedded") or {}).get("statuses") or []):
            if st.get("id") is not None:
                order[int(st["id"])] = int(st.get("sort") or 0)
    return order


def _main_first(leads: list[dict[str, Any]], order: dict[int, int]) -> list[dict[str, Any]]:
    """Дальше по воронке — первой; при равных этапах первой идёт старшая сделка.

    Так основной остаётся та, по которой уже работали, а в «Провал» уезжает более
    свежая заявка с начальных этапов."""
    return sorted(leads, key=lambda l: (-order.get(int(l.get("status_id") or 0), 0),
                                        l.get("created_at") or 0))


def _lead_chat_stats(client, lead_id: int):
    """Переписка сделки по общему потоку событий.

    Раньше здесь были только notes и talks — и то и другое в этом аккаунте пусто
    (Wazzup не пишет сообщения в карточку, `with=talks` на сделке ничего не
    отдаёт). Из-за этого сделка с 21 сообщением клиента считалась «без чата», и
    менеджер не получал даже подсказки, где искать переписку. Проверено 19.08.2026
    на сделке 29892373."""
    try:
        from app.chat_events import for_lead
        return for_lead(client, lead_id)
    except Exception as exc:  # noqa: BLE001 — дедуп важнее дайджеста
        log.warning("chat stats for %s failed: %s", lead_id, exc)
        return None


def _lead_has_chat(client, lead_id: int) -> bool:
    if _lead_chat_stats(client, lead_id):
        return True
    try:
        notes = client.get_notes("leads", lead_id)
    except Exception:
        return True  # не прочитали — считаем, что может быть → не удаляем
    if any(n.get("note_type") in _CHAT_NOTE_TYPES for n in notes):
        return True
    try:
        talks = client.get_lead_talks(lead_id)
        return bool(talks)
    except Exception:
        return True


def _transfer_notes(ctx: Ctx, src_id: int, dst_id: int) -> bool:
    """Копирует текст примечаний/звонков дубля в основную сделку. Возвращает False,
    если перенос невозможен (нечитаемо) → вызывающий уводит в ручную склейку."""
    try:
        notes = ctx.client.get_notes("leads", src_id)
    except Exception:
        return False
    lines: list[str] = []
    for n in notes:
        nt = n.get("note_type", "")
        params = n.get("params") or {}
        text = params.get("text") or params.get("comment")
        if nt in ("call_in", "call_out"):
            dur = params.get("duration")
            lines.append(f"[звонок {nt} {dur or ''}] {text or ''}".strip())
        elif text:
            lines.append(str(text))
    if lines:
        body = f"Перенос из дубль-сделки #{src_id}:\n" + "\n".join(f"— {x}" for x in lines)
        add_note(ctx, "deal", dst_id, body)
    return True


def _same_person(main: dict[str, Any], dup: dict[str, Any], person: set[int]) -> bool:
    """Сделки принадлежат одному клиенту.

    Одна карточка на обеих сделках — очевидный случай. Разные карточки годятся,
    только если ВСЕ они входят в группу «один человек» (`person_group`): иначе это
    может быть семья на общем номере, и такая пара уходит на ручной разбор."""
    mc, dc = lead_contact_ids(main), lead_contact_ids(dup)
    if mc & dc:
        return True
    return bool(mc and dc and mc <= person and dc <= person)


def _dedup_pair(ctx: Ctx, main: dict[str, Any], dup: dict[str, Any],
                person: set[int]) -> None:
    main_id, dup_id = int(main["id"]), int(dup["id"])
    if not _same_person(main, dup, person):
        tag_manual_pair(ctx, "deal", [main, dup], reason="deal_cross_contact")
        return
    # Живой чат на дубле больше не блокирует разбор: дубль не удаляется, а уходит
    # в «Провал», переписка остаётся и в сделке, и на карточке контакта. Раньше
    # такая пара навсегда оставалась висеть в воронке с тегом ручной склейки.
    chat = _lead_chat_stats(ctx.client, dup_id)
    had_chat = bool(chat) or _lead_has_chat(ctx.client, dup_id)
    if not _transfer_notes(ctx, dup_id, main_id):
        tag_manual_pair(ctx, "deal", [main, dup], reason="deal_notes_unreadable")
        return
    # Сообщения физически остаются на дубле: перенести чат через API нельзя.
    # Значит в живую карточку кладём адрес и цифры, иначе менеджер не узнает, что
    # переписка вообще была, — ровно это и произошло с 98 сообщениями за август.
    if chat:
        from app.chat_events import lead_url
        chat_hint = (f" Переписка осталась на дубле: {chat.describe()}"
                     f"\nОткрыть переписку: {lead_url(dup_id)}")
    elif had_chat:
        chat_hint = " На дубле была переписка — проверь её здесь."
    else:
        chat_hint = ""
    add_note(ctx, "deal", main_id,
             f"Дубль сделки #{dup_id} устранён (перенос примечаний), "
             f"{dup.get('created_at')}.{chat_hint}")
    if not ctx.flags.deal_dedup or ctx.shadow:
        log_decision(ctx, "deal_dedup.skip", main=main_id, dup=dup_id,
                     shadow=ctx.shadow, enabled=ctx.flags.deal_dedup)
        return
    snapshot_entity(ctx, "deal", dup_id, reason="deal_dedup_mark")
    from app.chat_events import lead_url
    add_note(ctx, "deal", dup_id,
             f"Дубль сделки #{main_id}: работа ведётся там.\n{lead_url(main_id)}")
    mark_duplicate(ctx, "deal", dup, reason=f"дубль сделки #{main_id}")
    log_decision(ctx, "deal_dedup.resolved", main=main_id, dup=dup_id, chat_on_dup=had_chat)


def _fold_into_assembly(ctx: Ctx, new_lead: dict[str, Any], assembly: dict[str, Any],
                        person: set[int]) -> None:
    """Свернуть новый Pipeline-лид в открытую Сборку (владелец Сборки сохраняется)."""
    new_id, asm_id = int(new_lead["id"]), int(assembly["id"])
    if not _same_person(new_lead, assembly, person):
        tag_manual_pair(ctx, "deal", [assembly, new_lead], reason="deal_cross_contact")
        return
    _transfer_notes(ctx, new_id, asm_id)
    add_note(ctx, "deal", asm_id,
             f"Новый лид #{new_id} свёрнут в Сборку при создании (межворонка).")
    if not (ctx.flags.cross_funnel and ctx.flags.deal_dedup) or ctx.shadow:
        log_decision(ctx, "cross_funnel.skip", new=new_id, assembly=asm_id, shadow=ctx.shadow)
        return
    snapshot_entity(ctx, "deal", new_id, reason="cross_funnel_fold")
    mark_duplicate(ctx, "deal", new_lead, reason=f"свёрнут в Сборку #{asm_id}")
    log_decision(ctx, "cross_funnel.folded", new=new_id, assembly=asm_id)


def resolve(ctx: Ctx, contact_id: int, new_lead_id: int | None, trigger: str,
            group_contact_ids: list[int] | None = None) -> dict[str, Any]:
    """Главная точка антидубля сделок для контакта.

    `group_contact_ids` — все карточки одного номера, включая те, что контактный
    антидубль решил НЕ склеивать. Без них дубль сделки на «чужой» карточке в
    разбор не попадал вовсе: смотрели только сделки итогового контакта.
    """
    ids = sorted({int(contact_id), *(int(x) for x in (group_contact_ids or []))})
    leads = load_leads(ctx.client, ids)
    person = person_group(ctx.client, ids, contact_id)
    order = status_order(ctx.client)
    # Уже помеченные дубли работой не считаются: иначе каждый следующий вебхук по
    # контакту снова «переносил» бы примечания.
    dup_tags = {settings.tag_dup_deal, settings.tag_dup_to_delete}
    open_leads = [l for l in leads
                  if _is_open(l) and not dup_tags & set(current_tags(l))]
    open_assembly = sorted(
        [l for l in open_leads if l.get("pipeline_id") == settings.assembly_pipeline_id],
        key=lambda l: l.get("created_at") or 0,
    )
    open_pipeline = sorted(
        [l for l in open_leads if l.get("pipeline_id") == settings.pipeline_id],
        key=lambda l: l.get("created_at") or 0,
    )

    # 1. Межворонка только при создании нового Pipeline-лида на этапе «Новая заявка».
    if trigger == "add_lead" and new_lead_id:
        new_lead = next((l for l in open_pipeline if int(l["id"]) == int(new_lead_id)), None)
        if (new_lead and new_lead.get("status_id") == settings.status_new
                and open_assembly):
            _fold_into_assembly(ctx, new_lead, open_assembly[0], person)
            return {"action": "cross_funnel_fold", "assembly": open_assembly[0]["id"]}

    # 2. Pipeline + Сборка параллельно (Pipeline продвинулся) — не дубль.
    #    (обрабатывается тем, что дубли ищем ТОЛЬКО внутри одной воронки ниже.)

    # 3. Дубли внутри одной воронки: основная — та, что дальше по этапам.
    # Handoff-сделки («сборка_из_продажи», см. app/handoff.py) исключаем из
    # пар: каждая законно пришла из своей выигранной продажи в Pipeline —
    # это не дубль контакта, даже если их несколько открыто одновременно.
    assembly_for_dedup = [l for l in open_assembly if settings.handoff_tag not in current_tags(l)]
    resolved: list[dict[str, int]] = []
    for funnel_leads in (assembly_for_dedup, open_pipeline):
        if len(funnel_leads) >= 2:
            ranked = _main_first(funnel_leads, order)
            main = ranked[0]
            for dup in ranked[1:]:
                _dedup_pair(ctx, main, dup, person)
                resolved.append({"main": int(main["id"]), "dup": int(dup["id"])})

    if resolved:
        return {"action": "deal_dedup", "pairs": resolved}

    # 4. Нет открытых дублей.
    return {"action": "deal_noop", "contacts": ids,
            "open_pipeline": [l["id"] for l in open_pipeline],
            "open_assembly": [l["id"] for l in open_assembly]}
