from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.amo_client import AmoClient, AmoError
from app.config import STAGE_NAMES, STAGE_ORDER, settings
from app.database import SessionLocal, init_db
from app.models import LeadSnapshot, Snapshot, StageReached, SyncState

log = logging.getLogger("collector")

MSK = ZoneInfo("Europe/Moscow")

# Сколько месяцев истории событий (смен этапа) пытаемся набрать бэкфиллом.
BACKFILL_MONTHS_CAP = 24
# Окно, которое пере-сканируем на КАЖДОМ обычном прогоне (ловит недавние правки).
EVENT_REFRESH_DAYS = 7

MANAGER_KEYS = ("eugene", "nikita", "elzar")


# ──────────────────────────── time helpers ────────────────────────────

def _now_msk() -> datetime:
    return datetime.now(MSK)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _msk_day(unix_ts: int | None) -> str | None:
    if not unix_ts:
        return None
    return datetime.fromtimestamp(unix_ts, MSK).strftime("%Y-%m-%d")


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ──────────────────────────── schema validation ────────────────────────────

class SchemaError(Exception):
    """Схема amoCRM не совпадает с ожидаемой — collector не должен писать данные."""


def _custom_field_enum_values(field: dict) -> set[str]:
    return {
        str(e.get("value", "")).strip()
        for e in field.get("enums", []) or []
        if e.get("value")
    }


def validate_schema(amo: AmoClient) -> list[str]:
    """Жёсткая проверка ID/константы перед сбором. Бросает SchemaError при
    несовпадении критичных вещей (аккаунт, воронка, менеджеры, поля).
    Возвращает список некритичных предупреждений (несовпадение enum-текстов)."""
    warnings: list[str] = []

    account = amo.account() or {}
    if account.get("id") != settings.amo_account_id:
        raise SchemaError(
            f"account_id не совпадает: ожидали {settings.amo_account_id}, "
            f"получили {account.get('id')}"
        )

    pipelines = amo.get("/api/v4/leads/pipelines") or {}
    pl_list = pipelines.get("_embedded", {}).get("pipelines", [])
    pl = next((p for p in pl_list if p.get("id") == settings.pipeline_sales), None)
    if pl is None:
        raise SchemaError(f"воронка pipeline_sales={settings.pipeline_sales} не найдена в amoCRM")

    statuses = pl.get("_embedded", {}).get("statuses", [])
    status_ids = {s.get("id") for s in statuses}
    missing_statuses = [sid for sid in STAGE_NAMES if sid not in status_ids]
    if settings.status_won not in status_ids or settings.status_lost not in status_ids:
        raise SchemaError(
            f"статусы Успех/Провал ({settings.status_won}/{settings.status_lost}) "
            f"не найдены в воронке {settings.pipeline_sales}"
        )
    if missing_statuses:
        warnings.append(f"статусы не найдены в воронке (возможно переименованы/удалены): {missing_statuses}")

    users_data = amo.get("/api/v4/users", {"limit": 250}) or {}
    user_ids = {u.get("id") for u in users_data.get("_embedded", {}).get("users", [])}
    missing_users = settings.allowed_manager_id_set - user_ids
    if missing_users:
        raise SchemaError(f"менеджеры не найдены как пользователи amoCRM: {missing_users}")

    fields_data = amo.get("/api/v4/leads/custom_fields", {"limit": 250}) or {}
    fields = {f.get("id"): f for f in fields_data.get("_embedded", {}).get("custom_fields", [])}

    required_fields = {
        "Источник": settings.field_source,
        "Тип оплаты": settings.field_payment_type,
        "Авто в обмен": settings.field_trade_in,
        "Причина провала": settings.field_loss_reason,
        "Согласовано": settings.field_agreed,
    }
    for label, fid in required_fields.items():
        if fid not in fields:
            raise SchemaError(f"custom field «{label}» (id={fid}) не найден в amoCRM")

    payment_values = _custom_field_enum_values(fields[settings.field_payment_type])
    if settings.payment_cash_value not in payment_values:
        raise SchemaError(
            f"в поле «Тип оплаты» нет варианта «{settings.payment_cash_value}» "
            f"(доступно: {payment_values})"
        )
    if not (settings.payment_invoice_value_set & payment_values):
        raise SchemaError(
            f"в поле «Тип оплаты» нет ни одного варианта из {settings.payment_invoice_value_set} "
            f"(доступно: {payment_values})"
        )

    tradein_values = _custom_field_enum_values(fields[settings.field_trade_in])
    if settings.trade_in_yes_value not in tradein_values:
        raise SchemaError(
            f"в поле «Авто в обмен» нет варианта «{settings.trade_in_yes_value}» "
            f"(доступно: {tradein_values})"
        )

    return warnings


# ──────────────────────────── amoCRM fetchers ────────────────────────────

def _cfv_text(lead: dict, field_id: int) -> str | None:
    """Первое текстовое значение custom field (для select/text полей)."""
    for cf in lead.get("custom_fields_values") or []:
        if cf.get("field_id") == field_id:
            values = cf.get("values") or []
            if values and values[0].get("value") is not None:
                return str(values[0]["value"]).strip() or None
    return None


def _cfv_bool(lead: dict, field_id: int) -> bool:
    """Значение checkbox-поля (True/False/"1"/"true")."""
    for cf in lead.get("custom_fields_values") or []:
        if cf.get("field_id") == field_id:
            values = cf.get("values") or []
            if values:
                v = values[0].get("value")
                if isinstance(v, bool):
                    return v
                return str(v).strip().lower() in ("1", "true", "да")
    return False


def _fetch_sales_leads(amo: AmoClient) -> list[dict]:
    """Все сделки воронки «Продажи» на трёх менеджеров (текущая выборка)."""
    params: dict[str, object] = {
        "filter[pipeline_id]": settings.pipeline_sales,
        "limit": 250,
    }
    for i, uid in enumerate(sorted(settings.allowed_manager_id_set)):
        params[f"filter[responsible_user_id][{i}]"] = uid
    return list(amo.paginate("/api/v4/leads", "leads", params))


def _fetch_status_events(amo: AmoClient, from_ts: int, to_ts: int) -> list[dict]:
    params = {
        "filter[entity][0]": "lead",
        "filter[type][0]": "lead_status_changed",
        "filter[created_at][from]": from_ts,
        "filter[created_at][to]": to_ts,
        "limit": 250,
    }
    try:
        return list(amo.paginate("/api/v4/events", "events", params))
    except AmoError as exc:
        log.warning("events fetch %s..%s: %s", from_ts, to_ts, exc)
        return []


# ──────────────────────────── persist raw snapshots ────────────────────────────

def _upsert_lead_snapshots(db, leads: list[dict]) -> set[int]:
    seen_ids: set[int] = set()
    rows = []
    for l in leads:
        lid = l.get("id")
        if not lid:
            continue
        seen_ids.add(lid)
        rows.append({
            "lead_id": lid,
            "pipeline_id": l.get("pipeline_id") or 0,
            "status_id": l.get("status_id") or 0,
            "responsible_user_id": l.get("responsible_user_id"),
            "created_at": l.get("created_at") or 0,
            "updated_at": l.get("updated_at") or 0,
            "closed_at": l.get("closed_at"),
            "price": int(l.get("price") or 0),
            "source": _cfv_text(l, settings.field_source),
            "payment_type": _cfv_text(l, settings.field_payment_type),
            "trade_in": _cfv_text(l, settings.field_trade_in) == settings.trade_in_yes_value,
            "agreed": _cfv_bool(l, settings.field_agreed),
            "loss_reason": _cfv_text(l, settings.field_loss_reason),
            "is_deleted": False,
        })
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        if not chunk:
            continue
        stmt = pg_insert(LeadSnapshot).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LeadSnapshot.lead_id],
            set_={
                "pipeline_id": stmt.excluded.pipeline_id,
                "status_id": stmt.excluded.status_id,
                "responsible_user_id": stmt.excluded.responsible_user_id,
                "created_at": stmt.excluded.created_at,
                "updated_at": stmt.excluded.updated_at,
                "closed_at": stmt.excluded.closed_at,
                "price": stmt.excluded.price,
                "source": stmt.excluded.source,
                "payment_type": stmt.excluded.payment_type,
                "trade_in": stmt.excluded.trade_in,
                "agreed": stmt.excluded.agreed,
                "loss_reason": stmt.excluded.loss_reason,
                "is_deleted": False,
            },
        )
        db.execute(stmt)
    db.commit()

    # Сделки, которые перестали попадать в выборку (сменили воронку/ответственного
    # за пределы whitelist), исключаем из активной агрегации, но не удаляем историю.
    if seen_ids:
        db.execute(
            LeadSnapshot.__table__.update()
            .where(LeadSnapshot.lead_id.not_in(seen_ids))
            .values(is_deleted=True)
        )
        db.commit()
    return seen_ids


def _upsert_stage_events(db, events: list[dict], member_ids: set[int]) -> int:
    # (lead_id, status_id) -> самый ранний известный момент достижения.
    # Дедуп ОБЯЗАТЕЛЕН: одна и та же сделка может несколько раз попадать в тот
    # же статус (reopen/откат), а Postgres не разрешает ON CONFLICT DO UPDATE
    # затронуть одну строку дважды в пределах одного INSERT.
    dedup: dict[tuple[int, int], dict] = {}
    for e in events:
        lead_id = e.get("entity_id")
        if lead_id not in member_ids:
            continue
        changes = e.get("value_after") or []
        if not changes:
            continue
        lead_status = (changes[0] or {}).get("lead_status") or {}
        status_id = lead_status.get("id")
        pipeline_id = lead_status.get("pipeline_id")
        created_at = e.get("created_at")
        if not (status_id and pipeline_id and created_at):
            continue
        key = (lead_id, status_id)
        prev = dedup.get(key)
        if prev is None or created_at < prev["first_reached_at"]:
            dedup[key] = {
                "lead_id": lead_id,
                "pipeline_id": pipeline_id,
                "status_id": status_id,
                "first_reached_at": created_at,
            }
    rows = list(dedup.values())
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        if not chunk:
            continue
        stmt = pg_insert(StageReached).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stage_reached",
            set_={"first_reached_at": func_min_first_reached(stmt)},
        )
        db.execute(stmt)
        n += len(chunk)
    db.commit()
    return n


def func_min_first_reached(stmt):
    """LEAST(текущее значение, новое) — храним самый ранний момент достижения."""
    return func.least(StageReached.first_reached_at, stmt.excluded.first_reached_at)


# ──────────────────────────── build DAILY from stored snapshots ────────────────────────────

def with_implied_visits(reached: set[int]) -> set[int]:
    """Достроить этапы визита там, где менеджер их пропустил в CRM.

    Машину нельзя выдать (или согласовать покупку) без визита в салон, а по
    «Сарафану» сделку часто тащат сразу в «Успех», минуя этапы. Поэтому Успех
    и «Покупка согласована» подразумевают оба этапа визита, а состоявшийся
    визит — что он был запланирован.
    """
    out = set(reached)
    if out & {settings.status_won, settings.status_purchase_agreed}:
        out |= {settings.status_visit_planned, settings.status_visit_done}
    if settings.status_visit_done in out:
        out.add(settings.status_visit_planned)
    return out


def _empty_bucket() -> dict:
    return {
        "traffic": 0, "cohort_won": 0, "cohort_lost": 0, "active": 0, "agreed": 0,
        "won": 0, "lost": 0, "cash": 0, "invoice": 0, "tradein": 0, "tradein_won": 0,
        "cycle_sum": 0.0, "cycle_n": 0,
        "src": defaultdict(int), "src_won": defaultdict(int),
        "stage": defaultdict(int), "loss": defaultdict(int),
        "mgr": {k: {
            "traffic": 0, "cohort_won": 0, "cohort_lost": 0, "active": 0, "agreed": 0,
            "won": 0, "lost": 0, "cash": 0, "tradein": 0, "tradein_won": 0,
            "visit_planned": 0, "visit_done": 0,
            "cycle_sum": 0.0, "cycle_n": 0,
        } for k in MANAGER_KEYS},
    }


def build_daily(db) -> dict:
    """Полная пересборка объекта DAILY из lead_snapshot + stage_reached.

    Идемпотентно и без накопленных искажений: каждый прогон читает текущее
    состояние всех сделок и историю смен этапов заново, старые значения не
    «доживают» после reopen / смены ответственного / правки поля.
    """
    mgr_map = settings.manager_key_map_dict

    leads = db.execute(
        select(LeadSnapshot).where(LeadSnapshot.is_deleted == False)  # noqa: E712
    ).scalars().all()

    stage_rows = db.execute(select(StageReached)).scalars().all()
    stages_by_lead: dict[int, set[int]] = defaultdict(set)
    for r in stage_rows:
        stages_by_lead[r.lead_id].add(r.status_id)

    days: dict[str, dict] = {}

    def bucket(day: str) -> dict:
        if day not in days:
            days[day] = _empty_bucket()
        return days[day]

    for lead in leads:
        mgr_key = mgr_map.get(lead.responsible_user_id or -1)
        day_created = _msk_day(lead.created_at)
        if not day_created:
            continue
        b = bucket(day_created)
        b["traffic"] += 1
        if lead.source:
            b["src"][lead.source] += 1
        if lead.trade_in:
            b["tradein"] += 1
        if lead.agreed:
            b["agreed"] += 1

        is_won = lead.status_id == settings.status_won
        is_lost = lead.status_id == settings.status_lost
        if is_won:
            b["cohort_won"] += 1
        elif is_lost:
            b["cohort_lost"] += 1
        else:
            b["active"] += 1

        reached = with_implied_visits(stages_by_lead.get(lead.lead_id, set()) | {lead.status_id})
        for sid in reached:
            name = STAGE_NAMES.get(sid)
            if name:
                b["stage"][name] += 1

        if mgr_key:
            m = b["mgr"][mgr_key]
            m["traffic"] += 1
            if lead.trade_in:
                m["tradein"] += 1
            if lead.agreed:
                m["agreed"] += 1
            if settings.status_visit_planned in reached:
                m["visit_planned"] += 1
            if settings.status_visit_done in reached:
                m["visit_done"] += 1
            if is_won:
                m["cohort_won"] += 1
            elif is_lost:
                m["cohort_lost"] += 1
            else:
                m["active"] += 1

        # Результаты — по дате закрытия (не смешиваем со «входящим трафиком»).
        if lead.closed_at and (is_won or is_lost):
            day_closed = _msk_day(lead.closed_at)
            if day_closed:
                bc = bucket(day_closed)
                cycle_days = max(0.0, (lead.closed_at - lead.created_at) / 86400.0)
                bc["cycle_sum"] += cycle_days
                bc["cycle_n"] += 1
                if mgr_key:
                    mc = bc["mgr"][mgr_key]
                    mc["cycle_sum"] += cycle_days
                    mc["cycle_n"] += 1
                if is_won:
                    bc["won"] += 1
                    bc["src_won"][lead.source or "Не указан"] += 1
                    if lead.payment_type == settings.payment_cash_value:
                        bc["cash"] += 1
                    elif lead.payment_type in settings.payment_invoice_value_set:
                        bc["invoice"] += 1
                    if lead.trade_in:
                        bc["tradein_won"] += 1
                    if mgr_key:
                        mc["won"] += 1
                        if lead.payment_type == settings.payment_cash_value:
                            mc["cash"] += 1
                        if lead.trade_in:
                            mc["tradein_won"] += 1
                else:
                    bc["lost"] += 1
                    reason = lead.loss_reason or "Не указана"
                    bc["loss"][reason] += 1
                    if mgr_key:
                        mc["lost"] += 1

    # defaultdict -> обычный dict для чистого JSON
    out: dict[str, dict] = {}
    for day, b in days.items():
        b["src"] = dict(b["src"])
        b["src_won"] = dict(b["src_won"])
        b["stage"] = dict(b["stage"])
        b["loss"] = dict(b["loss"])
        out[day] = b
    return out


def _stage_reliable_from(db) -> str | None:
    row = db.execute(select(StageReached.first_reached_at).order_by(StageReached.first_reached_at.asc())).first()
    if not row or not row[0]:
        return None
    return _msk_day(row[0])


# ──────────────────────────── sync_state helpers ────────────────────────────

def _set_state(db, key: str, value: str) -> None:
    stmt = pg_insert(SyncState).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SyncState.key],
        set_={"value": value, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()


def _get_state(db, key: str) -> str | None:
    row = db.get(SyncState, key)
    return row.value if row else None


def _save_daily_snapshot(db, daily: dict, stage_reliable_from: str | None) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage_reliable_from": stage_reliable_from,
        "days": daily,
    }
    stmt = pg_insert(Snapshot).values(section="daily", payload=payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Snapshot.section],
        set_={"payload": payload, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()


# ──────────────────────────── orchestration ────────────────────────────

def run_collection() -> None:
    """Один прогон: валидация схемы → full refetch сделок → окно событий →
    один шаг бэкфилла → полная пересборка DAILY."""
    init_db()
    if not settings.amo_access_token:
        log.error("AMO_ACCESS_TOKEN пуст — сбор пропущен")
        return

    amo = AmoClient()
    db = SessionLocal()
    started = datetime.now(timezone.utc)
    try:
        try:
            warnings = validate_schema(amo)
            for w in warnings:
                log.warning("schema warning: %s", w)
            _set_state(db, "schema_ok", "true")
        except SchemaError as exc:
            log.error("SCHEMA MISMATCH — сбор остановлен: %s", exc)
            _set_state(db, "schema_ok", "false")
            _set_state(db, "last_error", f"{datetime.now(timezone.utc).isoformat()} SchemaError: {exc}")
            return

        leads = _fetch_sales_leads(amo)
        member_ids = _upsert_lead_snapshots(db, leads)
        log.info("синхронизировано сделок: %d", len(member_ids))

        now = _now_msk()
        win_from = (now - timedelta(days=EVENT_REFRESH_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        events = _fetch_status_events(amo, _ts(win_from), _ts(now))
        n_ev = _upsert_stage_events(db, events, member_ids)
        log.info("окно событий этапов: %d записей", n_ev)

        _backfill_step(amo, db, member_ids, now, leads)

        daily = build_daily(db)
        reliable_from = _stage_reliable_from(db)
        _save_daily_snapshot(db, daily, reliable_from)

        _set_state(db, "last_success_at", datetime.now(timezone.utc).isoformat())
        _set_state(db, "last_error", "")
        log.info("collection ok in %.1fs (%d дней в DAILY)",
                  (datetime.now(timezone.utc) - started).total_seconds(), len(daily))

    except Exception as exc:  # noqa: BLE001
        log.exception("collection failed: %s", exc)
        db.rollback()  # без rollback сессия остаётся в aborted-транзакции и следующий execute тоже упадёт
        try:
            _set_state(db, "last_error", f"{datetime.now(timezone.utc).isoformat()} {exc}")
        except Exception:  # noqa: BLE001
            log.exception("не удалось записать last_error")
        raise
    finally:
        db.close()
        amo.close()


def _oldest_lead_created_at(leads: list[dict]) -> int | None:
    ts = [l.get("created_at") for l in leads if l.get("created_at")]
    return min(ts) if ts else None


def _backfill_cursor(db, now: datetime) -> datetime:
    raw = _get_state(db, "backfill_done_from")
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=MSK)
        except ValueError:
            pass
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _backfill_step(amo: AmoClient, db, member_ids: set[int], now: datetime, leads: list[dict]) -> bool:
    """Один более ранний месяц событий за прогон. Возвращает True, если ещё есть что бэкфиллить."""
    oldest_ts = _oldest_lead_created_at(leads)
    target_start = _month_start(now) - timedelta(days=BACKFILL_MONTHS_CAP * 31)
    target_start = _month_start(target_start)
    if oldest_ts:
        oldest_dt = datetime.fromtimestamp(oldest_ts, MSK)
        target_start = max(target_start, _month_start(oldest_dt))

    done_from = _backfill_cursor(db, now)
    if done_from <= target_start:
        _set_state(db, "backfill_complete", "true")
        return False

    month_start = _month_start(done_from - timedelta(days=1))
    month_end = done_from - timedelta(seconds=1)

    events = _fetch_status_events(amo, _ts(month_start), _ts(month_end))
    n = _upsert_stage_events(db, events, member_ids)
    _set_state(db, "backfill_done_from", month_start.isoformat())
    log.info("backfill %s: %d событий этапов", month_start.strftime("%Y-%m"), n)
    return True


def run_backfill_all() -> None:
    """Разовый полный бэкфилл истории этапов (до BACKFILL_MONTHS_CAP или до
    даты создания самой старой сделки — что раньше).

    Запуск на сервере (worker должен быть остановлен, чтобы не гонять
    параллельно с обычным run_collection):
        docker compose stop worker
        docker compose run --rm worker python -m app.collector backfill
        docker compose start worker
    """
    init_db()
    if not settings.amo_access_token:
        log.error("AMO_ACCESS_TOKEN пуст — бэкфилл пропущен")
        return
    amo = AmoClient()
    db = SessionLocal()
    try:
        warnings = validate_schema(amo)
        for w in warnings:
            log.warning("schema warning: %s", w)
        leads = _fetch_sales_leads(amo)
        member_ids = _upsert_lead_snapshots(db, leads)
        now = _now_msk()
        for _ in range(BACKFILL_MONTHS_CAP + 2):
            more = _backfill_step(amo, db, member_ids, now, leads)
            if not more:
                log.info("бэкфилл завершён")
                break
        daily = build_daily(db)
        reliable_from = _stage_reliable_from(db)
        _save_daily_snapshot(db, daily, reliable_from)
        _set_state(db, "last_success_at", datetime.now(timezone.utc).isoformat())
    except SchemaError as exc:
        log.error("SCHEMA MISMATCH — бэкфилл остановлен: %s", exc)
    finally:
        db.close()
        amo.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        run_backfill_all()
    else:
        run_collection()
