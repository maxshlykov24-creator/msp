"""Обработка входящих вебхуков от LiveInform.

Нативная интеграция LiveInform → amoCRM сама обновляет поле «Статус LiveInform».
Наша задача: принять вебхук → классифицировать смысловой статус → при попадании
в whitelist отправить шаблон привязанным клиентам в Telegram/MAX.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import amocrm_client
from app.config import get_settings
from app.delivery_notify_templates import template_for
from app.delivery_semantics import (
    classify_delivery_event,
    delivery_status_code,
    is_intentional_no_notify,
)
from app.liveinform_parse import extract_liveinform_id, extract_tracking_hint
from app.messenger import notify
from app.models import ContactBinding, LiveinformTrackingMap, ProcessedEvent
from app.ops_alert import maybe_alert_unclassified
from app.phone_utils import normalize_phone, normalize_tracking
from app.status_text import format_liveinform_crm_line

log = logging.getLogger(__name__)


def _upsert_tracking_map(db: Session, tracking_raw: str, liveinform_id: str) -> None:
    norm = normalize_tracking(tracking_raw)
    if not norm:
        return
    row = db.execute(
        select(LiveinformTrackingMap).where(LiveinformTrackingMap.tracking_normalized == norm)
    ).scalar_one_or_none()
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


def _mark_processed(db: Session, dedup_key: str) -> None:
    try:
        db.add(ProcessedEvent(source="liveinform", dedup_key=dedup_key))
        db.commit()
    except IntegrityError:
        db.rollback()


def sync_liveinform_event(db: Session, payload: dict[str, Any]) -> None:
    """Обрабатывает вебхук от LiveInform.

    1. Извлекает liveinform_id и трек из payload.
    2. Находит сделки amoCRM по трек-номеру, читает поле «Статус LiveInform».
    3. Классифицирует смысловой статус (код LI + алиасы СДЭК/Почта).
    4. Вне whitelist — тишина; внутри — шаблон один раз на (трек, semantic_id).
    """
    lf_id = extract_liveinform_id(payload)
    tracking_hint = extract_tracking_hint(payload)

    if not lf_id and not tracking_hint:
        log.warning(
            "LiveInform webhook: нет liveinform_id и трек-номера. payload_keys=%s",
            list(payload.keys())[:40],
        )
        return

    if lf_id and tracking_hint:
        _upsert_tracking_map(db, tracking_hint, lf_id)

    tracking = (tracking_hint or "").strip()
    if not tracking:
        log.info("LiveInform webhook: нет трек-номера, выходим")
        return

    tracking_norm = normalize_tracking(tracking) or tracking
    code = delivery_status_code(payload)
    dedup_piece = f"{code}|{payload.get('status', '')}|{payload.get('track_status', '')}|{payload.get('status_text', '')}"
    dedup_key = f"li:{lf_id or tracking_hint}:{dedup_piece}"[:511]

    ex = db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == "liveinform",
            ProcessedEvent.dedup_key == dedup_key,
        )
    ).scalar_one_or_none()
    if ex:
        log.info("LiveInform уже обработано: %s", dedup_key[:120])
        return

    # Даём нативной интеграции LI→amoCRM 2 секунды записать статус в поле
    time.sleep(2)

    s = get_settings()
    st_fid = int(s.amo_field_lead_liveinform_status or 0)
    leads = amocrm_client.find_leads_with_cdek(db, tracking)

    status_line = ""
    for lead in leads:
        lid = lead.get("id")
        if not isinstance(lid, int):
            continue
        fresh = amocrm_client.fetch_lead_by_id(db, lid) or lead
        v = amocrm_client.extract_lead_field_value(fresh, st_fid) if st_fid else ""
        if v.strip():
            status_line = v.strip()
            break

    if not status_line.strip():
        status_line = format_liveinform_crm_line(payload)

    semantic_id = classify_delivery_event(payload, status_line=status_line)
    if not semantic_id:
        if is_intentional_no_notify(payload):
            log.info(
                "liveinform: явный отказ (возврат/стоп), клиенту не шлём. track=%s",
                tracking_norm,
            )
        else:
            log.info(
                "liveinform: серый/нераспознанный статус, клиенту не шлём. track=%s status_line=%r",
                tracking_norm,
                status_line[:80] if status_line else "",
            )
            maybe_alert_unclassified(
                db,
                tracking_norm=tracking_norm,
                tracking_raw=tracking,
                liveinform_id=(lf_id or ""),
                payload=payload,
                status_line=status_line,
            )
        _mark_processed(db, dedup_key)
        return

    notify_dedup_key = f"li_tg:{tracking_norm}:{semantic_id}"[:511]
    notify_ex = db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == "liveinform",
            ProcessedEvent.dedup_key == notify_dedup_key,
        )
    ).scalar_one_or_none()
    if notify_ex:
        log.info("Статус уже отправлен клиенту, пропускаем: %s", notify_dedup_key[:120])
        _mark_processed(db, dedup_key)
        return

    tg_body = template_for(semantic_id)
    if not tg_body:
        log.warning("liveinform: нет шаблона для semantic_id=%s", semantic_id)
        _mark_processed(db, dedup_key)
        return

    contact_ids_set: set[int] = set(amocrm_client.collect_contact_ids_from_leads(leads))
    all_phones: set[str] = set()
    for cid in contact_ids_set:
        try:
            for raw_ph in amocrm_client.get_contact_phones(db, cid):
                nph = normalize_phone(raw_ph)
                if nph:
                    all_phones.add(nph)
        except Exception:
            log.warning("get_contact_phones(%s) failed", cid, exc_info=True)

    ph_li = normalize_phone(str(payload.get("phone", "") or ""))
    if ph_li:
        all_phones.add(ph_li)

    bindings: list[ContactBinding] = []
    seen_binding_ids: set[int] = set()
    if contact_ids_set:
        for b in db.execute(
            select(ContactBinding).where(ContactBinding.amo_contact_id.in_(contact_ids_set))
        ).scalars():
            if b.id not in seen_binding_ids:
                seen_binding_ids.add(b.id)
                bindings.append(b)
    if all_phones:
        for b in db.execute(
            select(ContactBinding).where(ContactBinding.phone_normalized.in_(all_phones))
        ).scalars():
            if b.id not in seen_binding_ids:
                seen_binding_ids.add(b.id)
                bindings.append(b)

    log.info(
        "liveinform: semantic=%s lead_contacts=%s phones=%s bindings=%s status=%r",
        semantic_id,
        sorted(contact_ids_set),
        sorted(all_phones),
        [(b.telegram_chat_id, b.channel, b.amo_contact_id) for b in bindings],
        status_line[:80] if status_line else "",
    )

    seen_keys: set[str] = set()
    for b in bindings:
        if b.is_blocked:
            continue
        key = f"{b.channel}:{b.telegram_chat_id}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ok, err = notify(b, tg_body)
        if not ok and err == "blocked":
            b.is_blocked = True
            try:
                db.commit()
            except Exception:
                db.rollback()
            amocrm_client.set_contact_bot_active(db, int(b.amo_contact_id), b.channel, False)

    _mark_processed(db, dedup_key)
    _mark_processed(db, notify_dedup_key)
