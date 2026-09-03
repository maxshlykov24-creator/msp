"""Метрики клиента и питомца в amoCRM: «когда последняя покупка», LTV, статус.

Режим «Покупатели» в аккаунте выключен, поэтому встроенного LTV нет — считаем по
нашей БД (она источник истины по визитам) и пишем в поля контакта. Так у Карины и
менеджера в одной карточке видно и переписку из Wazzup, и всю историю визитов, и
на что клиент нажать в реактивации.

Питомец — сущность «Компания» (АРХИТЕКТУРА_ЭКОСИСТЕМЫ.md, Принцип №2): свои
визиты и последняя стрижка считаются по кличке, а не по владельцу, иначе у
человека с двумя собаками история сливается в одну.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import amocrm_client, clock, max_bind, telegram_bind
from .booking_logic import active_subscription, subscription_balance
from .config import settings
from .models import Booking, BookingSource, BookingStatus, SubscriptionPlan

log = logging.getLogger("keris.amocrm.metrics")

STATUS_NEW = "Новый"
STATUS_ACTIVE = "Активный"
STATUS_SLEEPING = "Спящий 60+"
STATUS_LOST = "Потерянный 120+"

# Источник записи у нас → значение поля «Изначальный источник» на контакте
# (список значений заведён на Этапе 0, свои значения туда не добавляем).
SOURCE_TO_CONTACT_FIELD = {
    BookingSource.miniapp: "Телеграм-канал",
    BookingSource.website: "Сайт",
    BookingSource.yclients_maps: "Яндекс.Карты",
}

# Источник записи → значение поля «Источник записи» на сделке.
SOURCE_TO_LEAD_FIELD = {
    BookingSource.miniapp: "Mini App",
    BookingSource.website: "Сайт",
    BookingSource.yclients_maps: "Яндекс.Карты / 2ГИС",
    BookingSource.admin_bot: "Админ-бот",
}

CLOSED_STATUSES = (BookingStatus.cancelled, BookingStatus.no_show)


def _date(value: datetime | None) -> str:
    return value.date().isoformat() if value else ""


def is_visit(booking: Booking, now: datetime) -> bool:
    """Состоявшийся визит = «покупка».

    Отметку «Пришел» в журнале администратор ставит не всегда, поэтому прошедшая
    неотменённая запись тоже считается визитом — иначе LTV занижен и клиент
    уезжает в «спящие», хотя вчера был в салоне.
    """
    return booking.status not in CLOSED_STATUSES and booking.starts_at <= now


def client_metrics(db: Session, phone: str, now: datetime | None = None) -> dict:
    """Сводка по клиенту для полей контакта."""
    now = now or clock.now().replace(tzinfo=None)
    rows = list(db.execute(
        select(Booking).where(Booking.owner_phone == phone).order_by(Booking.starts_at)
    ).scalars().all())

    visits = [b for b in rows if is_visit(b, now)]
    future = [b for b in rows if b.status not in CLOSED_STATUSES and b.starts_at > now]
    spent = sum(int(b.price or 0) for b in visits)

    metrics: dict[str, object] = {
        "Груминг: визитов всего": len(visits),
        "Груминг: сумма всего": spent,
        "Груминг: средний чек": round(spent / len(visits)) if visits else 0,
        "Груминг: первый визит": _date(visits[0].starts_at) if visits else "",
        "Груминг: последний визит": _date(visits[-1].starts_at) if visits else "",
        "Груминг: следующий визит": _date(future[0].starts_at) if future else "",
        "Груминг: отмен": sum(1 for b in rows if b.status == BookingStatus.cancelled),
        "Груминг: неявок": sum(1 for b in rows if b.status == BookingStatus.no_show),
        "Груминг: статус клиента": client_status(visits, future, now),
        "Клиент груминга": True,
        "Подписан на TG-бот": telegram_bind.chat_id_for_phone(db, phone) is not None,
        "Подписан на Max-бот": max_bind.user_id_for_phone(db, phone) is not None,
    }

    consented = next((b for b in rows if b.personal_data_consent), None)
    if consented is not None:
        metrics["Согласие ПДн"] = True
        metrics["Дата согласия ПДн"] = _date(consented.created_at)

    sub = active_subscription(db, phone, now)
    if sub is not None:
        plan = db.get(SubscriptionPlan, sub.plan_id)
        metrics["Абонемент: план"] = plan.name if plan else sub.plan_id
        metrics["Абонемент: остаток визитов"] = f"{subscription_balance(sub):g}"
        metrics["Абонемент: действует до"] = _date(sub.expires_at)
    return metrics


def client_status(visits: list[Booking], future: list[Booking], now: datetime) -> str:
    """Сегмент для реактивации: на нём владелец строит рассылку в WhatsApp."""
    if future:
        return STATUS_ACTIVE
    if not visits:
        return STATUS_NEW
    days = (now - visits[-1].starts_at).days
    if days >= settings.amocrm_lost_days:
        return STATUS_LOST
    if days >= settings.amocrm_sleeping_days:
        return STATUS_SLEEPING
    return STATUS_ACTIVE


def initial_source(db: Session, phone: str) -> str:
    """Значение «Изначальный источник» по самой первой записи клиента."""
    first = db.execute(
        select(Booking).where(Booking.owner_phone == phone).order_by(Booking.created_at).limit(1)
    ).scalars().first()
    if first is None:
        return ""
    return SOURCE_TO_CONTACT_FIELD.get(first.source, "")


def push_client_metrics(db: Session, contact_id: int, phone: str,
                        now: datetime | None = None) -> bool:
    """Обновить метрики на контакте. Best-effort, наружу не бросает."""
    try:
        fields = client_metrics(db, phone, now)
        source = initial_source(db, phone)
        if source:
            contact = amocrm_client.get_contact(contact_id)
            # «Изначальный» — значит первый: перезаписывать его нельзя.
            if not amocrm_client.has_field_value(contact, "contacts", "Изначальный источник"):
                fields["Изначальный источник"] = source
        amocrm_client.update_contact(contact_id, fields)
        return True
    except (amocrm_client.AmoCrmNotConfigured, amocrm_client.AmoCrmBlocked):
        return False
    except Exception:  # noqa: BLE001 — метрики не важнее записи
        log.warning("метрики контакта %s не обновлены (%s)", contact_id, phone, exc_info=True)
        return False


_last_daily_date: str = ""


def run_daily(db: Session, now: datetime | None = None, hour: int = 4) -> int:
    """Раз в сутки пересчитать метрики всем клиентам с визитами.

    Статус «Спящий» и «Потерянный» меняется не от события, а от того, что прошло
    время: без этого прохода клиент, который просто перестал приходить, навсегда
    остался бы «Активным» и не попал в реактивацию. Возвращает число обновлённых.
    """
    global _last_daily_date
    now = now or clock.now().replace(tzinfo=None)
    today = now.date().isoformat()
    if _last_daily_date == today or now.hour != hour:
        return 0
    _last_daily_date = today
    phones = [p for p in db.execute(select(Booking.owner_phone).distinct()).scalars().all() if p]
    updated = sum(1 for phone in phones if refresh_for_phone(db, phone, now))
    log.info("суточный пересчёт метрик: клиентов обновлено %d из %d", updated, len(phones))
    return updated


def refresh_for_phone(db: Session, phone: str, now: datetime | None = None) -> bool:
    """Пересчитать метрики по телефону, если контакт в amoCRM уже есть.

    Точка для событий без записи: клиент привязал бота, дал согласие, купил
    абонемент. Новый контакт при этом не создаём — плодить карточки на людей,
    которые ещё ничего не заказывали, незачем.
    """
    if not settings.amocrm_ready or amocrm_client.blocked():
        return False
    try:
        contact = amocrm_client.find_contact_by_phone(phone)
    except Exception:  # noqa: BLE001
        log.warning("поиск контакта по телефону не удался", exc_info=True)
        return False
    if not contact or not isinstance(contact.get("id"), int):
        return False
    return push_client_metrics(db, contact["id"], phone, now)


# ---------------------------------------------------------------------------
# Питомец
# ---------------------------------------------------------------------------

def pet_fields(db: Session, booking: Booking, now: datetime | None = None) -> dict:
    """Поля карточки питомца. Пустые значения отбрасываются в amocrm_client —
    администратор мог заполнить породу руками, наша пустота её не затрёт."""
    now = now or clock.now().replace(tzinfo=None)
    visits = [
        b for b in db.execute(
            select(Booking).where(
                Booking.owner_phone == booking.owner_phone,
                Booking.pet_name == booking.pet_name,
            ).order_by(Booking.starts_at)
        ).scalars().all()
        if is_visit(b, now)
    ]
    return {
        "Имя собаки": booking.pet_name,
        "Порода": booking.pet_breed,
        "Вес, кг": f"{booking.pet_weight_kg:g}" if booking.pet_weight_kg else "",
        "Размер по прайсу": booking.pet_size,
        "Дата рождения": booking.pet_birth_date,
        "Визитов всего": len(visits),
        "Последний визит": _date(visits[-1].starts_at) if visits else "",
    }
