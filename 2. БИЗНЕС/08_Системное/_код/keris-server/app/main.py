from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (
    amocrm_client,
    amocrm_stages,
    clock,
    max_bind,
    notify_admins,
    notify_karina,
    photos,
    reminders,
    sms_otp,
    sync,
    telegram_bind,
)
from .booking_logic import (
    BookingError,
    active_subscription,
    booking_horizon_days,
    check_horizon,
    compute_price_and_duration,
    has_overlap,
    next_booking_id,
    normalize_phone,
    refund_subscription_visit,
    resolve_free_addons,
    service_and_addons,
    slots_for_master,
    subscription_balance,
    subscription_charge,
    visits_cost,
)
from .config import settings
from .db import Base, engine, get_db
from .migrate import ensure_columns
from .models import (
    Addon,
    Booking,
    BookingSource,
    BookingStatus,
    Master,
    MasterDayOff,
    PetType,
    Review,
    SalonClosure,
    Service,
    Subscription,
    SubscriptionPlan,
)
from .seed import apply_seed, booking_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("keris.server")

app = FastAPI(title="Keris Server — Груминг (Этап 2)")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    from .db import SessionLocal
    db = SessionLocal()
    try:
        apply_seed(db)
        if settings.yclients_ready:
            try:
                sync.refresh_from_yclients(db, force=True)
            except Exception:  # noqa: BLE001 — без YCLIENTS сервер всё равно поднимается
                log.warning("стартовая сверка мастеров с YCLIENTS не удалась", exc_info=True)
    finally:
        db.close()
    log.info("Keris Server запущен. Время салона: %s. YCLIENTS настроен: %s. amoCRM настроен: %s",
              clock.now().strftime("%d.%m %H:%M"), settings.yclients_ready, settings.amocrm_ready)
    if settings.reminders_enabled or settings.amocrm_ready or settings.yclients_ready:
        asyncio.create_task(reminders_loop())


async def reminders_loop() -> None:
    """Раз в 5 минут: напоминания о визите (24 ч и 3 ч) и сверка воронки amoCRM.

    Проход по amoCRM здесь же, а не отдельным циклом: именно он гарантирует, что
    сделка появится даже если в момент записи CRM была недоступна, и что этапы
    «Завтра запись» / «Сегодня запись» переключатся после полуночи сами.
    """
    from .db import SessionLocal
    while True:
        await asyncio.sleep(reminders.CHECK_INTERVAL_SEC)
        if settings.reminders_enabled:
            try:
                db = SessionLocal()
                try:
                    await asyncio.to_thread(reminders.run_once, db)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
                log.warning("цикл напоминаний: ошибка итерации", exc_info=True)
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(amocrm_stages.run_once, db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            log.warning("цикл amoCRM: ошибка итерации", exc_info=True)
        if settings.yclients_ready:
            try:
                db = SessionLocal()
                try:
                    await asyncio.to_thread(sync.refresh_from_yclients, db)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                log.warning("цикл графика YCLIENTS: ошибка итерации", exc_info=True)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "yclients_ready": settings.yclients_ready,
        "amocrm_ready": settings.amocrm_ready,
        "targetsms_ready": settings.targetsms_ready,
    }


class AuthPhoneIn(BaseModel):
    phone: str


class AuthVerifyIn(BaseModel):
    phone: str
    code: str


@app.post("/api/auth/request-code")
def auth_request_code(payload: AuthPhoneIn, db: Session = Depends(get_db)) -> dict:
    """Код входа в кабинет: Telegram → MAX → SMS (см. sms_otp).

    Ответ всегда 200: `need_bind=true` со ссылками на боты — не ошибка, а шаг
    маршрута, фронт остаётся на вводе кода.
    """
    try:
        return sms_otp.request_code(db, payload.phone)
    except sms_otp.OtpError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@app.post("/api/auth/verify")
def auth_verify(payload: AuthVerifyIn, db: Session = Depends(get_db)) -> dict:
    try:
        return sms_otp.verify_code(db, payload.phone, payload.code)
    except sms_otp.OtpError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Публичный API — вызывается из prototype.html (Mini App / сайт)
# ---------------------------------------------------------------------------

@app.get("/api/rules")
def list_rules(db: Session = Depends(get_db)) -> dict:
    rules = dict(booking_rules())
    rules["horizon_days"] = booking_horizon_days(db)
    return rules


@app.get("/api/services")
def list_services(pet_type: PetType, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Service).where(Service.pet_type == pet_type, Service.active.is_(True))).scalars().all()
    return [
        {"id": r.id, "name": r.name, "prices": r.prices, "durations": r.durations,
         "duration": r.duration_min, "group": r.group, "desc": r.description,
         "includes": r.includes, "visits": visits_cost(r.id)}
        for r in rows
    ]


@app.get("/api/addons")
def list_addons(pet_type: PetType, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Addon).where(Addon.pet_type == pet_type, Addon.active.is_(True))).scalars().all()
    return [
        {"id": r.id, "name": r.name, "price": r.price, "prices": r.prices,
         "price_from": r.price_from, "duration": r.duration_min, "durations": r.durations,
         "group": r.group}
        for r in rows
    ]


@app.get("/api/masters")
def list_masters(db: Session = Depends(get_db)) -> list[dict]:
    sync.refresh_from_yclients(db)
    rows = db.execute(select(Master).where(Master.active.is_(True))).scalars().all()
    reviews: dict[str, list[dict]] = {}
    for r in db.execute(
        select(Review).where(Review.published.is_(True)).order_by(Review.created_at.desc())
    ).scalars().all():
        reviews.setdefault(r.master_id, []).append(
            {"name": r.author_name, "stars": r.stars, "date": r.created_at.date().isoformat(), "text": r.text}
        )
    return [
        {"id": r.id, "name": r.name, "caption": r.caption, "reviews": reviews.get(r.id, [])}
        for r in rows
    ]


class ReviewIn(BaseModel):
    phone: str
    master_id: str
    stars: int
    text: str = ""


@app.post("/api/reviews")
def create_review(payload: ReviewIn, db: Session = Depends(get_db)) -> dict:
    """Отзыв принимается только от клиента с уже состоявшимся визитом к этому мастеру."""
    if payload.stars < 1 or payload.stars > 5:
        raise HTTPException(422, "Оценка — от 1 до 5")
    if db.get(Master, payload.master_id) is None:
        raise HTTPException(404, "Мастер не найден")
    phone = normalize_phone(payload.phone)
    visit = db.execute(
        select(Booking)
        .where(
            Booking.owner_phone == phone,
            Booking.master_id == payload.master_id,
            Booking.status != BookingStatus.cancelled,
            Booking.ends_at <= clock.now().replace(tzinfo=None),
        )
        .order_by(Booking.starts_at.desc())
    ).scalars().first()
    if visit is None:
        raise HTTPException(403, "Отзыв доступен после завершённого визита у этого мастера")
    existing = db.execute(
        select(Review).where(Review.owner_phone == phone, Review.booking_id == visit.id)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(409, "Отзыв об этом визите уже оставлен")
    review = Review(
        master_id=payload.master_id,
        booking_id=visit.id,
        owner_phone=phone,
        author_name=visit.owner_name or "Клиент",
        stars=payload.stars,
        text=payload.text.strip()[:2000],
    )
    db.add(review)
    db.commit()
    return {
        "ok": True,
        "review": {
            "name": review.author_name,
            "stars": review.stars,
            "date": review.created_at.date().isoformat(),
            "text": review.text,
        },
    }


@app.get("/api/client")
def client_profile(phone: str, db: Session = Depends(get_db)) -> dict:
    """Личный кабинет «Мой Keris»: питомцы, абонемент и визиты по номеру телефона."""
    normalized = normalize_phone(phone)
    bookings = db.execute(
        select(Booking).where(Booking.owner_phone == normalized).order_by(Booking.starts_at)
    ).scalars().all()

    service_names = {s.id: s.name for s in db.execute(select(Service)).scalars().all()}
    addon_names = {a.id: a.name for a in db.execute(select(Addon)).scalars().all()}

    # Питомец в кабинете — только если в записи есть кличка. Без клички (карты YCLIENTS,
    # старые тесты) раньше получалась заглушка «Собака»/«Кошка» рядом с реальной Моней.
    pets: dict[tuple[str, str], dict] = {}
    for b in bookings:
        name = (b.pet_name or "").strip()
        if not name:
            continue
        key = (name.lower(), b.pet_type.value)
        pet = pets.get(key)
        if pet is None:
            pet = {
                "id": f"pet{len(pets) + 1}",
                "name": name,
                "type": b.pet_type.value,
                "breed": b.pet_breed or "",
                "size": b.pet_size,
                "weight": f"{b.pet_weight_kg:g} кг" if b.pet_weight_kg else "",
                "birth_date": b.pet_birth_date or "",
                "note": "",
            }
            pets[key] = pet
        # последняя известная информация о питомце — из самой свежей записи
        pet["breed"] = b.pet_breed or pet["breed"]
        pet["size"] = b.pet_size
        pet["birth_date"] = b.pet_birth_date or pet["birth_date"]
        if b.comment:
            pet["note"] = b.comment

    def _visit_pet_id(b: Booking) -> str:
        name = (b.pet_name or "").strip()
        if name:
            hit = pets.get((name.lower(), b.pet_type.value))
            if hit:
                return hit["id"]
        # запись без клички: привязать только если у клиента ровно один питомец того же вида
        same = [p for p in pets.values() if p["type"] == b.pet_type.value]
        return same[0]["id"] if len(same) == 1 else ""

    now = clock.now().replace(tzinfo=None)
    photos_by_booking = photos.by_booking_ids(db, [b.id for b in bookings])
    visits = []
    for b in bookings:
        if b.status == BookingStatus.cancelled:
            status = "cancelled"
        elif b.status == BookingStatus.no_show:
            status = "no_show"
        elif b.status == BookingStatus.completed or b.ends_at <= now:
            status = "done"
        else:
            status = "upcoming"
        if b.subscription_id and b.visits_charged:
            charged = f"{b.visits_charged:g}".replace(".", ",")
            pay = f"Абонемент · списано {charged} визита"
        else:
            pay = "Оплата в салоне"
        visits.append({
            "id": b.id,
            "status": status,
            "date": b.starts_at.date().isoformat(),
            "time": b.starts_at.strftime("%H:%M"),
            "duration": int((b.ends_at - b.starts_at).total_seconds() // 60),
            "masterId": b.master_id,
            "petId": _visit_pet_id(b),
            "service": service_names.get(b.service_id, b.service_id),
            "addons": [addon_names.get(a, a) for a in (b.addon_ids or [])],
            "total": b.price,
            "pay": pay,
            "comment": b.comment or "",
            # Фото до/после этого визита. Лежат у нас, поэтому прошлые визиты не
            # «выцветают»: клиент открывает запись через месяц и видит те же фото.
            "photos": photos_by_booking.get(b.id, []),
        })

    sub = active_subscription(db, normalized)
    subscription = None
    if sub is not None:
        plan = db.get(SubscriptionPlan, sub.plan_id)
        subscription = {
            "name": plan.name if plan else sub.plan_id,
            "left": subscription_balance(sub),
            "total": sub.visits_total,
            "until": sub.expires_at.date().isoformat(),
        }

    # Согласия — по последней записи: клиент, уже подтверждавший их у нас, не должен
    # заново отмечать чекбоксы при следующей записи (актуально последнее решение).
    last = bookings[-1] if bookings else None
    consent = {
        "personal_data": bool(last and last.personal_data_consent),
        "marketing": bool(last and last.marketing_consent),
        "media": bool(last and last.media_consent),
    }

    return {
        "phone": normalized,
        "name": bookings[-1].owner_name if bookings else "",
        "pets": list(pets.values()),
        "subscription": subscription,
        "visits": visits,
        "consent": consent,
    }


class TelegramBindIn(BaseModel):
    phone: str
    chat_id: int


class MaxBindIn(BaseModel):
    phone: str
    user_id: int


@app.post("/api/telegram/bind")
def telegram_bind_client(payload: TelegramBindIn, db: Session = Depends(get_db)) -> dict:
    """Бот прислал контакт клиента. Пишем phone→chat_id и цепляем будущие записи без Telegram."""
    row, attached = telegram_bind.bind_client(db, payload.phone, payload.chat_id)
    if row is None:
        raise HTTPException(422, "Не удалось распознать номер телефона")
    return {"phone": row.phone, "chat_id": row.chat_id, "attached_bookings": attached}


@app.post("/api/max/bind")
def max_bind_client(payload: MaxBindIn, db: Session = Depends(get_db)) -> dict:
    """MAX-бот прислал номер текстом. Пишем phone→user_id, тот же канон +7XXXXXXXXXX что у Telegram."""
    row, attached = max_bind.bind_client(db, payload.phone, payload.user_id)
    if row is None:
        raise HTTPException(422, "Не удалось распознать номер телефона")
    return {"phone": row.phone, "user_id": row.user_id, "attached_bookings": attached}


@app.get("/api/subscriptions")
def list_subscriptions(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(SubscriptionPlan)).scalars().all()
    return [
        {"id": r.id, "name": r.name, "visits": r.visits, "months": r.validity_months,
         "prices": r.prices, "bonus": r.bonus, "bonus_spa": r.bonus_spa}
        for r in rows
    ]


@app.get("/api/subscriptions/balance")
def subscription_balance_for_phone(phone: str, db: Session = Depends(get_db)) -> dict:
    """Баланс визитов клиента — показывается в мини-аппе при выборе услуги."""
    sub = active_subscription(db, phone)
    if sub is None:
        return {"active": False}
    plan = db.get(SubscriptionPlan, sub.plan_id)
    return {
        "active": True,
        "subscription_id": sub.id,
        "plan_id": sub.plan_id,
        "plan_name": plan.name if plan else sub.plan_id,
        "size": sub.size,
        "visits_total": sub.visits_total,
        "visits_left": subscription_balance(sub),
        "expires_at": sub.expires_at.isoformat(),
        "bonus_spa_left": max((plan.bonus_spa if plan else 0) - sub.bonus_spa_used, 0),
        "bonus_extra_used": sub.bonus_extra_used,
    }


@app.get("/api/promo-preview")
def promo_preview(phone: str, service_id: str, db: Session = Depends(get_db)) -> dict:
    """Что клиент получит бесплатно на этой записи — показываем до подтверждения."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(404, "Услуга не найдена")
    sub = active_subscription(db, phone) if phone else None
    free_ids, applied = resolve_free_addons(db, phone, service, sub)
    names = {
        a.id: a.name
        for a in db.execute(select(Addon).where(Addon.id.in_(free_ids))).scalars().all()
    } if free_ids else {}
    return {
        "free_addons": [{"id": aid, "name": names.get(aid, aid)} for aid in free_ids],
        "promos": applied,
    }


@app.get("/api/slots")
def get_slots(
    date_iso: str,
    service_id: str,
    addon_ids: str = "",
    master_id: Optional[str] = None,
    size: str = "",
    db: Session = Depends(get_db),
) -> dict:
    try:
        day = date.fromisoformat(date_iso)
    except ValueError:
        raise HTTPException(422, "date_iso должен быть в формате YYYY-MM-DD")

    addons_list = [a for a in addon_ids.split(",") if a]
    try:
        check_horizon(day, db=db)
        service, addons = service_and_addons(db, service_id, addons_list)
        _, duration = compute_price_and_duration(service, addons, size or next(iter(service.prices)))
    except BookingError as e:
        raise HTTPException(422, e.message)

    sync.refresh_from_yclients(db)
    master_ids = [master_id] if master_id and master_id != "any" else [
        m.id for m in db.execute(select(Master).where(Master.active.is_(True))).scalars().all()
    ]
    result = {}
    for mid in master_ids:
        master = db.get(Master, mid)
        if master is None or not master.active:
            continue
        result[mid] = slots_for_master(db, master, day, duration)
    return {"duration": duration, "masters": result}


@app.get("/api/slots/range")
def get_slots_range(
    date_from: str,
    date_to: str,
    service_id: str,
    addon_ids: str = "",
    master_id: Optional[str] = None,
    size: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Слоты на весь горизонт графика одним ответом — лента дат в записи."""
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(422, "date_from и date_to должны быть YYYY-MM-DD")
    if end < start:
        raise HTTPException(422, "date_to раньше date_from")

    sync.refresh_from_yclients(db)
    today = clock.today()
    if start < today:
        start = today
    horizon_end = today + timedelta(days=booking_horizon_days(db, today))
    if end > horizon_end:
        end = horizon_end

    addons_list = [a for a in addon_ids.split(",") if a]
    try:
        service, addons = service_and_addons(db, service_id, addons_list)
        _, duration = compute_price_and_duration(service, addons, size or next(iter(service.prices)))
    except BookingError as e:
        raise HTTPException(422, e.message)

    master_ids = [master_id] if master_id and master_id != "any" else [
        m.id for m in db.execute(select(Master).where(Master.active.is_(True))).scalars().all()
    ]
    days: dict[str, dict] = {}
    cursor = start
    while cursor <= end:
        day_slots = {}
        for mid in master_ids:
            master = db.get(Master, mid)
            if master is None or not master.active:
                continue
            day_slots[mid] = slots_for_master(db, master, cursor, duration)
        days[cursor.isoformat()] = day_slots
        cursor += timedelta(days=1)
    return {"duration": duration, "masters_by_date": days}


class BookingIn(BaseModel):
    owner_name: str
    owner_phone: str
    pet_name: str = ""
    pet_type: PetType
    pet_breed: str = ""
    pet_weight_kg: Optional[float] = None
    pet_birth_date: str = ""
    pet_size: str
    service_id: str
    addon_ids: list[str] = []
    master_id: str
    date_iso: str
    time_hhmm: str
    source: BookingSource = BookingSource.miniapp
    personal_data_consent: bool = False
    marketing_consent: bool = False
    media_consent: bool = False
    use_subscription: bool = False
    telegram_chat_id: Optional[int] = None
    comment: str = ""


@app.post("/api/bookings")
def create_booking(payload: BookingIn, db: Session = Depends(get_db)) -> dict:
    if not payload.personal_data_consent:
        raise HTTPException(422, "Требуется согласие на обработку персональных данных")

    # Телефон — ключ клиента (кабинет, абонемент, промо), храним в одном формате.
    owner_phone = normalize_phone(payload.owner_phone)
    subscription = None
    visits_charged = None
    top_up = 0
    try:
        check_horizon(date.fromisoformat(payload.date_iso), db=db)
        service, addons = service_and_addons(db, payload.service_id, payload.addon_ids)
        if service.pet_type != payload.pet_type:
            raise BookingError("pet_type_mismatch", "Услуга не подходит для указанного типа питомца")

        if payload.use_subscription:
            subscription = active_subscription(db, owner_phone)
            if subscription is None:
                raise BookingError("subscription_not_found", "Активного абонемента с достаточным балансом не найдено")
            visits_charged, top_up = subscription_charge(db, subscription, service)

        free_addon_ids, applied_promos = resolve_free_addons(db, owner_phone, service, subscription)
        # В журнал и YCLIENTS идут только допы, которые клиент отметил сам.
        # Подарки остаются в free_addon_ids для уведомлений, без отдельных строк в записи.
        price, duration = compute_price_and_duration(service, addons, payload.pet_size, free_addon_ids)
        if subscription is not None:
            # услуга покрыта визитами абонемента: к оплате только доплата и платные допы
            price = price - int(service.prices[payload.pet_size]) + top_up
    except BookingError as e:
        raise HTTPException(422, e.message)

    master_id = payload.master_id
    if master_id == "any":
        day = date.fromisoformat(payload.date_iso)
        free_master = None
        for m in db.execute(select(Master).where(Master.active.is_(True))).scalars().all():
            if payload.time_hhmm in slots_for_master(db, m, day, duration):
                free_master = m
                break
        if free_master is None:
            raise HTTPException(409, "На это время свободных мастеров не осталось")
        master_id = free_master.id

    chosen = db.get(Master, master_id)
    if chosen is None or not chosen.active:
        raise HTTPException(422, "Этот мастер сейчас не принимает онлайн-запись")

    starts_at = datetime.combine(date.fromisoformat(payload.date_iso), datetime.min.time()) + timedelta(
        hours=int(payload.time_hhmm.split(":")[0]), minutes=int(payload.time_hhmm.split(":")[1])
    )
    ends_at = starts_at + timedelta(minutes=duration)

    # Повторная проверка слота прямо перед записью — анти-гонка (принцип §3 плана/ТЗ).
    if has_overlap(db, master_id, starts_at, ends_at):
        raise HTTPException(409, "Этот слот только что заняли — выберите другое время")

    # Двойное нажатие «Записаться»: тот же телефон на пересекающееся время у любого мастера.
    same_client = db.execute(
        select(Booking).where(
            Booking.owner_phone == owner_phone,
            Booking.status != BookingStatus.cancelled,
            Booking.starts_at < ends_at,
            Booking.ends_at > starts_at,
        )
    ).scalars().first()
    if same_client is not None:
        raise HTTPException(409, f"На это время у вас уже есть запись {same_client.id}")

    booking = Booking(
        id=next_booking_id(db),
        owner_name=payload.owner_name,
        owner_phone=owner_phone,
        pet_name=payload.pet_name,
        pet_type=payload.pet_type,
        pet_breed=payload.pet_breed,
        pet_weight_kg=payload.pet_weight_kg,
        pet_birth_date=payload.pet_birth_date,
        pet_size=payload.pet_size,
        service_id=payload.service_id,
        addon_ids=list(payload.addon_ids),
        free_addon_ids=free_addon_ids,
        applied_promos=applied_promos,
        master_id=master_id,
        starts_at=starts_at,
        ends_at=ends_at,
        price=price,
        status=BookingStatus.confirmed,
        source=payload.source,
        personal_data_consent=True,
        marketing_consent=payload.marketing_consent,
        media_consent=payload.media_consent,
        telegram_chat_id=telegram_bind.resolve_chat_id(db, owner_phone, payload.telegram_chat_id),
        max_user_id=max_bind.resolve_user_id(db, owner_phone),
        comment=payload.comment,
        subscription_id=subscription.id if subscription else None,
        visits_charged=visits_charged,
    )
    db.add(booking)
    if subscription is not None and visits_charged:
        subscription.visits_used = round(subscription.visits_used + visits_charged, 2)
    db.commit()

    sync.push_booking_to_yclients(db, booking)
    sync.sync_booking_to_amocrm(
        db, booking,
        note=f"Запись оформлена клиентом: {sync.source_title(booking)}",
    )
    addon_titles = {a.id: a.name for a in db.execute(select(Addon)).scalars().all()}
    master = db.get(Master, booking.master_id)
    created_text = notify_karina.booking_created_text(
        booking, service.name, addon_titles, master_name=master.name if master else ""
    )
    notify_karina.notify(created_text)
    notify_admins.notify(created_text)
    reminders.send_booking_created(booking, service.name)

    return {
        "booking_id": booking.id,
        "master_id": booking.master_id,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "price": booking.price,
        "status": booking.status.value,
        "free_addon_ids": booking.free_addon_ids,
        "applied_promos": booking.applied_promos,
        "subscription": None if subscription is None else {
            "id": subscription.id,
            "visits_charged": visits_charged,
            "balance_left": subscription_balance(subscription),
            "top_up": top_up,
        },
    }


class RescheduleIn(BaseModel):
    date_iso: str
    time_hhmm: str
    phone: str = ""


class CancelIn(BaseModel):
    phone: str = ""


def owned_booking(db: Session, booking_id: str, phone: str, admin_key: str = "") -> Booking:
    """Запись, которой клиент вправе распоряжаться.

    Id записей последовательные (KERIS-1001, 1002, …), поэтому одного id для
    переноса и отмены мало — сверяем телефон владельца. Админский ключ
    (бот Карины) проверку проходит без телефона.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    if settings.admin_api_key and admin_key == settings.admin_api_key:
        return booking
    if not phone or normalize_phone(phone) != booking.owner_phone:
        raise HTTPException(403, "Запись оформлена на другой номер телефона")
    return booking


def assert_change_window(booking: Booking, action: str) -> None:
    """Бесплатный перенос/отмена — не позже чем за free_reschedule_hours (24 ч).
    Позже решение принимает мастер, поэтому клиента отправляем в Telegram."""
    hours = int(booking_rules().get("free_reschedule_hours", 24))
    now = clock.now().replace(tzinfo=None)
    if booking.starts_at - now < timedelta(hours=hours):
        raise HTTPException(
            422,
            f"До визита меньше {hours} ч — {action} только через мастера. "
            "Напишите в Telegram @kerisclubbot или позвоните +7 993 618-98-71",
        )


ACTIVE_STATUSES = (BookingStatus.pending, BookingStatus.confirmed)


@app.post("/api/bookings/{booking_id}/reschedule")
def reschedule_booking(booking_id: str, payload: RescheduleIn,
                       x_admin_key: str = Header(default=""),
                       db: Session = Depends(get_db)) -> dict:
    """Перенос той же записи: мастер, услуга и длительность сохраняются, меняется
    только время. Старый слот освобождается сам — второй записи не появляется."""
    booking = owned_booking(db, booking_id, payload.phone, x_admin_key)
    admin = bool(settings.admin_api_key and x_admin_key == settings.admin_api_key)
    if booking.status not in ACTIVE_STATUSES:
        raise HTTPException(409, "Эту запись уже нельзя перенести — оформите новую")
    if not admin:
        assert_change_window(booking, "перенос")

    try:
        day = date.fromisoformat(payload.date_iso)
        check_horizon(day, db=db)
    except BookingError as e:
        raise HTTPException(422, e.message)
    except ValueError:
        raise HTTPException(422, "Некорректная дата переноса")

    duration = int((booking.ends_at - booking.starts_at).total_seconds() // 60)
    starts_at = datetime.combine(day, datetime.min.time()) + timedelta(
        hours=int(payload.time_hhmm.split(":")[0]), minutes=int(payload.time_hhmm.split(":")[1])
    )
    ends_at = starts_at + timedelta(minutes=duration)
    now = clock.now().replace(tzinfo=None)
    min_lead = int(booking_rules().get("min_lead_hours", 2))
    if not admin and starts_at - now < timedelta(hours=min_lead):
        raise HTTPException(422, f"Записаться можно не раньше чем через {min_lead} ч")
    if has_overlap(db, booking.master_id, starts_at, ends_at, exclude_booking_id=booking.id):
        raise HTTPException(409, "Этот слот только что заняли — выберите другое время")

    was = booking.starts_at
    booking.starts_at = starts_at
    booking.ends_at = ends_at
    # Напоминания за 24 ч и 3 ч считаются от нового времени.
    booking.reminders_sent = []
    db.commit()
    sync.push_booking_to_yclients(db, booking)
    sync.sync_booking_to_amocrm(
        db, booking,
        note=f"Перенос: было {was:%d.%m %H:%M}, стало {booking.starts_at:%d.%m %H:%M}",
    )
    rescheduled_text = notify_karina.booking_rescheduled_text(booking)
    notify_karina.notify(rescheduled_text)
    notify_admins.notify(rescheduled_text)
    return {"booking_id": booking.id, "status": booking.status.value,
            "starts_at": booking.starts_at.isoformat(), "ends_at": booking.ends_at.isoformat()}


@app.post("/api/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, payload: CancelIn = CancelIn(),
                   x_admin_key: str = Header(default=""),
                   db: Session = Depends(get_db)) -> dict:
    """Отмена записи клиентом. В журнале YCLIENTS запись не удаляется, а получает
    статус «Не пришел» (см. sync.push_booking_to_yclients)."""
    booking = owned_booking(db, booking_id, payload.phone, x_admin_key)
    admin = bool(settings.admin_api_key and x_admin_key == settings.admin_api_key)
    if booking.status == BookingStatus.cancelled:
        return {"booking_id": booking.id, "status": booking.status.value, "visits_refunded": 0}
    if booking.status not in ACTIVE_STATUSES:
        raise HTTPException(409, "Эту запись уже нельзя отменить")
    if not admin:
        assert_change_window(booking, "отмена")

    booking.status = BookingStatus.cancelled
    refunded = refund_subscription_visit(db, booking)
    db.commit()
    sync.push_booking_to_yclients(db, booking)
    note = "Отмена клиентом" if not admin else "Отмена через админ-бот"
    if refunded:
        note += f", визитов возвращено на абонемент: {refunded:g}"
    sync.sync_booking_to_amocrm(db, booking, note=note)
    cancelled_text = notify_karina.booking_cancelled_text(booking)
    notify_karina.notify(cancelled_text)
    notify_admins.notify(cancelled_text)
    return {"booking_id": booking.id, "status": booking.status.value, "visits_refunded": refunded}


# ---------------------------------------------------------------------------
# Webhook YCLIENTS — запись/изменение через виджет карт
# ---------------------------------------------------------------------------

@app.post("/api/bookings/{booking_id}/confirm")
def confirm_booking(booking_id: str, db: Session = Depends(get_db)) -> dict:
    """Клиент нажал «Подтверждаю» в напоминании (кнопка в keris-bot). Без спецавторизации —
    тот же паттерн, что у /cancel и /reschedule: booking_id достаточен как признак записи."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    reminders.confirm_booking(db, booking)
    sync.sync_booking_to_amocrm(db, booking, note="Клиент подтвердил приход кнопкой в напоминании")
    return {"booking_id": booking.id, "confirmed_at": booking.client_confirmed_at.isoformat()}


@app.post("/webhooks/yclients")
async def yclients_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Приёмник вебхуков YCLIENTS.

    YCLIENTS marketplace **не доставляет** ни query (`?secret=`), ни path-хвост
    (`/webhooks/yclients/<secret>`) — в nginx всегда приходит голый
    `POST /webhooks/yclients` (User-Agent: GuzzleHttp/7), без auth-заголовков.
    Поэтому проверка секрета здесь намеренно не делается: иначе все события
    стабильно получают 401. Защита — непубличный host + валидация payload в sync.
    Опциональный path-секрет остаётся ниже на случай, если платформа начнёт
    сохранять полный URL.
    """
    if settings.yclients_webhook_secret:
        provided = request.query_params.get("secret") or request.headers.get("X-Webhook-Secret", "")
        # Секрет принимаем, если прислали; отсутствие — не 401 (см. docstring).
        if provided and provided != settings.yclients_webhook_secret:
            raise HTTPException(401, "invalid webhook secret")
    payload = await request.json()
    return sync.handle_yclients_webhook(db, payload)


@app.post("/webhooks/yclients/{secret}")
async def yclients_webhook_path_secret(secret: str, request: Request,
                                        db: Session = Depends(get_db)) -> dict:
    """Тот же приёмник с секретом в пути (на случай, если YCLIENTS начнёт его сохранять)."""
    if settings.yclients_webhook_secret and secret != settings.yclients_webhook_secret:
        raise HTTPException(401, "invalid webhook secret")
    payload = await request.json()
    return sync.handle_yclients_webhook(db, payload)


# ---------------------------------------------------------------------------
# Админ API — вызывается личным ботом Карины (тот же сервер = источник истины)
# ---------------------------------------------------------------------------

def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "invalid admin key")


@app.get("/admin/bookings", dependencies=[Depends(require_admin)])
def admin_bookings(when: str = "today", db: Session = Depends(get_db)) -> list[dict]:
    if when == "today":
        day = clock.today()
    elif when == "tomorrow":
        day = clock.today() + timedelta(days=1)
    elif when == "yesterday":
        day = clock.today() - timedelta(days=1)
    else:
        try:
            day = date.fromisoformat(when)
        except ValueError:
            raise HTTPException(422, "when: today | yesterday | tomorrow | ГГГГ-ММ-ДД")
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    rows = db.execute(
        select(Booking).where(Booking.starts_at >= day_start, Booking.starts_at < day_end,
                               Booking.status != BookingStatus.cancelled)
        .order_by(Booking.starts_at)
    ).scalars().all()
    names = {s.id: s.name for s in db.execute(select(Service)).scalars().all()}
    addon_names = {a.id: a.name for a in db.execute(select(Addon)).scalars().all()}
    booking_photos = photos.by_booking_ids(db, [b.id for b in rows])
    out = []
    for b in rows:
        gifts = [addon_names.get(a, a) for a in (b.free_addon_ids or [])]
        shots = booking_photos.get(b.id, [])
        out.append({
            "id": b.id, "time": b.starts_at.strftime("%H:%M"), "owner": b.owner_name,
            "phone": b.owner_phone, "pet": b.pet_name, "size": b.pet_size,
            "service_id": b.service_id, "service": names.get(b.service_id, b.service_id),
            "master_id": b.master_id, "price": b.price, "status": b.status.value,
            "extras": [addon_names.get(a, a) for a in (b.addon_ids or [])],
            "gifts": gifts, "promos": b.applied_promos or [],
            "by_subscription": b.subscription_id is not None,
            "visits_charged": b.visits_charged, "comment": b.comment or "",
            "admin_note": b.admin_note or "",
            "photos_before": sum(1 for p in shots if p["kind"] == "before"),
            "photos_after": sum(1 for p in shots if p["kind"] == "after"),
        })
    return out


class DayOffIn(BaseModel):
    date_iso: str
    reason: str = ""


@app.post("/admin/masters/{master_id}/day-off", dependencies=[Depends(require_admin)])
def admin_master_day_off(master_id: str, payload: DayOffIn, db: Session = Depends(get_db)) -> dict:
    master = db.get(Master, master_id)
    if master is None:
        raise HTTPException(404, "Мастер не найден")
    existing = db.query(MasterDayOff).filter_by(master_id=master_id, date_iso=payload.date_iso).one_or_none()
    if existing is None:
        db.add(MasterDayOff(master_id=master_id, date_iso=payload.date_iso, reason=payload.reason))
        db.commit()
    return {"master_id": master_id, "date_iso": payload.date_iso, "status": "closed"}


class MasterIn(BaseModel):
    id: str
    name: str
    caption: str = ""
    work_start: str = "10:00"
    work_end: str = "20:00"


@app.post("/admin/masters", dependencies=[Depends(require_admin)])
def admin_add_master(payload: MasterIn, db: Session = Depends(get_db)) -> dict:
    if db.get(Master, payload.id) is not None:
        raise HTTPException(409, "Мастер с таким id уже существует")
    db.add(Master(id=payload.id, name=payload.name, caption=payload.caption,
                   work_start=payload.work_start, work_end=payload.work_end))
    db.commit()
    return {"id": payload.id, "status": "created"}


class PriceIn(BaseModel):
    size: str
    price: int


@app.patch("/admin/services/{service_id}/price", dependencies=[Depends(require_admin)])
def admin_update_price(service_id: str, payload: PriceIn, db: Session = Depends(get_db)) -> dict:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(404, "Услуга не найдена")
    prices = dict(service.prices)
    prices[payload.size] = payload.price
    service.prices = prices
    db.commit()
    return {"id": service_id, "prices": service.prices}


class BookingPriceIn(BaseModel):
    price: int
    reason: str = ""


@app.patch("/admin/bookings/{booking_id}/price", dependencies=[Depends(require_admin)])
def admin_override_booking_price(booking_id: str, payload: BookingPriceIn, db: Session = Depends(get_db)) -> dict:
    """Финальная сумма по факту визита.

    Покрывает п.2.2 оферты (отказ в услуге — оплата фактически потраченного времени,
    но не менее min_refusal_fee) и любые ручные корректировки объёма на месте.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    if payload.price < 0:
        raise HTTPException(422, "Сумма не может быть отрицательной")
    was = booking.price
    booking.price = payload.price
    note = payload.reason.strip()
    booking.admin_note = f"Сумма изменена: {was} → {payload.price} ₽. {note}".strip()
    db.commit()
    return {"booking_id": booking.id, "price": booking.price, "was": was, "note": booking.admin_note,
            "min_refusal_fee": booking_rules().get("min_refusal_fee")}


# ---------------------------------------------------------------------------
# Фото до/после — загружает бот администратора (@kerisclubphotobot)
# ---------------------------------------------------------------------------

class BookingPhotoIn(BaseModel):
    kind: str  # before / after
    file_id: str = ""  # file_id из Telegram: сервер сам скачивает файл себе
    added_by: str = ""


@app.get("/admin/bookings/{booking_id}/photos", dependencies=[Depends(require_admin)])
def admin_list_photos(booking_id: str, db: Session = Depends(get_db)) -> dict:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    return {"booking_id": booking.id, "photos": photos.as_dicts(db, booking.id)}


@app.post("/admin/bookings/{booking_id}/photos", dependencies=[Depends(require_admin)])
def admin_add_photo(booking_id: str, payload: BookingPhotoIn, db: Session = Depends(get_db)) -> dict:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    if not payload.file_id:
        raise HTTPException(422, "file_id обязателен")
    try:
        row = photos.save_from_telegram(
            db, booking, payload.kind, payload.file_id, added_by=payload.added_by,
        )
    except photos.PhotoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {
        "booking_id": booking.id,
        "kind": row.kind,
        "url": photos.url_for(row),
        "photos": photos.as_dicts(db, booking.id),
    }


@app.post("/admin/bookings/{booking_id}/report/send", dependencies=[Depends(require_admin)])
def admin_send_report(booking_id: str, db: Session = Depends(get_db)) -> dict:
    """Отчёт клиенту в бота. Клиент не в боте — фото остаются в кабинете, а ответ
    приходит с `pending=true` и ссылками, которые администратор ему передаёт."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Запись не найдена")
    service = db.get(Service, booking.service_id)
    try:
        return photos.send_report(db, booking, service.name if service else booking.service_id)
    except photos.PhotoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@app.get("/media/{booking_id}/{filename}")
def media_file(booking_id: str, filename: str) -> FileResponse:
    """Отдача фото. На проде это делает nginx (`location /media/`), маршрут —
    страховка для дев-окружения и для случая, когда nginx ещё не обновлён."""
    if "/" in filename or ".." in filename or ".." in booking_id:
        raise HTTPException(404, "not found")
    path = photos.root() / booking_id / filename
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path)


class SubscriptionExtendIn(BaseModel):
    days: int
    reason: str = ""


@app.get("/admin/subscriptions", dependencies=[Depends(require_admin)])
def admin_subscriptions(phone: str, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Subscription).where(Subscription.owner_phone == phone)).scalars().all()
    out = []
    for s in rows:
        plan = db.get(SubscriptionPlan, s.plan_id)
        out.append({
            "id": s.id, "plan_id": s.plan_id, "plan_name": plan.name if plan else s.plan_id,
            "size": s.size, "visits_total": s.visits_total,
            "visits_left": subscription_balance(s), "expires_at": s.expires_at.isoformat(),
            "bonus": plan.bonus if plan else "",
            "bonus_spa_left": max(0, (plan.bonus_spa if plan else 0) - (s.bonus_spa_used or 0)),
        })
    return out


@app.post("/admin/subscriptions/{subscription_id}/extend", dependencies=[Depends(require_admin)])
def admin_extend_subscription(subscription_id: int, payload: SubscriptionExtendIn, db: Session = Depends(get_db)) -> dict:
    """Продление срока действия — заморозка по медпоказаниям делается этим же (вручную)."""
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(404, "Абонемент не найден")
    sub.expires_at = sub.expires_at + timedelta(days=payload.days)
    db.commit()
    return {"subscription_id": sub.id, "expires_at": sub.expires_at.isoformat()}


@app.post("/admin/reminders/run", dependencies=[Depends(require_admin)])
def admin_run_reminders(db: Session = Depends(get_db)) -> dict:
    """Ручной прогон рассылки — для проверки на демо, не дожидаясь пятиминутного цикла."""
    return {"sent": reminders.run_once(db)}


class CloseDayIn(BaseModel):
    reason: str = ""


@app.post("/admin/days/{date_iso}/close", dependencies=[Depends(require_admin)])
def admin_close_day(date_iso: str, payload: CloseDayIn, db: Session = Depends(get_db)) -> dict:
    if db.get(SalonClosure, date_iso) is None:
        db.add(SalonClosure(date_iso=date_iso, reason=payload.reason))
        db.commit()
    return {"date_iso": date_iso, "status": "closed"}


class PuppyIn(BaseModel):
    name: str
    litter: str = ""
    birth_date: str = ""
    sex: str = ""
    color: str = ""
    price: Optional[int] = None


@app.post("/admin/puppies", dependencies=[Depends(require_admin)])
def admin_add_puppy(payload: PuppyIn) -> dict:
    """Карточка щенка в amoCRM (реюз воронки «Щенки» Этапа 0)."""
    try:
        lead_id = amocrm_client.create_puppy_lead(
            name=payload.name,
            fields={},  # field_id полей помёта/д.р./пола/окраса/цены — из .env Этапа 0
        )
    except amocrm_client.AmoCrmNotConfigured as e:
        raise HTTPException(503, str(e))
    if lead_id is None:
        raise HTTPException(502, "amoCRM не вернул id сделки")
    return {"amocrm_lead_id": lead_id, "status": "created"}
