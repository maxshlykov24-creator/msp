from datetime import date, datetime, timezone

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.reservation_reminders import (
    TABLES, MSK, TEMPLATE, ReminderAttempt, ReminderEvent, ReminderJob, ReminderSettings,
    expiry, in_window, primary_contact, recipient, reconcile, record_events, render,
    send_job, sync_jobs,
)
from app import reservation_reminders as module


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def cfg():
    return ReminderSettings(mode="live", webhook_ready=True, legacy_checked=True,
        telegram_channel="tg", whatsapp_channel="wa", max_channel="max",
        api_key=SecretStr("test-key"), webhook_secret=SecretStr("test-hook"))


def lead(until="2026-10-24", paid=True, **kw):
    return {"id": 17, "status_id": 77086494,
        "_embedded": {"contacts": [{"id": 44, "is_main": True}]},
        "custom_fields_values": [
            {"field_id": 3810737, "values": [{"enum_id": 5452541 if paid else 5452539}]},
            {"field_id": 3810739, "values": [{"value": int(datetime.fromisoformat(until).replace(tzinfo=MSK).timestamp())}]}], **kw}


NOW = datetime(2026, 10, 21, 10, tzinfo=MSK)
CONTACT = {"id": 44, "custom_fields_values": [
    {"field_id": 3346485, "values": [{"value": "435207481"}]},
    {"field_id": 3807405, "values": [{"value": "max-test-id"}]},
    {"field_code": "PHONE", "values": [{"value": "+7 (999) 000-00-01", "enum_code": "WORK"}]}]}


class FakeClients:
    def __init__(self, outcomes, *, current_lead=None, contact=None):
        self.outcomes = list(outcomes)
        self.current_lead = current_lead or lead()
        self.contact = contact or CONTACT
        self.sent = []
        self.wz = self

    def get(self, path, **kw):
        if path == "/channels":
            return httpx.Response(200, request=httpx.Request("GET", "https://example.test"), json=[
                {"channelId": key, "transport": t, "state": "active"}
                for key, t in [("tg", "tgapi"), ("wa", "whatsapp"), ("max", "max")]])
        return self.current_lead if "/leads/" in path else self.contact

    def post(self, path, json):
        self.sent.append(json)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return httpx.Response(out[0], json=out[1], request=httpx.Request("POST", "https://example.test"))


def job(db):
    j = ReminderJob(lead_id=17, until=date(2026, 10, 24))
    db.add(j)
    db.commit()
    return j


def test_calendar_and_exact_template(cfg):
    assert expiry(lead(), cfg) == date(2026, 10, 24)
    assert expiry(lead(paid=False), cfg) is None
    assert in_window(date(2026, 10, 24), NOW, cfg)
    assert not in_window(date(2026, 10, 24), NOW.replace(hour=9, minute=59), cfg)
    assert not in_window(date(2026, 10, 24), NOW.replace(day=22), cfg)
    assert not in_window(date(2026, 10, 24), NOW.replace(hour=20), cfg)
    assert render(date(2026, 10, 24)) == TEMPLATE.replace("(Отложка до)", "24.10.2026")
    assert in_window(date(2026, 10, 24), NOW.astimezone(timezone.utc), cfg)


def test_success_never_falls_back_or_repeats(store, cfg):
    j = job(store)
    c = FakeClients([(201, {"messageId": "m1"})])
    send_job(store, j.id, c, cfg, NOW)
    send_job(store, j.id, c, cfg, NOW)
    assert len(c.sent) == 1 and c.sent[0]["chatType"] == "telegram"
    assert j.state == "pending"
    record_events(store, {"statuses": [{"messageId": "m1", "status": "delivered"}]})
    reconcile(store)
    send_job(store, j.id, c, cfg, NOW)
    assert j.state == "sent" and len(c.sent) == 1


def test_confirmed_async_failure_uses_next_channel_once(store, cfg):
    j = job(store)
    c = FakeClients([(201, {"messageId": "m1"}), (201, {"messageId": "m2"})])
    send_job(store, j.id, c, cfg, NOW)
    record_events(store, {"statuses": [{"messageId": "m1", "status": "error", "error": {"error": "BAD_CONTACT"}}]})
    reconcile(store)
    send_job(store, j.id, c, cfg, NOW)
    reconcile(store)  # Replaying the old error must not reset the pending WhatsApp attempt.
    send_job(store, j.id, c, cfg, NOW)
    assert [x["chatType"] for x in c.sent] == ["telegram", "whatsapp"]
    assert j.state == "pending"


def test_exact_order_all_failures_only_once(store, cfg):
    j = job(store)
    c = FakeClients([(400, {"error": "BAD_CONTACT"})] * 3)
    send_job(store, j.id, c, cfg, NOW)
    send_job(store, j.id, c, cfg, NOW)
    assert [x["chatType"] for x in c.sent] == ["telegram", "whatsapp", "max"]
    assert j.state == "failed"


@pytest.mark.parametrize("outcome", [httpx.ReadTimeout("lost response"), (500, {}), (201, {})])
def test_unknown_result_stops_chain(store, cfg, outcome):
    j = job(store)
    c = FakeClients([outcome])
    send_job(store, j.id, c, cfg, NOW)
    store.expire_all()  # Simulate reload after restart.
    send_job(store, j.id, c, cfg, NOW)
    assert len(c.sent) == 1
    assert store.get(ReminderJob, j.id).state == "uncertain"


def test_crash_after_claim_does_not_resend(store, cfg):
    j = job(store)
    j.state = "inflight"
    store.add(ReminderAttempt(job_id=j.id, transport="telegram", state="inflight", crm_message_id="claimed"))
    store.commit()
    c = FakeClients([])
    reconcile(store)
    send_job(store, j.id, c, cfg, NOW)
    assert c.sent == []


@pytest.mark.parametrize("changed", [lead(paid=False), lead("2026-10-25"), lead(status_id=142), lead(is_deleted=True)])
def test_recheck_cancels_changed_conditions(store, cfg, changed):
    j = job(store)
    c = FakeClients([], current_lead=changed)
    send_job(store, j.id, c, cfg, NOW)
    assert j.state == "cancelled" and c.sent == []


def test_scans_dedup_cancel_without_catchup(store, cfg):
    for _ in range(2):
        sync_jobs(store, [lead(), lead("2026-10-23", id=18)], cfg, NOW)
    assert len(list(store.scalars(select(ReminderJob)))) == 1
    sync_jobs(store, [lead("2026-10-25")], cfg, NOW)
    jobs = list(store.scalars(select(ReminderJob).order_by(ReminderJob.id)))
    assert [j.state for j in jobs] == ["cancelled", "ready"]


def test_callback_before_response_and_duplicate_callbacks(store, cfg):
    record_events(store, {"statuses": [{"messageId": "m1", "status": "sent"}]})
    record_events(store, {"statuses": [{"messageId": "m1", "status": "sent"}]})
    assert len(list(store.scalars(select(ReminderEvent)))) == 1
    j = job(store)
    c = FakeClients([(201, {"messageId": "m1"})])
    send_job(store, j.id, c, cfg, NOW)
    assert j.state == "sent" and len(c.sent) == 1


def test_preview_cannot_send(store, cfg):
    cfg.mode = "preview"
    c = FakeClients([])
    j = job(store)
    send_job(store, j.id, c, cfg, NOW)
    assert not c.sent


def test_contact_selection_and_addressing(cfg):
    assert primary_contact(lead()) == 44
    assert primary_contact({"_embedded": {"contacts": [{"id": 1}, {"id": 2}]}}) is None
    assert recipient(CONTACT, "telegram", cfg) == {"chatId": "435207481"}
    assert recipient(CONTACT, "whatsapp", cfg) == {"chatId": "79990000001"}
    assert recipient(CONTACT, "max", cfg) == {"chatId": "max-test-id"}
    assert recipient({}, "max", cfg) is None


def test_success_is_monotonic_over_error_callbacks(store, cfg):
    j = job(store)
    c = FakeClients([(201, {"messageId": "m1"})])
    send_job(store, j.id, c, cfg, NOW)
    record_events(store, {"statuses": [
        {"messageId": "m1", "status": "delivered"},
        {"messageId": "m1", "status": "error", "error": {"error": "late"}}]})
    reconcile(store)
    send_job(store, j.id, c, cfg, NOW)
    assert j.state == "sent" and len(c.sent) == 1


def test_proxy_preserves_existing_subscriber(monkeypatch):
    cfg = ReminderSettings(webhook_secret=SecretStr("hook"), forward_url=SecretStr("https://example.test/private"))
    forwarded = []
    stored = []

    class Remote:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, content, headers):
            forwarded.append((url, content, headers))
            return httpx.Response(200, content=b'{"kept":true}', headers={"content-type": "application/json"})

    class DB:
        def close(self):
            pass

    monkeypatch.setattr(module, "ReminderSettings", lambda: cfg)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: Remote())
    monkeypatch.setattr(module, "get_session_factory", lambda: DB)
    monkeypatch.setattr(module, "record_events", lambda db, body: stored.append(body))
    client = TestClient(module.app)  # No lifespan; no live DB or worker.
    response = client.post('/wazzup/hook', content=b'{"test":true}',
        headers={"content-type": "application/json", "authorization": "Bearer original"})
    assert response.status_code == 200 and response.content == b'{"kept":true}'
    assert forwarded[0] == ("https://example.test/private", b'{"test":true}',
        {"content-type": "application/json", "authorization": "Bearer original"})
    assert stored == [{"test": True}]
    assert client.post('/wazzup/wrong', json={"test": True}).status_code == 403
