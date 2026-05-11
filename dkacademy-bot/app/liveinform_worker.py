from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import amocrm_client, ui_text
from app.config import get_settings
from app.liveinform_client import LiveinformError, track_liveinform
from app.liveinform_parse import dedup_suffix_from_track_result, extract_liveinform_id, extract_tracking_hint
from app.models import ContactBinding, LiveinformTrackingMap, ProcessedEvent
from app.phone_utils import normalize_phone, normalize_tracking
from app.status_text import format_liveinform_crm_line, telegram_line_from_track
from app.telegram_api import send_message_sync

log = logging.getLogger(__name__)


def _resolve_liveinform_id(db: Session, body: dict[str, Any]) -> Optional[str]:
    lid = extract_liveinform_id(body)
    if lid:
        return lid
    hint = extract_tracking_hint(body)
    if not hint:
        return None
    norm = normalize_tracking(hint)
    row = db.execute(select(LiveinformTrackingMap).where(LiveinformTrackingMap.tracking_normalized == norm)).scalar_one_or_none()
    if row and row.liveinform_id:
        return str(row.liveinform_id).strip()
    return None


def _upsert_tracking_map(db: Session, tracking_raw: str, liveinform_id: str) -> None:
    norm = normalize_tracking(tracking_raw)
    if not norm:
        return
    row = db.execute(select(LiveinformTrackingMap).where(LiveinformTrackingMap.tracking_normalized == norm)).scalar_one_or_none()
    vid = liveinform_id.strip()
    try:
        if row is None:
            db.add(LiveinformTrackingMap(tracking_normalized=norm, liveinform_id=vid))
        else:
            row.liveinform_id = vid
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Не удалось сохранить liveinform_tracking_map", exc_info=True)


def sync_liveinform_event(db: Session, payload: dict[str, Any]) -> None:
    s = get_settings()
    lf_id = _resolve_liveinform_id(db, payload)
    if not lf_id:
        log.warning(
            "LiveInform webhook: не найден liveinform_id (и нет записи по трек-номеру в БД). payload_keys=%s",
            list(payload.keys())[:40],
        )
        return

    try:
        tr = track_liveinform(liveinform_id=lf_id)
    except LiveinformError as e:
        log.warning("LiveInform track error: %s", e)
        return

    rdict = tr.result or {}
    if not (tr.tracking or "").strip():
        log.warning("LiveInform track: пустой tracking в ответе API")
        return
    if tr.tracking:
        _upsert_tracking_map(db, tr.tracking, lf_id)

    dedup_piece = dedup_suffix_from_track_result(rdict)
    dedup_key = f"li:{lf_id}:{dedup_piece}"
    if len(dedup_key) > 511:
        dedup_key = dedup_key[:511]

    ex = db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == "liveinform",
            ProcessedEvent.dedup_key == dedup_key,
        )
    ).scalar_one_or_none()
    if ex:
        log.info("LiveInform уже обработано: %s", dedup_key[:120])
        return

    status_line = format_liveinform_crm_line(rdict)
    leads = amocrm_client.find_leads_with_cdek(db, tr.tracking)
    seen_lead_ids: set[int] = set()
    for lead in leads:
        lid = lead.get("id")
        if isinstance(lid, int) and lid not in seen_lead_ids:
            seen_lead_ids.add(lid)
            amocrm_client.patch_lead_liveinform(db, lid, status_line)

    cats: list[dict[str, Any]] = []
    try:
        cats = amocrm_client.find_catalog_elements_with_cdek(db, tr.tracking)
        cat_id_int = int(s.amo_catalog_id or 0)
        for el in cats:
            eid = el.get("id")
            if isinstance(eid, int) and cat_id_int > 0:
                amocrm_client.patch_catalog_liveinform(db, cat_id_int, eid, status_line)
    except Exception:
        log.warning("catalog update path failed", exc_info=True)

    contact_ids_set: set[int] = set(amocrm_client.collect_contact_ids_from_leads(leads))

    # Собираем ВСЕ телефоны контактов сделок (у одного контакта может быть
    # несколько номеров: доставка + личный для бота).
    all_phones: set[str] = set()
    for cid in contact_ids_set:
        try:
            for raw_ph in amocrm_client.get_contact_phones(db, cid):
                nph = normalize_phone(raw_ph)
                if nph:
                    all_phones.add(nph)
        except Exception:
            log.warning("get_contact_phones(%s) failed", cid, exc_info=True)
    # Плюс телефон получателя из ответа LiveInform.
    ph_li = normalize_phone(tr.phone or "")
    if ph_li:
        all_phones.add(ph_li)

    token = (s.telegram_bot_token or "").strip()
    tmpl = (s.tg_notify_template or "{status_text}").strip()
    tg_body = telegram_line_from_track(
        tmpl,
        tracking=tr.tracking,
        status_text=status_line,
        delivery=(tr.delivery or ""),
    )
    notify_kb = {
        "inline_keyboard": [
            [
                {"text": "📦 Мои доставки", "callback_data": ui_text.CB_MY_DELIVERIES},
                {
                    "text": "💬 Менеджер",
                    "url": ui_text.manager_link(s.manager_telegram_username),
                },
            ]
        ]
    }

    bindings: list[ContactBinding] = []
    seen_binding_ids: set[int] = set()
    if contact_ids_set:
        for b in db.execute(
            select(ContactBinding).where(
                ContactBinding.amo_contact_id.in_(contact_ids_set)
            )
        ).scalars():
            if b.id not in seen_binding_ids:
                seen_binding_ids.add(b.id)
                bindings.append(b)
    if all_phones:
        for b in db.execute(
            select(ContactBinding).where(
                ContactBinding.phone_normalized.in_(all_phones)
            )
        ).scalars():
            if b.id not in seen_binding_ids:
                seen_binding_ids.add(b.id)
                bindings.append(b)

    log.info(
        "liveinform: lead_contacts=%s phones_collected=%s bindings_found=%s",
        sorted(contact_ids_set),
        sorted(all_phones),
        [(b.telegram_chat_id, b.amo_contact_id) for b in bindings],
    )

    chat_ids_seen: set[str] = set()
    if token and tg_body:
        for b in bindings:
            if b.is_blocked:
                continue
            cid_s = str(b.telegram_chat_id)
            if cid_s in chat_ids_seen:
                continue
            chat_ids_seen.add(cid_s)
            ok, err = send_message_sync(
                token, b.telegram_chat_id, tg_body, reply_markup=notify_kb
            )
            if ok:
                continue
            if err == "blocked":
                b.is_blocked = True
                try:
                    db.commit()
                except Exception:
                    db.rollback()

    try:
        db.add(ProcessedEvent(source="liveinform", dedup_key=dedup_key))
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("Race duplicate liveinform dedup skipped")
