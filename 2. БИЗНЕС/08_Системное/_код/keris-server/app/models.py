"""Доменная модель груминга Keris Club (Этап 2).

Источник истины бизнес-логики — эти таблицы, а не YCLIENTS. YCLIENTS —
синхронное зеркало (см. sync.py) для календаря менеджера и записи с карт.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class PetType(str, enum.Enum):
    dog = "dog"
    cat = "cat"


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


class BookingSource(str, enum.Enum):
    miniapp = "miniapp"
    website = "website"
    yclients_maps = "yclients_maps"
    admin_bot = "admin_bot"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pet_type: Mapped[PetType] = mapped_column(Enum(PetType), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    prices: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # {"XS": 5500, ...}
    # Длительность зависит от размера питомца (сетка из прайса). duration_min — фолбэк,
    # если для размера нет своего значения.
    durations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    group: Mapped[str] = mapped_column(String(32), default="base")  # base / signature
    description: Mapped[str] = mapped_column(String(2000), default="")
    includes: Mapped[str] = mapped_column(String(2000), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    yclients_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # legacy, не используется
    # {"XS": yc_id, "S": yc_id, ...} — своя услуга YCLIENTS на каждый размер (точная цена, без диапазона).
    yclients_service_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Addon(Base):
    __tablename__ = "addons"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pet_type: Mapped[PetType] = mapped_column(Enum(PetType), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    # Окрашивание и т.п. — цена и время зависят от размера; иначе используются price/duration_min.
    prices: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    durations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    price_from: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    group: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    yclients_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # legacy, не используется
    # Допы с ценой по размеру (окрашивание) — {"XS": yc_id, ...}; допы с фикс. ценой — {"flat": yc_id}.
    yclients_service_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    caption: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    work_start: Mapped[str] = mapped_column(String(5), default="10:00")
    work_end: Mapped[str] = mapped_column(String(5), default="20:00")
    # Сменный график: shift_on рабочих дней, затем shift_off выходных, цикл считается
    # от shift_start (первый рабочий день). 0/0 — мастер работает без цикла, каждый день.
    shift_on: Mapped[int] = mapped_column(Integer, default=0)
    shift_off: Mapped[int] = mapped_column(Integer, default=0)
    shift_start: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    yclients_staff_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    days_off: Mapped[list["MasterDayOff"]] = relationship(back_populates="master", cascade="all, delete-orphan")


class MasterDayOff(Base):
    __tablename__ = "master_days_off"
    __table_args__ = (UniqueConstraint("master_id", "date_iso", name="uq_master_day_off"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[str] = mapped_column(ForeignKey("masters.id"), nullable=False)
    date_iso: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    reason: Mapped[str] = mapped_column(String(300), default="")

    master: Mapped["Master"] = relationship(back_populates="days_off")


class MasterShift(Base):
    """Смена мастера на конкретный день — копия графика из YCLIENTS.

    Смену ведёт администратор в журнале, поэтому источник правды по рабочим часам —
    YCLIENTS, а не `Master.work_start/work_end` (там общая рамка). Без этой копии
    онлайн-запись отдавала слоты по своей рамке, YCLIENTS отвечал на пуш
    `409 «Выбранное время недоступно»`, и запись оставалась только у нас.

    Строка есть, а `starts_at`/`ends_at` пустые — день синхронизирован и мастер в нём
    не работает. Строки нет вовсе — день ещё не синхронизирован (тогда работает
    рамка `Master`, чтобы дев и первый запуск не остались вообще без слотов).
    """

    __tablename__ = "master_shifts"
    __table_args__ = (UniqueConstraint("master_id", "date_iso", name="uq_master_shift"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[str] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    date_iso: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    starts_at: Mapped[str] = mapped_column(String(5), default="")  # "10:00"
    ends_at: Mapped[str] = mapped_column(String(5), default="")  # "21:00"
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SalonClosure(Base):
    """Форс-мажор: «закрыть день для записи» (болезнь мастера и т.п.)."""

    __tablename__ = "salon_closures"

    date_iso: Mapped[str] = mapped_column(String(10), primary_key=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "KERIS-1234"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_phone: Mapped[str] = mapped_column(String(32), nullable=False)

    pet_name: Mapped[str] = mapped_column(String(200), default="")
    pet_type: Mapped[PetType] = mapped_column(Enum(PetType), nullable=False)
    pet_breed: Mapped[str] = mapped_column(String(200), default="")
    pet_weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Строкой (не Date): приходит и от нашей формы, и из доп. поля YCLIENTS,
    # формат не всегда строгий ISO — валидировать/приводить сложнее, чем показать как есть.
    pet_birth_date: Mapped[str] = mapped_column(String(20), default="")
    pet_size: Mapped[str] = mapped_column(String(16), nullable=False)  # XS..XL / short_small..long_large

    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), nullable=False)
    addon_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Допы, выданные бесплатно: промо старта или «безлимит при визите» по абонементу.
    free_addon_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    applied_promos: Mapped[list[str]] = mapped_column(JSON, default=list)
    master_id: Mapped[str] = mapped_column(ForeignKey("masters.id"), nullable=False)

    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.confirmed)
    source: Mapped[BookingSource] = mapped_column(Enum(BookingSource), default=BookingSource.miniapp)
    personal_data_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    media_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    # Ручная корректировка суммы (отказ в услуге по офёрте, изменение объёма на месте).
    admin_note: Mapped[str] = mapped_column(String(500), default="")
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # Какие напоминания уже отправлены: [24, 3] — часы из booking_rules.reminder_hours.
    reminders_sent: Mapped[list[int]] = mapped_column(JSON, default=list)
    # Каналы, куда уже ушло «спасибо за запись»: telegram / max. SMS сюда не пишем —
    # его не ретраим, чтобы не списать баланс дважды.
    thanks_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    # True — хотя бы один привязанный канал (TG/MAX) не доставил «спасибо».
    # Пятиминутный цикл добирает только такие записи, старые без флага не спамит.
    thanks_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    # Клиент нажал «Подтверждаю» в напоминании — тогда же уходит attendance=2 в YCLIENTS.
    client_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    visits_charged: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    yclients_record_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amocrm_lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Карточка питомца в amoCRM (сущность «Компания», см. АРХИТЕКТУРА_ЭКОСИСТЕМЫ.md).
    amocrm_company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Имя этапа, на который сделку уже переводили. Без него пятиминутный проход
    # дёргал бы amoCRM по каждой живой записи на каждом цикле.
    amocrm_stage: Mapped[str] = mapped_column(String(64), default="")


class BookingPhoto(Base):
    """Фото до/после по визиту. Файл лежит у нас (`path` относительно PHOTOS_DIR),
    а не ссылкой на Telegram: file_path в Telegram живёт около часа, и через месяц
    клиент открыл бы прошлый визит с битыми картинками."""

    __tablename__ = "booking_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # before / after
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # chat_id администратора, который загрузил — чтобы в логе было видно автора.
    added_by: Mapped[str] = mapped_column(String(32), default="")
    # Когда отчёт ушёл клиенту в бота. Пусто — фото уже в кабинете, но не отправлено.
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TelegramClient(Base):
    """Связка телефон ↔ Telegram chat_id. Источник: кнопка «поделиться номером» в боте
    или Mini App (user.id + телефон формы). По ней сайт и YCLIENTS находят, куда писать."""

    __tablename__ = "telegram_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    pd_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MaxClient(Base):
    """Связка телефон ↔ MAX user_id. Источник: номер текстом после согласия ПДн.
    Отдельная таблица: Telegram chat_id и MAX user_id — разные пространства, смешивать нельзя."""

    __tablename__ = "max_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    pd_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # know / habit / trust
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    visits: Mapped[int] = mapped_column(Integer, nullable=False)
    validity_months: Mapped[int] = mapped_column(Integer, nullable=False)
    prices: Mapped[dict] = mapped_column(JSON, nullable=False)
    bonus: Mapped[str] = mapped_column(String(300), default="")
    bonus_spa: Mapped[int] = mapped_column(Integer, default=0)


class Subscription(Base):
    """Купленный клиентом абонемент — списание визитов по коэффициентам услуг."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("subscription_plans.id"), nullable=False)
    owner_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    size: Mapped[str] = mapped_column(String(8), nullable=False)
    visits_total: Mapped[float] = mapped_column(Float, nullable=False)  # в единицах "визит на базовую услугу"
    visits_used: Mapped[float] = mapped_column(Float, default=0.0)
    bonus_used: Mapped[bool] = mapped_column(Boolean, default=False)
    bonus_spa_used: Mapped[int] = mapped_column(Integer, default=0)
    bonus_extra_used: Mapped[bool] = mapped_column(Boolean, default=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    amocrm_lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    bookings: Mapped[list["Booking"]] = relationship(backref="subscription")


class Review(Base):
    """Отзыв о мастере. Оставить может только клиент с завершённым визитом у него."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[str] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    booking_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    owner_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    author_name: Mapped[str] = mapped_column(String(200), default="Клиент")
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(2000), default="")
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SmsOtp(Base):
    """Одноразовый код входа в кабинет «Мой Keris». Хранится хеш, не сам код."""

    __tablename__ = "sms_otps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Куда ушёл код: telegram / max / sms. Пусто — доставить было некуда, клиент
    # ушёл привязывать бота; по этому признаку deliver_pending выдаёт новый код.
    channel: Mapped[str] = mapped_column(String(16), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ClientOutreach(Base):
    """Что мы уже писали клиенту сами: поздравление с ДР питомца, возврат «давно не был».

    `tag` задаёт цикл, в котором сообщение уникально: для ДР это год и кличка,
    для возврата — дата последнего визита. Поэтому повтор в тот же год или по тому
    же «затишью» невозможен, а следующий цикл сработает.
    """

    __tablename__ = "client_outreach"
    __table_args__ = (UniqueConstraint("phone", "kind", "tag", name="uq_outreach"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # birthday / reactivation
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    channels: Mapped[str] = mapped_column(String(64), default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncLogDirection(str, enum.Enum):
    push_to_yclients = "push_to_yclients"
    webhook_from_yclients = "webhook_from_yclients"
    push_to_amocrm = "push_to_amocrm"


class SyncLog(Base):
    """Идемпотентность и аудит вебхуков/пушей: паттерн §9 ТЗ_ЭТАП0_ПИТОМНИК.md."""

    __tablename__ = "sync_log"
    __table_args__ = (UniqueConstraint("direction", "event_id", name="uq_sync_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[SyncLogDirection] = mapped_column(Enum(SyncLogDirection), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    booking_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")  # ok / retry / failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
