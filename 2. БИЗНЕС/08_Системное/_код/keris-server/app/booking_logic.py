"""Расчёт цены/длительности записи и доступности слотов.

Кто работает и в какие часы — копия графика YCLIENTS (`MasterShift`).
Цена, длительность услуги и занятость наших записей считаются здесь.
Гонка слотов закрывается повторной проверкой в create_booking (см. main.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock
from .models import (
    Addon,
    Booking,
    BookingStatus,
    Master,
    MasterDayOff,
    MasterShift,
    SalonClosure,
    Service,
    Subscription,
    SubscriptionPlan,
)
from .seed import (
    booking_rules,
    min_balance_visits,
    promos,
    slot_step_min,
    subscription_coefficients,
    subscription_included_addons,
)


# Кусок, похожий на телефон: +/цифра, дальше цифры и разделители, минимум 10 знаков.
_PHONE_CANDIDATE_RE = re.compile(r"[+\d][\d()\-.\s]{8,}\d")


def normalize_phone(raw: str) -> str:
    """+7 999 368-98-71, 89993689871, 79993689871 → +79993689871.

    Если набор цифр не складывается в российский 10-значный номер, возвращает
    исходную строку (как раньше) — вызывающий код сам решает, это ошибка или нет.
    """
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    elif len(digits) == 10:
        pass
    elif len(digits) > 10:
        digits = digits[-10:]
    else:
        return str(raw or "")
    return "+7" + digits if len(digits) == 10 else str(raw or "")


def is_canonical_phone(value: str) -> bool:
    return bool(value) and value.startswith("+7") and len(value) == 12 and value[1:].isdigit()


def extract_phone_from_text(text: str) -> str:
    """Достаёт российский номер из произвольного ввода и приводит к +7XXXXXXXXXX.

    Понимает +7 / 8 / 7, скобки, пробелы, дефисы, точки и текст вокруг
    («мой номер 8 (999) 123-45-67»). Пустая строка — если номера нет.
    Один и тот же человек в любом из этих видов даёт один ключ, без дублей.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    for cand in _PHONE_CANDIDATE_RE.findall(raw):
        n = normalize_phone(cand)
        if is_canonical_phone(n):
            return n
    n = normalize_phone(raw)
    return n if is_canonical_phone(n) else ""


class BookingError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def service_and_addons(db: Session, service_id: str, addon_ids: list[str]) -> tuple[Service, list[Addon]]:
    service = db.get(Service, service_id)
    if service is None or not service.active:
        raise BookingError("service_not_found", f"Услуга {service_id} не найдена")
    addons = []
    for aid in addon_ids:
        addon = db.get(Addon, aid)
        if addon is None or not addon.active:
            raise BookingError("addon_not_found", f"Допуслуга {aid} не найдена")
        if addon.pet_type != service.pet_type:
            raise BookingError("addon_mismatch", f"Допуслуга {aid} не подходит питомцу")
        addons.append(addon)
    return service, addons


def addon_price(addon: Addon, size: str) -> int:
    """Цена допа: сетка по размеру (окрашивание), иначе фиксированная."""
    if addon.prices and size in addon.prices:
        return int(addon.prices[size])
    return int(addon.price)


def addon_duration(addon: Addon, size: str) -> int:
    if addon.durations and size in addon.durations:
        return int(addon.durations[size])
    return int(addon.duration_min)


def service_duration(service: Service, size: str) -> int:
    if service.durations and size in service.durations:
        return int(service.durations[size])
    return int(service.duration_min)


def compute_price_and_duration(
    service: Service,
    addons: list[Addon],
    size: str,
    free_addon_ids: tuple[str, ...] | list[str] = (),
) -> tuple[int, int]:
    """Итог по записи. Бесплатные допы (промо/абонемент) не добавляют к цене.

    Длительность считается по допам, которые клиент выбрал сам: подарочные
    (когти/уши/маска) мастер выполняет внутри процесса комплекса, иначе слот
    в календаре расходился бы с тем, что клиент видел при записи.
    """
    if size not in service.prices:
        raise BookingError("size_not_found", f"Размер {size} не найден для услуги {service.id}")
    price = int(service.prices[size])
    duration = service_duration(service, size)
    free = set(free_addon_ids)
    for addon in addons:
        if addon.id not in free:
            price += addon_price(addon, size)
        duration += addon_duration(addon, size)
    return price, duration


MAX_HORIZON_DAYS = 180


def booking_horizon_days(db: Session | None = None, today: date | None = None) -> int:
    """Горизонт записи = последняя рабочая смена в копии графика YCLIENTS.

    Нет смен (тесты, первый запуск) — запасной `horizon_days` из seed.
    Дальше 180 дней не открываем: иначе лента дат и таблица смен растут без нужды.
    """
    fallback = int(booking_rules().get("horizon_days", 60))
    today = today or clock.today()
    if db is None:
        return fallback
    last = db.execute(
        select(func.max(MasterShift.date_iso)).where(MasterShift.starts_at != "")
    ).scalar()
    if not last:
        return fallback
    try:
        last_day = date.fromisoformat(str(last)[:10])
    except ValueError:
        return fallback
    return min(max((last_day - today).days, 0), MAX_HORIZON_DAYS)


def check_horizon(
    day: date,
    rules: dict | None = None,
    today: date | None = None,
    db: Session | None = None,
) -> None:
    """Запись открыта на все дни, где в YCLIENTS стоит смена (плюс запасной горизонт)."""
    rules = rules or booking_rules()
    today = today or clock.today()
    if day < today:
        raise BookingError("date_in_past", "Эта дата уже прошла")
    horizon = booking_horizon_days(db, today) if db is not None else int(rules.get("horizon_days", 60))
    if (day - today).days > horizon:
        raise BookingError(
            "horizon_exceeded",
            f"Запись открыта на {horizon} дней вперёд — выберите дату раньше",
        )


def _time_to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _min_to_time(total_min: int) -> str:
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def available_masters(db: Session, exclude_ids: tuple[str, ...] = ()) -> list[Master]:
    stmt = select(Master).where(Master.active.is_(True))
    return [m for m in db.execute(stmt).scalars().all() if m.id not in exclude_ids]


def shift_day_off(master: Master, day: date) -> bool:
    """Выходной по сменному графику (4/3 и т.п.). Без графика — мастер работает всегда."""
    if not master.shift_on or not master.shift_off or not master.shift_start:
        return False
    try:
        anchor = date.fromisoformat(master.shift_start)
    except ValueError:
        return False
    cycle = master.shift_on + master.shift_off
    return (day - anchor).days % cycle >= master.shift_on


def master_is_off(db: Session, master_id: str, day: date) -> bool:
    day_iso = day.isoformat()
    if db.get(SalonClosure, day_iso) is not None:
        return True
    master = db.get(Master, master_id)
    if master is not None and shift_day_off(master, day):
        return True
    stmt = select(MasterDayOff).where(MasterDayOff.master_id == master_id, MasterDayOff.date_iso == day_iso)
    return db.execute(stmt).scalar_one_or_none() is not None


def busy_intervals(db: Session, master_id: str, day: date) -> list[tuple[datetime, datetime]]:
    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)
    stmt = select(Booking).where(
        Booking.master_id == master_id,
        Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
        Booking.starts_at < day_end,
        Booking.ends_at > day_start,
    )
    return [(b.starts_at, b.ends_at) for b in db.execute(stmt).scalars().all()]


def slots_for_master(db: Session, master: Master, day: date, duration_min: int, now: datetime | None = None) -> list[str]:
    """Свободные начала ("HH:MM"): смена из YCLIENTS минус занятость и длительность услуги.

    Есть строка `master_shifts` — часы берём оттуда (пустые = выходной в журнале).
    Строки нет — запасная рамка `Master` и цикл 4/3, чтобы тесты и первый запуск
    не остались без слотов.
    """
    now = now or clock.now()
    rules = booking_rules()
    step = slot_step_min()
    day_iso = day.isoformat()

    if db.get(SalonClosure, day_iso) is not None:
        return []
    personal_off = db.execute(
        select(MasterDayOff).where(MasterDayOff.master_id == master.id, MasterDayOff.date_iso == day_iso)
    ).scalar_one_or_none()
    if personal_off is not None:
        return []

    shift = db.execute(
        select(MasterShift).where(MasterShift.master_id == master.id, MasterShift.date_iso == day_iso)
    ).scalar_one_or_none()
    if shift is not None:
        if not shift.starts_at or not shift.ends_at:
            return []
        start_min = _time_to_min(shift.starts_at)
        end_min = _time_to_min(shift.ends_at)
    else:
        if shift_day_off(master, day):
            return []
        start_min = _time_to_min(master.work_start)
        end_min = _time_to_min(master.work_end)
    busy = busy_intervals(db, master.id, day)
    min_lead = timedelta(hours=rules["min_lead_hours"])

    free: list[str] = []
    t = start_min
    while t + duration_min <= end_min:
        slot_start = datetime.combine(day, time.min) + timedelta(minutes=t)
        slot_end = slot_start + timedelta(minutes=duration_min)
        if slot_start < now + min_lead:
            t += step
            continue
        overlap = any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy)
        if not overlap:
            free.append(_min_to_time(t))
        t += step
    return free


def has_overlap(db: Session, master_id: str, starts_at: datetime, ends_at: datetime, exclude_booking_id: str | None = None) -> bool:
    day_start = datetime.combine(starts_at.date(), time.min)
    day_end = day_start + timedelta(days=1)
    stmt = select(Booking).where(
        Booking.master_id == master_id,
        Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
        Booking.starts_at < day_end,
        Booking.ends_at > day_start,
    )
    if exclude_booking_id:
        stmt = stmt.where(Booking.id != exclude_booking_id)
    for b in db.execute(stmt).scalars().all():
        if starts_at < b.ends_at and ends_at > b.starts_at:
            return True
    return False


# ---------------------------------------------------------------------------
# Промо старта и абонементы
# ---------------------------------------------------------------------------

def _client_bookings(db: Session, phone: str) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.owner_phone == phone, Booking.status != BookingStatus.cancelled)
        .order_by(Booking.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def resolve_free_addons(
    db: Session,
    phone: str,
    service: Service,
    subscription: Subscription | None = None,
    now: datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Какие допы клиент получает бесплатно и по какой причине.

    Считается автоматически на момент создания записи: промо старта по документу §7
    и «безлимит при каждом визите» для активного абонемента.
    """
    now = now or clock.now()
    pet = service.pet_type.value
    history = _client_bookings(db, phone)
    free: list[str] = []
    applied: list[str] = []
    cfg = promos()

    welcome = cfg.get("welcome_gift", {})
    if welcome.get("enabled") and not history:
        addon_id = (welcome.get("free_addon") or {}).get(pet)
        if addon_id:
            free.append(addon_id)
            applied.append(welcome.get("title", "Welcome-подарок"))

    intro = cfg.get("znakomstvo", {})
    if intro.get("enabled") and service.id in intro.get("target_services", []):
        # «первый месяц клиента» — считаем от самой первой записи; для новичка это сегодня
        first_visit = history[0].created_at if history else now
        if (now - first_visit).days <= int(intro.get("days_from_first_visit", 30)):
            for addon_id in (intro.get("free_addons") or {}).get(pet, []):
                free.append(addon_id)
            applied.append(intro.get("title", "Акция «Знакомство»"))

    if subscription is not None:
        included = [a for a in subscription_included_addons() if a.startswith(f"{pet}_")]
        free.extend(included)
        if included:
            applied.append("Абонемент: гигиена включена в визит")

    # порядок сохраняем, дубли убираем
    unique_free = list(dict.fromkeys(free))
    return unique_free, applied


def active_subscription(db: Session, phone: str, now: datetime | None = None) -> Subscription | None:
    now = now or clock.now()
    stmt = (
        select(Subscription)
        .where(Subscription.owner_phone == phone, Subscription.expires_at > now)
        .order_by(Subscription.expires_at)
    )
    for sub in db.execute(stmt).scalars().all():
        if sub.visits_total - sub.visits_used >= min_balance_visits():
            return sub
    return None


def subscription_balance(sub: Subscription) -> float:
    return round(sub.visits_total - sub.visits_used, 2)


def visits_cost(service_id: str) -> float | None:
    """Сколько визитов списывает услуга. None — услуга не входит в абонемент."""
    return subscription_coefficients().get(service_id)


def subscription_charge(
    db: Session,
    sub: Subscription,
    service: Service,
    now: datetime | None = None,
) -> tuple[float, int]:
    """Списание визита по абонементу. Возвращает (списано визитов, доплата ₽).

    Правила §5 документа: пакет действует с буфером (expires_at), при остатке
    меньше 0,5 визита провести процедуру по пакету нельзя — считается доплата.
    Стоимость визита берётся по сетке ПАКЕТА (размер зафиксирован при покупке),
    а не по более узкой сетке базового прайса.
    """
    now = now or clock.now()
    if sub.expires_at <= now:
        raise BookingError("subscription_expired", "Срок действия абонемента истёк")

    cost = visits_cost(service.id)
    if cost is None:
        raise BookingError("service_not_in_subscription", f"Услуга «{service.name}» не входит в абонемент")

    balance = subscription_balance(sub)
    if balance < min_balance_visits():
        raise BookingError("subscription_balance_low", "На балансе меньше 0,5 визита — нужен новый пакет")

    plan = db.get(SubscriptionPlan, sub.plan_id)
    if plan is None:
        raise BookingError("plan_not_found", "Тариф абонемента не найден")
    price_per_visit = int(plan.prices[sub.size]) / plan.visits

    if balance >= cost:
        return cost, 0

    # Остатка не хватает на всю услугу: списываем остаток, разницу клиент доплачивает на месте.
    covered = balance
    top_up = int(round((cost - covered) * price_per_visit))
    return covered, top_up


def refund_subscription_visit(db: Session, booking: Booking) -> float:
    """Вернуть списанный визит на абонемент при отмене записи.

    Вызывается только для отмены. При неявке (`no_show`) визит сгорает — это
    санкция из оферты §2.4, поэтому здесь такой записи быть не должно.
    Идемпотентно: после возврата `visits_charged` обнуляется, повторный вызов
    ничего не меняет.
    """
    if not booking.subscription_id or not booking.visits_charged:
        return 0.0
    sub = db.get(Subscription, booking.subscription_id)
    if sub is None:
        return 0.0
    refunded = float(booking.visits_charged)
    sub.visits_used = round(max(sub.visits_used - refunded, 0.0), 2)
    booking.visits_charged = None
    return refunded


def next_booking_id(db: Session) -> str:
    last = db.execute(select(Booking).order_by(Booking.id.desc()).limit(1)).scalar_one_or_none()
    if last is None:
        n = 1001
    else:
        try:
            n = int(last.id.split("-")[-1]) + 1
        except ValueError:
            n = 1001
    return f"KERIS-{n}"
