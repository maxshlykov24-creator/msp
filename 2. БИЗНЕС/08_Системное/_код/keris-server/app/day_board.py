"""Срез дня салона из Postgres. Тот же журнал, что отдаёт админ-бот.

Кнопка в боте читает уже собранный срез. Сбор раз в 10 минут, не в момент нажатия.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Booking, BookingStatus, FinanceSnapshot

CACHE_SEC = 600
_today: dict | None = None
_today_at: datetime | None = None
_stats: dict | None = None
_stats_at: datetime | None = None
_lists: dict[tuple[str, int], tuple[datetime, list]] = {}


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())


def period_stats(db: Session, start: date, end: date, *, now: datetime | None = None) -> dict:
    """end не входит. Визиты — не отмена и не неявка. Сумма по ним."""
    moment = now or clock.now()
    start_at, end_at = _bounds(start, end)
    rows = db.execute(
        select(Booking).where(Booking.starts_at >= start_at, Booking.starts_at < end_at)
    ).scalars().all()
    visits = [b for b in rows if b.status not in (BookingStatus.cancelled, BookingStatus.no_show) and b.starts_at <= moment]
    started = [b for b in visits if b.starts_at <= moment]
    from . import photos
    shots = photos.by_booking_ids(db, [b.id for b in started])
    missing = 0
    ready = 0
    for booking in started:
        kinds = {item["kind"] for item in shots.get(booking.id, [])}
        if "before" in kinds and "after" in kinds:
            ready += 1
        else:
            missing += 1
    return {
        "visits": len(visits),
        **period_revenue(db, start, end, now=moment),
        "cancelled": sum(1 for b in rows if b.status == BookingStatus.cancelled),
        "no_show": sum(1 for b in rows if b.status == BookingStatus.no_show),
        "visits_started": len(started),
        "photos_ready": ready,
        "photos_missing": missing,
    }


def finance_days(db: Session, transactions: list, *, now: datetime, record_meta: dict | None = None) -> list:
    """Каноническая разбивка оплат; цена визита не является оплатой."""
    bookings = {b.yclients_record_id: b for b in db.execute(select(Booking)).scalars()
                if b.yclients_record_id}
    from types import SimpleNamespace
    for rid, meta in (record_meta or {}).items():
        if int(rid) not in bookings:
            bookings[int(rid)] = SimpleNamespace(id=f"yc-{rid}", status=meta["status"],
                starts_at=datetime.fromisoformat(meta["starts_at"]), master_id=meta.get("master_id", ""))
    days = {}
    for tx in transactions:
        paid_at = datetime.fromisoformat(tx["paid_at"])
        if paid_at > now:
            continue
        key = paid_at.date().isoformat()
        row = days.setdefault(key, {"date": key, "services_kop": 0, "goods_kop": 0,
                                    "prepayments_kop": 0, "unresolved_kop": 0,
                                    "paid_records": [], "master_services_kop": {}})
        amount = tx["amount_kop"]
        kind = tx["kind"]
        if kind not in ("service", "goods_transaction", "good"):
            continue
        booking = bookings.get(tx.get("record_id"))
        if booking is not None and booking.status in (BookingStatus.cancelled, BookingStatus.no_show):
            continue
        if booking is None and (kind == "service" or tx.get("record_id")):
            row["unresolved_kop"] += amount
            continue
        if booking is not None and booking.starts_at > now:
            row["prepayments_kop"] += amount
            continue
        if kind == "service":
            row["services_kop"] += amount
            if booking.id not in row["paid_records"]:
                row["paid_records"].append(booking.id)
            master = row["master_services_kop"]
            master[booking.master_id] = master.get(booking.master_id, 0) + amount
        else:
            row["goods_kop"] += amount
    return [days[k] for k in sorted(days)]


def refresh_finance(db: Session, *, transactions: list | None = None, now: datetime | None = None) -> dict:
    """Полная пагинированная сверка: удалённые оплаты/возвраты не остаются в сумме."""
    from decimal import Decimal
    from . import yclients_client
    from .config import settings
    moment = now or clock.now()
    cid = settings.yclients_company_id or "test"
    saved = db.get(FinanceSnapshot, cid)
    if transactions is None and not settings.yclients_ready:
        return {"skipped": True}
    try:
        source, seen = [], set()
        if transactions is None:
            for page in range(1, 1001):
                response = yclients_client._request("GET", f"/transactions/{cid}", params={"count": 250, "page": page})
                batch = response.get("data")
                if response.get("success") is not True or not isinstance(batch, list):
                    raise ValueError("Неполный ответ финансов YCLIENTS")
                if not batch:
                    break
                for tx in batch:
                    if not tx.get("id") or tx["id"] in seen:
                        raise ValueError("Повторная страница финансов YCLIENTS")
                    seen.add(tx["id"])
                    source.append(tx)
            else:
                raise ValueError("История финансов не дочитана")
        else:
            source = transactions
        normalized = []
        for tx in source:
            value = Decimal(str(tx["amount"])) * 100
            if not value.is_finite() or value != value.to_integral_value():
                raise ValueError("Некорректная денежная сумма YCLIENTS")
            raw_date = str(tx["date"]).replace("Z", "+00:00")
            try:
                parsed_date = datetime.fromisoformat(raw_date)
            except ValueError:
                parsed_date = datetime.strptime(raw_date, "%Y-%m-%dT%H:%M:%S%z")
            paid_at = clock.to_salon_naive(parsed_date)
            normalized.append({"id": int(tx["id"]), "paid_at": paid_at.isoformat(),
                               "amount_kop": int(value), "kind": tx.get("sold_item_type"),
                               "record_id": int(tx.get("record_id") or 0) if tx.get("sold_item_type")=="service" or tx.get("visit_id") else 0})
        if len({t["id"] for t in normalized}) != len(normalized):
            raise ValueError("Повторные транзакции")
        known = {b.yclients_record_id for b in db.execute(select(Booking)).scalars() if b.yclients_record_id}
        record_meta = {}
        if settings.yclients_ready:
            from .models import Master
            masters = {m.yclients_staff_id: m.id for m in db.execute(select(Master)).scalars() if m.yclients_staff_id}
            for rid in {t["record_id"] for t in normalized if t["record_id"] and t["record_id"] not in known}:
                raw = yclients_client.get_record(rid)
                record = raw.get("data", raw)
                at = clock.to_salon_naive(datetime.fromisoformat(record.get("datetime") or record["date"]))
                status = "cancelled" if record.get("deleted") else "no_show" if record.get("attendance")==-1 else "completed" if record.get("attendance")==1 else "confirmed"
                record_meta[str(rid)] = {"starts_at": at.isoformat(), "status": status,
                                         "master_id": masters.get(record.get("staff_id"), "")}
        data = {"transactions": normalized, "record_meta": record_meta,
                "days": finance_days(db, normalized, now=moment, record_meta=record_meta),
                "basis": "payment_date", "source": "yclients", "as_of": moment.isoformat()}
        if saved is None:
            saved = FinanceSnapshot(company_id=cid)
            db.add(saved)
        saved.data, saved.synced_at, saved.last_error = data, moment, ""
        db.commit()
        return {"ok": True, "transactions": len(normalized), "as_of": moment.isoformat()}
    except Exception as exc:
        db.rollback()
        if saved is not None:
            saved.last_error = type(exc).__name__
            db.commit()
        return {"ok": False, "error": type(exc).__name__}


def reclassify_finance(db: Session) -> None:
    """После ручной смены статуса сразу исключить неявку из финансового среза."""
    from .config import settings
    saved = db.get(FinanceSnapshot, settings.yclients_company_id or "test")
    if saved is not None and saved.data:
        data = dict(saved.data)
        data["days"] = finance_days(db, data.get("transactions", []), now=clock.now(), record_meta=data.get("record_meta"))
        saved.data = data
        db.commit()


def period_revenue(db: Session, start: date, end: date, *, now: datetime | None = None) -> dict:
    from .config import settings
    saved = db.get(FinanceSnapshot, settings.yclients_company_id or "test")
    moment = now or clock.now()
    result = {"revenue": 0, "revenue_services": 0, "revenue_goods": 0, "prepayments": 0,
              "paid_visits": 0, "finance_available": saved is not None and saved.synced_at is not None,
              "finance_stale": True, "finance_as_of": None, "finance_unresolved": 0}
    if saved is None or saved.synced_at is None:
        return result
    services = goods = advances = unresolved = 0
    paid = set()
    # Переклассификация предоплаты по текущему состоянию без повторной записи оплаты.
    days = finance_days(db, saved.data.get("transactions", []), now=moment, record_meta=saved.data.get("record_meta"))
    for row in days:
        if not start.isoformat() <= row["date"] < end.isoformat():
            continue
        services += row["services_kop"]
        goods += row["goods_kop"]
        advances += row["prepayments_kop"]
        unresolved += row["unresolved_kop"]
        paid.update(row["paid_records"])
    result.update(revenue=(services+goods)/100, revenue_services=services/100,
                  revenue_goods=goods/100, prepayments=advances/100, paid_visits=len(paid),
                  finance_stale=bool(saved.last_error) or (moment-saved.synced_at).total_seconds()>1800,
                  finance_as_of=saved.synced_at.isoformat(), finance_unresolved=unresolved/100)
    return result


def today_stats(db: Session, *, now: datetime | None = None) -> dict:
    moment = now or clock.now()
    return period_stats(db, moment.date(), moment.date() + timedelta(days=1), now=moment)


def week_range(moment: datetime) -> tuple[date, date]:
    """Понедельник и следующий понедельник. Воскресенье входит в неделю."""
    start = moment.date() - timedelta(days=moment.weekday())
    return start, start + timedelta(days=7)


def prev_week_range(moment: datetime) -> tuple[date, date]:
    start, _end = week_range(moment)
    return start - timedelta(days=7), start


def _span(start: date, end: date) -> str:
    last = end - timedelta(days=1)

    def dot(day: date) -> str:
        return f"{day.day}.{day.month:02d}"

    if start == last:
        return dot(start)
    return f"{dot(start)} по {dot(last)}"


def _slice(db: Session, start: date, end: date, moment: datetime, title: str) -> dict:
    body = period_stats(db, start, end, now=moment)
    body["title"] = title
    body["span"] = _span(start, end)
    return body


def build_stats(db: Session, *, now: datetime | None = None) -> dict:
    moment = now or clock.now()
    day = moment.date()
    this_start, this_end = week_range(moment)
    prev_start, prev_end = prev_week_range(moment)
    return {
        "today": _slice(db, day, day + timedelta(days=1), moment, "Сегодня"),
        "yesterday": _slice(db, day - timedelta(days=1), day, moment, "Вчера"),
        "week": _slice(db, this_start, this_end, moment, "Эта неделя"),
        "prev_week": _slice(db, prev_start, prev_end, moment, "Прошлая неделя"),
    }


def cache_enabled() -> bool:
    return os.environ.get("KERIS_BOARD_CACHE", "1") != "0"


def peek_today() -> dict | None:
    if not cache_enabled() or _today is None or _today_at is None:
        return None
    if (clock.now() - _today_at).total_seconds() > CACHE_SEC:
        return None
    return _today


def store_today(payload: dict, *, at: datetime | None = None) -> dict:
    global _today, _today_at
    _today = payload
    _today_at = at or clock.now()
    return payload


def refresh_today(db: Session) -> dict:
    return store_today(today_stats(db))


def peek_stats() -> dict | None:
    if not cache_enabled() or _stats is None or _stats_at is None:
        return None
    if (clock.now() - _stats_at).total_seconds() > CACHE_SEC:
        return None
    return _stats


def store_stats(payload: dict, *, at: datetime | None = None) -> dict:
    global _stats, _stats_at
    _stats = payload
    _stats_at = at or clock.now()
    return payload


def refresh_board(db: Session) -> dict:
    payload = build_stats(db)
    store_stats(payload)
    store_today(payload["today"])
    return payload


def peek_bookings(when: str, for_photos: int) -> list | None:
    if not cache_enabled():
        return None
    row = _lists.get((when, int(for_photos)))
    if row is None:
        return None
    stored_at, rows = row
    if (clock.now() - stored_at).total_seconds() > CACHE_SEC:
        return None
    return rows


def store_bookings(when: str, for_photos: int, rows: list, *, at: datetime | None = None) -> None:
    _lists[(when, int(for_photos))] = (at or clock.now(), rows)


def drop_lists() -> None:
    _lists.clear()


def drop_cache() -> None:
    """После нового фото срезы и списки визитов собираются заново, а не ждут десять минут."""
    global _stats, _stats_at, _today, _today_at
    _lists.clear()
    _stats = None
    _stats_at = None
    _today = None
    _today_at = None
