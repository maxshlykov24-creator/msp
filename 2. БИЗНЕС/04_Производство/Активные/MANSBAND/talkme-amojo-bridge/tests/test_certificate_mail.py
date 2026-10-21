from datetime import datetime, timedelta, timezone
import smtplib

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.certificate_mail import (
    BODY, MSK, TABLES, CertificateMailJob, CertificateTransition, InvalidCertificate,
    MailSettings, email_address, ingest, process_job, render, transitions,
)

NOW = datetime(2026, 10, 22, 0, 20, tzinfo=MSK)


@pytest.fixture
def cfg():
    return MailSettings(mode="live", status_id=76675038, activated_at=int(NOW.timestamp()) - 10,
        legacy_checked=True, webhook_secret=SecretStr("test-hook"), smtp_password=SecretStr("test-smtp"))


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    with Session(engine) as db:
        yield db


def lead(**kw):
    return {"id": 17, "pipeline_id": 9601214, "status_id": 76675038, "price": 10000,
        "custom_fields_values": [
            {"field_id": 3829210, "values": [{"value": "owner@example.test"}]},
            {"field_id": 3169911, "values": [{"value": int(datetime(2027, 10, 22, tzinfo=MSK).timestamp())}]},
            {"field_id": 3135463, "values": [{"value": "TEST-17"}]}], **kw}


def form(**kw):
    row = {"id": "17", "status_id": "76675038", "old_status_id": "76654886",
        "pipeline_id": "9601214", "account_id": "32417154", "last_modified": str(int(NOW.timestamp())), **kw}
    return {f"leads[status][0][{k}]": [v] for k, v in row.items()}


def enqueue(store, cfg):
    assert ingest(store, transitions(form(), cfg, NOW)) == 1
    return store.get(CertificateMailJob, 17)


def test_substitutions_moscow_current_date_and_number(cfg):
    recipient, body = render(lead(), cfg, NOW.astimezone(timezone.utc))
    assert recipient == "owner@example.test"
    assert "на сумму 10 000 в сеть" in body
    assert "с 22.10.2026 по 22.10.2027" in body
    assert "{{" not in body and "89310090251" in body
    assert body == BODY.replace("{{lead.sale}}", "10 000").replace("{{date.now}}", "22.10.2026").replace("{{lead.cf(3169911)}}", "22.10.2027")
    cfg.include_number = True
    assert "Номер сертификата: TEST-17" in render(lead(), cfg, NOW)[1]


@pytest.mark.parametrize("address", ["", "a@b.ru,c@d.ru", "name <a@b.ru>", "a@b.ru\nBcc:x@y.ru", "a@@b.ru", "a@-b.ru", "a@b", ".a@b.ru", "a..b@c.ru"])
def test_invalid_addresses_never_send(address):
    with pytest.raises(InvalidCertificate):
        email_address(address)


def test_dedup_and_status_transition_filter(store, cfg):
    assert ingest(store, transitions(form(), cfg, NOW)) == 1
    assert ingest(store, transitions(form(), cfg, NOW)) == 0
    assert len(list(store.scalars(select(CertificateTransition)))) == 1
    assert transitions(form(status_id="142"), cfg, NOW) == []
    assert transitions(form(old_status_id="76675038"), cfg, NOW) == []
    assert transitions(form(pipeline_id="5"), cfg, NOW) == []
    assert transitions(form(account_id="5"), cfg, NOW) == []
    assert transitions(form(last_modified=str(cfg.activated_at - 1)), cfg, NOW) == []


def test_smtp_only_once_even_after_stage_reentry(store, cfg):
    job = enqueue(store, cfg)
    calls = []
    deliver = lambda *args: calls.append(args)
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=deliver)
    assert job.state == "accepted" and job.attempted_at is not None
    ingest(store, transitions(form(last_modified=str(int(NOW.timestamp())+1)), cfg, NOW + timedelta(seconds=1)))
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=deliver)
    assert len(calls) == 1
    assert calls[0][1] == "owner@example.test"


@pytest.mark.parametrize("change,expected", [
    ({"status_id": 142}, "cancelled"),
    ({"pipeline_id": 5}, "cancelled"),
    ({"is_deleted": True}, "cancelled"),
    ({"price": 0}, "blocked"),
    ({"custom_fields_values": []}, "blocked"),
    ({"custom_fields_values": [{"field_id": 3829210, "values": [{"value": "owner@example.test"}]}]}, "blocked"),
])
def test_missing_fields_or_changed_stage_no_send(store, cfg, change, expected):
    job = enqueue(store, cfg)
    calls = []
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(**change), deliver=lambda *args: calls.append(args))
    assert job.state == expected and not calls and job.attempted_at is None


@pytest.mark.parametrize("error,state", [
    (TimeoutError("lost SMTP acknowledgement"), "uncertain"),
    (smtplib.SMTPServerDisconnected("lost connection"), "uncertain"),
    (smtplib.SMTPDataError(550, b"rejected"), "failed"),
])
def test_errors_do_not_repeat_smtp(store, cfg, error, state):
    job = enqueue(store, cfg)
    calls = []
    def deliver(*args):
        calls.append(args)
        raise error
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=deliver)
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=deliver)
    assert job.state == state and len(calls) == 1


def test_preview_and_crash_guard(store, cfg):
    job = enqueue(store, cfg)
    calls = []
    cfg.mode = "preview"
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=lambda *args: calls.append(args))
    assert not calls
    cfg.mode = "live"
    job.state, job.attempted_at = "sending", NOW
    store.commit()
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=lambda *args: calls.append(args))
    assert not calls


def test_blocked_requires_new_transition_not_field_edit_or_replayed_event(store, cfg):
    job = enqueue(store, cfg)
    calls = []
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(custom_fields_values=[]), deliver=lambda *args: calls.append(args))
    assert job.state == "blocked"
    assert ingest(store, transitions(form(), cfg, NOW)) == 0
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(), deliver=lambda *args: calls.append(args))
    assert not calls
    newer = NOW + timedelta(seconds=1)
    assert ingest(store, transitions(form(last_modified=str(int(newer.timestamp()))), cfg, newer)) == 1
    process_job(store, 17, cfg, newer, fetch=lambda *args: lead(), deliver=lambda *args: calls.append(args))
    assert len(calls) == 1
