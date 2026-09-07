"""Автоназначение ответственного в воронке «Повторные продажи».

Когда сделка контакта создаётся или переходит на этап «Требует касания» /
«Не обработан» в воронке «Повторные продажи» — ответственным становится тот,
кто закрыл последнюю успешную сделку этого контакта в «Продажи» или
«Повторные продажи» (по closed_at). Берём самую свежую из двух воронок.
Тот же пользователь ставится ответственным и на контакте.

Если успехов нет, либо ответственный последней продажи — служебный аккаунт
(Максим / Максим Дейкало) — берём ответственного с контакта и ставим его
на сделку.

Срабатывает от веб-хука amoCRM (`app.main`, POST /webhooks/amocrm/{token}).
На каждый вход в целевые этапы переназначаем заново (даже если ответственного
до этого поменяли руками) — так согласовано с владельцем продукта.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app import amocrm_client
from app.config import get_settings

log = logging.getLogger(__name__)

_NOTE_WON = (
    "🤖 Ответственный назначен автоматически — по последней закрытой продаже контакта "
    "(воронка «Продажи» или «Повторные продажи»)."
)
_NOTE_CONTACT = (
    "🤖 Ответственный назначен автоматически — с карточки контакта "
    "(успешной продажи в «Продажи»/«Повторные продажи» нет)."
)


def _target_status_ids() -> set[int]:
    s = get_settings()
    ids = {int(s.amo_status_trebuet_kasaniya or 0), int(s.amo_status_ne_obrabotan or 0)}
    return {i for i in ids if i > 0}


def _usable_user_id(user_id: object, dump_ids: set[int]) -> Optional[int]:
    if not isinstance(user_id, int) or user_id <= 0 or user_id in dump_ids:
        return None
    return user_id


def resolve_responsible_source(
    won_resp: object,
    contact_resp: object,
    dump_ids: set[int],
) -> Optional[tuple[str, int]]:
    """Источник ответственного: последняя продажа, иначе контакт.

    Служебные id (Максим / Дейкало) источником не считаем.
    """
    won = _usable_user_id(won_resp, dump_ids)
    if won is not None:
        return ("won", won)
    contact = _usable_user_id(contact_resp, dump_ids)
    if contact is not None:
        return ("contact", contact)
    return None


def maybe_reassign_responsible(
    db: Session,
    lead_id: int,
    *,
    extra_status_ids: set[int] | None = None,
) -> bool:
    """Проверить сделку и при необходимости переназначить ответственного.

    extra_status_ids — дополнительные этапы (разовый прогон «Взято в работу»).
    Возвращает True, если ответственный был реально изменён на сделке и/или контакте.
    """
    s = get_settings()
    repeat_pipeline = int(s.amo_pipeline_repeat or 0)
    sales_pipeline = int(s.amo_pipeline_sales or 0)
    targets = _target_status_ids()
    if extra_status_ids:
        targets |= {int(x) for x in extra_status_ids if int(x or 0) > 0}
    if repeat_pipeline <= 0 or not targets:
        log.warning(
            "repeat_responsible: AMO_PIPELINE_REPEAT/этапы не настроены — пропуск lead=%s",
            lead_id,
        )
        return False

    lead = amocrm_client.fetch_lead_by_id(db, lead_id, with_contacts=True)
    if not lead:
        log.info("repeat_responsible: lead=%s не найден/недоступен", lead_id)
        return False

    if lead.get("pipeline_id") != repeat_pipeline or lead.get("status_id") not in targets:
        return False

    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    contact_id = next((c.get("id") for c in contacts if isinstance(c.get("id"), int)), None)
    if not isinstance(contact_id, int):
        log.info("repeat_responsible: lead=%s без контакта — пропуск", lead_id)
        return False

    won = amocrm_client.find_last_won_sale(
        db,
        contact_id,
        pipeline_ids={sales_pipeline, repeat_pipeline},
        status_id=int(s.amo_status_won),
        exclude_lead_id=lead_id,
    )
    contact = amocrm_client.fetch_contact_by_id(db, contact_id)
    source = resolve_responsible_source(
        (won or {}).get("responsible_user_id"),
        (contact or {}).get("responsible_user_id"),
        s.service_user_ids,
    )
    if source is None:
        log.info(
            "repeat_responsible: lead=%s contact=%s — нет рабочего ответственного "
            "(нет успеха / на сделке и контакте служебный аккаунт)",
            lead_id, contact_id,
        )
        return False

    kind, resp = source
    source_lead_id = won.get("id") if kind == "won" and won else None
    changed = False

    if lead.get("responsible_user_id") != resp:
        if amocrm_client.set_lead_responsible(db, lead_id, resp):
            changed = True
            note = _NOTE_WON if kind == "won" else _NOTE_CONTACT
            amocrm_client.add_lead_note(db, lead_id, note)
            log.info(
                "repeat_responsible: lead=%s contact=%s → responsible_user_id=%s "
                "(source=%s source_lead=%s)",
                lead_id, contact_id, resp, kind, source_lead_id,
            )
        else:
            log.warning(
                "repeat_responsible: не удалось сменить ответственного сделки lead=%s → %s",
                lead_id, resp,
            )
    else:
        log.info(
            "repeat_responsible: lead=%s уже на нужном ответственном (%s, source=%s source_lead=%s)",
            lead_id, resp, kind, source_lead_id,
        )

    if kind == "won":
        if not contact:
            log.warning(
                "repeat_responsible: contact=%s не найден при сверке ответственного",
                contact_id,
            )
            return changed
        if contact.get("responsible_user_id") != resp:
            if amocrm_client.set_contact_responsible(db, contact_id, resp):
                changed = True
                log.info(
                    "repeat_responsible: contact=%s → responsible_user_id=%s (source_lead=%s)",
                    contact_id, resp, source_lead_id,
                )
            else:
                log.warning(
                    "repeat_responsible: не удалось сменить ответственного контакта contact=%s → %s",
                    contact_id, resp,
                )
        else:
            log.info(
                "repeat_responsible: contact=%s уже на нужном ответственном (%s)",
                contact_id, resp,
            )

    return changed


def reassign_dump_open_leads(db: Session) -> dict[str, int]:
    """Разовый прогон: «Не обработан» + «Взято в работу», ответственный служебный."""
    s = get_settings()
    dump = s.service_user_ids
    extra = {int(s.amo_status_vzyato_v_rabotu or 0)}
    status_ids = {
        int(s.amo_status_ne_obrabotan or 0),
        int(s.amo_status_vzyato_v_rabotu or 0),
    }
    status_ids = {x for x in status_ids if x > 0}

    leads = amocrm_client.fetch_leads_on_statuses(
        db, int(s.amo_pipeline_repeat), status_ids
    )
    stats = {
        "scanned": len(leads),
        "dump": 0,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": 0,
    }
    for lead in leads:
        lid = lead.get("id")
        resp = lead.get("responsible_user_id")
        if not isinstance(lid, int):
            continue
        if resp not in dump:
            continue
        stats["dump"] += 1
        try:
            if maybe_reassign_responsible(db, lid, extra_status_ids=extra):
                stats["changed"] += 1
            else:
                stats["unchanged"] += 1
        except Exception:
            stats["errors"] += 1
            log.exception("repeat_responsible backfill failed lead=%s", lid)
    log.info("repeat_responsible backfill: %s", stats)
    return stats
