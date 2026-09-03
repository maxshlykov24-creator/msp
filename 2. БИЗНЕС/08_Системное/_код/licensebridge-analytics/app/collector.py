"""Сбор среза Kommo → snapshot.json.

Принцип: каждая цифра дашборда должна быть выводима из Kommo, поэтому здесь
только выгрузка и детерминированный пересчёт, без «досчётов на глаз» и без
кэшей, которые могли бы разъехаться с CRM. Объём аккаунта небольшой
(~1.7 тыс. сделок, ~8 тыс. событий), поэтому каждый прогон — полная
перевыгрузка: дрейфа между CRM и дашбордом быть не может.

Правила метрик зафиксированы в
`2. БИЗНЕС/04_Производство/Активные/LicenseBridge_USA/дашборд/ПАСПОРТ_МЕТРИК.md`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import (
    CHANNEL_JUNK,
    FB_ALIASES,
    FORMER_USERS,
    SLA_DAYS,
    TALK_ORIGINS,
    TALK_ORIGINS_NO_SUFFIX,
    UNKNOWN_USER_LABEL,
    settings,
)
from app.kommo import KommoClient

log = logging.getLogger("collector")

WON_DEFINITION = (
    "Первое из двух: переход в Closed-won (status 142) внутри Pipeline или "
    "перенос сделки в воронку Сборка. Продажу оформляют и тем, и другим способом: "
    "из 70 продаж 49 прошли только через Сборку, и до 03.09.2026 дашборд их не видел."
)
WON_ATTRIBUTION = (
    "ответственный на момент продажи: перехода в status 142 или переноса в Сборку "
    "(entity_responsible_changed ≤ won_at)"
)


class SchemaError(Exception):
    """Структура Kommo не совпадает с ожидаемой — публиковать срез нельзя."""


def _biz_tz() -> ZoneInfo:
    return ZoneInfo(settings.business_tz)


def _day(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, _biz_tz()).strftime("%Y-%m-%d")


# ──────────────────────────── валидация схемы ────────────────────────────


def validate_schema(client: KommoClient) -> tuple[dict[int, str], dict[int, dict[str, Any]]]:
    """Жёсткая проверка перед сбором: тот ли аккаунт, есть ли обе воронки и
    служебные статусы. Возвращает (карта статусов, карта воронок)."""
    account = client.account()
    if account.get("id") != settings.kommo_account_id:
        raise SchemaError(
            f"account_id не совпадает: ожидали {settings.kommo_account_id}, "
            f"получили {account.get('id')}"
        )

    pipelines = {p["id"]: p for p in client.pipelines()}
    for pid, label in (
        (settings.pipeline_main, "Pipeline"),
        (settings.pipeline_sborka, "Сборка"),
    ):
        if pid not in pipelines:
            raise SchemaError(f"воронка {label} (id={pid}) не найдена в Kommo")

    status_names: dict[int, str] = {}
    for pid, pipeline in pipelines.items():
        for status in pipeline.get("_embedded", {}).get("statuses", []):
            status_names[(pid, status["id"])] = status["name"]

    main_statuses = {
        s["id"] for s in pipelines[settings.pipeline_main]["_embedded"]["statuses"]
    }
    if settings.status_won not in main_statuses or settings.status_lost not in main_statuses:
        raise SchemaError("в воронке Pipeline нет служебных статусов 142/143")

    unknown_sla = set(SLA_DAYS) - {
        s["name"] for s in pipelines[settings.pipeline_main]["_embedded"]["statuses"]
    }
    if unknown_sla:
        raise SchemaError(
            f"этапы из норм SLA отсутствуют в воронке (переименованы?): {sorted(unknown_sla)}"
        )
    return status_names, pipelines


# ──────────────────────────── источник лида ────────────────────────────


def _custom_field(lead: dict[str, Any], code: str) -> str:
    for field in lead.get("custom_fields_values") or []:
        if field.get("field_code") == code:
            values = field.get("values") or []
            if values:
                return str(values[0].get("value") or "").strip()
    return ""


def lead_source(lead: dict[str, Any], talk_origin: str | None) -> str:
    """Комбинированный источник: FB-тег → Channel → utm_source → первый чат.

    Порядок подобран сверкой с согласованным срезом 22.07.2026: он даёт то же
    распределение каналов (расхождение 11 сделок из 1611 — те, у кого источник
    реально изменился в CRM). Тег `fb…` ставит интеграция Facebook Ads, и он
    точнее канала переписки: пришедший с рекламы клиент потом пишет в e-chat.
    Чат берём самый ранний (первое обращение) и помечаем «(talk)».
    Покрытие на срезе — 92%; см. ПАСПОРТ_МЕТРИК.md §2.2.
    """
    for tag in lead.get("_embedded", {}).get("tags") or []:
        if str(tag.get("name") or "").lower().startswith("fb"):
            return "Facebook"

    for code in ("APPEAL_CHANNEL", "UTM_SOURCE"):
        value = _custom_field(lead, code)
        low = value.lower()
        if not value or low in CHANNEL_JUNK:
            continue
        return "Facebook" if low in FB_ALIASES else value

    if talk_origin:
        label = TALK_ORIGINS.get(talk_origin, talk_origin)
        return label if talk_origin in TALK_ORIGINS_NO_SUFFIX else f"{label} (talk)"
    return ""


# ──────────────────────────── история ответственных ────────────────────────────


class ResponsibleHistory:
    """Кто был ответственным по сделке в момент T.

    Текущее поле `responsible_user_id` для истории не годится: 82.8% отказов
    закреплены не на том, кто вёл сделку при закрытии (ПАСПОРТ_МЕТРИК.md §2.3).
    """

    def __init__(self) -> None:
        self._by_lead: dict[int, list[tuple[int, int | None, int | None]]] = {}

    def add(self, lead_id: int, ts: int, before: int | None, after: int | None) -> None:
        self._by_lead.setdefault(lead_id, []).append((ts, before, after))

    def sort(self) -> None:
        for events in self._by_lead.values():
            events.sort(key=lambda x: x[0])

    def at(self, lead_id: int, ts: int | None, current: int | None) -> int | None:
        events = self._by_lead.get(lead_id)
        if not events or ts is None:
            return current
        result: int | None = None
        found = False
        for event_ts, before, after in events:
            if event_ts <= ts:
                result = after
                found = True
            else:
                if not found:
                    # момент раньше первой смены — ответственным был тот, с кого сменили
                    return before
                break
        return result if found else current


# ──────────────────────────── сбор ────────────────────────────


def collect(client: KommoClient) -> dict[str, Any]:
    status_names, _pipelines = validate_schema(client)
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    active_users = {u["id"]: u.get("name") or f"user {u['id']}" for u in client.users()}
    loss_reasons = {r["id"]: r["name"] for r in client.loss_reasons()}

    def user_name(uid: int | None) -> str:
        """Имя ответственного. Уволенных Kommo из /users убирает (404), но в
        истории они остаются — иначе больше 80% старых отказов стали бы
        безымянными и разрез «кто терял» потерял бы смысл."""
        if not uid:
            return ""
        if uid in active_users:
            return active_users[uid]
        return FORMER_USERS.get(uid, UNKNOWN_USER_LABEL)

    leads = [
        lead
        for lead in client.paginate(
            "/leads",
            {
                "filter[pipeline_id][]": [settings.pipeline_main, settings.pipeline_sborka],
            },
        )
        if not lead.get("is_deleted")
    ]
    if not leads:
        raise SchemaError("Kommo вернул пустой список сделок — срез не публикуем")

    # открытые задачи → «без задачи» / «просрочка»
    open_task = set()
    overdue_task = set()
    for task in client.paginate("/tasks", {"filter[is_completed]": 0}):
        if task.get("entity_type") != "leads":
            continue
        lead_id = task.get("entity_id")
        open_task.add(lead_id)
        if (task.get("complete_till") or 0) and task["complete_till"] < now_ts:
            overdue_task.add(lead_id)

    # чаты → источник последнего резерва; берём самый ранний диалог по сделке
    first_talk: dict[int, tuple[int, str]] = {}
    for talk in client.paginate("/talks"):
        if talk.get("entity_type") != "lead" or not talk.get("entity_id"):
            continue
        created = talk.get("created_at") or 0
        current = first_talk.get(talk["entity_id"])
        if current is None or created < current[0]:
            first_talk[talk["entity_id"]] = (created, talk.get("origin") or "")
    talk_origin = {lead_id: origin for lead_id, (_, origin) in first_talk.items()}

    # Актуальные этапы основной воронки (без служебных 142/143). Старые/удалённые
    # status_id из истории в «где теряются» не попадают.
    main_status_order = [
        s
        for s in _pipelines[settings.pipeline_main]["_embedded"]["statuses"]
        if s["id"] not in (settings.status_won, settings.status_lost)
    ]
    known_main_statuses = {s["id"] for s in main_status_order}

    # события смены этапа: возраст на этапе, переходы Pipeline → Сборка,
    # первый переход в 142, этап перед отказом (ПАСПОРТ_МЕТРИК.md §2.4)
    stage_since: dict[int, int] = {}
    to_sborka: dict[int, int] = {}
    to_won_status: dict[int, int] = {}
    lost_from: dict[int, tuple[int, int]] = {}  # lead_id → (ts, status_id)
    for event in client.paginate(
        "/events", {"filter[type][]": "lead_status_changed", "filter[entity][]": "lead"}
    ):
        lead_id = event.get("entity_id")
        ts = event.get("created_at") or 0
        if not lead_id or not ts:
            continue
        if ts > stage_since.get(lead_id, 0):
            stage_since[lead_id] = ts
        after = (event.get("value_after") or [{}])[0].get("lead_status") or {}
        before = (event.get("value_before") or [{}])[0].get("lead_status") or {}
        # «В производстве» датируем ПЕРВЫМ переходом в «Сборку».
        if (
            before.get("pipeline_id") == settings.pipeline_main
            and after.get("pipeline_id") == settings.pipeline_sborka
            and ts < to_sborka.get(lead_id, ts + 1)
        ):
            to_sborka[lead_id] = ts
        if (
            after.get("pipeline_id") == settings.pipeline_main
            and after.get("id") == settings.status_won
            and ts < to_won_status.get(lead_id, ts + 1)
        ):
            to_won_status[lead_id] = ts

        # Отказ: ПОСЛЕДНИЙ переход в 143 — этап, с которого реально ушли.
        if (
            after.get("pipeline_id") == settings.pipeline_main
            and after.get("id") == settings.status_lost
            and before.get("pipeline_id") == settings.pipeline_main
            and before.get("id") in known_main_statuses
        ):
            prev = lost_from.get(lead_id)
            if prev is None or ts >= prev[0]:
                lost_from[lead_id] = (ts, int(before["id"]))

    responsible = ResponsibleHistory()
    for event in client.paginate(
        "/events", {"filter[type][]": "entity_responsible_changed", "filter[entity][]": "lead"}
    ):
        lead_id = event.get("entity_id")
        ts = event.get("created_at") or 0
        if not lead_id or not ts:
            continue
        after = (event.get("value_after") or [{}])[0].get("responsible_user") or {}
        before = (event.get("value_before") or [{}])[0].get("responsible_user") or {}
        responsible.add(lead_id, ts, before.get("id"), after.get("id"))
    responsible.sort()

    # ── сборка leads[] ──
    out_leads: list[dict[str, Any]] = []
    funnel_counts: dict[int, int] = {}
    for lead in leads:
        lead_id = lead["id"]
        pipeline_id = lead["pipeline_id"]
        status_id = lead["status_id"]
        closed_at = lead.get("closed_at") or None
        is_open = status_id not in (settings.status_won, settings.status_lost)
        stage_name = status_names.get((pipeline_id, status_id), f"status {status_id}")

        stage_days = sla_days = None
        stuck = False
        if is_open:
            since = stage_since.get(lead_id) or lead.get("created_at")
            if since:
                stage_days = round((now_ts - since) / 86400, 1)
            sla_days = SLA_DAYS.get(stage_name)
            stuck = bool(sla_days and stage_days is not None and stage_days > sla_days)
            if pipeline_id == settings.pipeline_main:
                funnel_counts[status_id] = funnel_counts.get(status_id, 0) + 1

        at_close = responsible.at(lead_id, closed_at, lead.get("responsible_user_id"))
        lost_stage = ""
        if status_id == settings.status_lost and lead_id in lost_from:
            lost_stage = status_names.get(
                (settings.pipeline_main, lost_from[lead_id][1]), ""
            )

        row: dict[str, Any] = {
            "id": lead_id,
            "pipeline_id": pipeline_id,
            "status_id": status_id,
            "responsible_id": lead.get("responsible_user_id"),
            "created": _day(lead.get("created_at")),
            "closed": _day(closed_at) if not is_open else None,
            "price": lead.get("price") or 0,
            "responsible_name": user_name(lead.get("responsible_user_id")),
            "responsible_at_close_name": user_name(at_close) if closed_at else "",
            "stage_name": stage_name,
            "loss_reason": loss_reasons.get(lead.get("loss_reason_id"), ""),
            "lost_from_stage": lost_stage,
            "source": lead_source(lead, talk_origin.get(lead_id)),
            "has_open_task": lead_id in open_task,
            "has_overdue_task": lead_id in overdue_task,
            "stage_days": stage_days,
            "sla_days": sla_days,
            "stuck": stuck,
        }
        if pipeline_id == settings.pipeline_sborka:
            row["is_sborka"] = True
            row["won_at"] = _day(to_sborka.get(lead_id))
        out_leads.append(row)

    # ── wins[] = продажа: 142 внутри Pipeline либо перенос в «Сборку» ──
    # Считаем по тому событию, которое случилось раньше: у восьми сделок есть оба,
    # и по одному признаку продажа задвоилась бы.
    price_by_lead = {lead["id"]: lead.get("price") or 0 for lead in leads}
    won_at: dict[int, tuple[int, str]] = {}
    for lead_id, ts in to_won_status.items():
        won_at[lead_id] = (ts, "pipeline_status_142")
    for lead_id, ts in to_sborka.items():
        known = won_at.get(lead_id)
        if known is None or ts < known[0]:
            won_at[lead_id] = (ts, "moved_to_sborka")
    wins = []
    for lead_id, (ts, method) in sorted(won_at.items(), key=lambda kv: kv[1][0]):
        if lead_id not in price_by_lead:
            continue
        owner_id = responsible.at(lead_id, ts, None)
        wins.append(
            {
                "date": _day(ts),
                "lead_id": lead_id,
                "price": price_by_lead.get(lead_id, 0),
                "responsible_name": user_name(owner_id),
                "responsible_id": owner_id,
                "method": method,
            }
        )
    won_dates = [w["date"] for w in wins if w.get("date")]
    won_comparable_from = min(won_dates) if won_dates else None

    funnel_open = [
        {"id": s["id"], "name": s["name"], "value": funnel_counts.get(s["id"], 0)}
        for s in sorted(main_status_order, key=lambda s: s["sort"])
    ]

    created_days = sorted(d for d in (lead["created"] for lead in out_leads) if d)
    now_biz = now.astimezone(_biz_tz())
    now_msk = now.astimezone(ZoneInfo(settings.tz))
    snapshot = {
        "meta": {
            "source": "licensebridgeusa.kommo.com",
            "asOf": now_biz.strftime("%Y-%m-%d"),
            "minDate": created_days[0] if created_days else now_biz.strftime("%Y-%m-%d"),
            "maxDate": now_biz.strftime("%Y-%m-%d"),
            "updatedAt": now_msk.isoformat(timespec="seconds"),
            "updatedAtLabel": now_msk.strftime("%d.%m.%Y, %H:%M"),
            "leadsGrain": "lead",
            "leadsPipelines": [settings.pipeline_main, settings.pipeline_sborka],
            "leadsCount": len(out_leads),
            "wonDefinition": WON_DEFINITION,
            "wonAttribution": WON_ATTRIBUTION,
            "wonComparableFrom": won_comparable_from,
            "businessTz": settings.business_tz,
        },
        "users": {
            **{str(uid): {"name": name, "role": "активный"} for uid, name in active_users.items()},
            **{str(uid): {"name": name, "role": "бывший"} for uid, name in FORMER_USERS.items()},
        },
        "funnel_open": funnel_open,
        "leads": out_leads,
        "wins": wins,
        "periodDefaults": {"default": "quarter"},
    }
    log.info(
        "срез собран: %s сделок, %s wins, открытых в Pipeline %s",
        len(out_leads),
        len(wins),
        sum(funnel_counts.values()),
    )
    return snapshot


# ──────────────────────────── публикация ────────────────────────────


def snapshot_path() -> Path:
    return Path(settings.data_dir) / "snapshot.json"


def state_path() -> Path:
    return Path(settings.data_dir) / "state.json"


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_collection() -> dict[str, Any]:
    """Один прогон. При любой ошибке прежний snapshot остаётся нетронутым —
    лучше показать честно устаревшие данные с баннером, чем неполные."""
    started = datetime.now(timezone.utc)
    state = read_state()
    try:
        with KommoClient() as client:
            snapshot = collect(client)
        _write_atomic(snapshot_path(), snapshot)
        state.update(
            {
                "last_success_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_error": None,
                "schema_ok": True,
                "leads_count": snapshot["meta"]["leadsCount"],
                "duration_sec": round(
                    (datetime.now(timezone.utc) - started).total_seconds(), 1
                ),
            }
        )
        _write_atomic(state_path(), state)
        return state
    except Exception as e:  # noqa: BLE001 — любая ошибка не должна ронять сервис
        log.exception("сбор не удался")
        state.update(
            {
                "last_error": f"{type(e).__name__}: {e}"[:500],
                "last_error_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "schema_ok": not isinstance(e, SchemaError),
            }
        )
        _write_atomic(state_path(), state)
        return state
