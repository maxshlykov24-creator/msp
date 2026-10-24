from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.reservation_reminders import ReminderEvent, ReminderSettings
from app.stage_notifications import (TABLES, StageJob, StageAttempt, StageSettings,
    Conversation, MSK, dispatch_time, ingest, order_for, render, reconcile, send_job,
    record_messages, resolve_plan)

NOW = datetime(2026, 10, 24, 12, tzinfo=MSK).astimezone(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in [*TABLES, ReminderEvent.__table__]: table.create(engine)
    with Session(engine) as session: yield session


@pytest.fixture
def cfg():
    return StageSettings(mode="live", activated_at=int(NOW.timestamp())-100,
        legacy_checked=True, origins_checked=True, pyatnitskaya_address="ул. Пятницкая, д. 8")


def lead(status=76655110):
    return {"id": 17, "pipeline_id": 9601214, "status_id": status,
        "custom_fields_values": [{"field_id": 3133923, "values": [{"value": "TRACK123"}]}],
        "_embedded": {"contacts": [{"id": 123, "is_main": True}]}}


def job(db, **kwargs):
    j = StageJob(lead_id=17, status_id=76655110, event_key="key", event_at=NOW,
        check_at=NOW, state="ready", contact_id=123, plan=kwargs.pop("plan", [
            {"transport": t, "channelId": t, "recipient": {"chatId": "123456789"}}
            for t in ("telegram", "whatsapp", "max", "vk")]), **kwargs)
    db.add(j); db.commit(); return j


class Fake:
    def __init__(self, responses=()):
        self.wz = self
        self.responses = list(responses)
        self.posts = []
        self.document = lead()
        self.events = []
        self.talks = []

    def get(self, path, **kwargs):
        if path == "/channels":
            return httpx.Response(200, request=httpx.Request("GET", "https://example.test"), json=[
                {"channelId": t, "transport": "tgapi" if t == "telegram" else t, "state": "active"}
                for t in ("telegram", "whatsapp", "max", "vk")])
        if path.startswith("/api/v4/leads/"): return self.document
        if path.startswith("/api/v4/contacts/"): return {"id": 123}
        if path == "/api/v4/talks": return {"_embedded": {"talks": self.talks}}
        if path == "/api/v4/events": return {"_embedded": {"events": self.events}}
        raise AssertionError(path)

    def post(self, path, json):
        self.posts.append(json)
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return httpx.Response(result[0], json=result[1])


def run(db, j, c, cfg, now=NOW, clock=None):
    send_job(db, j, c, cfg, ReminderSettings(), now, clock=clock or (lambda: now))


@pytest.mark.parametrize("source,expected", [
    ("whatsapp", ["whatsapp", "telegram", "max"]),
    ("max", ["max", "telegram", "whatsapp"]),
    ("telegram", ["telegram", "whatsapp", "max"]),
    ("vk", ["telegram", "whatsapp", "max", "vk"]),
    ("avito", ["telegram", "whatsapp", "max", "avito"]),
    ("instagram", ["telegram", "whatsapp", "max", "instagram"]),
])
def test_channel_priority(source, expected): assert order_for(source) == expected


@pytest.mark.parametrize("hour,day,expected_hour", [(9,25,10),(10,24,10),(19,24,19),(20,25,10),(23,25,10)])
def test_window(hour,day,expected_hour):
    out = dispatch_time(datetime(2026,10,24,hour,tzinfo=MSK)).astimezone(MSK)
    assert (out.day, out.hour) == (day, expected_hour)


def test_pending_does_not_fan_out_and_success_is_terminal(db,cfg):
    j = job(db);c = Fake([(201,{"messageId":"m1"})])
    run(db,j,c,cfg); run(db,j,c,cfg)
    assert len(c.posts)==1 and j.state=="pending"
    db.add(ReminderEvent(key="sent1",message_id="m1",status="sent"));db.commit()
    reconcile(db,NOW);run(db,j,c,cfg)
    assert j.state=="sent" and len(c.posts)==1
    db.add(ReminderEvent(key="late-error",message_id="m1",status="error"));db.commit()
    reconcile(db,NOW)
    assert j.state=="sent"


def test_confirmed_error_only_tries_next_channel(db,cfg):
    j=job(db);c=Fake([(201,{"messageId":"m1"}),(201,{"messageId":"m2"})])
    run(db,j,c,cfg)
    db.add(ReminderEvent(key="err1",message_id="m1",status="error"));db.commit()
    reconcile(db,NOW);run(db,j,c,cfg)
    assert [p["chatType"] for p in c.posts]==["telegram","whatsapp"]
    assert len({p["crmMessageId"] for p in c.posts})==2


@pytest.mark.parametrize("result", [(500,{}),httpx.ReadTimeout("lost reply"),(400,{"error":"UNRECOGNIZED"})])
def test_unknown_result_never_uses_next_channel(db,cfg,result):
    j=job(db);c=Fake([result]);run(db,j,c,cfg);run(db,j,c,cfg)
    assert len(c.posts)==1 and j.state=="uncertain"


def test_fallback_stops_at_close_of_window(db,cfg):
    j=job(db);c=Fake([(400,{"error":"BAD_CONTACT"})])
    times=iter([NOW,datetime(2026,10,24,20,tzinfo=MSK)])
    run(db,j,c,cfg,clock=lambda:next(times))
    # BAD_CONTACT is an explicit rejection; another channel must wait until tomorrow.
    assert len(c.posts)==1
    assert j.state=="ready" and j.check_at.day==25


def test_changed_stage_cancels_before_send(db,cfg):
    j=job(db);c=Fake();c.document["status_id"]=142
    run(db,j,c,cfg);assert j.state=="cancelled" and not c.posts


def test_missing_track_does_not_send(db,cfg):
    j=job(db);c=Fake();c.document["custom_fields_values"]=[]
    run(db,j,c,cfg);assert j.state=="blocked" and not c.posts


def test_template_store_requires_exact_enum(cfg):
    data=lead(76666306)
    with pytest.raises(ValueError): render(data,cfg)
    data["custom_fields_values"]=[{"field_id":3158921,"values":[{"value":"на Пятницкой","enum_id":4765787}]}]
    assert "ул. Пятницкая, д. 8" in render(data,cfg)
    data["custom_fields_values"][0]["values"][0]["enum_id"]=4765789
    assert "Спартаковская площадь, 14с2" in render(data,cfg)


def test_latest_incoming_not_last_outgoing_controls_source(db,cfg):
    c=Fake();c.talks=[{"talk_id":1,"contact_id":123,"origin":"com.wazzup24-1","updated_at":999},
        {"talk_id":2,"contact_id":123,"origin":"com.wazzup24","updated_at":1}]
    c.events=[{"created_at":100,"value_after":[{"message":{"talk_id":1,"origin":"com.wazzup24-1"}}]},
        {"created_at":200,"value_after":[{"message":{"talk_id":2,"origin":"com.wazzup24"}}]}]
    plan=resolve_plan(db,c,lead(),{"id":123},cfg,ReminderSettings())
    assert [x["transport"] for x in plan]==["telegram","whatsapp","max","vk"]


def test_unknown_origin_does_not_guess(db,cfg):
    c=Fake();c.talks=[{"talk_id":1,"contact_id":123,"origin":"unknown"}]
    with pytest.raises(ValueError): resolve_plan(db,c,lead(),{"id":123},cfg,ReminderSettings())


def test_incoming_cache_ignores_echo_and_older_messages(db):
    message={"chatType":"vk","channelId":"vk","chatId":"123","dateTime":NOW.isoformat(),"isEcho":False,"text":"DO NOT STORE"}
    record_messages(db,{"messages":[message]})
    record_messages(db,{"messages":[{**message,"dateTime":(NOW-timedelta(days=1)).isoformat()}]})
    record_messages(db,{"messages":[{**message,"chatId":"other","isEcho":True}]})
    assert len(list(db.scalars(select(Conversation))))==1
    assert "text" not in Conversation.__table__.columns


def test_new_event_dedup_and_no_resend_after_attempt(db,cfg):
    def form(stamp):
        return {f"leads[status][0][{k}]": [str(v)] for k,v in {
            "id":17,"status_id":76655110,"old_status_id":1,"pipeline_id":9601214,
            "account_id":32417154,"last_modified":int(stamp.timestamp())}.items()}
    assert ingest(db,form(NOW),NOW,cfg)==1
    assert ingest(db,form(NOW),NOW,cfg)==0
    j=db.scalar(select(StageJob));j.plan=[{"transport":"telegram","channelId":"telegram","recipient":{"chatId":"123"}}];db.commit()
    c=Fake([(201,{"messageId":"m1"})]);run(db,j,c,cfg)
    assert ingest(db,form(NOW+timedelta(seconds=1)),NOW+timedelta(seconds=1),cfg)==0
