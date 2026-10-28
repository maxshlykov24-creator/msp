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
        if path.startswith("/api/v4/contacts/"): return getattr(self,"contact",{"id": 123})
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


@pytest.mark.parametrize("next_result,expected", [((201,{"messageId":"m2"}),"pending"),
    (httpx.ReadTimeout("lost second reply"),"uncertain")])
def test_old_error_cannot_reopen_next_attempt(db,cfg,next_result,expected):
    j=job(db);c=Fake([(201,{"messageId":"m1"}),next_result])
    run(db,j,c,cfg)
    db.add(ReminderEvent(key="first-failed",message_id="m1",status="error"));db.commit()
    reconcile(db,NOW);run(db,j,c,cfg)
    assert j.state==expected
    for _ in range(3):
        reconcile(db,NOW);run(db,j,c,cfg)
    assert j.state==expected and len(c.posts)==2


def test_stage_changes_while_routes_are_resolving(db,cfg):
    j=job(db);c=Fake();original=c.get;reads=[]
    def get(path,**kwargs):
        data=original(path,**kwargs)
        if path.startswith("/api/v4/leads/"):
            reads.append(path)
            if len(reads)>1:return {**data,"status_id":142}
        return data
    c.get=get
    run(db,j,c,cfg)
    assert j.state=="cancelled" and not c.posts
    assert list(db.scalars(select(StageAttempt)))==[]


def test_max_phone_not_registered_is_confirmed_rejection(db,cfg):
    j=job(db);c=Fake([(400,{"error":"CHANNEL_MAX_PHONE_NOT_OCCUPIED"}),(201,{"messageId":"m2"})])
    run(db,j,c,cfg)
    assert len(c.posts)==2 and j.state=="pending"


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
    j=db.scalar(select(StageJob));j.contact_id=123;j.plan=[{"transport":"telegram","channelId":"telegram","recipient":{"chatId":"123"}}];db.commit()
    c=Fake([(201,{"messageId":"m1"})]);run(db,j,c,cfg)
    assert ingest(db,form(NOW+timedelta(seconds=1)),NOW+timedelta(seconds=1),cfg)==0

# Calendar-specific acceptance: real date boundaries, durable repeat protection,
# unchanged stage engine and customer-specific fallback restrictions.
from app.stage_notifications import (CalendarSettings, CalendarJob, CalendarAttempt,
    CALENDAR_RULES, sync_calendar, send_calendar, render_calendar, calendar_valid)

CAL_NOW = datetime(2026, 10, 29, 10, tzinfo=MSK)


def calendar_lead(kind="meeting_today", store=4765787, target=None):
    settings = CalendarSettings()
    status, field, offset, hour, minute = CALENDAR_RULES[kind]
    target = target or (CAL_NOW.date() - timedelta(days=offset))
    data = lead(getattr(settings, status))
    data["custom_fields_values"] = [
        {"field_id": getattr(settings, field), "values": [{"value": int(datetime.combine(target, datetime.min.time(), MSK).timestamp())}]}]
    if store is not None:
        data["custom_fields_values"].append({"field_id": 3158921, "values": [{"enum_id":store}]})
    return data


def calendar_job(db, data, kind="meeting_today"):
    sync_calendar(db, [data], CalendarSettings(), CAL_NOW-timedelta(days=1))
    j = db.scalar(select(CalendarJob).where(CalendarJob.kind == kind))
    j.plan = [{"transport": t,"channelId":t,"recipient":{"chatId":"79990000001"}}
              for t in ("whatsapp","telegram","max")]
    j.contact_id = 123
    db.commit()
    return j


def calrun(db, j, c, cfg, now=None):
    if not hasattr(c,"contact"):
        c.contact={"id":123,"custom_fields_values":[
            {"field_id":3346485,"values":[{"value":"79990000001"}]},
            {"field_code":"PHONE","values":[{"value":"79990000001","enum_code":"WORK"}]}]}
    now = now or j.due_at.replace(tzinfo=timezone.utc)
    send_calendar(db,j,c,cfg,ReminderSettings(),CalendarSettings(mode="live"),now,clock=lambda:now)


@pytest.mark.parametrize("kind",CALENDAR_RULES)
def test_calendar_schedule_and_template_selection(db,cfg,kind):
    c=Fake([(201,{"messageId":"cal-1"})]);c.document=calendar_lead(kind)
    j=calendar_job(db,c.document,kind)
    _,_,_,hour,minute=CALENDAR_RULES[kind]
    assert (j.due_at.replace(tzinfo=timezone.utc).astimezone(MSK).hour,j.due_at.minute)==(hour,minute)
    calrun(db,j,c,cfg,j.due_at.replace(tzinfo=timezone.utc)-timedelta(seconds=1))
    assert not c.posts
    calrun(db,j,c,cfg)
    assert j.state=="pending" and len(c.posts)==1
    assert c.posts[0]["chatType"]=="whatsapp"
    assert "calendar-17-" in c.posts[0]["crmMessageId"]
    assert c.posts[0]["text"]==render_calendar(c.document,j,cfg)
    calrun(db,j,c,cfg)
    assert len(c.posts)==1


@pytest.mark.parametrize("kind",CALENDAR_RULES)
def test_calendar_changed_date_or_stage_prevents_post(db,cfg,kind):
    c=Fake();c.document=calendar_lead(kind);j=calendar_job(db,c.document,kind)
    c.document=calendar_lead(kind,target=j.target_date+timedelta(days=1))
    calrun(db,j,c,cfg)
    assert j.state=="cancelled" and not c.posts


def test_calendar_date_changes_during_preflight(db,cfg):
    c=Fake();c.document=calendar_lead();j=calendar_job(db,c.document)
    get=c.get;calls=[]
    def changing(path,**kwargs):
        data=get(path,**kwargs)
        if path.startswith('/api/v4/leads/'):
            calls.append(path)
            if len(calls)>1:return calendar_lead(target=j.target_date+timedelta(days=1))
        return data
    c.get=changing;calrun(db,j,c,cfg)
    assert j.state=="cancelled" and not c.posts


@pytest.mark.parametrize("store,phrase",[(4765787,"ул. Пятницкая, д. 8"),(4765789,"Спартаковская пл., д. 14"),(4893873,"Адрес №2:"),(None,"Адрес №2:")])
def test_calendar_meeting_addresses(db,cfg,store,phrase):
    data=calendar_lead(store=store);j=calendar_job(db,data)
    assert phrase in render_calendar(data,j,cfg)
    assert "8 931 009 02 51" in render_calendar(data,j,cfg)


@pytest.mark.parametrize("kind",["rental_pickup","rental_return"])
@pytest.mark.parametrize("store",[None,4893873,5171717])
def test_calendar_rental_missing_or_online_store_never_guessed(db,cfg,kind,store):
    c=Fake();c.document=calendar_lead(kind,store);j=calendar_job(db,c.document,kind)
    calrun(db,j,c,cfg)
    assert not c.posts and j.state=="blocked"


def test_calendar_followup_exact_text(db,cfg):
    data=calendar_lead('meeting_followup',None);j=calendar_job(db,data,'meeting_followup')
    assert render_calendar(data,j,cfg)=="Здравствуйте! Удалось ли посетить нас вчера, подобрали желаемый образ?🙌🏻"


def test_calendar_restart_same_event_no_duplicate_and_new_date_is_new_job(db,cfg):
    c=Fake([(201,{"messageId":"mcal"})]);c.document=calendar_lead();j=calendar_job(db,c.document)
    calrun(db,j,c,cfg)
    for _ in range(3):sync_calendar(db,[c.document],CalendarSettings(),CAL_NOW)
    assert len(list(db.scalars(select(CalendarJob))))==2  # today plus tomorrow's followup
    assert len(list(db.scalars(select(CalendarAttempt))))==1
    later=calendar_lead(target=j.target_date+timedelta(days=2))
    sync_calendar(db,[later],CalendarSettings(),CAL_NOW)
    assert len(list(db.scalars(select(CalendarJob))))==4
    calrun(db,j,c,cfg)
    assert len(c.posts)==1


@pytest.mark.parametrize("result,state",[(httpx.ReadTimeout('lost'),'uncertain'),((500,{}),'uncertain'),((201,{'messageId':'mcal'}),'pending')])
def test_calendar_uncertain_or_pending_never_fans_out(db,cfg,result,state):
    c=Fake([result]);c.document=calendar_lead();j=calendar_job(db,c.document)
    calrun(db,j,c,cfg);calrun(db,j,c,cfg)
    assert j.state==state and len(c.posts)==1


def test_calendar_confirmed_failure_then_success_is_terminal(db,cfg):
    c=Fake([(400,{'error':'BAD_CONTACT'}),(201,{'messageId':'mcal'})]);c.document=calendar_lead();j=calendar_job(db,c.document)
    calrun(db,j,c,cfg)
    assert [p['chatType'] for p in c.posts]==['whatsapp','telegram']
    db.add(ReminderEvent(key='cal_sent',message_id='mcal',status='sent'));db.commit()
    reconcile(db,CAL_NOW,job_type=CalendarJob,attempt_type=CalendarAttempt,dispatch=lambda stamp:stamp)
    calrun(db,j,c,cfg)
    assert j.state=='sent' and len(c.posts)==2


def test_calendar_late_initial_send_expires(db,cfg):
    c=Fake();c.document=calendar_lead();j=calendar_job(db,c.document)
    calrun(db,j,c,cfg,j.due_at.replace(tzinfo=timezone.utc)+timedelta(hours=1))
    assert j.state=='expired' and not c.posts


@pytest.mark.parametrize('source,expected', [('com.wazzup24.wz',['whatsapp','telegram','max']),
    ('com.wazzup24.avito',['telegram','whatsapp','max','avito']),
    ('com.wazzup24.instagram',['telegram','whatsapp','max','instagram']),
    ('com.wazzup24',['telegram','whatsapp','max'])])
def test_calendar_routing_never_adds_unseen_social_or_vk(db,cfg,source,expected):
    c=Fake();c.talks=[{'talk_id':1,'contact_id':123,'origin':source}]
    plan=resolve_plan(db,c,lead(),{'id':123},cfg,ReminderSettings(),calendar=True)
    assert [p['transport'] for p in plan]==expected


def test_calendar_website_uses_direct_only(db,cfg,monkeypatch):
    monkeypatch.setenv('CALENDARNOTIFY_WEBSITE_ORIGIN','verified-site-origin')
    c=Fake();c.talks=[{'talk_id':1,'contact_id':123,'origin':'verified-site-origin'}]
    plan=resolve_plan(db,c,lead(),{'id':123},cfg,ReminderSettings(),calendar=True)
    assert [p['transport'] for p in plan]==['telegram','whatsapp','max']


def test_calendar_followup_reuses_successful_channel_when_no_new_incoming(db,cfg):
    data=calendar_lead();j=calendar_job(db,data);j.state='sent'
    db.add(CalendarAttempt(job_id=j.id,position=0,transport='whatsapp',state='sent',crm_message_id='prior-day'));db.commit()
    follow=db.scalar(select(CalendarJob).where(CalendarJob.kind=='meeting_followup'))
    c=Fake();c.talks=[{'talk_id':1,'contact_id':123,'origin':'com.wazzup24-1'}]
    plan=resolve_plan(db,c,data,{'id':123},cfg,ReminderSettings(),calendar=True,calendar_job=follow)
    assert plan[0]['transport']=='whatsapp'


def test_calendar_changed_recipient_is_not_sent_to_old_phone(db,cfg):
    c=Fake();c.document=calendar_lead();j=calendar_job(db,c.document)
    c.contact={'id':123,'custom_fields_values':[{'field_code':'PHONE','values':[{'value':'79990000002'}]}]}
    calrun(db,j,c,cfg)
    assert j.state=='cancelled' and j.reason=='recipient_changed' and not c.posts


def test_calendar_native_instagram_is_social_fallback_only(db,cfg):
    c=Fake();c.talks=[{'talk_id':1,'contact_id':123,'origin':'instagram_business'}]
    plan=resolve_plan(db,c,lead(),{'id':123},cfg,ReminderSettings(),calendar=True)
    assert [p['transport'] for p in plan]==['telegram','whatsapp','max','instagram']


def test_calendar_newer_incoming_overrides_yesterdays_success(db,cfg):
    data=calendar_lead();j=calendar_job(db,data);j.state='sent'
    db.add(CalendarAttempt(job_id=j.id,position=0,transport='whatsapp',state='sent',crm_message_id='prior-day'));db.commit()
    follow=db.scalar(select(CalendarJob).where(CalendarJob.kind=='meeting_followup'))
    c=Fake();c.talks=[{'talk_id':1,'contact_id':123,'origin':'com.wazzup24.max'}]
    c.events=[{'created_at':int(j.due_at.replace(tzinfo=timezone.utc).timestamp())+10,
        'value_after':[{'message':{'talk_id':1,'origin':'com.wazzup24.max'}}]}]
    plan=resolve_plan(db,c,data,{'id':123},cfg,ReminderSettings(),calendar=True,calendar_job=follow)
    assert plan[0]['transport']=='max'


def test_calendar_late_failure_does_not_use_next_channel_after_scheduled_day(db,cfg):
    c=Fake([(201,{'messageId':'pending-cal'})]);c.document=calendar_lead();j=calendar_job(db,c.document)
    calrun(db,j,c,cfg)
    db.add(ReminderEvent(key='late-cal-error',message_id='pending-cal',status='error'));db.commit()
    later=j.due_at.replace(tzinfo=timezone.utc)+timedelta(days=1)
    reconcile(db,later,job_type=CalendarJob,attempt_type=CalendarAttempt,dispatch=lambda stamp:stamp)
    calrun(db,j,c,cfg,later)
    assert len(c.posts)==1 and j.state=='expired'
