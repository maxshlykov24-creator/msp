from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import message_templates as m
from app.reservation_reminders import ReminderEvent, MSK
from app.stage_notifications import order_for

STAMP=datetime(2026,10,30,10,tzinfo=MSK).astimezone(timezone.utc)

@pytest.fixture
def db():
    e=create_engine('sqlite://')
    for table in [*m.TABLES,ReminderEvent.__table__]:table.create(e)
    with Session(e) as d:
        route=m.Routing().model_dump();d.add(m.Document(id='routing',kind='routing',draft=route,active=route));d.commit()
        yield d


def rule(**patch):
    data={'name':'Тест','enabled':True,'pipelines':[9601214],'statuses':[77086494],
          'trigger':{'type':'date','field':3810739,'offset':1,'time':'10:00'},'content':'Здравствуйте {{lead.id}}'}
    data.update(patch);return m.Rule(**data)


def lead():
    return {'id':17,'pipeline_id':9601214,'status_id':77086494,'custom_fields_values':[
        {'field_id':3810737,'values':[{'enum_id':5452539,'value':'Бесплатная'}]},
        {'field_id':3810739,'values':[{'value':int(datetime(2026,10,29,tzinfo=MSK).timestamp())}]}]}


def doc(db,r=None):
    data=(r or rule()).model_dump();d=m.Document(id='test-rule',kind='rule',draft=data,active=data,published_at=STAMP-timedelta(days=3));db.add(d);db.commit();return d


def job(db,d):
    j=m.Job(rule_id=d.id,lead_id=17,occurrence='2026-10-29',due_at=STAMP);db.add(j);db.commit();return j


@pytest.mark.parametrize('op,value,expected',[('eq',5452539,True),('ne',5452541,True),('eq',5452541,False),('empty',None,False),('filled',None,True)])
def test_conditions(op,value,expected):
    assert m.condition(lead(),m.Condition(field=3810737,op=op,value=value))==expected


def test_missing_numeric_never_matches():
    assert not m.condition(lead(),m.Condition(field=1,op='gt',value=3))


def test_date_and_calendar_month():
    assert m.date_due(lead(),rule())==STAMP
    data=lead();data['custom_fields_values'][1]['values'][0]['value']=int(datetime(2028,3,31,tzinfo=MSK).timestamp())
    r=rule(trigger={'type':'date','field':3810739,'offset':-1,'unit':'months','time':'11:00'})
    assert m.date_due(data,r).astimezone(MSK)==datetime(2028,2,29,11,tzinfo=MSK)


def test_render_missing_or_unknown_marker_blocks():
    with pytest.raises(ValueError):m.render(lead(),rule(content='{{lead.field.123}}'))
    with pytest.raises(ValueError):m.render(lead(),rule(content='{{untrusted}}'))
    assert m.render(lead(),rule())=='Здравствуйте 17'


def test_store_variants_never_guess():
    with pytest.raises(ValueError):m.render(lead(),rule(variants={'12':'магазин'}))
    assert m.render(lead(),rule(variants={'default':'общий'}))=='общий'


@pytest.mark.parametrize('source,expected',[(None,['max','telegram','whatsapp']),('whatsapp',['whatsapp','max','telegram']),('instagram',['max','telegram','whatsapp','instagram']),('vk',['max','telegram','whatsapp'])])
def test_configurable_routing(source,expected):
    assert order_for(source,{'order':['max','telegram','whatsapp'],'previous_first':True,'social_fallback':True})==expected


def test_invalid_routing_rejected():
    with pytest.raises(ValueError):m.Routing(order=['telegram','telegram','max'])


class Fake:
    def __init__(self,response=(201,{'messageId':'m1'})):
        self.wz=self;self.document=lead();self.posts=[];self.response=response
    def get(self,path,**kwargs):
        if path.startswith('/api/v4/leads/'):return self.document
        if path=='/channels':return httpx.Response(200,json=[{'channelId':'tg','transport':'tgapi','state':'active'}])
        raise AssertionError(path)
    def post(self,path,json):
        self.posts.append(json)
        if isinstance(self.response,Exception):raise self.response
        return httpx.Response(self.response[0],json=self.response[1])


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(m,'now',lambda:STAMP)
    monkeypatch.setattr(m,'catalog',lambda c:{})
    monkeypatch.setattr(m,'validate_catalog',lambda r,c:None)
    monkeypatch.setattr(m,'make_plan',lambda *args:([{'transport':'telegram','channelId':'tg','recipient':{'phone':'79990000001'}}],'key'))
    return Fake()


def test_send_pending_and_duplicate_claim(db,transport):
    j=job(db,doc(db));m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    assert len(transport.posts)==1 and j.state=='pending'
    db.add(ReminderEvent(key='cb',message_id='m1',status='delivered'));db.commit();m.reconcile(db)
    assert j.state=='delivered'


def test_unknown_never_retries(db,transport):
    transport.response=httpx.ReadTimeout('lost');j=job(db,doc(db));m.send(db,j,transport,STAMP)
    assert j.state=='uncertain'
    # Dispatcher only selects ready/blocked; no unknown attempt can be repeated.
    m.send(db,j,transport,STAMP)
    assert len(transport.posts)==1


def test_changed_recipient_no_post(db,transport):
    d=doc(db);j=job(db,d);j.snapshot=m.snapshot(db,d);j.recipient_key='different';db.commit()
    m.send(db,j,transport,STAMP)
    assert not transport.posts and j.state=='blocked'


def test_disabled_no_post(db,transport):
    d=doc(db);j=job(db,d);d.active={**d.active,'enabled':False};db.commit()
    m.send(db,j,transport,STAMP);assert not transport.posts and j.state=='cancelled'


def test_late_no_catchup(db,transport):
    j=job(db,doc(db));m.send(db,j,transport,STAMP+timedelta(minutes=6))
    assert not transport.posts and j.state=='expired'


def test_seed_idempotent(db):
    m.seed(db);m.seed(db)
    docs=list(db.scalars(select(m.Document)))
    assert len(docs)==15 # routing + 14 messenger rules, excluding certificate email
    assert not any('mail' in d.id for d in docs)


def form(stamp,old=76654894):
    return {f'leads[status][0][{k}]':[str(v)] for k,v in {'id':17,'status_id':77086494,'old_status_id':old,'pipeline_id':9601214,'account_id':32417154,'last_modified':int(stamp.timestamp())}.items()}


def test_each_reentry_new_job_but_duplicate_and_late_event_ignored(db,monkeypatch):
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live')
    doc(db,rule(trigger={'type':'stage','delay_minutes':60,'repeat':'each_entry'}))
    assert m.ingest(db,form(STAMP),STAMP)==1
    assert m.ingest(db,form(STAMP),STAMP)==0
    assert m.ingest(db,form(STAMP+timedelta(minutes=2)),STAMP+timedelta(minutes=2))==1
    assert m.ingest(db,form(STAMP-timedelta(seconds=1)),STAMP)==0
    jobs=list(db.scalars(select(m.Job).order_by(m.Job.id)))
    assert len(jobs)==2 and jobs[0].state=='cancelled' and jobs[1].state=='ready'


def test_once_preserves_legacy_repeat(db,monkeypatch):
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live');doc(db,rule(trigger={'type':'stage','repeat':'once'}))
    assert m.ingest(db,form(STAMP),STAMP)==1
    assert m.ingest(db,form(STAMP+timedelta(hours=1)),STAMP+timedelta(hours=1))==0


class CRM:
    def __init__(self):
        self.amo=self;self.posts=[];self.patches=[];self.deletes=[]
        self.remote={'id':5,'name':'Приветствие','content':'Привет {{contact.name}}','type':'amocrm','buttons':[{'type':'url','url':'https://example.test','text':'Сайт'}],'attachment':{'id':'asset'},'updated_at':100}
        self.uncertain=False
        self.fields=[{'id':3810739,'type':'date','name':'Отложка до'},{'id':3810737,'type':'select','name':'Отложка','enums':[{'id':5452539,'value':'Бесплатная'}]}]
    def get(self,path,**params):
        if path=='/api/v4/leads/pipelines':return {'_embedded':{'pipelines':[{'id':9601214,'_embedded':{'statuses':[{'id':77086494}]}}]}}
        if path=='/api/v4/leads/custom_fields':return {'_embedded':{'custom_fields':self.fields}}
        if path=='/api/v4/leads':return {'_embedded':{'leads':[lead()]}}
        if path.startswith('/api/v4/leads/'):return lead()
        if path=='/api/v4/chats/templates':return {'_embedded':{'chat_templates':[self.remote] if self.remote else []}}
        if path.startswith('/api/v4/chats/templates/'):return self.remote
        raise AssertionError(path)
    def post(self,path,json):
        self.posts.append(json)
        self.remote={**json[0],'id':7,'updated_at':101}
        if self.uncertain:raise httpx.ReadTimeout('lost reply')
        return httpx.Response(200,request=httpx.Request('POST','https://example.test'),json={'_embedded':{'chat_templates':[self.remote]}})
    def patch(self,path,json):
        self.patches.append(json);self.remote={**self.remote,**json,'updated_at':101}
        return httpx.Response(200,request=httpx.Request('PATCH','https://example.test'),json=self.remote)
    def delete(self,path):
        self.deletes.append(path);self.remote=None
        return httpx.Response(204,request=httpx.Request('DELETE','https://example.test'))


def manual_draft(db,c):
    payload={'remote_id':5,'name':'Новое название','content':'Новый текст','source_hash':m.remote_digest(c.remote)}
    data=m.Change(id='manual-5',kind='manual',version=0,payload=payload,actor='admin')
    m.draft(data,db,c)
    return db.get(m.Document,'manual-5')


def test_manual_patch_preserves_attachments_and_markers(db):
    c=CRM();d=manual_draft(db,c)
    m.check(d.id,{'version':1},db,c)
    out=m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    assert out['remote_id']==5
    assert c.remote['attachment']=={'id':'asset'} and c.remote['buttons'][0]['text']=='Сайт'
    assert set(c.patches[0])=={'name','content'}
    assert db.get(m.Document,d.id).active['source_hash']==m.remote_digest(c.remote)


def test_manual_external_change_conflict(db):
    from fastapi import HTTPException
    c=CRM();d=manual_draft(db,c);c.remote={**c.remote,'content':'Изменено в CRM'}
    with pytest.raises(HTTPException) as exc:m.check(d.id,{'version':1},db,c)
    assert exc.value.status_code==409 and not c.patches


def test_manual_unknown_create_recovered_without_repost(db):
    from fastapi import HTTPException
    c=CRM();c.uncertain=True
    data={'name':'Новый шаблон','content':'Текст'}
    m.draft(m.Change(id='new-manual',kind='manual',version=0,payload=data,actor='admin'),db,c)
    d=db.get(m.Document,'new-manual');m.check(d.id,{'version':1},db,c)
    with pytest.raises(HTTPException):m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    result=m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    assert result['remote_id']==7 and len(c.posts)==1


def test_manual_delete_requires_confirmation(db):
    from fastapi import HTTPException
    c=CRM();d=manual_draft(db,c);d.draft={**d.draft,'delete':True};db.commit();m.check(d.id,{'version':1},db,c)
    with pytest.raises(HTTPException):m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    assert not c.deletes
    m.publish(d.id,m.Publish(version=1,actor='admin',delete_confirmed=True),db,c)
    assert len(c.deletes)==1 and d.active['original']['attachment']=={'id':'asset'}


def test_rule_publish_requires_check_and_expected_version(db):
    from fastapi import HTTPException
    c=CRM();d=doc(db)
    with pytest.raises(HTTPException):m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    with pytest.raises(HTTPException):m.draft(m.Change(id=d.id,kind='rule',version=0,payload=d.draft,actor='admin'),db,c)
    assert not list(db.scalars(select(m.History)))


def test_schema_deletion_and_enum_mismatch_blocks():
    c=CRM();r=rule(conditions=[{'field':3810737,'op':'eq','value':5452539}]);cat=m.catalog(c)
    m.validate_catalog(r,cat)
    cat['fields'][1]['enums']=[]
    with pytest.raises(ValueError):m.validate_catalog(r,cat)
    with pytest.raises(ValueError):m.validate_catalog(rule(content='{{lead.date.3810737}}'),cat)


def test_preview_never_posts_and_reports_overlap(db,monkeypatch):
    monkeypatch.setattr(m,"make_plan",lambda *args:([{"transport":"telegram","recipient":{"phone":"79990000001"}}],"key"))
    c=CRM();d=doc(db)
    second=rule(name='Другой').model_dump();db.add(m.Document(id='other',kind='rule',draft=second,active=second));db.commit()
    result=m.check(d.id,{'version':1},db,c)
    assert result['total']==1 and result['overlaps'][0]['id']=='other' and not c.posts


def test_routing_publish_preserves_started_snapshot(db):
    c=CRM();d=doc(db);j=job(db,d);j.snapshot=m.snapshot(db,d);j.plan=[{'transport':'telegram'}]
    db.add(m.Attempt(job_id=j.id,position=0,transport='telegram',state='error',crm_id='old'));db.commit()
    route=db.get(m.Document,'routing');route.draft={**route.draft,'order':['max','whatsapp','telegram']};route.checked=m.digest([route.version,route.draft]);db.commit()
    m.publish('routing',m.Publish(version=route.version,actor='admin'),db,c)
    assert j.snapshot['routing']['order']==['telegram','whatsapp','max']
    assert route.active['order']==['max','whatsapp','telegram']


def test_migration_preserves_sent_and_uncertain_ledger(db):
    from app import stage_notifications as st
    from app.reservation_reminders import ReminderJob,ReminderAttempt
    for table in [*st.TABLES,ReminderJob.__table__,ReminderAttempt.__table__]:
        table.create(db.bind,checkfirst=True)
    m.seed(db)
    old=st.CalendarJob(lead_id=99,status_id=76654894,kind='meeting_today',target_date=date(2026,10,30),due_at=STAMP,check_at=STAMP,state='pending')
    db.add(old);db.flush();db.add(st.CalendarAttempt(job_id=old.id,position=0,transport='telegram',state='pending',message_id='accepted',crm_message_id='legacy-original'));db.commit()
    with pytest.raises(RuntimeError):m.migrate_jobs(db)
    assert not list(db.scalars(select(m.Job)))
    old.state='sent';db.commit()
    m.migrate_jobs(db);m.migrate_jobs(db)
    j=db.scalar(select(m.Job));a=db.scalar(select(m.Attempt))
    assert j.state=='sent' and a.message_id=='accepted' and a.crm_id=='legacy-original'
    db.add(ReminderEvent(key='late',message_id='accepted',status='sent'));db.commit();m.reconcile(db)
    assert j.state=='sent' and len(list(db.scalars(select(m.Job))))==1


def test_internal_read_requires_key(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI();app.include_router(m.router)
    monkeypatch.setenv('REMINDER_ADMIN_KEY','a-secret-key-long-enough')
    with TestClient(app) as client:
        response=client.get('/internal/templates')
        assert response.status_code==403


def test_recipient_phone_routing_order_without_prior_history(db,monkeypatch):
    from app.stage_notifications import certificate_plan,StageSettings,CalendarSettings
    from app.reservation_reminders import ReminderSettings
    class NoHistory:
        def get(self,path,**params):
            assert path=='/api/v4/contacts'
            return {}
    data={'custom_fields_values':[{'field_id':3830052,'values':[{'value':'79990000001'}]}]}
    plan=certificate_plan(db,NoHistory(),data,StageSettings(),ReminderSettings(),CalendarSettings(),routing={'order':['max','whatsapp','telegram'],'previous_first':True,'social_fallback':True})
    assert [p['transport'] for p in plan]==['max','whatsapp','telegram']


def test_newer_event_does_not_resurrect_old_entry(db,monkeypatch):
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live');d=doc(db,rule(trigger={'type':'stage','delay_minutes':60}))
    m.ingest(db,form(STAMP+timedelta(minutes=2)),STAMP+timedelta(minutes=2))
    m.ingest(db,form(STAMP),STAMP+timedelta(minutes=2))
    assert len(list(db.scalars(select(m.Job))))==1


def test_unknown_create_cannot_be_bypassed_by_new_draft_version(db):
    from fastapi import HTTPException
    c=CRM();c.uncertain=True
    payload={'name':'Новый','content':'Текст'}
    m.draft(m.Change(id='same-doc',kind='manual',version=0,payload=payload,actor='admin'),db,c)
    d=db.get(m.Document,'same-doc');m.check(d.id,{'version':1},db,c)
    with pytest.raises(HTTPException):m.publish(d.id,m.Publish(version=1,actor='admin'),db,c)
    m.draft(m.Change(id=d.id,kind='manual',version=1,payload={**payload,'content':'Изменённый'},actor='admin'),db,c)
    m.check(d.id,{'version':2},db,c)
    with pytest.raises(HTTPException):m.publish(d.id,m.Publish(version=2,actor='admin'),db,c)
    assert len(c.posts)==1


def test_publish_reschedules_unstarted_job_immediately(db):
    c=CRM();d=doc(db);j=job(db,d)
    d.draft={**d.draft,'trigger':{**d.draft['trigger'],'offset':2}}
    d.checked=m.digest([d.version,d.draft]);db.commit()
    m.publish(d.id,m.Publish(version=d.version,actor='admin'),db,c)
    assert m.aware(j.due_at)==STAMP+timedelta(days=1) and j.snapshot is None


def test_once_unattempted_reentry_restarts_delay(db,monkeypatch):
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live');doc(db,rule(trigger={'type':'stage','delay_minutes':60,'repeat':'once'}))
    m.ingest(db,form(STAMP),STAMP)
    later=STAMP+timedelta(minutes=2);m.ingest(db,form(later),later)
    j=db.scalar(select(m.Job));assert m.aware(j.due_at)==later+timedelta(minutes=60)


def test_disable_broken_rule_does_not_require_deleted_fields(db):
    c=CRM();d=doc(db);j=job(db,d);c.fields=[]
    d.draft={**d.draft,'enabled':False};db.commit()
    result=m.check(d.id,{'version':d.version},db,c)
    assert result['disabled'] is True
    m.publish(d.id,m.Publish(version=d.version,actor='admin'),db,c)
    assert j.state=='cancelled' and not d.active['enabled']


def test_late_delivered_callback_updates_sent_journal(db,transport):
    j=job(db,doc(db));m.send(db,j,transport,STAMP)
    db.add(ReminderEvent(key='sent-only',message_id='m1',status='sent'));db.commit();m.reconcile(db)
    assert j.state=='sent'
    db.add(ReminderEvent(key='later-delivered',message_id='m1',status='delivered'));db.commit();m.reconcile(db)
    assert j.state=='delivered'


def test_snapshot_uses_published_not_draft_version(db):
    d=doc(db);d.published_version=1;d.version=2;db.commit()
    assert m.snapshot(db,d)['version']==1


def test_legacy_paid_allows_same_day_late_initial_send(db,transport,monkeypatch):
    later=STAMP+timedelta(hours=2);monkeypatch.setattr(m,'now',lambda:later)
    j=job(db,doc(db,rule(late_policy='same_day')))
    m.send(db,j,transport,later)
    assert j.state=='pending' and len(transport.posts)==1


def test_legacy_stage_missing_fields_does_not_expire_after_five_minutes(db,transport,monkeypatch):
    later=STAMP+timedelta(days=1,hours=2);monkeypatch.setattr(m,'now',lambda:later)
    j=job(db,doc(db,rule(late_policy='legacy_stage',trigger={'type':'stage','repeat':'once'})))
    m.send(db,j,transport,later)
    assert j.state=='pending' and len(transport.posts)==1


def test_new_overlap_after_check_requires_new_review(db):
    from fastapi import HTTPException
    c=CRM();d=doc(db);m.check(d.id,{'version':d.version},db,c)
    other=rule(name='Добавленный после проверки').model_dump()
    db.add(m.Document(id='new-overlap',kind='rule',draft=other,active=other));db.commit()
    with pytest.raises(HTTPException) as exc:m.publish(d.id,m.Publish(version=d.version,actor='admin',overlaps_confirmed=True),db,c)
    assert exc.value.status_code==409


def test_expired_unattempted_date_can_be_rescheduled_by_publication(db):
    c=CRM();d=doc(db);j=job(db,d);j.state='expired';db.commit()
    d.draft={**d.draft,'trigger':{**d.draft['trigger'],'offset':2}};d.checked=m.digest([d.version,d.draft]);db.commit()
    m.publish(d.id,m.Publish(version=d.version,actor='admin'),db,c)
    assert j.state=='ready' and m.aware(j.due_at)==STAMP+timedelta(days=1)


def recovery_event(stamp=STAMP, event_id='event-1'):
    return {'id':event_id,'type':'lead_status_changed','entity_type':'lead',
            'entity_id':17,'account_id':32417154,'created_at':int(stamp.timestamp()),
            'value_before':[{'lead_status':{'id':76654894,'pipeline_id':9601214}}],
            'value_after':[{'lead_status':{'id':77086494,'pipeline_id':9601214}}]}


class EventCRM:
    def __init__(self, pages):self.pages=pages;self.calls=[]
    def get(self,path,**params):
        assert path=='/api/v4/events' and params['limit']==100
        self.calls.append(params)
        result=self.pages[params['page']-1]
        if isinstance(result,Exception):raise result
        return result


def recovery_setup(db,monkeypatch):
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live')
    doc(db,rule(trigger={'type':'stage','delay_minutes':60,'repeat':'once'}))
    c=EventCRM([])
    assert m.recover_stage_events(db,c,STAMP-timedelta(minutes=1))==0
    assert not c.calls
    return c


def test_event_recovery_missing_webhook_exact_time_and_overlap_dedup(db,monkeypatch):
    c=recovery_setup(db,monkeypatch)
    c.pages=[{'_embedded':{'events':[recovery_event()]}}]
    assert m.recover_stage_events(db,c,STAMP)==1
    j=db.scalar(select(m.Job))
    assert m.aware(j.source_at)==STAMP and m.aware(j.due_at)==STAMP+timedelta(hours=1)
    assert m.recover_stage_events(db,c,STAMP+timedelta(seconds=30))==0
    assert m.ingest(db,form(STAMP),STAMP)==0
    assert len(list(db.scalars(select(m.Job))))==1


def test_event_recovery_preserves_accepted_attempt(db,monkeypatch):
    c=recovery_setup(db,monkeypatch)
    m.ingest(db,form(STAMP),STAMP);j=db.scalar(select(m.Job));j.state='pending'
    db.add(m.Attempt(job_id=j.id,position=0,transport='telegram',state='pending',message_id='accepted',crm_id='unique'));db.commit()
    c.pages=[{'_embedded':{'events':[recovery_event()]}}]
    assert m.recover_stage_events(db,c,STAMP)==0
    assert j.state=='pending' and len(list(db.scalars(select(m.Attempt))))==1


def test_event_recovery_incomplete_page_never_advances_cursor(db,monkeypatch):
    c=recovery_setup(db,monkeypatch)
    before=dict(db.get(m.Operation,'stage-events-cursor-v1').result)
    c.pages=[{'_embedded':{'events':[recovery_event()]},'_links':{'next':{'href':'next'}}},httpx.ReadTimeout('read')]
    with pytest.raises(httpx.ReadTimeout):m.recover_stage_events(db,c,STAMP)
    db.rollback()
    assert db.get(m.Operation,'stage-events-cursor-v1').result==before
    assert db.scalar(select(m.Job)) is None
    c.pages[1]={}
    assert m.recover_stage_events(db,c,STAMP)==1


def test_event_recovery_orders_events_and_honors_activation(db,monkeypatch):
    c=recovery_setup(db,monkeypatch)
    old=recovery_event(STAMP-timedelta(days=4),'old')
    c.pages=[{'_embedded':{'events':[recovery_event(STAMP+timedelta(seconds=2),'later'),old,recovery_event()]}}]
    assert m.recover_stage_events(db,c,STAMP+timedelta(seconds=3))==1
    assert m.aware(db.scalar(select(m.Job)).source_at)==STAMP+timedelta(seconds=2)


def test_scan_outages_do_not_stop_already_queued_sends(db,transport,monkeypatch):
    from contextlib import nullcontext
    from app import reservation_reminders as rr
    monkeypatch.setenv('TEMPLATES_EXECUTOR','live')
    j=job(db,doc(db))
    conn=SimpleNamespace(execute=lambda *args,**kw:SimpleNamespace(scalar=lambda:True))
    monkeypatch.setattr(m,'get_engine',lambda:SimpleNamespace(connect=lambda:nullcontext(conn)))
    monkeypatch.setattr(m,'get_session_factory',lambda:lambda:nullcontext(db))
    transport.close=lambda:None
    monkeypatch.setattr(rr,'Clients',lambda *args:transport)
    def outage(*args):raise httpx.ReadTimeout('scan unavailable')
    monkeypatch.setattr(m,'synchronize',outage)
    monkeypatch.setattr(m,'recover_stage_events',outage)
    monkeypatch.setattr(m,'last_scan',None);monkeypatch.setattr(m,'last_event_scan',None)
    m.tick()
    assert j.state=='pending' and len(transport.posts)==1


def test_unknown_provider_records_safe_diagnostic_without_retry(db,transport):
    transport.response=(503,{'error':'PROVIDER_UNAVAILABLE'})
    j=job(db,doc(db));m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    a=db.scalar(select(m.Attempt))
    assert j.state=='uncertain' and a.reason=='http_503:PROVIDER_UNAVAILABLE'
    assert len(transport.posts)==1


def test_render_failure_stays_blocked_across_ticks_and_recovers_after_field_fix(db,transport):
    d=doc(db,rule(variants={'12':'Спасибо за покупку'}));j=job(db,d)
    m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    assert j.state=='blocked' and j.snapshot is None and not transport.posts
    transport.document['custom_fields_values'].append({'field_id':3158921,'values':[{'enum_id':12}]})
    m.send(db,j,transport,STAMP)
    assert j.state=='pending' and transport.posts[0]['text']=='Спасибо за покупку'


def test_historical_partial_snapshot_never_posts_empty_body(db,transport):
    d=doc(db);j=job(db,d);j.snapshot=m.snapshot(db,d);j.plan=[];j.rendered='';db.commit()
    m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    assert j.state=='blocked' and not transport.posts


@pytest.mark.parametrize('code',['INVALID_MESSAGE_DATA','WRONG_TRANSPORT'])
def test_explicit_invalid_request_is_failed_without_channel_fanout(db,transport,code):
    transport.response=(400,{'error':code})
    j=job(db,doc(db));m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    assert j.state=='failed' and j.reason==code and len(transport.posts)==1
    assert db.scalar(select(m.Attempt)).state=='error'


def test_telegram_phone_absent_is_explicit_rejection(db,transport):
    transport.response=(400,{'error':'CHANNEL_TGAPI_PHONE_NOT_OCCUPIED'})
    j=job(db,doc(db));m.send(db,j,transport,STAMP)
    a=db.scalar(select(m.Attempt))
    assert a.state=='error' and a.reason=='CHANNEL_TGAPI_PHONE_NOT_OCCUPIED'
    assert j.state=='failed' and j.reason=='all_channels_exhausted'


def echo(**patch):
    return {'messageId':'echo-1','channelId':'tg','chatType':'telegram','chatId':'tg-user',
            'contact':{'phone':'79990000001'},'text':'Здравствуйте 17','isEcho':True,
            'dateTime':STAMP.isoformat(),'status':'sent',**patch}


def test_manual_echo_before_dispatch_prevents_post(db,transport):
    j=job(db,doc(db));m.record_outbound(db,{'messages':[echo()]})
    m.send(db,j,transport,STAMP)
    assert not transport.posts and j.state=='sent' and j.reason=='confirmed_outgoing_copy'
    assert db.get(m.Operation,f'outgoing-proof-{j.id}').result['message_id']=='echo-1'


def test_lost_response_resolved_by_echo_and_late_callback(db,transport):
    j=job(db,doc(db));transport.response=httpx.ReadTimeout('lost')
    m.send(db,j,transport,STAMP);a=db.scalar(select(m.Attempt))
    assert a.state=='unknown' and not a.message_id
    m.record_outbound(db,{'messages':[echo()]});m.reconcile(db)
    assert j.state=='sent' and a.state=='unknown'  # Do not invent who sent it.
    db.add(ReminderEvent(key='late-echo',message_id='echo-1',status='read'));db.commit()
    m.reconcile(db);m.send(db,j,transport,STAMP)
    assert j.state=='delivered' and len(transport.posts)==1


@pytest.mark.parametrize('patch',[
    {'contact':{'phone':'79990000002'}}, {'channelId':'other'}, {'chatType':'whatsapp'},
    {'text':'Здравствуйте 18'}, {'isEcho':False}, {'isEdited':True}, {'isDeleted':True},
    {'status':'error'}, {'dateTime':(STAMP-timedelta(seconds=1)).isoformat()},
    {'dateTime':'2026-10-30T10:00:00'}, {'dateTime':'bad'}])
def test_unrelated_or_unproven_echo_never_suppresses_send(db,transport,patch):
    j=job(db,doc(db));m.record_outbound(db,{'messages':[echo(**patch)]})
    m.send(db,j,transport,STAMP)
    assert j.state=='pending' and len(transport.posts)==1


def test_callback_replay_does_not_downgrade_or_restore_deleted_message(db,transport):
    m.record_outbound(db,{'messages':[echo(status='read'),echo(status='sent')]})
    assert db.get(m.OutboundEvidence,'echo-1').status=='read'
    m.record_outbound(db,{'messages':[echo(isDeleted=True),echo()]})
    assert db.get(m.OutboundEvidence,'echo-1').status=='changed'


def test_known_both_store_review_suppresses_duplicate(db,transport):
    from app.stage_notifications import REVIEW_PREFIX,REVIEW_PYAT,REVIEW_BAUM
    d=doc(db,rule(content=REVIEW_PYAT));d.id='legacy-review';db.commit();j=job(db,d)
    m.record_outbound(db,{'messages':[echo(text=REVIEW_PYAT+'\n\n'+REVIEW_BAUM.removeprefix(REVIEW_PREFIX))]})
    m.send(db,j,transport,STAMP)
    assert j.state=='sent' and not transport.posts


def test_pre_success_manual_message_does_not_suppress_stage_rule(db,transport):
    j=job(db,doc(db));j.source_at=STAMP+timedelta(seconds=1);db.commit()
    m.record_outbound(db,{'messages':[echo()]});m.send(db,j,transport,STAMP)
    assert len(transport.posts)==1


def test_connection_failure_retries_same_claim_after_backoff(db,transport,monkeypatch):
    j=job(db,doc(db));transport.response=httpx.ConnectTimeout('not connected')
    m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP)
    a=db.scalar(select(m.Attempt));crm_id=a.crm_id
    assert len(transport.posts)==1 and a.state=='retry' and j.state=='ready'
    monkeypatch.setattr(m,'now',lambda:STAMP+timedelta(seconds=31))
    transport.response=(201,{'messageId':'m1'});m.send(db,j,transport,STAMP+timedelta(seconds=31))
    assert len(transport.posts)==2 and j.state=='pending'
    assert len(list(db.scalars(select(m.Attempt))))==1 and a.crm_id==crm_id


@pytest.mark.parametrize('exc',[httpx.ReadTimeout('lost'),httpx.WriteTimeout('partial'),httpx.RemoteProtocolError('lost')])
def test_possible_submitted_request_never_retries(db,transport,exc):
    j=job(db,doc(db));transport.response=exc
    m.send(db,j,transport,STAMP);m.send(db,j,transport,STAMP+timedelta(hours=1))
    assert j.state=='uncertain' and len(transport.posts)==1
    trace=db.get(m.Operation,f'send-trace-{db.scalar(select(m.Attempt)).id}')
    assert trace.result['exception']==type(exc).__name__
    assert m.delivery_attention(db)['attention_count']==1


def test_saved_http_response_recovers_after_crash(db,transport):
    j=job(db,doc(db));m.send(db,j,transport,STAMP);a=db.scalar(select(m.Attempt))
    a.message_id='';a.state='inflight';j.state='inflight';db.commit()
    db.add(ReminderEvent(key='proof',message_id='m1',status='delivered'));db.commit()
    m.reconcile(db)
    assert a.message_id=='m1' and j.state=='delivered' and len(transport.posts)==1


def test_pending_watchdog_waits_then_exposes_request_ids(db,transport,monkeypatch):
    j=job(db,doc(db));m.send(db,j,transport,STAMP)
    assert not m.delivery_attention(db)['items']
    monkeypatch.setattr(m,'now',lambda:STAMP+timedelta(minutes=31))
    item=m.delivery_attention(db)['items'][0]
    assert item['state']=='pending' and item['attempts'][0]['message_id']=='m1'
    assert 'повтор запрещён' in item['next_action']


def test_explicit_recovery_clears_attention_not_original_history(db,transport):
    j=job(db,doc(db));j.state='failed';j.reason='INVALID_MESSAGE_DATA';db.commit()
    db.add(m.Job(rule_id=j.rule_id,lead_id=j.lead_id,occurrence='recovery',due_at=STAMP,state='delivered'))
    db.commit()
    assert not m.delivery_attention(db)['items'] and j.state=='failed'


def test_connection_retries_are_bounded(db,transport,monkeypatch):
    j=job(db,doc(db));transport.response=httpx.ConnectError('offline')
    for seconds in (0,31,92,213,454):
        stamp=STAMP+timedelta(seconds=seconds)
        monkeypatch.setattr(m,'now',lambda:stamp)
        m.send(db,j,transport,stamp)
    m.send(db,j,transport,stamp)
    assert len(transport.posts)==5 and j.state=='failed' and j.reason=='connection_unavailable'
