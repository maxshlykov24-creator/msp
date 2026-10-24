from datetime import datetime, timedelta, timezone
import smtplib

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.certificate_mail import (
    BODY, MSK, TABLES, CertificateMailJob, CertificateTransition, InvalidCertificate,
    MailSettings, email_address, ingest, process_job, render, transitions,
    anniversary,
)

NOW = datetime(2026, 10, 22, 0, 20, tzinfo=MSK)


@pytest.fixture
def cfg():
    return MailSettings(mode="live", status_id=76675038, activated_at=int(NOW.timestamp()) - 10,
        delay_seconds=0, legacy_checked=True, webhook_secret=SecretStr("test-hook"), smtp_password=SecretStr("test-smtp"))


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
            {"field_id": 3829442, "values": [{"value": int(datetime(2026, 10, 22, tzinfo=MSK).timestamp())}]},
            {"field_id": 3169911, "values": [{"value": int(datetime(2027, 10, 22, tzinfo=MSK).timestamp())}]},
            {"field_id": 3135463, "values": [{"value": "TEST-17"}]}], **kw}


def form(**kw):
    row = {"id": "17", "status_id": "76675038", "old_status_id": "76654886",
        "pipeline_id": "9601214", "account_id": "32417154", "last_modified": str(int(NOW.timestamp())), **kw}
    return {f"leads[status][0][{k}]": [v] for k, v in row.items()}


def enqueue(store, cfg):
    assert ingest(store, transitions(form(), cfg, NOW)) == 1
    return store.get(CertificateMailJob, 17)


def set_field(data, fid, value):
    data["custom_fields_values"] = [f for f in data["custom_fields_values"] if f["field_id"] != fid]
    data["custom_fields_values"].append({"field_id": fid, "values": [{"value": value}]})


def expiry_writer(data, calls=None):
    def write(db, lid, stamp, settings):
        if calls is not None:
            calls.append(stamp)
        set_field(data, settings.until_field, stamp)
    return write


def test_five_minute_delay_and_required_expiry_autofill(store, cfg):
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    data = lead()
    set_field(data, cfg.until_field, "")
    sends, writes = [], []
    def run(now):
        process_job(store, 17, cfg, now, fetch=lambda *args: data,
            deliver=lambda *args: sends.append(args), persist_expiry=expiry_writer(data, writes))
    run(NOW)
    assert job.state == "queued" and not sends and len(writes) == 1
    assert job.due_at.replace(tzinfo=timezone.utc) == (NOW + timedelta(minutes=5)).astimezone(timezone.utc)
    run(NOW + timedelta(minutes=4, seconds=59))
    assert not sends and len(writes) == 1
    run(NOW + timedelta(minutes=5))
    assert job.state == "accepted" and len(sends) == 1


def test_future_start_survives_days_and_refetches_changes(store, cfg):
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    data = lead()
    future = datetime(2026, 11, 1, tzinfo=MSK)
    set_field(data, cfg.from_field, int(future.timestamp()))
    sends = []
    def run(now):
        process_job(store, 17, cfg, now, fetch=lambda *args: data,
            deliver=lambda *args: sends.append(args), persist_expiry=expiry_writer(data))
    run(NOW)
    assert job.state == "queued" and job.due_at.replace(tzinfo=timezone.utc) == future.astimezone(timezone.utc)
    run(NOW + timedelta(days=2))
    assert not sends and job.state == "queued"
    # Manager moves start later: neither the old date nor old expiry may be used.
    later = future + timedelta(days=1)
    set_field(data, cfg.from_field, int(later.timestamp()))
    run(future)
    assert not sends and job.due_at.replace(tzinfo=timezone.utc) == later.astimezone(timezone.utc)
    run(later)
    assert len(sends) == 1 and "с 02.11.2026 по 02.11.2027" in sends[0][2]


def test_stage_change_during_delay_cancels(store, cfg):
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    data = lead()
    sends = []
    process_job(store, 17, cfg, NOW, fetch=lambda *args: data, deliver=lambda *args: sends.append(args))
    data["status_id"] = 142
    process_job(store, 17, cfg, NOW + timedelta(minutes=5), fetch=lambda *args: data,
                deliver=lambda *args: sends.append(args))
    assert job.state == "cancelled" and not sends


def test_reentry_restarts_delay_even_if_exit_was_not_observed(store, cfg):
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    later = NOW + timedelta(minutes=4)
    assert ingest(store, transitions(form(last_modified=str(int(later.timestamp()))), cfg, later)) == 1
    calls = []
    process_job(store, 17, cfg, NOW + timedelta(minutes=5), fetch=lambda *args: lead(),
                deliver=lambda *args: calls.append(args))
    assert job.state == "queued" and not calls
    process_job(store, 17, cfg, later + timedelta(minutes=5), fetch=lambda *args: lead(),
                deliver=lambda *args: calls.append(args))
    assert len(calls) == 1


def test_expiry_write_must_be_confirmed_before_send(store, cfg):
    job = enqueue(store, cfg)
    data = lead()
    set_field(data, cfg.until_field, "")
    sends = []
    with pytest.raises(RuntimeError, match="expiry_update_not_confirmed"):
        process_job(store, 17, cfg, NOW, fetch=lambda *args: data,
            deliver=lambda *args: sends.append(args), persist_expiry=lambda *args: None)
    assert job.attempted_at is None and not sends


def test_leap_anniversary():
    assert str(anniversary(datetime(2028, 2, 29).date())) == "2029-02-28"


def test_substitutions_moscow_current_date_and_number(cfg):
    recipient, body = render(lead(), cfg, NOW.astimezone(timezone.utc))
    assert recipient == "owner@example.test"
    assert "на сумму 10 000 в сеть" in body
    assert "с 22.10.2026 по 22.10.2027" in body
    assert "{{" not in body and "89310090251" in body
    assert body == BODY.replace("{{lead.sale}}", "10 000").replace("{{lead.cf(3829442)}}", "22.10.2026").replace("{{lead.cf(3169911)}}", "22.10.2027")
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


@pytest.mark.parametrize("missing_field", [3829210, 3829442, 3135463])
def test_each_required_field_blocks_smtp(store, cfg, missing_field):
    cfg.include_number = True
    job = enqueue(store, cfg)
    data = lead()
    data["custom_fields_values"] = [
        field for field in data["custom_fields_values"]
        if field["field_id"] != missing_field
    ]
    calls = []
    process_job(store, 17, cfg, NOW, fetch=lambda *args: data,
                deliver=lambda *args: calls.append(args))
    assert job.state == "blocked" and not calls and job.attempted_at is None


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


@pytest.mark.parametrize("missing", ["email", "number", "price", "start"])
def test_missing_data_resumes_days_later_without_new_transition(store, cfg, missing):
    cfg.include_number = True
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    data = lead()
    if missing == "price":
        data["price"] = 0
    else:
        fid = {"email": cfg.email_field, "number": cfg.number_field, "start": cfg.from_field}[missing]
        set_field(data, fid, "")
    calls = []
    def run(now):
        process_job(store, 17, cfg, now, fetch=lambda *args: data,
            deliver=lambda *args: calls.append(args), persist_expiry=expiry_writer(data))
    run(NOW + timedelta(minutes=5))
    assert job.state == "blocked" and not calls and job.attempted_at is None
    assert job.check_at.replace(tzinfo=None) == (NOW + timedelta(minutes=5, hours=1)).replace(tzinfo=None)
    assert ingest(store, transitions(form(), cfg, NOW)) == 0
    # Still incomplete several days later; it must remain waiting, not expire.
    run(NOW + timedelta(days=2))
    assert job.state == "blocked" and not calls
    data = lead()
    store.expire_all()  # Simulate reloading the persisted queue after a restart.
    run(NOW + timedelta(days=3))
    job = store.get(CertificateMailJob, 17)
    assert job.state == "accepted" and len(calls) == 1
    run(NOW + timedelta(days=4))
    assert len(calls) == 1


def test_blocked_deal_leaving_stage_cancels(store, cfg):
    job = enqueue(store, cfg)
    calls = []
    process_job(store, 17, cfg, NOW, fetch=lambda *args: lead(custom_fields_values=[]),
                deliver=lambda *args: calls.append(args))
    assert job.state == "blocked"
    process_job(store, 17, cfg, NOW + timedelta(days=2), fetch=lambda *args: lead(status_id=142),
                deliver=lambda *args: calls.append(args))
    assert job.state == "cancelled" and not calls


def test_late_start_field_still_respects_future_date(store, cfg):
    cfg.delay_seconds = 300
    job = enqueue(store, cfg)
    calls = []
    data = lead()
    set_field(data, cfg.from_field, "")
    def run(now):
        process_job(store, 17, cfg, now, fetch=lambda *args: data,
            deliver=lambda *args: calls.append(args), persist_expiry=expiry_writer(data))
    run(NOW)
    assert job.state == "blocked"
    future = datetime(2026, 11, 1, tzinfo=MSK)
    set_field(data, cfg.from_field, int(future.timestamp()))
    run(NOW + timedelta(days=3))
    assert job.state == "queued" and not calls
    run(future - timedelta(seconds=1))
    assert not calls
    run(future)
    assert len(calls) == 1
