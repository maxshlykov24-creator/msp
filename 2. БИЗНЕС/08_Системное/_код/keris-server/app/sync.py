"""Двусторонний sync с YCLIENTS + релей в amoCRM.

- push_booking_to_yclients: вызывается сразу после create/reschedule/cancel у нас.
- handle_yclients_webhook: приёмник изменений, сделанных через виджет карт.
- Идемпотентность — таблица sync_log (unique direction+event_id), см. models.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (
    amocrm_client,
    amocrm_metrics,
    amocrm_stages,
    clock,
    notify_admins,
    notify_karina,
    telegram_bind,
    yclients_client,
)
from .config import settings

try:
    from . import max_bind
except ImportError:
    max_bind = None  # MAX-связка может быть не задеплоена на этом хосте
from .booking_logic import (
    has_overlap,
    next_booking_id,
    normalize_phone,
    refund_subscription_visit,
    service_duration,
)
from .models import (
    Addon,
    Booking,
    BookingSource,
    BookingStatus,
    Master,
    MasterShift,
    PetType,
    Service,
    SyncLog,
    SyncLogDirection,
)

_REFRESH_TTL_SEC = 5 * 60
_last_refresh = 0.0
_refresh_lock = threading.Lock()

_CYR_SLUG = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_DUMMY_STAFF = {"сотрудник 1", "сотрудник"}
_ADMIN_MARKERS = ("администратор", "админ")

log = logging.getLogger("keris.sync")

# Статусы, при которых слот у нас свободен (busy_intervals их не учитывает),
# а в журнале YCLIENTS запись помечена «Не пришел».
CLOSED_STATUSES = (BookingStatus.cancelled, BookingStatus.no_show)

# «Не пришел» в журнале — это и неявка, и способ отмены (регламент администратора):
# в обоих случаях слот должен освободиться у нас, поэтому статус no_show.
ATTENDANCE_STATUS = {
    yclients_client.ATTENDANCE_NO_SHOW: BookingStatus.no_show,
    yclients_client.ATTENDANCE_WAITING: BookingStatus.confirmed,
    yclients_client.ATTENDANCE_CAME: BookingStatus.completed,
    yclients_client.ATTENDANCE_CONFIRMED: BookingStatus.confirmed,
}


def attendance_status(data: dict) -> BookingStatus | None:
    """Статус визита из журнала YCLIENTS. None — поля нет или значение незнакомое."""
    raw = data.get("attendance")
    if raw is None:
        return None
    try:
        return ATTENDANCE_STATUS.get(int(raw))
    except (TypeError, ValueError):
        return None


def service_yclients_id(service: Service, size: str) -> int | None:
    """С 2026-08-07 у каждого размера — своя услуга YCLIENTS (без price_min/max диапазона)."""
    return (service.yclients_service_ids or {}).get(size)


def addon_yclients_id(addon: Addon, size: str) -> int | None:
    ids = addon.yclients_service_ids or {}
    if "flat" in ids:
        return ids["flat"]
    return ids.get(size)


def selected_addon_yclients_ids(db: Session, booking: Booking) -> list[int]:
    """В журнал YCLIENTS уходят только допы, которые клиент отметил сам.

    `free_addon_ids` (welcome/акции/абонемент) — для уведомлений. Их нельзя
    дописывать в `record.services[]`: YCLIENTS ставит прайс каталога, и в
    журнале появляется платная строка, которой клиент не выбирал.
    """
    ids: list[int] = []
    for aid in booking.addon_ids or []:
        addon = db.get(Addon, aid)
        if addon is None:
            continue
        yc_id = addon_yclients_id(addon, booking.pet_size)
        if yc_id:
            ids.append(yc_id)
    return ids


def _already_processed(db: Session, direction: SyncLogDirection, event_id: str) -> bool:
    existing = db.query(SyncLog).filter_by(direction=direction, event_id=event_id).one_or_none()
    if existing is None:
        return False
    # failed/retry не считаем обработанными — иначе YCLIENTS отдал 200, вебхук
    # не ретраится платформой, а повторный прогон у нас тоже отбрасывался.
    return existing.status not in ("failed", "retry")


def _log_sync(db: Session, direction: SyncLogDirection, event_id: str, booking_id: str | None,
              status: str, payload: dict) -> None:
    row = db.query(SyncLog).filter_by(direction=direction, event_id=event_id).one_or_none()
    if row is None:
        row = SyncLog(direction=direction, event_id=event_id, booking_id=booking_id, status=status,
                       attempts=1, payload=payload)
        db.add(row)
    else:
        row.status = status
        row.attempts += 1
        row.payload = payload
        if booking_id:
            row.booking_id = booking_id
    db.commit()


def pull_staff_shifts(db: Session, days: int = 60) -> dict:
    """Забирает график мастеров из YCLIENTS в `master_shifts` на `days` дней вперёд.

    Смену ведёт администратор в журнале, поэтому рабочие часы приходят оттуда, а не
    из нашей рамки `Master.work_start/work_end`. День, которого нет в ответе YCLIENTS,
    сохраняется пустой сменой — значит мастер не работает и слотов в нём быть не должно.
    """
    masters = [m for m in db.execute(select(Master)).scalars().all() if m.yclients_staff_id]
    if not masters:
        return {"masters": 0, "days": 0, "working": 0}

    start = clock.now().date()
    end = start + timedelta(days=days)
    by_staff = {int(m.yclients_staff_id): m for m in masters}
    try:
        rows = yclients_client.get_staff_schedule(
            list(by_staff), start.isoformat(), end.isoformat(),
        )
    except yclients_client.YClientsError:
        log.warning("график мастеров из YCLIENTS не получен — прежние смены оставлены", exc_info=True)
        return {"masters": len(masters), "days": 0, "working": 0, "error": True}

    # (master_id, дата) -> смена. Несколько слотов в дне сводим к внешним границам:
    # запись у нас непрерывная, и перерыв внутри смены YCLIENTS всё равно виден в busy.
    fresh: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        master = by_staff.get(int(row.get("staff_id") or 0))
        date_iso = str(row.get("date") or "")[:10]
        slots = row.get("slots") or []
        if master is None or not date_iso or not slots:
            continue
        starts = sorted(str(s.get("from") or "")[:5] for s in slots if s.get("from"))
        ends = sorted(str(s.get("to") or "")[:5] for s in slots if s.get("to"))
        if not starts or not ends:
            continue
        fresh[(master.id, date_iso)] = (starts[0], ends[-1])

    existing = {
        (r.master_id, r.date_iso): r
        for r in db.execute(select(MasterShift)).scalars().all()
    }
    now = clock.now()
    working = 0
    for master in masters:
        for offset in range(days + 1):
            date_iso = (start + timedelta(days=offset)).isoformat()
            begins, finishes = fresh.get((master.id, date_iso), ("", ""))
            if begins:
                working += 1
            row = existing.get((master.id, date_iso))
            if row is None:
                db.add(MasterShift(
                    master_id=master.id, date_iso=date_iso,
                    starts_at=begins, ends_at=finishes, synced_at=now,
                ))
            else:
                row.starts_at, row.ends_at, row.synced_at = begins, finishes, now
    # Прошедшие дни не нужны: они только растят таблицу и никогда не читаются.
    for (master_id, date_iso), row in existing.items():
        if date_iso < start.isoformat():
            db.delete(row)
    db.commit()
    log.info("график из YCLIENTS: мастеров %d, дней %d, рабочих смен %d",
             len(masters), days + 1, working)
    return {"masters": len(masters), "days": days + 1, "working": working}


def _truthy(value: object) -> bool:
    return value in (1, True, "1")


def _is_salon_master(person: dict) -> bool:
    """Грумер из журнала, не админ и не заготовка филиала."""
    if _truthy(person.get("fired")) or _truthy(person.get("is_fired")):
        return False
    if _truthy(person.get("hidden")) or _truthy(person.get("is_deleted")):
        return False
    name = (person.get("name") or "").strip().lower()
    if name in _DUMMY_STAFF:
        return False
    spec = (person.get("specialization") or "").lower()
    if any(marker in spec for marker in _ADMIN_MARKERS):
        return False
    return True


def _slug_master_id(name: str, staff_id: int) -> str:
    raw = "".join(_CYR_SLUG.get(ch, ch) for ch in name.lower())
    slug = "".join(ch for ch in raw if ch.isalnum())[:32]
    return slug or f"yc{staff_id}"


def _find_or_create_master(db: Session, person: dict) -> Master:
    staff_id = int(person["id"])
    name = (person.get("name") or "").strip() or f"Мастер {staff_id}"
    row = db.execute(select(Master).where(Master.yclients_staff_id == staff_id)).scalars().first()
    if row is None:
        by_name = db.execute(select(Master).where(Master.name == name)).scalars().first()
        if by_name is not None and by_name.yclients_staff_id in (None, staff_id):
            row = by_name
        else:
            slug = _slug_master_id(name, staff_id)
            if slug == "any" or (db.get(Master, slug) is not None and db.get(Master, slug).yclients_staff_id not in (None, staff_id)):
                slug = f"yc{staff_id}"
            row = db.get(Master, slug)
            if row is None:
                row = Master(id=slug, name=name, work_start="10:00", work_end="22:00")
                db.add(row)
    row.name = name
    row.yclients_staff_id = staff_id
    spec = (person.get("specialization") or "").strip()
    if spec:
        row.caption = spec
    elif not row.caption:
        row.caption = "Груминг"
    return row


def _catalog_yclients_ids(db: Session) -> set[int]:
    ids: set[int] = set()
    for service in db.execute(select(Service)).scalars().all():
        ids.update(int(v) for v in (service.yclients_service_ids or {}).values() if v)
    for addon in db.execute(select(Addon)).scalars().all():
        ids.update(int(v) for v in (addon.yclients_service_ids or {}).values() if v)
    return ids


def sync_staff_from_yclients(db: Session) -> dict:
    """Состав онлайн-записи = сотрудники филиала, а не ручной seed.

    Берём тех, кто не уволен и не админ. Active — если в YCLIENTS мастер
    онлайн-записываемый или у него есть график. Новых заводим сами.
    """
    try:
        people = yclients_client.get_staff()
    except yclients_client.YClientsError:
        log.warning("сотрудники YCLIENTS не получены", exc_info=True)
        return {"error": True, "created": 0, "activated": 0, "hidden": 0}

    seen_staff: set[int] = set()
    need_link: list[int] = []
    active_staff_ids: list[int] = []
    farthest: date | None = None
    created = 0
    activated = 0
    for person in people:
        if not person.get("id") or not _is_salon_master(person):
            continue
        raw_till = str(person.get("schedule_till") or "")[:10]
        if len(raw_till) == 10:
            try:
                till = date.fromisoformat(raw_till)
            except ValueError:
                till = None
            if till and (farthest is None or till > farthest):
                farthest = till
        staff_id = int(person["id"])
        seen_staff.add(staff_id)
        known = db.execute(select(Master).where(Master.yclients_staff_id == staff_id)).scalars().first()
        master = _find_or_create_master(db, person)
        if known is None and master in db.new:
            created += 1
        was_active = bool(master.active)
        visible = _truthy(person.get("is_bookable")) or _truthy(person.get("has_schedule"))
        master.active = visible
        if master.active and not was_active:
            activated += 1
        if master.active:
            active_staff_ids.append(staff_id)
        links = person.get("services_links") or []
        if master.active and not links:
            need_link.append(staff_id)

    hidden = 0
    for master in db.execute(select(Master)).scalars().all():
        if not master.yclients_staff_id:
            continue
        if int(master.yclients_staff_id) not in seen_staff and master.active:
            master.active = False
            hidden += 1

    linked = {"updated": 0}
    if need_link:
        # Нового мастера ещё нет в БД до commit — берём id из этого прохода, не select.
        try:
            linked = yclients_client.ensure_staff_on_services(
                active_staff_ids or need_link, _catalog_yclients_ids(db)
            )
            log.info("привязка мастеров к услугам YCLIENTS: %s", linked)
        except yclients_client.YClientsError:
            log.warning("не удалось привязать мастеров к услугам YCLIENTS", exc_info=True)

    db.commit()
    log.info("сотрудники из YCLIENTS: новых %d, включено %d, скрыто %d", created, activated, hidden)
    return {
        "created": created, "activated": activated, "hidden": hidden,
        "linked": linked, "need_link": need_link,
        "schedule_till": farthest.isoformat() if farthest else "",
    }


def refresh_from_yclients(db: Session, days: int | None = None, force: bool = False) -> dict:
    """Состав + график из YCLIENTS. Не чаще чем раз в 5 минут, кроме force."""
    global _last_refresh
    if not settings.yclients_ready:
        return {"skipped": True, "reason": "yclients_not_ready"}
    if not force and time.time() - _last_refresh < _REFRESH_TTL_SEC:
        return {"skipped": True, "age_sec": int(time.time() - _last_refresh)}
    with _refresh_lock:
        if not force and time.time() - _last_refresh < _REFRESH_TTL_SEC:
            return {"skipped": True, "age_sec": int(time.time() - _last_refresh)}
        from .seed import booking_rules
        staff = sync_staff_from_yclients(db)
        if days is None:
            till = staff.get("schedule_till")
            if till:
                try:
                    horizon = min(max((date.fromisoformat(till) - clock.now().date()).days, 1), 180)
                except ValueError:
                    horizon = int(booking_rules().get("horizon_days", 60))
            else:
                horizon = int(booking_rules().get("horizon_days", 60))
        else:
            horizon = days
        shifts = pull_staff_shifts(db, days=horizon)
        if shifts.get("error"):
            log.warning("график из YCLIENTS не доехал — прежний состав не прячем")
        elif not shifts.get("working"):
            log.warning("график из YCLIENTS пуст — состав не прячем")
        else:
            working_ids = {
                row.master_id
                for row in db.execute(select(MasterShift).where(MasterShift.starts_at != "")).scalars().all()
            }
            for master in db.execute(select(Master)).scalars().all():
                if not master.yclients_staff_id or not master.active:
                    continue
                if master.id not in working_ids:
                    master.active = False
            db.commit()
        _last_refresh = time.time()
        return {"staff": staff, "shifts": shifts}


def push_booking_to_yclients(db: Session, booking: Booking) -> None:
    """Пуш записи в YCLIENTS почти в реальном времени. Не бросает исключение
    наверх — при сбое запись у нас остаётся валидной, попытка логируется
    как retry и может быть повторена отдельным воркером/командой."""
    event_id = f"{booking.id}:{booking.updated_at.isoformat()}"
    if _already_processed(db, SyncLogDirection.push_to_yclients, event_id):
        return

    master = db.get(Master, booking.master_id)
    service = db.get(Service, booking.service_id)
    if master is None or service is None:
        _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "failed",
                   {"error": "master_or_service_missing"})
        return

    service_yc_id = service_yclients_id(service, booking.pet_size)
    if master.yclients_staff_id is None or service_yc_id is None:
        _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "retry",
                   {"error": "yclients_ids_not_mapped"})
        log.info("push skipped booking=%s: мастер/услуга (размер %s) ещё не сопоставлены с YCLIENTS",
                  booking.id, booking.pet_size)
        return

    addon_yc_ids = selected_addon_yclients_ids(db, booking)

    try:
        payload = yclients_client.booking_to_yclients_payload(
            booking, master.yclients_staff_id, service_yc_id, addon_yc_ids
        )
        if booking.status in CLOSED_STATUSES and booking.yclients_record_id:
            # Не удаляем запись из журнала: ставим «Не пришел» — так работает
            # администратор, слот освобождается, история визитов не теряется.
            yclients_client.mark_record_no_show(booking.yclients_record_id, payload)
        elif booking.status in CLOSED_STATUSES:
            _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "ok",
                       {"skipped": "no_yclients_record"})
            return
        elif booking.yclients_record_id:
            yclients_client.update_record(booking.yclients_record_id, payload)
        else:
            result = yclients_client.create_record(payload)
            record = (result.get("data") or [{}])[0] if isinstance(result.get("data"), list) else result.get("data") or {}
            record_id = record.get("id") if isinstance(record, dict) else None
            if record_id:
                booking.yclients_record_id = record_id
                db.commit()
        _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "ok", {})
    except yclients_client.YClientsNotConfigured as e:
        _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "retry", {"error": str(e)})
        log.warning("YCLIENTS не настроен, push отложен: booking=%s", booking.id)
    except Exception as e:  # noqa: BLE001 — sync не должен ронять основной поток записи
        _log_sync(db, SyncLogDirection.push_to_yclients, event_id, booking.id, "retry", {"error": str(e)})
        log.warning("push_booking_to_yclients failed booking=%s: %s", booking.id, e)


def push_confirm_to_yclients(db: Session, booking: Booking) -> None:
    """Клиент подтвердил визит кнопкой в напоминании — best-effort attendance=2
    в YCLIENTS. Не бросает исключение наверх: локально подтверждение уже сохранено
    (Booking.client_confirmed_at), YCLIENTS — это синхронизация, а не источник истины."""
    if not booking.yclients_record_id:
        return
    master = db.get(Master, booking.master_id)
    service = db.get(Service, booking.service_id)
    if master is None or service is None or master.yclients_staff_id is None:
        log.warning("push_confirm_to_yclients: мастер/услуга не сопоставлены, booking=%s", booking.id)
        return
    service_yc_id = service_yclients_id(service, booking.pet_size)
    if service_yc_id is None:
        log.warning("push_confirm_to_yclients: услуга (размер %s) не сопоставлена, booking=%s",
                     booking.pet_size, booking.id)
        return
    addon_yc_ids = selected_addon_yclients_ids(db, booking)
    try:
        payload = yclients_client.booking_to_yclients_payload(
            booking, master.yclients_staff_id, service_yc_id, addon_yc_ids
        )
        yclients_client.confirm_record(booking.yclients_record_id, payload)
    except Exception as e:  # noqa: BLE001 — подтверждение у нас уже сохранено, YCLIENTS не должен всё ронять
        log.warning("push_confirm_to_yclients failed booking=%s: %s", booking.id, e)


def _service_name(db: Session, booking: Booking) -> str:
    service = db.get(Service, booking.service_id)
    return service.name if service else booking.service_id


def _addon_names(db: Session, booking: Booking) -> str:
    """Допы визита. Подарочные помечаем — иначе в сделке видна услуга,
    за которую клиент не платил, и сумма не сходится с составом."""
    parts = []
    for aid in booking.addon_ids or []:
        addon = db.get(Addon, aid)
        if addon is not None:
            parts.append(addon.name)
    for aid in booking.free_addon_ids or []:
        addon = db.get(Addon, aid)
        if addon is not None:
            parts.append(f"{addon.name} (подарок)")
    return ", ".join(parts)


def _payment_text(booking: Booking) -> str:
    if booking.subscription_id:
        text = "По абонементу"
        if booking.visits_charged:
            text += f" (списано визитов: {booking.visits_charged:g})"
        if booking.price:
            text += f", доплата {booking.price} ₽"
        return text
    return f"{booking.price} ₽"


def _yclients_day_url(booking: Booking) -> str:
    """Ссылка на журнал за день записи: прямой ссылки на запись YCLIENTS не даёт
    (нужен record_hash, которого нет ни в вебхуке, ни в ответе на создание)."""
    company = settings.yclients_company_id
    if not company:
        return ""
    return f"https://yclients.com/timetable/{company}/{booking.starts_at.date().isoformat()}"


def _lead_fields(db: Session, booking: Booking) -> dict:
    master = db.get(Master, booking.master_id)
    return {
        "Номер записи": booking.id,
        "Дата и время визита": int(booking.starts_at.timestamp()),
        "Мастер": master.name if master else booking.master_id,
        "Услуга": _service_name(db, booking),
        "Допуслуги": _addon_names(db, booking),
        "Питомец": booking.pet_name or booking.pet_type.value,
        "Размер": booking.pet_size,
        "Источник записи": amocrm_metrics.SOURCE_TO_LEAD_FIELD.get(booking.source, ""),
        "Оплата": _payment_text(booking),
        "Журнал YCLIENTS": _yclients_day_url(booking),
        "Что уточнить": booking.admin_note,
    }


def source_title(booking: Booking) -> str:
    """Человеческое название канала записи — для примечаний в ленте сделки."""
    return amocrm_metrics.SOURCE_TO_LEAD_FIELD.get(booking.source, booking.source.value)


def _lead_name(db: Session, booking: Booking) -> str:
    pet = booking.pet_name or booking.pet_type.value
    return f"Груминг {booking.id}: {pet} — {booking.starts_at.strftime('%d.%m %H:%M')}"


def _pet_company_id(db: Session, booking: Booking, contact_id: int) -> int | None:
    """Карточка питомца. Без клички создавать нечего — с карт она приходит не
    всегда, тогда сделка остаётся только с контактом-владельцем."""
    if not (booking.pet_name or "").strip():
        return None
    if booking.amocrm_company_id:
        try:
            amocrm_client.update_pet_company(
                booking.amocrm_company_id, amocrm_metrics.pet_fields(db, booking)
            )
        except Exception:  # noqa: BLE001
            log.warning("карточка питомца %s не обновлена", booking.amocrm_company_id, exc_info=True)
        return booking.amocrm_company_id
    company_id = amocrm_client.find_or_create_pet_company(
        contact_id, booking.pet_name.strip(), amocrm_metrics.pet_fields(db, booking)
    )
    if company_id:
        booking.amocrm_company_id = company_id
        db.commit()
    return company_id


def sync_booking_to_amocrm(db: Session, booking: Booking, note: str = "",
                            extra_fields: dict | None = None) -> bool:
    """Запись → amoCRM: контакт-владелец, карточка питомца, сделка-визит, этап, метрики.

    Вызывается на каждом событии записи и повторно из `amocrm_stages.run_once`,
    поэтому идемпотентна: сделка создаётся один раз (`booking.amocrm_lead_id`),
    дальше только обновляется. Наружу не бросает — запись у нас важнее CRM.
    """
    event_id = f"{booking.id}:amocrm:{booking.updated_at.isoformat()}"
    try:
        contact_id = amocrm_client.find_or_create_contact(booking.owner_name, booking.owner_phone)
        if contact_id is None:
            _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "retry",
                       {"error": "contact_not_created"})
            return False

        company_id = _pet_company_id(db, booking, contact_id)
        fields = _lead_fields(db, booking)
        fields.update(extra_fields or {})
        created = False
        if booking.amocrm_lead_id:
            amocrm_client.update_grooming_lead(
                booking.amocrm_lead_id, price=booking.price, fields=fields,
                company_id=company_id, name=_lead_name(db, booking),
            )
        else:
            lead_id = amocrm_client.create_grooming_lead(
                contact_id=contact_id, booking_summary=_lead_name(db, booking),
                price=booking.price, status=amocrm_stages.STAGE_CREATED,
                fields=fields, company_id=company_id,
            )
            if lead_id is None:
                _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "retry",
                           {"error": "lead_not_created"})
                return False
            booking.amocrm_lead_id = lead_id
            booking.amocrm_stage = amocrm_stages.STAGE_CREATED
            db.commit()
            created = True

        if note:
            amocrm_client.add_lead_note(booking.amocrm_lead_id, note)
        amocrm_stages.sync_stage(db, booking)
        amocrm_metrics.push_client_metrics(db, contact_id, booking.owner_phone)
        _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "ok",
                   {"lead_id": booking.amocrm_lead_id, "company_id": company_id,
                    "created": created})
        return True
    except amocrm_client.AmoCrmNotConfigured as e:
        _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "retry", {"error": str(e)})
        log.warning("amoCRM не настроен, push отложен: booking=%s", booking.id)
        return False
    except amocrm_client.AmoCrmBlocked as e:
        # Причина одна на весь аккаунт и уже в логе — здесь только пометка в
        # sync_log, чтобы запись добралась в CRM после оплаты подписки.
        db.rollback()
        _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "retry",
                   {"error": str(e), "reason": "amocrm_blocked"})
        return False
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _log_sync(db, SyncLogDirection.push_to_amocrm, event_id, booking.id, "retry", {"error": str(e)})
        log.warning("sync_booking_to_amocrm failed booking=%s: %s", booking.id, e, exc_info=True)
        return False


def webhook_event_id(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _maps_admin_note(pet_info: dict, in_bot: bool = False) -> str:
    """Чего не хватает менеджеру по записи с карт/журнала. Кличку и породу
    просим уточнить, только если их не прислали доп. полями записи YCLIENTS —
    согласия ПДн/фото доп. полями не покрываются, спрашиваем всегда.

    Клиента вне бота отдельно отмечаем со ссылками: без бота он не получит ни
    напоминаний, ни фото-отчёта, ни кабинета — это тот самый разрыв на записях с
    карт, и закрывает его администратор одним сообщением.
    """
    missing = []
    if not pet_info.get("pet_name"):
        missing.append("кличку")
    if not pet_info.get("pet_breed"):
        missing.append("породу")
    missing.append("согласия ПДн/фото")
    note = f"Запись из YCLIENTS (карты): уточнить {', '.join(missing)} у клиента"
    if not in_bot:
        note += (
            f". Клиента нет в боте — дать ссылку и попросить «Поделиться номером»: "
            f"{settings.telegram_bot_url} или {settings.max_bot_url}"
        )
    return note


def _record_datetime(data: dict) -> datetime | None:
    """`datetime` приходит с offset (`...+03:00`), `date` — уже местным naive."""
    raw = data.get("datetime") or data.get("date")
    if not raw:
        return None
    try:
        return clock.to_salon_naive(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def _master_by_staff(db: Session, staff_id: int | None) -> Master | None:
    if not staff_id:
        return None
    return db.execute(select(Master).where(Master.yclients_staff_id == staff_id)).scalars().first()


def _service_by_yclients_id(db: Session, yc_service_id: int | None) -> tuple[Service, str] | None:
    """Каждый размер — своя услуга YCLIENTS (см. yclients_setup --sync-catalog),
    поэтому по id услуги узнаём не только услугу, но и точный размер питомца."""
    if not yc_service_id:
        return None
    for service in db.execute(
        select(Service).where(Service.yclients_service_ids.isnot(None))
    ).scalars().all():
        for size, yid in (service.yclients_service_ids or {}).items():
            if int(yid) == int(yc_service_id):
                return service, size
    return None


def _addon_by_yclients_id(db: Session, yc_service_id: int | None) -> tuple[Addon, str] | None:
    if not yc_service_id:
        return None
    for addon in db.execute(
        select(Addon).where(Addon.yclients_service_ids.isnot(None))
    ).scalars().all():
        for size, yid in (addon.yclients_service_ids or {}).items():
            if int(yid) == int(yc_service_id):
                return addon, size
    return None


def _resolve_catalog_from_record(db: Session, data: dict) -> tuple[Service | None, str | None, list[str]]:
    """Первая позиция из record.services, которая есть в наших Service —
    основная услуга (размер узнаём по конкретной услуге YCLIENTS, а не гадаем);
    остальные, замапленные на Addon, — допы."""
    service: Service | None = None
    size: str | None = None
    addon_ids: list[str] = []
    for item in data.get("services") or []:
        if not isinstance(item, dict):
            continue
        yc_id = item.get("id")
        if service is None:
            found = _service_by_yclients_id(db, yc_id)
            if found is not None:
                service, size = found
                continue
        found_addon = _addon_by_yclients_id(db, yc_id)
        if found_addon is not None:
            addon, _addon_size = found_addon
            if addon.id not in addon_ids:
                addon_ids.append(addon.id)
    return service, size, addon_ids


def _default_size(service: Service) -> str:
    return "M" if "M" in (service.prices or {}) else next(iter(service.prices or {"M": 0}))


def _yclients_cost(data: dict) -> int:
    total = 0
    for item in data.get("services") or []:
        if isinstance(item, dict):
            total += int(item.get("cost_to_pay") or item.get("cost") or 0)
    return total


def handle_yclients_webhook(db: Session, payload: dict) -> dict:
    """Запись создана/изменена/отменена в YCLIENTS (карты, журнал менеджера) ->
    приводим нашу БД к тому же состоянию. Идемпотентно по хэшу payload.

    Обратно в YCLIENTS ничего не пушим — иначе получился бы цикл
    webhook → push → webhook. Наш собственный push тоже возвращается сюда эхом,
    но находится по `yclients_record_id` и отрабатывает как `unchanged`.
    """
    event_id = webhook_event_id(payload)
    if _already_processed(db, SyncLogDirection.webhook_from_yclients, event_id):
        return {"status": "duplicate_ignored"}

    log.info("YCLIENTS webhook получен: %s", str(payload)[:300])
    resource = str(payload.get("resource") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if resource != "record":
        # client/goods_operations и прочие ресурсы держим только в журнале.
        _log_sync(db, SyncLogDirection.webhook_from_yclients, event_id, None, "ok", payload)
        return {"status": "ignored", "resource": resource}

    try:
        result = _apply_yclients_record(db, data, str(payload.get("status") or ""))
    except Exception as e:  # noqa: BLE001 — вебхук не должен отдавать 500 платформе
        db.rollback()
        _log_sync(db, SyncLogDirection.webhook_from_yclients, event_id, None, "failed",
                   {**payload, "error": str(e)})
        log.warning("handle_yclients_webhook failed record=%s: %s", data.get("id"), e, exc_info=True)
        return {"status": "failed", "error": str(e)}

    sync_status = result.pop("sync_status", "ok")
    _log_sync(db, SyncLogDirection.webhook_from_yclients, event_id, result.get("booking_id"),
               sync_status, payload)
    return result


def _hydrate_record_from_api(data: dict) -> dict:
    """Вебхук журнала часто приходит в момент создания слота, ещё без услуг.
    Без GET запись не импортируется, клиент не получает «спасибо» в бота."""
    if data.get("services"):
        return data
    record_id = data.get("id")
    if not isinstance(record_id, int):
        return data
    try:
        raw = yclients_client.get_record(record_id)
    except yclients_client.YClientsError as e:
        log.warning("не удалось дочитать запись YCLIENTS %s: %s", record_id, e)
        return data
    full = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(full, dict) or not full.get("services"):
        return data
    merged = dict(data)
    for key in ("services", "seance_length", "client", "staff_id", "date", "datetime", "comment", "custom_fields"):
        val = full.get(key)
        if val in (None, [], ""):
            continue
        if key in ("services", "seance_length") or not merged.get(key):
            merged[key] = val
    log.info("YCLIENTS record %s: услуги дочитаны из API (%s шт.)",
             record_id, len(merged.get("services") or []))
    return merged


def _apply_yclients_record(db: Session, data: dict, status: str) -> dict:
    record_id = data.get("id")
    if not isinstance(record_id, int):
        return {"status": "skipped", "reason": "no_record_id", "sync_status": "failed"}

    booking = db.execute(
        select(Booking).where(Booking.yclients_record_id == record_id)
    ).scalars().first()

    if status == "delete" or data.get("deleted"):
        if booking is None:
            return {"status": "skipped", "reason": "unknown_record"}
        if booking.status != BookingStatus.cancelled:
            booking.status = BookingStatus.cancelled
            # Отмену сделал салон — визит абонемента клиенту возвращаем.
            refund_subscription_visit(db, booking)
            db.commit()
            cancelled_text = notify_karina.booking_cancelled_text(booking)
            notify_karina.notify(cancelled_text)
            notify_admins.notify(cancelled_text)
            sync_booking_to_amocrm(db, booking, note="Запись удалена в журнале YCLIENTS")
        return {"status": "cancelled", "booking_id": booking.id}

    data = _hydrate_record_from_api(data)
    starts_at = _record_datetime(data)
    if starts_at is None:
        return {"status": "skipped", "reason": "no_datetime", "sync_status": "failed"}
    master = _master_by_staff(db, data.get("staff_id"))
    if master is None:
        log.warning("YCLIENTS record %s: staff_id=%s не сопоставлен", record_id, data.get("staff_id"))
        return {"status": "skipped", "reason": "staff_not_mapped", "sync_status": "failed"}
    duration_min = int(data.get("seance_length") or 0) // 60

    if booking is not None:
        return _update_from_yclients(db, booking, master, starts_at, duration_min, data)
    return _create_from_yclients(db, data, record_id, master, starts_at, duration_min)


def _target_status(booking: Booking, from_attendance: BookingStatus | None, moved: bool) -> BookingStatus | None:
    """Новый статус записи по событию журнала. None — статус не меняем.

    Статус визита в YCLIENTS главнее, если он пришёл в событии. Исключение —
    «Ожидание»/«Подтвердил» по уже отменённой записи: сама по себе такая отметка
    отмену не снимает, иначе слот снова стал бы занятым. Возврат в работу
    считается только вместе с переносом записи в журнале.
    """
    if from_attendance is None:
        return BookingStatus.confirmed if (moved and booking.status in CLOSED_STATUSES) else None
    if (booking.status in CLOSED_STATUSES and from_attendance == BookingStatus.confirmed
            and not moved):
        return None
    return from_attendance if from_attendance != booking.status else None


def _pet_info_from_record(data: dict) -> dict[str, str | float]:
    """Кличка/порода/вес/дата рождения из доп. полей записи YCLIENTS (коды —
    `settings.yclients_cf_pet_*`). Поле без настроенного кода просто не читаем."""
    fields = yclients_client.extract_custom_fields(data)
    if not fields:
        return {}
    result: dict[str, str | float] = {}

    def _raw(code: str) -> str | None:
        value = fields.get(code)
        if isinstance(value, list):
            value = value[0] if value else None
        if value in (None, ""):
            return None
        return str(value).strip() or None

    if settings.yclients_cf_pet_name:
        val = _raw(settings.yclients_cf_pet_name)
        if val:
            result["pet_name"] = val
    if settings.yclients_cf_pet_breed:
        val = _raw(settings.yclients_cf_pet_breed)
        if val:
            result["pet_breed"] = val
    if settings.yclients_cf_pet_weight:
        val = _raw(settings.yclients_cf_pet_weight)
        if val:
            try:
                result["pet_weight_kg"] = float(val.replace(",", "."))
            except ValueError:
                log.warning("YCLIENTS custom_fields: вес питомца не число: %r", val)
    if settings.yclients_cf_pet_birth_date:
        val = _raw(settings.yclients_cf_pet_birth_date)
        if val:
            result["pet_birth_date"] = val
    return result


def _apply_pet_info_from_record(booking: Booking, data: dict) -> bool:
    """Дописываем в уже созданный слот кличку/породу/вес/дату рождения, если
    администратор заполнил доп. поля записи в YCLIENTS позже создания слота."""
    changed = False
    for attr, value in _pet_info_from_record(data).items():
        if getattr(booking, attr, None) != value:
            setattr(booking, attr, value)
            changed = True
    return changed


def _apply_catalog_from_record(db: Session, booking: Booking, data: dict) -> bool:
    """Если в журнале появились услуги — дописываем их в уже созданный слот."""
    service, size, addon_ids = _resolve_catalog_from_record(db, data)
    if service is None:
        return False
    size = size or _default_size(service)
    price = _yclients_cost(data) or int((service.prices or {}).get(size) or 0)
    changed = (
        booking.service_id != service.id
        or booking.pet_size != size
        or list(booking.addon_ids or []) != list(addon_ids)
        or booking.price != price
        or booking.pet_type != service.pet_type
    )
    if not changed:
        return False
    booking.service_id = service.id
    booking.pet_size = size
    booking.addon_ids = addon_ids
    booking.price = price
    booking.pet_type = service.pet_type
    return True


def _update_from_yclients(db: Session, booking: Booking, master: Master,
                           starts_at: datetime, duration_min: int, data: dict) -> dict:
    if duration_min <= 0:
        duration_min = int((booking.ends_at - booking.starts_at).total_seconds() // 60)
    ends_at = starts_at + timedelta(minutes=duration_min)
    moved = (booking.starts_at != starts_at or booking.ends_at != ends_at
             or booking.master_id != master.id)
    target = _target_status(booking, attendance_status(data), moved)
    catalog_changed = _apply_catalog_from_record(db, booking, data)
    pet_changed = _apply_pet_info_from_record(booking, data)
    if not moved and target is None and not catalog_changed and not pet_changed:
        return {"status": "unchanged", "booking_id": booking.id}

    if moved:
        booking.starts_at = starts_at
        booking.ends_at = ends_at
        booking.master_id = master.id
    if target is not None:
        booking.status = target
    db.commit()

    if target == BookingStatus.no_show:
        # Слот у нас освободился — сообщаем и Карине, и в админский чат.
        no_show_text = notify_karina.booking_no_show_text(booking)
        notify_karina.notify(no_show_text)
        notify_admins.notify(no_show_text)
        sync_booking_to_amocrm(db, booking, note="Отметка в журнале YCLIENTS: клиент не пришёл")
        return {"status": "no_show", "booking_id": booking.id}
    if (catalog_changed or pet_changed) and not moved and target is None:
        sync_booking_to_amocrm(db, booking)
        return {"status": "catalog_updated", "booking_id": booking.id}
    if not moved:
        sync_booking_to_amocrm(db, booking, note=_status_note(booking))
        return {"status": "status_updated", "booking_id": booking.id,
                "booking_status": booking.status.value}

    rescheduled_text = notify_karina.booking_rescheduled_text(booking)
    notify_karina.notify(rescheduled_text)
    notify_admins.notify(rescheduled_text)
    sync_booking_to_amocrm(
        db, booking,
        note=f"Перенос в журнале YCLIENTS: визит {booking.starts_at:%d.%m %H:%M}",
    )
    return {"status": "updated", "booking_id": booking.id}


def _status_note(booking: Booking) -> str:
    if booking.status == BookingStatus.completed:
        return "Отметка в журнале YCLIENTS: клиент пришёл"
    if booking.status == BookingStatus.confirmed:
        return "Отметка в журнале YCLIENTS: визит подтверждён"
    return ""


def _create_from_yclients(db: Session, data: dict, record_id: int, master: Master,
                           starts_at: datetime, duration_min: int) -> dict:
    service, size, addon_ids = _resolve_catalog_from_record(db, data)
    display_title: str | None = None
    catalog_guess = False
    if service is None:
        # Клиент с карт мог взять только доп (отдельная позиция в виджете).
        only_addon_match = None
        for item in data.get("services") or []:
            if isinstance(item, dict):
                only_addon_match = _addon_by_yclients_id(db, item.get("id"))
                if only_addon_match is not None:
                    break
        if only_addon_match is not None:
            only_addon, addon_size = only_addon_match
            # Фолбэк: базовая гигиена того же типа питомца (тот же размер, если применим) + выбранный доп.
            fallback_id = "dog_hygiene" if only_addon.pet_type == PetType.dog else "cat_hygiene"
            service = db.get(Service, fallback_id)
            if service is None:
                return {"status": "skipped", "reason": "service_not_mapped", "sync_status": "failed"}
            size = addon_size if addon_size in (service.prices or {}) else _default_size(service)
            addon_ids = [only_addon.id]
            note_extra = f". В YCLIENTS выбрана только допуслуга «{only_addon.name}» — базовая услуга поставлена как гигиена"
        else:
            # Журнал часто шлёт create до сохранения услуг. Без слота сотрудники
            # не получают уведомление, а YCLIENTS на 200 вебхук не ретраит.
            service = db.get(Service, "dog_hygiene")
            if service is None:
                return {"status": "skipped", "reason": "service_not_mapped", "sync_status": "failed"}
            size = _default_size(service)
            addon_ids = []
            catalog_guess = True
            titles = [
                str(item.get("title") or "").strip()
                for item in (data.get("services") or [])
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]
            if titles:
                shown = ", ".join(titles)
                note_extra = f". Услуги из YCLIENTS не сопоставлены: {shown}"
                display_title = shown
            else:
                note_extra = ". Услуга в вебхуке YCLIENTS не пришла — состав визита уточнить в журнале"
                display_title = "запись в журнале"
            log.warning(
                "YCLIENTS record %s: услуг нет или не сопоставлены — создаём слот с фолбэком",
                record_id,
            )
    else:
        size = size or _default_size(service)
        note_extra = ""

    if duration_min <= 0:
        duration_min = service_duration(service, size)
    ends_at = starts_at + timedelta(minutes=duration_min)
    client = data.get("client") if isinstance(data.get("client"), dict) else {}
    price = _yclients_cost(data)
    if price == 0 and not catalog_guess:
        price = int((service.prices or {}).get(size) or 0)

    pet_info = _pet_info_from_record(data)
    phone = normalize_phone(client.get("phone") or "")
    chat_id = telegram_bind.resolve_chat_id(db, phone)
    max_user_id = max_bind.resolve_user_id(db, phone) if max_bind is not None else None
    note = (_maps_admin_note(pet_info, in_bot=bool(chat_id or max_user_id)) + note_extra)[:500]
    if has_overlap(db, master.id, starts_at, ends_at):
        # Отказать нельзя — запись уже существует в YCLIENTS; помечаем для менеджера.
        note = (note + ". ⚠️ Слот пересекается с существующей записью")[:500]

    booking = Booking(
        id=next_booking_id(db),
        owner_name=(client.get("display_name") or client.get("name") or "Клиент с карт"),
        owner_phone=phone,
        pet_name=pet_info.get("pet_name", ""),
        pet_breed=pet_info.get("pet_breed", ""),
        pet_weight_kg=pet_info.get("pet_weight_kg"),
        pet_birth_date=pet_info.get("pet_birth_date", ""),
        pet_type=service.pet_type,
        pet_size=size,
        service_id=service.id,
        addon_ids=addon_ids,
        free_addon_ids=[],
        applied_promos=[],
        master_id=master.id,
        starts_at=starts_at,
        ends_at=ends_at,
        price=price,
        status=BookingStatus.confirmed,
        source=BookingSource.yclients_maps,
        comment=str(data.get("comment") or ""),
        admin_note=note,
        yclients_record_id=record_id,
        telegram_chat_id=chat_id,
        max_user_id=max_user_id,
    )
    db.add(booking)
    db.commit()

    sync_booking_to_amocrm(
        db, booking,
        note="Запись создана в YCLIENTS (Яндекс.Карты / 2ГИС или журнал администратора)",
    )
    service_title = display_title or service.name
    created_text = (
        notify_karina.booking_created_text(booking, service_title, master_name=master.name)
        + "\n\n📍 Источник: YCLIENTS (Яндекс/2ГИС) — данные питомца не заполнены"
    )
    if not (chat_id or max_user_id):
        created_text += (
            "\n\n📲 Клиента нет в боте: напоминания, фото-отчёт и кабинет он получит"
            " после «Поделиться номером».\n"
            f"Telegram: {settings.telegram_bot_url}\nMAX: {settings.max_bot_url}"
        )
    notify_karina.notify(created_text)
    notify_admins.notify(created_text)
    from . import reminders
    reminders.send_booking_created(booking, service_title)
    return {"status": "created", "booking_id": booking.id}
