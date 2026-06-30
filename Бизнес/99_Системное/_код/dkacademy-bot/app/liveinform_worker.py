"""Обработка входящих вебхуков от LiveInform.

Нативная интеграция LiveInform → amoCRM сама обновляет поле «Статус LiveInform».
Наша задача: принять вебхук → прочитать актуальный статус из amoCRM (или fallback
из тела вебхука) → отправить Telegram-уведомление привязанным клиентам.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import amocrm_client, ui_text
from app.config import get_settings
from app.liveinform_parse import extract_liveinform_id, extract_tracking_hint
from app.messenger import notify
from app.models import ContactBinding, LiveinformTrackingMap, ProcessedEvent
from app.phone_utils import normalize_phone, normalize_tracking
from app.status_text import format_liveinform_crm_line, telegram_line_from_track

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


def sync_liveinform_event(db: Session, payload: dict[str, Any]) -> None:
    """Обрабатывает вебхук от LiveInform.

    1. Извлекает liveinform_id и трек из payload.
    2. Находит сделки amoCRM по трек-номеру, читает поле «Статус LiveInform»
       (GET лида — актуальные custom_fields после нативной интеграции LI).
    3. Если поле ещё пустое — fallback: текст из тела вебхука (format_liveinform_crm_line).
    4. Шлёт Telegram-уведомление привязанным клиентам (без PATCH лида в amo).
    """
    s = get_settings()

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

    dedup_piece = str(payload.get("status", "")) + str(payload.get("status_text", ""))
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

    # Дедуп по итоговому тексту уведомления — не шлём тот же статус дважды
    notify_dedup_key = f"li_tg:{lf_id or tracking}:{status_line}"[:511]
    notify_ex = db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == "liveinform",
            ProcessedEvent.dedup_key == notify_dedup_key,
        )
    ).scalar_one_or_none()
    if notify_ex:
        log.info("Статус уже отправлен клиенту, пропускаем: %s", notify_dedup_key[:120])
        # Всё равно сохраняем первичный dedup чтобы не обрабатывать этот payload повторно
        try:
            db.add(ProcessedEvent(source="liveinform", dedup_key=dedup_key))
            db.commit()
        except IntegrityError:
            db.rollback()
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

    tmpl = (s.tg_notify_template or "{status_text}").strip()
    tg_body = telegram_line_from_track(
        tmpl,
        tracking=tracking,
        status_text=status_line,
        delivery=str(payload.get("delivery", "") or ""),
    )

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
        "liveinform: lead_contacts=%s phones=%s bindings=%s status=%r",
        sorted(contact_ids_set),
        sorted(all_phones),
        [(b.telegram_chat_id, b.channel, b.amo_contact_id) for b in bindings],
        status_line[:80] if status_line else "",
    )

    seen_keys: set[str] = set()
    if tg_body:
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
                # Снимаем флажок «Подписан на бот» в карточке amo — аудитория реальная
                amocrm_client.set_contact_bot_active(db, int(b.amo_contact_id), b.channel, False)

    try:
        db.add(ProcessedEvent(source="liveinform", dedup_key=dedup_key))
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("Race duplicate liveinform dedup skipped")
    try:
        db.add(ProcessedEvent(source="liveinform", dedup_key=notify_dedup_key))
        db.commit()
    except IntegrityError:
        db.rollback()
