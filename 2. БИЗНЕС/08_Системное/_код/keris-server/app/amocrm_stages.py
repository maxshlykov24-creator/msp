"""Этапы воронки «Груминг»: куда должна стоять сделка по состоянию записи.

Сделка = один визит. Этапы «Завтра запись» и «Сегодня запись» ведёт сервер, а не
автоматизация amoCRM: у amoCRM нет условия «дата в поле наступила завтра», а
источник истины по времени визита всё равно наш (`bookings.starts_at`).

Переходы считаются в цикле каждые 5 минут (`main.reminders_loop`), плюс сразу
после каждого события записи. Отправку сообщений строит владелец сейлсботом —
наша задача только держать этап честным и своевременным.

Оговорка про день создания: в сутки, когда запись оформили, сделка стоит на
«Запись создана» и по датам не двигается. Иначе запись «на завтра», созданная
сегодня, через пять минут уезжала бы в «Завтра запись» и клиент получал бы в
WhatsApp сразу и «записали вас», и «напоминаем, завтра визит».
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import amocrm_client, clock
from .models import Booking, BookingStatus

log = logging.getLogger("keris.amocrm.stages")

STAGE_CREATED = "Запись создана"
STAGE_WAITING = "Ожидает визита"
STAGE_TOMORROW = "Завтра запись"
STAGE_TODAY = "Сегодня запись"
STAGE_ARRIVED = "Клиент пришёл"
STAGE_CANCELLED = "Отменена"
STAGE_NO_SHOW = "Не пришёл"
STAGE_DONE = "Визит завершён"  # системный Успех (142), см. amocrm_client.stage_id

# Окно, в котором сделки вообще пересчитываются: прошлые визиты дальше 14 дней
# уже закрыты, а горизонт записи — 30 дней (booking_rules), с запасом 90.
PAST_WINDOW_DAYS = 14
FUTURE_WINDOW_DAYS = 90


def target_stage(booking: Booking, now: datetime) -> str:
    """Этап, на котором сделка должна стоять прямо сейчас."""
    if booking.status == BookingStatus.cancelled:
        return STAGE_CANCELLED
    if booking.status == BookingStatus.no_show:
        return STAGE_NO_SHOW
    if booking.status == BookingStatus.completed:
        # «Пришел» в журнале ставят на входе, поэтому визит считается завершённым
        # только когда закончилось его время.
        return STAGE_DONE if booking.ends_at <= now else STAGE_ARRIVED

    created = booking.created_at or now
    if created.date() >= now.date():
        return STAGE_CREATED

    days_left = (booking.starts_at.date() - now.date()).days
    if days_left < 0:
        # Визит был раньше, а «Пришел» в журнале никто не поставил. Днём визита
        # это ещё ждёт отметки, а на следующий день «Сегодня запись» — вранье:
        # сейлсбот отправил бы напоминание о визите, который уже прошёл. Считаем,
        # что клиент был (так же считают метрики), но в Успех сами не закрываем.
        return STAGE_ARRIVED
    if days_left == 0:
        return STAGE_TODAY
    if days_left == 1:
        return STAGE_TOMORROW
    return STAGE_WAITING


def sync_stage(db: Session, booking: Booking, now: datetime | None = None) -> str | None:
    """Перевести сделку на актуальный этап. Возвращает имя этапа, если перевели.

    Best-effort: сбой amoCRM не должен ронять поток записи, поэтому исключение
    наружу не летит — только предупреждение в лог, следующий проход повторит.
    """
    if not booking.amocrm_lead_id:
        return None
    now = now or clock.now().replace(tzinfo=None)
    target = target_stage(booking, now)
    if target == booking.amocrm_stage:
        return None
    try:
        if not amocrm_client.move_lead_to_stage(booking.amocrm_lead_id, target):
            return None
    except (amocrm_client.AmoCrmNotConfigured, amocrm_client.AmoCrmBlocked):
        return None
    except Exception:  # noqa: BLE001 — этап не важнее самой записи
        log.warning("этап сделки не обновлён: booking=%s → «%s»", booking.id, target, exc_info=True)
        return None
    booking.amocrm_stage = target
    db.commit()
    log.info("amoCRM: %s → этап «%s» (lead %s)", booking.id, target, booking.amocrm_lead_id)
    return target


def _bookings_in_window(db: Session, now: datetime) -> list[Booking]:
    return list(db.execute(
        select(Booking).where(
            Booking.starts_at >= now - timedelta(days=PAST_WINDOW_DAYS),
            Booking.starts_at <= now + timedelta(days=FUTURE_WINDOW_DAYS),
        )
    ).scalars().all())


def run_once(db: Session, now: datetime | None = None) -> dict:
    """Проход по живым записям: добрать пропущенные сделки и подвинуть этапы.

    Это и есть гарантия «без пропусков»: если в момент записи amoCRM был
    недоступен или токен протух, сделка появится на следующем проходе.
    """
    if not amocrm_client.settings.amocrm_ready or amocrm_client.blocked():
        return {"created": 0, "moved": 0}
    now = now or clock.now().replace(tzinfo=None)
    from . import amocrm_metrics, sync  # локальный импорт: они зовут этот модуль

    created = 0
    moved = 0
    for booking in _bookings_in_window(db, now):
        if amocrm_client.blocked():
            # Подписка не оплачена: прерываем проход целиком, а не перебираем
            # все записи по одной с тем же результатом.
            break
        if not booking.amocrm_lead_id:
            if sync.sync_booking_to_amocrm(db, booking):
                created += 1
            continue
        if sync_stage(db, booking, now):
            moved += 1
    metrics = amocrm_metrics.run_daily(db, now)
    if created or moved:
        log.info("amoCRM проход: сделок добрано %d, этапов обновлено %d", created, moved)
    return {"created": created, "moved": moved, "metrics": metrics}
