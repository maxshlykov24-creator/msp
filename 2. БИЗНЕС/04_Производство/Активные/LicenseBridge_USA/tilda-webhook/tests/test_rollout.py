from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models import Decision, InboxEvent
from app.rollout import (
    STAGE_CONTACTS_LIGHT,
    STAGE_DEALS,
    STAGE_SHADOW,
    current_flags,
    describe,
    flags_for_stage,
    get_state,
    maybe_promote,
    set_paused,
    set_stage,
)


@pytest.fixture()
def auto(monkeypatch):
    monkeypatch.setattr(settings, "auto_rollout", True)
    monkeypatch.setattr(settings, "rollout_shadow_hours", 24)
    monkeypatch.setattr(settings, "rollout_stage_hours", 24)
    monkeypatch.setattr(settings, "rollout_min_decisions", 20)


def _age_stage(s, hours: float) -> None:
    st = get_state(s)
    st.stage_entered_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    s.flush()


def _seed_decisions(s, n: int) -> None:
    for i in range(n):
        s.add(Decision(action="test", shadow=True, detail={"i": i}))
    s.flush()


def test_stage_flags_ladder():
    assert flags_for_stage(STAGE_SHADOW).shadow is True
    assert flags_for_stage(STAGE_DEALS).deal_dedup is True
    assert flags_for_stage(STAGE_DEALS).contact_soft_merge is False
    assert flags_for_stage(STAGE_CONTACTS_LIGHT).light_chat_merge is True


def test_no_promote_before_dwell_time(session, auto):
    _seed_decisions(session, 30)
    _age_stage(session, 5)
    assert maybe_promote(session).stage == STAGE_SHADOW


def test_promote_out_of_shadow_after_time_and_sample(session, auto):
    _seed_decisions(session, 30)
    _age_stage(session, 30)
    state = maybe_promote(session)
    assert state.stage == 1
    assert current_flags(session).shadow is False
    assert current_flags(session).assignment is True


def test_shadow_holds_without_enough_decisions(session, auto):
    _seed_decisions(session, 3)
    _age_stage(session, 30)
    assert maybe_promote(session).stage == STAGE_SHADOW


def test_deadletter_blocks_promotion(session, auto):
    _seed_decisions(session, 30)
    _age_stage(session, 30)
    session.add(InboxEvent(event_key="k1", source="kommo", event_type="add_lead",
                           payload={}, status="deadletter"))
    session.flush()
    state = maybe_promote(session)
    assert state.stage == STAGE_SHADOW
    assert "dead-letter" in (state.note or "")


def test_pause_blocks_and_resume_restores(session, auto):
    _seed_decisions(session, 30)
    _age_stage(session, 30)
    set_paused(session, True)
    assert maybe_promote(session).stage == STAGE_SHADOW
    set_paused(session, False)
    _age_stage(session, 30)
    assert maybe_promote(session).stage == 1


def test_set_stage_manual_and_describe(session, auto):
    set_stage(session, STAGE_DEALS, note="ручной перевод")
    flags = current_flags(session)
    assert flags.deal_dedup and flags.cross_funnel and not flags.contact_soft_merge
    info = describe(session)
    assert info["stage_name"] == "deals"
    assert info["health"]["ok"] is True


def test_manual_env_flag_forces_feature(session, auto, monkeypatch):
    monkeypatch.setattr(settings, "enable_assignment", True)
    assert current_flags(session).assignment is True   # поверх этапа shadow
    assert current_flags(session).shadow is True       # но мутации ещё заблокированы


def test_manual_mode_uses_env_only(session, monkeypatch):
    monkeypatch.setattr(settings, "auto_rollout", False)
    monkeypatch.setattr(settings, "shadow_mode", False)
    monkeypatch.setattr(settings, "enable_deal_dedup", True)
    flags = current_flags(session)
    assert flags.shadow is False and flags.deal_dedup is True
