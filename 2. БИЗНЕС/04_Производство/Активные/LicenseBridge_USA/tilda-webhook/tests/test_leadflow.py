"""Лид-машина: вариант текста, окно звонка, AI-звонок и разбор итога Pleep."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import leadflow
from app.config import settings
from app.models import AiCall

PHONE_LA = "+12139310879"   # Лос-Анджелес, America/Los_Angeles
PHONE_NY = "+17187911783"   # Нью-Йорк, America/New_York


@pytest.fixture()
def lead(fake):
    cid = fake.add_contact(name="Test", phone=PHONE_LA)
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new,
                        responsible=settings.default_sales_owner_id)
    return fake.leads[lid]


@pytest.fixture()
def calls(monkeypatch):
    """Перехват AMI: звонок никуда не уходит, но видно, что и с чем позвали."""
    fired: list[dict] = []

    def fake_originate(phone, variables=None, caller_id="", ring_sec=None):
        fired.append({"phone": phone, "variables": variables or {}})
        return "test-action-id"

    monkeypatch.setattr("app.ami.originate", fake_originate)
    return fired


def _window(monkeypatch, open_: bool) -> None:
    monkeypatch.setattr(leadflow, "in_call_window", lambda tz, now=None: open_)


# ── вариант текста и окно ──
def test_wa_variant_cycles():
    variants = {leadflow.wa_variant(base + i) for base in (1000, 2000) for i in range(4)}
    assert variants == {"1", "2", "3", "4"}
    assert leadflow.wa_variant(4242) == leadflow.wa_variant(4242)


def test_client_timezone_by_area_code():
    assert leadflow.client_timezone(PHONE_LA) == "America/Los_Angeles"
    assert leadflow.client_timezone(PHONE_NY) == "America/New_York"
    # мусор вместо номера не должен ронять поток — работаем по часам офиса
    assert leadflow.client_timezone("не номер") == settings.call_window_default_tz


def test_call_window_bounds():
    tz = "America/Los_Angeles"
    day = datetime(2026, 8, 10, 19, 0, tzinfo=timezone.utc)      # 12:00 в LA
    night = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)     # 23:00 в LA
    assert leadflow.in_call_window(tz, day)
    assert not leadflow.in_call_window(tz, night)
    start = leadflow.next_window_start(tz, night)
    assert start > night
    assert leadflow.in_call_window(tz, start)


def test_enrich_lead_writes_fields(ctx, fake, lead, enable_leadflow):
    res = leadflow.enrich_lead(ctx, lead, PHONE_LA)
    assert res["written"] is True
    cfs = {c["field_id"]: c["values"][0]["value"]
           for c in fake.leads[lead["id"]]["custom_fields_values"]}
    assert cfs[settings.field_wa_variant] in {"1", "2", "3", "4"}
    assert "America/Los_Angeles" in cfs[settings.field_call_window]


def test_enrich_lead_noop_without_flag(ctx, fake, lead):
    assert leadflow.enrich_lead(ctx, lead, PHONE_LA)["written"] is False


# ── AI-звонок ──
def test_ai_call_starts_in_window(ctx, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    res = leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    assert res["action"] == "ai_call.started"
    assert calls[0]["phone"] == PHONE_LA
    assert calls[0]["variables"]["LB_AI_NUMBER"] == settings.pleep_number
    row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead["id"]))
    assert (row.status, row.attempts) == ("dialing", 1)


def test_ai_call_deferred_in_quiet_hours(ctx, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, False)
    res = leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    assert res["action"] == "ai_call.deferred"
    assert not calls
    row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead["id"]))
    assert row.status == "scheduled" and row.scheduled_at is not None


def test_deferred_call_fires_when_window_opens(ctx, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, False)
    leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    row = ctx.s.scalar(select(AiCall).where(AiCall.lead_id == lead["id"]))
    assert leadflow.run_due_calls(ctx, datetime.now(timezone.utc)) == []
    fired = leadflow.run_due_calls(ctx, row.scheduled_at + timedelta(minutes=1))
    assert fired[0]["action"] == "ai_call.started"
    assert len(calls) == 1


def test_ai_call_not_repeated(ctx, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    again = leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    assert again["action"] == "ai_call.already"
    assert len(calls) == 1


def test_ai_call_by_phone_only(ctx, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    res = leadflow.handle_ai_call(ctx, {"phone": PHONE_LA})
    assert res["action"] == "ai_call.started"
    assert res["lead"] == lead["id"]


def test_ai_call_skipped_for_do_not_call(ctx, fake, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    fake.set_tags("leads", lead["id"], [settings.tag_do_not_call])
    res = leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    assert res["action"] == "ai_call.do_not_call"
    assert not calls


def test_ai_call_disabled_without_flag(ctx, lead, calls, monkeypatch):
    _window(monkeypatch, True)
    res = leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    assert res["action"] == "ai_call.disabled"
    assert not calls


# ── итог разговора ──
def test_outcome_qualified_moves_stage_and_creates_task(ctx, fake, lead, enable_leadflow):
    res = leadflow.handle_pleep_outcome(ctx, {
        "lead_id": lead["id"], "outcome": "qualified", "summary": "Нужна лицензия C-10",
    })
    assert res["outcome"] == "qualified"
    assert fake.leads[lead["id"]]["status_id"] == settings.status_in_work
    assert fake.tasks and fake.tasks[0]["responsible_user_id"] == settings.default_sales_owner_id
    assert not fake.bot_runs


def test_outcome_refused_runs_sms_bot(ctx, fake, lead, enable_leadflow):
    res = leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "declined"})
    assert res["sms"] is True
    assert fake.bot_runs[0]["bot_id"] == settings.sms_bot_id
    assert fake.leads[lead["id"]]["status_id"] == settings.status_new


def test_sms_one_per_phone_even_if_two_leads(ctx, fake, lead, enable_leadflow):
    cid = (lead.get("_embedded") or {}).get("contacts")[0]["id"]
    other_id = fake.add_lead(cid, settings.pipeline_id, settings.status_new,
                             responsible=settings.default_sales_owner_id)
    first = leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "refused"})
    second = leadflow.handle_pleep_outcome(ctx, {"lead_id": other_id, "outcome": "no_answer"})
    assert first["sms"] is True
    assert second["sms"] is False
    assert len(fake.bot_runs) == 1


def test_outcome_without_sms_bot_falls_back_to_task(ctx, fake, lead, enable_leadflow):
    settings.sms_bot_id = 0
    res = leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "no_answer"})
    assert res["sms"] is False and res["task"]
    assert not fake.bot_runs


def test_outcome_do_not_call_tags_lead(ctx, fake, lead, enable_leadflow):
    leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "stop"})
    tags = [t["name"] for t in fake.leads[lead["id"]]["_embedded"]["tags"]]
    assert settings.tag_do_not_call in tags
    assert not fake.bot_runs


def test_outcome_processed_once(ctx, fake, lead, enable_leadflow):
    leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "refused"})
    again = leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "qualified"})
    assert again["action"] == "pleep.duplicate"
    assert len(fake.bot_runs) == 1
    assert fake.leads[lead["id"]]["status_id"] == settings.status_new


def test_unknown_outcome_only_notes(ctx, fake, lead, enable_leadflow):
    res = leadflow.handle_pleep_outcome(ctx, {"lead_id": lead["id"], "outcome": "хз"})
    assert res["action"] == "pleep.unknown_outcome"
    assert fake.notes[("leads", lead["id"])]


def test_no_answer_from_asterisk_triggers_sms(ctx, fake, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    res = leadflow.on_ai_call_finished(ctx, PHONE_LA, answered=False, lead_id=None)
    assert res["outcome"] == "no_answer"
    assert fake.bot_runs[0]["entity_id"] == lead["id"]


def test_answered_call_waits_for_pleep(ctx, fake, lead, calls, enable_leadflow, monkeypatch):
    _window(monkeypatch, True)
    leadflow.handle_ai_call(ctx, {"lead_id": lead["id"]})
    res = leadflow.on_ai_call_finished(ctx, PHONE_LA, answered=True, lead_id=lead["id"])
    assert res["action"] == "ai_call.answered"
    assert not fake.bot_runs
