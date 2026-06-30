"""Автоматизация «Требует касания» (повторное касание).

Логика (всё считается ОТ ДАТЫ УСПЕХА = closed_at успешной сделки в «Продажи»):

1. Через `repeat_touch_msg_days` (35) дней — если у клиента нет открытой сделки,
   бот шлёт напоминание о пополнении (`repeat_touch_message`) и ставит примечание в карточку контакта.
2. Через `repeat_touch_deal_days` (40) дней — если клиент сам не написал
   (нет открытой сделки, кроме этапа «Требует касания») и у него ещё нет сделки
   на этом этапе, создаётся новая сделка в «Повторные продажи → Требует касания».

amoCRM не умеет фильтровать по условиям «нет открытых сделок / прошло N дней»,
поэтому проверки делаются здесь, на сервере. Идемпотентность — через ProcessedEvent
(ключ привязан к id успешной сделки, чтобы после следующей покупки касание повторилось).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import amocrm_client
from app.config import get_settings
from app.database import get_session_factory
from app.messenger import notify
from app.models import ContactBinding, ProcessedEvent

log = logging.getLogger(__name__)

SOURCE = "repeat_touch"


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _already(db: Session, key: str) -> bool:
    return db.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.source == SOURCE, ProcessedEvent.dedup_key == key
        )
    ).scalar_one_or_none() is not None


def _mark(db: Session, key: str) -> None:
    try:
        db.add(ProcessedEvent(source=SOURCE, dedup_key=key[:511]))
        db.commit()
    except IntegrityError:
        db.rollback()


def _send_reminder(db: Session, contact_id: int) -> bool:
    """Отправить напоминание во все живые привязки контакта. True если ушло хоть одно."""
    s = get_settings()
    bindings = list(
        db.execute(
            select(ContactBinding).where(ContactBinding.amo_contact_id == int(contact_id))
        ).scalars()
    )
    sent = False
    for b in bindings:
        if b.is_blocked:
            continue
        ok, err = notify(b, s.repeat_touch_message)
        if ok:
            sent = True
        elif err == "blocked":
            b.is_blocked = True
            try:
                db.commit()
            except Exception:
                db.rollback()
            amocrm_client.set_contact_bot_active(db, int(b.amo_contact_id), b.channel, False)
    return sent


def process_repeat_touch(
    db: Session,
    *,
    send_messages: bool = True,
    create_deals: bool = True,
    lookback_days: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Один прогон. Возвращает счётчики для логов."""
    s = get_settings()
    touch_status = int(s.amo_status_trebuet_kasaniya or 0)
    if create_deals and touch_status <= 0:
        log.warning("repeat_touch: AMO_STATUS_TREBUET_KASANIYA не задан — сделки не создаём")
        create_deals = False

    look = int(lookback_days if lookback_days is not None else s.repeat_touch_lookback_days)
    now = _now_ts()
    from_ts = now - look * 86400

    won = amocrm_client.fetch_won_leads_closed(
        db, int(s.amo_pipeline_sales), int(s.amo_status_won), from_ts, now
    )
    log.info("repeat_touch: успешных сделок в окне %dд: %d", look, len(won))

    exclude = {touch_status} if touch_status > 0 else set()
    active_fid = int(s.amo_field_contact_active or 0)
    stats = {"won": len(won), "messaged": 0, "deals": 0, "skipped_open": 0}
    seen: set[int] = set()

    for lead in won:
        lead_id = lead.get("id")
        closed_at = lead.get("closed_at")
        if not isinstance(lead_id, int) or not closed_at:
            continue
        days = (now - int(closed_at)) / 86400.0

        contacts = (lead.get("_embedded") or {}).get("contacts") or []
        contact_id = next((c.get("id") for c in contacts if isinstance(c.get("id"), int)), None)
        if not isinstance(contact_id, int) or contact_id in seen:
            continue
        seen.add(contact_id)

        # Опциональная сверка флажка «Действующий».
        if active_fid > 0 and not amocrm_client.contact_field_is_true(db, contact_id, active_fid):
            continue

        # ── Сообщение через msg_days ──
        if send_messages and days >= s.repeat_touch_msg_days:
            key = f"msg:{lead_id}"
            if not _already(db, key):
                if amocrm_client.has_open_deal_excluding(db, contact_id, exclude):
                    stats["skipped_open"] += 1
                elif dry_run:
                    log.info("[DRY] msg → contact %s (lead %s, %.0fд)", contact_id, lead_id, days)
                    stats["messaged"] += 1
                elif _send_reminder(db, contact_id):
                    amocrm_client.add_contact_note(
                        db, contact_id,
                        f"🤖 Бот отправил напоминание о пополнении: «{s.repeat_touch_message}»",
                    )
                    _mark(db, key)
                    stats["messaged"] += 1

        # ── Сделка через deal_days ──
        if create_deals and days >= s.repeat_touch_deal_days:
            key = f"deal:{lead_id}"
            if not _already(db, key):
                if amocrm_client.has_open_deal_excluding(db, contact_id, exclude):
                    stats["skipped_open"] += 1
                    # клиент сам написал/есть активная сделка — касание не нужно
                    _mark(db, key)
                elif amocrm_client.has_lead_on_status(db, contact_id, touch_status):
                    _mark(db, key)
                elif dry_run:
                    log.info("[DRY] deal → contact %s (lead %s, %.0fд)", contact_id, lead_id, days)
                    stats["deals"] += 1
                else:
                    new_id = amocrm_client.create_lead_on_stage(
                        db,
                        pipeline_id=int(s.amo_pipeline_repeat),
                        status_id=touch_status,
                        contact_id=contact_id,
                        name="Повторное касание (авто)",
                    )
                    if new_id:
                        _mark(db, key)
                        stats["deals"] += 1

    log.info("repeat_touch итог: %s", stats)
    return stats


def run_once() -> dict[str, int]:
    SessLocal = get_session_factory()
    db = SessLocal()
    try:
        return process_repeat_touch(db)
    finally:
        db.close()


async def run_repeat_touch_forever() -> None:
    s = get_settings()
    if not s.repeat_touch_enabled:
        log.info("repeat_touch отключён (REPEAT_TOUCH_ENABLED=false)")
        return
    interval = max(1, int(s.repeat_touch_interval_hours)) * 3600
    log.info("repeat_touch воркер запущен, период %dч", s.repeat_touch_interval_hours)
    while True:
        try:
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            log.info("repeat_touch остановлен")
            return
        except Exception:
            log.exception("repeat_touch прогон упал")
        await asyncio.sleep(interval)
