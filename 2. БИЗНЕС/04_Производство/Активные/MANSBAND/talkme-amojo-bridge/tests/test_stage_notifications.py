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


@pytest.mark.parametrize("kind",["meeting_today","meeting_followup","rental_pickup","rental_return"])
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


@pytest.mark.parametrize("kind",["meeting_today","meeting_followup","rental_pickup","rental_return"])
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


from datetime import date
from app.stage_notifications import (calendar_due, normalize_recipient_phone,
    certificate_phone, certificate_plan, CERTIFICATE_KINDS, EXTRA_KINDS,
    CERTIFICATE_MONTH, CERTIFICATE_FORTNIGHT, WANTS_ORDER)


@pytest.mark.parametrize('raw,expected',[
    ('+7 (999) 000-00-02','79990000002'),('8 (999) 000-00-02','79990000002'),
    ('9990000002','79990000002'),(79990000002,'79990000002'),
    (79990000002.0,'79990000002'),('79990000002.0','79990000002'),
    ('7.9990000002e10','79990000002'),('+375 29 123-45-67','375291234567')])
def test_recipient_phone_normalization(raw,expected):
    assert normalize_recipient_phone(raw)==expected


@pytest.mark.parametrize('raw',[None,'',True,0,False,'79990000001,79990000002',
    '79990000001/79990000002','79990000001\n79990000002','call 79990000002',
    '79990000002 доб.123','12345',float('nan'),float('inf'),79990000002.5,'1e1000'])
def test_recipient_phone_refuses_ambiguous_or_invalid_data(raw):
    with pytest.raises(ValueError):normalize_recipient_phone(raw)


@pytest.mark.parametrize('target,expected',[
    (date(2026,11,29),date(2026,10,29)),(date(2026,3,31),date(2026,2,28)),
    (date(2028,3,31),date(2028,2,29)),(date(2027,1,31),date(2026,12,31)),
    (date(2026,5,31),date(2026,4,30))])
def test_certificate_month_is_calendar_month_not_30_days(target,expected):
    out=calendar_due('certificate_month',target).astimezone(MSK)
    assert out.date()==expected and (out.hour,out.minute)==(11,0)


def test_fortnight_is_14_days_and_order_is_10_moscow():
    assert calendar_due('certificate_fortnight',date(2026,11,12)).astimezone(MSK)==datetime(2026,10,29,11,tzinfo=MSK)
    assert calendar_due('wants_order',date(2026,10,29)).astimezone(MSK)==datetime(2026,10,29,10,tzinfo=MSK)


def extra_lead(kind='certificate_month',phone='8 (999) 000-00-02'):
    settings=CalendarSettings(extra_enabled=True)
    status,field,_,_,_=CALENDAR_RULES[kind]
    target={'certificate_month':date(2026,11,29),'certificate_fortnight':date(2026,11,12),'wants_order':date(2026,10,29)}[kind]
    data=lead(getattr(settings,status))
    data['custom_fields_values']=[{'field_id':getattr(settings,field),'values':[
        {'value':int(datetime.combine(target,datetime.min.time(),MSK).timestamp())}]}]
    if phone is not None:
        data['custom_fields_values'].append({'field_id':settings.recipient_phone_field,'values':[{'value':phone}]})
    return data


class RecipientFake(Fake):
    def __init__(self,responses=()):
        super().__init__(responses)
        self.recipient_contacts=[]
        self.reads=[]
    def get(self,path,**kwargs):
        self.reads.append(path)
        if path=='/api/v4/contacts':return {'_embedded':{'contacts':self.recipient_contacts}}
        if path.startswith('/api/v4/contacts/'):
            return next((x for x in self.recipient_contacts if str(x['id'])==path.rsplit('/',1)[1]),{'id':123})
        return super().get(path,**kwargs)


def extra_job(db,c,kind='certificate_month'):
    sync_calendar(db,[c.document],CalendarSettings(extra_enabled=True),CAL_NOW-timedelta(days=1))
    return db.scalar(select(CalendarJob).where(CalendarJob.kind==kind))


def extra_run(db,j,c,cfg,now=None):
    now=now or j.due_at.replace(tzinfo=timezone.utc)
    send_calendar(db,j,c,cfg,ReminderSettings(telegram_channel='telegram',whatsapp_channel='whatsapp',max_channel='max'),
        CalendarSettings(mode='live',extra_enabled=True),now,clock=lambda:now)


@pytest.mark.parametrize('kind',sorted(CERTIFICATE_KINDS))
def test_certificate_sends_only_to_lead_recipient_without_buyer_contact(db,cfg,kind):
    c=RecipientFake([(201,{'messageId':'recipient-message'})]);c.document=extra_lead(kind)
    j=extra_job(db,c,kind);extra_run(db,j,c,cfg)
    assert j.state=='pending' and len(c.posts)==1
    assert c.posts[0]['phone']=='79990000002' and c.posts[0]['chatType']=='telegram'
    assert '/api/v4/contacts/123' not in c.reads
    assert '79990000001' not in str(c.posts)
    extra_run(db,j,c,cfg)
    assert len(c.posts)==1


@pytest.mark.parametrize('phone',[None,'not a phone','79990000001,79990000002'])
def test_certificate_never_falls_back_to_buyer_when_phone_invalid(db,cfg,phone):
    c=RecipientFake();c.document=extra_lead(phone=phone)
    j=extra_job(db,c);extra_run(db,j,c,cfg)
    assert j.state=='blocked' and not c.posts and '/api/v4/contacts/123' not in c.reads
    assert not list(db.scalars(select(CalendarAttempt)))


def test_certificate_uses_recipient_history_but_not_their_unrelated_telegram_id(db,cfg):
    c=RecipientFake([(201,{'messageId':'recipient-message'})]);c.document=extra_lead()
    c.recipient_contacts=[{'id':999,'custom_fields_values':[
        {'field_code':'PHONE','values':[{'value':'+7 999 000-00-02'}]},
        {'field_id':3346485,'values':[{'value':'unrelated-telegram-id'}]}]}]
    c.talks=[{'talk_id':1,'contact_id':999,'origin':'com.wazzup24.wz'}]
    j=extra_job(db,c);extra_run(db,j,c,cfg)
    assert c.posts[0]['chatType']=='whatsapp' and c.posts[0]['chatId']=='79990000002'
    assert 'unrelated-telegram-id' not in str(c.posts)
    assert '/api/v4/contacts/123' not in c.reads


def test_certificate_search_requires_exact_phone_match(db,cfg):
    c=RecipientFake([(201,{'messageId':'recipient-message'})]);c.document=extra_lead()
    c.recipient_contacts=[{'id':123,'custom_fields_values':[{'field_code':'PHONE','values':[{'value':'79990000001'}]}]}]
    c.talks=[{'talk_id':1,'contact_id':123,'origin':'com.wazzup24.wz'}]
    j=extra_job(db,c);extra_run(db,j,c,cfg)
    assert c.posts[0]['chatType']=='telegram' and c.posts[0]['phone']=='79990000002'
    assert '/api/v4/talks' not in c.reads


def test_certificate_phone_change_during_preflight_stops_send(db,cfg):
    c=RecipientFake();c.document=extra_lead();j=extra_job(db,c);original=c.get;reads=[]
    def changing(path,**kwargs):
        data=original(path,**kwargs)
        if path.startswith('/api/v4/leads/'):
            reads.append(path)
            if len(reads)>1:return extra_lead(phone='79990000003')
        return data
    c.get=changing;extra_run(db,j,c,cfg)
    assert j.state=='cancelled' and not c.posts


def test_certificate_confirmed_failure_changes_only_transport(db,cfg):
    c=RecipientFake([(400,{'error':'BAD_CONTACT'}),(201,{'messageId':'recipient-message'})]);c.document=extra_lead()
    j=extra_job(db,c);extra_run(db,j,c,cfg)
    assert [p['chatType'] for p in c.posts]==['telegram','whatsapp']
    assert c.posts[0]['phone']==c.posts[1]['chatId']=='79990000002'


def test_certificate_network_uncertainty_never_uses_next_channel(db,cfg):
    c=RecipientFake([httpx.ReadTimeout('unknown')]);c.document=extra_lead()
    j=extra_job(db,c);extra_run(db,j,c,cfg);extra_run(db,j,c,cfg)
    assert j.state=='uncertain' and len(c.posts)==1


def test_new_scenarios_use_separate_keys_and_preserve_templates(db,cfg):
    c=RecipientFake();c.document=extra_lead();extra_job(db,c)
    for _ in range(3):sync_calendar(db,[c.document],CalendarSettings(extra_enabled=True),CAL_NOW)
    jobs=list(db.scalars(select(CalendarJob)))
    assert len(jobs)==2 and {j.kind for j in jobs}==CERTIFICATE_KINDS
    assert render_calendar(c.document,next(j for j in jobs if j.kind=='certificate_month'),cfg)==CERTIFICATE_MONTH
    assert render_calendar(c.document,next(j for j in jobs if j.kind=='certificate_fortnight'),cfg)==CERTIFICATE_FORTNIGHT
    assert 'остался 1 месяц' in CERTIFICATE_MONTH and 'осталось 2 НЕДЕЛИ' in CERTIFICATE_FORTNIGHT
    assert '(Примерка без записи)' in CERTIFICATE_MONTH and '(Примерка без записи)' not in CERTIFICATE_FORTNIGHT


def test_wants_order_uses_approximate_date_and_existing_client_routing(db,cfg):
    c=Fake([(201,{'messageId':'order-message'})]);c.document=extra_lead('wants_order',phone=None)
    c.contact={'id':123,'custom_fields_values':[{'field_id':3346485,'values':[{'value':'tg-client'}]}]}
    j=extra_job(db,c,'wants_order');j.plan=[{'transport':'telegram','channelId':'telegram','recipient':{'chatId':'tg-client'}}];j.contact_id=123;db.commit()
    extra_run(db,j,c,cfg)
    assert j.state=='pending' and c.posts[0]['chatId']=='tg-client' and c.posts[0]['text']==WANTS_ORDER


def test_new_rules_do_not_activate_before_explicit_enable(db):
    sync_calendar(db,[extra_lead()],CalendarSettings(),CAL_NOW)
    assert not list(db.scalars(select(CalendarJob)))


def test_certificate_duplicate_recipient_cards_do_not_choose_buyer_history(db,cfg):
    c=RecipientFake();c.document=extra_lead()
    c.recipient_contacts=[{'id':i,'custom_fields_values':[{'field_code':'PHONE','values':[{'value':'79990000002'}]}]} for i in (123,999)]
    j=extra_job(db,c);extra_run(db,j,c,cfg)
    assert j.state=='blocked' and j.reason=='ambiguous_recipient_history' and not c.posts


@pytest.mark.parametrize('kind',sorted(EXTRA_KINDS))
@pytest.mark.parametrize('change',['stage','date'])
def test_new_scenarios_recheck_stage_and_date(db,cfg,kind,change):
    c=RecipientFake();c.document=extra_lead(kind);j=extra_job(db,c,kind)
    if change=='stage':c.document['status_id']=142
    else:c.document['custom_fields_values'][0]['values'][0]['value']+=86400
    extra_run(db,j,c,cfg)
    assert j.state=='cancelled' and not c.posts
