"""Авто-раскатка feature-flags по этапам.

Владелец не принимает решение на каждом шаге: сервер сам двигается по лестнице
этапов, если прошло контрольное время И нет признаков сбоя. Любая проблема
(dead-letter, рост очереди, отказ preflight) останавливает продвижение на текущем
этапе — назад не откатываемся автоматически, чтобы поведение не «мигало».

Лестница:
    0 shadow            — решения считаются, Kommo не меняется (кроме приёма лидов)
    1 assignment        — распределение новых лидов (обратимо: смена ответственного)
    2 deals             — дубль-сделки: перенос примечаний + удаление, свёртка в «Сборку»
    3 contacts_no_chat  — soft-merge контактов без переписки
    4 contacts_light    — merge контактов с лёгким читаемым чатом (последний шаг)

Ручное управление (без деплоя): POST /internal/rollout {"action": "pause"|"resume"
|"set_stage", "stage": N}. Полный стоп: pause → этап замораживается; для возврата
в shadow — set_stage 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Decision, InboxEvent, RolloutState

log = logging.getLogger("rollout")

STAGE_SHADOW = 0
STAGE_ASSIGNMENT = 1
STAGE_DEALS = 2
STAGE_CONTACTS_NO_CHAT = 3
STAGE_CONTACTS_LIGHT = 4
STAGE_MAX = STAGE_CONTACTS_LIGHT

STAGE_NAMES = {
    STAGE_SHADOW: "shadow",
    STAGE_ASSIGNMENT: "assignment",
    STAGE_DEALS: "deals",
    STAGE_CONTACTS_NO_CHAT: "contacts_no_chat",
    STAGE_CONTACTS_LIGHT: "contacts_light",
}


@dataclass(frozen=True)
class FlagSet:
    shadow: bool
    assignment: bool
    deal_dedup: bool
    cross_funnel: bool
    contact_soft_merge: bool
    light_chat_merge: bool


def flags_for_stage(stage: int) -> FlagSet:
    return FlagSet(
        shadow=stage <= STAGE_SHADOW,
        assignment=stage >= STAGE_ASSIGNMENT,
        deal_dedup=stage >= STAGE_DEALS,
        cross_funnel=stage >= STAGE_DEALS,
        contact_soft_merge=stage >= STAGE_CONTACTS_NO_CHAT,
        light_chat_merge=stage >= STAGE_CONTACTS_LIGHT,
    )


def flags_from_settings() -> FlagSet:
    """Ручной режим (AUTO_ROLLOUT=false) и дефолт для юнит-тестов."""
    return FlagSet(
        shadow=settings.shadow_mode,
        assignment=settings.enable_assignment,
        deal_dedup=settings.enable_deal_dedup,
        cross_funnel=settings.enable_cross_funnel,
        contact_soft_merge=settings.enable_contact_soft_merge,
        light_chat_merge=settings.enable_light_chat_merge,
    )


def get_state(s: Session) -> RolloutState:
    state = s.get(RolloutState, 1)
    if state is None:
        state = RolloutState(id=1, stage=STAGE_SHADOW, paused=False)
        s.add(state)
        s.flush()
    return state


def current_flags(s: Session) -> FlagSet:
    """Действующие флаги. В авто-режиме shadow определяется этапом, а ручные
    ENABLE_* из .env работают как принудительное «включить сейчас» поверх этапа.
    Полный стоп: AUTO_ROLLOUT=false + SHADOW_MODE=true (или set_stage 0 + pause)."""
    if not settings.auto_rollout:
        return flags_from_settings()
    auto = flags_for_stage(get_state(s).stage)
    manual = flags_from_settings()
    return FlagSet(
        shadow=auto.shadow,
        assignment=auto.assignment or manual.assignment,
        deal_dedup=auto.deal_dedup or manual.deal_dedup,
        cross_funnel=auto.cross_funnel or manual.cross_funnel,
        contact_soft_merge=auto.contact_soft_merge or manual.contact_soft_merge,
        light_chat_merge=auto.light_chat_merge or manual.light_chat_merge,
    )


def _stage_hours(stage: int) -> int:
    """Сколько держим этап до перехода на следующий."""
    if stage == STAGE_SHADOW:
        return settings.rollout_shadow_hours
    return settings.rollout_stage_hours


@dataclass(frozen=True)
class Health:
    ok: bool
    reason: str
    deadletter: int
    pending: int
    decisions: int


def health(s: Session) -> Health:
    """Признаки сбоя за окно наблюдения текущего этапа."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.rollout_health_window_hours)
    deadletter = s.scalar(
        select(func.count()).select_from(InboxEvent).where(InboxEvent.status == "deadletter")
    ) or 0
    pending = s.scalar(
        select(func.count()).select_from(InboxEvent).where(InboxEvent.status == "pending")
    ) or 0
    decisions = s.scalar(
        select(func.count()).select_from(Decision).where(Decision.created_at >= since)
    ) or 0
    if deadletter > settings.rollout_max_deadletter:
        return Health(False, f"dead-letter {deadletter}", deadletter, pending, decisions)
    if pending > settings.rollout_max_pending:
        return Health(False, f"очередь {pending}", deadletter, pending, decisions)
    return Health(True, "ok", deadletter, pending, decisions)


def maybe_promote(s: Session) -> RolloutState:
    """Вызывается воркером по таймеру. Двигает этап, если можно."""
    state = get_state(s)
    if not settings.auto_rollout or state.paused or state.stage >= STAGE_MAX:
        return state

    now = datetime.now(timezone.utc)
    entered = state.stage_entered_at or state.updated_at or now
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    hours = (now - entered).total_seconds() / 3600.0
    need = _stage_hours(state.stage)
    if hours < need:
        return state

    h = health(s)
    if not h.ok:
        state.note = f"пауза продвижения: {h.reason}"
        log.warning("rollout hold on stage %s: %s", state.stage, h.reason)
        s.flush()
        return state

    # из shadow не выходим, пока не увидели достаточную выборку решений
    if state.stage == STAGE_SHADOW and h.decisions < settings.rollout_min_decisions:
        state.note = f"shadow: решений {h.decisions} < {settings.rollout_min_decisions}"
        s.flush()
        return state

    prev = state.stage
    state.stage = prev + 1
    state.stage_entered_at = now
    state.note = f"авто-переход {STAGE_NAMES[prev]} → {STAGE_NAMES[state.stage]}"
    s.add(Decision(action="rollout.promote", shadow=False, detail={
        "from": STAGE_NAMES[prev], "to": STAGE_NAMES[state.stage],
        "hours_on_stage": round(hours, 1), "deadletter": h.deadletter,
        "pending": h.pending, "decisions_window": h.decisions,
    }))
    s.flush()
    log.info("rollout %s → %s", STAGE_NAMES[prev], STAGE_NAMES[state.stage])
    return state


def set_stage(s: Session, stage: int, note: str = "ручная установка") -> RolloutState:
    state = get_state(s)
    prev = state.stage
    state.stage = max(STAGE_SHADOW, min(STAGE_MAX, int(stage)))
    state.stage_entered_at = datetime.now(timezone.utc)
    state.note = note
    s.add(Decision(action="rollout.set_stage", shadow=False, detail={
        "from": STAGE_NAMES.get(prev), "to": STAGE_NAMES.get(state.stage), "note": note,
    }))
    s.flush()
    return state


def set_paused(s: Session, paused: bool, note: str = "") -> RolloutState:
    state = get_state(s)
    state.paused = bool(paused)
    state.note = note or ("пауза продвижения" if paused else "продвижение возобновлено")
    s.add(Decision(action="rollout.pause" if paused else "rollout.resume",
                   shadow=False, detail={"note": state.note}))
    s.flush()
    return state


def describe(s: Session) -> dict:
    state = get_state(s)
    h = health(s)
    flags = current_flags(s)
    entered = state.stage_entered_at
    return {
        "stage": state.stage,
        "stage_name": STAGE_NAMES.get(state.stage, "?"),
        "paused": state.paused,
        "auto_rollout": settings.auto_rollout,
        "stage_entered_at": entered.isoformat() if entered else None,
        "next_stage_after_hours": _stage_hours(state.stage) if state.stage < STAGE_MAX else None,
        "note": state.note,
        "health": {"ok": h.ok, "reason": h.reason, "deadletter": h.deadletter,
                   "pending": h.pending, "decisions_window": h.decisions},
        "flags": flags.__dict__,
    }
