from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
import httpx
import pytest
from app import campaign as m
from app.reservation_reminders import ReminderEvent

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    m.Base.metadata.create_all(engine,tables=m.TABLES+[ReminderEvent.__table__])
    with Session(engine) as session:yield session

def job(db):
    j=m.Recipient(person=m.key('79000000001'),segment=1,test=True,lead_id=123,snapshot={'text':'approved','routes':[{'channelId':'one','transport':'telegram','recipient':{'phone':'79000000001'}},{'channelId':'two','transport':'max','recipient':{'phone':'79000000001'}}]})
    db.add(j);db.commit();return j

class Wz:
    def __init__(self,reply):self.reply=reply;self.posts=[]
    def get(self,url):return httpx.Response(200,json=[{'channelId':'one','transport':'tgapi','state':'active'},{'channelId':'two','transport':'max','state':'active'}],request=httpx.Request('GET','https://example.com'))
    def post(self,url,json):
        self.posts.append(json)
        if isinstance(self.reply,Exception):raise self.reply
        return httpx.Response(*self.reply,request=httpx.Request('POST','https://example.com'))
class Client:
    def __init__(self,r):self.wz=Wz(r)

def test_unknown_never_reposts(db):
    j=job(db);c=Client(httpx.ReadTimeout('unknown'))
    assert m.send_part(db,j,c,0,'text','asset')=='halt'
    assert j.state=='attention'
    m.send_part(db,j,c,0,'text','asset')
    assert len(c.wz.posts)==1

def test_accepted_is_not_sent(db):
    j=job(db);c=Client((201,))
    c.wz.post=lambda *a,**kw:httpx.Response(201,json={'messageId':'message'})
    assert m.send_part(db,j,c,0,'text','asset')=='pending'
    assert j.sent_at is None
    db.add(ReminderEvent(key='event',message_id='message',status='sent'));db.commit()
    m.reconcile(db)
    assert j.state=='photo_ready' and j.sent_at

def test_spam_not_fallback(db):
    j=job(db);c=Client((400,))
    c.wz.post=lambda *a,**kw:httpx.Response(400,json={'error':'MESSAGES_IS_SPAM'})
    assert m.send_part(db,j,c,0,'text','asset')=='halt'
    assert j.state=='attention'

def test_only_absent_allows_fallback(db):
    j=job(db);c=Client((400,))
    c.wz.post=lambda *a,**kw:httpx.Response(400,json={'error':'CHANNEL_TGAPI_PHONE_NOT_OCCUPIED'})
    assert m.send_part(db,j,c,0,'text','asset')=='absent'
    assert j.state=='prepared'

def test_text_photo_mutually_exclusive(db):
    j=job(db);c=Client((500,))
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='prior',at=m.now()-timedelta(seconds=30)));db.commit()
    db.add(m.Control(id='confirmed-prior',data={'at':(m.now()-timedelta(seconds=30)).isoformat()}));db.commit()
    m.send_part(db,j,c,0,'photo','https://asset')
    assert c.wz.posts[0]['contentUri']=='https://asset'
    assert 'text' not in c.wz.posts[0]

def test_dedup_and_classification(db):
    j=job(db);j.sent_at=m.now();db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='text'));db.commit()
    msg={'messageId':'reply','isEcho':False,'dateTime':m.now().isoformat(),'channelId':'one','chatType':'telegram','chatId':'79000000001','text':'Не пишите мне'}
    m.record_messages(db,{'messages':[msg]});m.record_messages(db,{'messages':[msg]})
    assert len(list(db.scalars(select(m.ResponseEvent))))==1
    assert j.classification=='unsubscribe'
    assert db.get(m.Control,'unsubscribe-'+j.person)

@pytest.mark.parametrize('body,result',[('Спасибо, не нужно','refusal'),('Нет','review'),('Почему вы мне пишете?','review'),('Какой размер есть?','interest'),('Есть доставка?','interest'),('Не нужно, какой размер?','review'),('СТОП','unsubscribe')])
def test_rules(body,result):assert m.classify(body)==result

def test_greeting_only():
    t='{Имя}, добрый день!\nЦена 22 600. {ссылка}'
    stamp=datetime(2026,11,10,16,tzinfo=timezone.utc)
    assert m.render(t,'Нина',1,stamp).startswith('Нина, добрый вечер!\nЦена 22 600.')
    assert m.render(t,'',1,stamp).startswith('Здравствуйте!\nЦена 22 600.')


def test_photo_requires_confirmed_same_channel(db):
    j=job(db);c=Client((500,))
    assert m.send_part(db,j,c,0,'photo','asset')=='halt'
    assert not c.wz.posts
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='prior',at=m.now()-timedelta(seconds=30)));db.commit()
    assert m.send_part(db,j,c,1,'photo','asset')=='halt'
    assert not c.wz.posts

def test_second_channel_forbidden_after_acceptance(db):
    j=job(db);c=Client((500,))
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='pending',crm_id='prior'));db.commit()
    assert m.send_part(db,j,c,1,'text','asset')=='halt'
    assert not c.wz.posts

def test_response_before_photo_stops_bundle(db):
    j=job(db);c=Client((500,));j.replied_at=m.now();db.commit()
    assert m.send_part(db,j,c,0,'photo','asset')=='halt'
    assert j.state=='reply_handoff' and not c.wz.posts

def test_test_people_excluded_from_metrics(db):
    j=job(db);j.sent_at=m.now();j.classification='interest';j.replied_at=m.now();db.commit()
    assert m.detailed_report(db)['recipients']==0
    assert m.detailed_report(db,True)['responses']['interest']==1


def test_blank_line_after_link():
    t='{Имя}, добрый день!\nВыбрать: {ссылка}  \nНапишите нам.'
    rendered=m.render(t,'Нина',1,m.now())
    assert 'auditor_1\n\nНапишите нам.' in rendered
    assert m.render(rendered,'Нина',1,m.now())==rendered


def test_photo_delay_starts_at_confirmation_not_post(db):
    j=job(db);c=Client((500,))
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='prior',at=m.now()-timedelta(minutes=5)))
    db.add(m.Control(id='confirmed-prior',data={'at':m.now().isoformat()}));db.commit()
    assert m.send_part(db,j,c,0,'photo','asset')=='halt'
    assert not c.wz.posts

def test_queue_waits_for_segment_and_history(db):
    a=job(db);a.test=False;a.state='queued';a.snapshot={**a.snapshot,'enriched':True,'last_incoming':10};db.commit()
    b=m.Recipient(person=m.key('79000000002'),segment=1,test=False,state='queued',snapshot={'enriched':False})
    c=m.Recipient(person=m.key('79000000003'),segment=2,test=False,state='queued',snapshot={'enriched':True,'last_incoming':100});db.add_all([b,c]);db.commit()
    assert m.queue_next(db) is None
    b.snapshot={'enriched':True,'last_incoming':20};db.commit()
    assert m.queue_next(db).id==b.id

def test_shared_identity_blocks_second_phone(db):
    a=job(db);b=m.Recipient(person=m.key('79000000002'),segment=1,test=False,snapshot={});db.add(b);db.commit()
    contact={'id':88,'custom_fields_values':[]}
    assert m.claim_identities(db,a,contact,a.snapshot['routes'])
    assert not m.claim_identities(db,b,contact,[])
    assert b.state=='duplicate'

def test_budget_reserves_photo_and_counts_manual(db):
    j=job(db);j.test=False;db.commit()
    for n in range(39):db.add(m.ChannelUsage(id='manual'+str(n),channel='one',transport='telegram',at=m.now()))
    db.commit()
    assert not m.channel_room(db,j,0,'text')
    assert m.channel_room(db,j,1,'text')

def test_ramp_is_persisted_and_segment_specific(db):
    stamp=datetime(2026,11,11,7,tzinfo=timezone.utc)
    assert m.day_budget(db,1,stamp)==5
    db.add(m.Control(id='segment-start-1',data={'day':'2026-11-11'}));db.commit()
    assert m.day_budget(db,1,stamp+timedelta(days=1))==10
    assert m.day_budget(db,2,stamp+timedelta(days=1))==15

def test_purchase_attribution_dedup_refund_old_order_and_tests(db):
    j=job(db);j.test=False;j.sent_at=m.now()-timedelta(days=1);db.commit()
    record={'document_id':'document','order_id':'order','person':j.person,'amo_lead_id':j.lead_id,'paid':True,'posted':True,'at':m.now().isoformat(),'order_created_at':m.now().isoformat(),'amount_kopecks':10000,'refund_kopecks':2000}
    assert m.ingest_purchases(db,[record])['linked']==1
    m.ingest_purchases(db,[record]);assert len(list(db.scalars(select(m.Purchase))))==1
    assert m.detailed_report(db)['net_kopecks']==8000
    assert m.ingest_purchases(db,[{**record,'document_id':'old','order_created_at':(m.now()-timedelta(days=3)).isoformat()}])['ignored']==1
    assert m.ingest_purchases(db,[{**record,'document_id':'test','test':True}])['ignored']==1
    assert m.ingest_purchases(db,[{**record,'document_id':'unpaid','paid':False}])['ignored']==1

def test_purchases_phone_match_is_not_causal_attribution(db):
    j=job(db);j.test=False;j.sent_at=m.now()-timedelta(days=1);db.commit()
    r={'document_id':'document','person':j.person,'amo_lead_id':999,'paid':True,'posted':True,'at':m.now().isoformat(),'order_created_at':m.now().isoformat(),'amount_kopecks':10000}
    assert m.ingest_purchases(db,[r])['observed']==1
    assert m.detailed_report(db)['linked_purchases']==0

def test_technical_channel_unavailability_falls_back_before_post(db):
    j=job(db);c=Client((500,))
    c.wz.get=lambda _:httpx.Response(200,json=[{'channelId':'one','transport':'tgapi','state':'phoneUnavailable'}],request=httpx.Request('GET','https://example.com'))
    assert m.send_part(db,j,c,0,'text','asset')=='absent'
    assert not c.wz.posts and not db.get(m.Control,'campaign-halt')

def test_channel_block_preflight_stops_whole_campaign(db):
    j=job(db);c=Client((500,))
    c.wz.get=lambda _:httpx.Response(200,json=[{'channelId':'one','transport':'tgapi','state':'blocked'}],request=httpx.Request('GET','https://example.com'))
    assert m.send_part(db,j,c,0,'text','asset')=='halt'
    assert not c.wz.posts and db.get(m.Control,'campaign-halt')

def test_async_spam_freezes_other_recipients(db):
    j=job(db);db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='pending',crm_id='spam',message_id='spam-msg'))
    db.add(ReminderEvent(key='spam-event',message_id='spam-msg',status='error',error_code='MESSAGES_IS_SPAM'));db.commit()
    m.reconcile(db)
    assert db.get(m.Control,'campaign-halt') and j.state=='attention'

def test_production_reply_uses_identity_index(db):
    j=job(db);j.test=False;j.sent_at=m.now();db.commit()
    m.claim_identities(db,j,{'id':44},j.snapshot['routes'])
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='production-text'));db.commit()
    m.record_messages(db,{'messages':[{'messageId':'real-reply','isEcho':False,'dateTime':m.now().isoformat(),'channelId':'one','chatType':'telegram','chatId':'79000000001','text':'Есть размер 54?'}]})
    assert j.classification=='interest' and j.replied_at

def test_inactive_purchase_does_not_inflate_report(db):
    j=job(db);j.test=False;j.sent_at=m.now();db.commit()
    db.add(m.Purchase(id='kassa:voided',recipient_id=j.id,at=m.now(),amount_kopecks=500,refund_kopecks=0,linked=True,evidence={'active':False}));db.commit()
    assert m.detailed_report(db)['observed_purchases']==0

def test_first_pilot_does_not_pick_vk_only_history(db):
    j=job(db);j.test=False;j.state='queued';j.snapshot={**j.snapshot,'enriched':True,'last_incoming':100,'chat_history':'new'};db.commit()
    b=m.Recipient(person=m.key('79000000002'),segment=1,test=False,state='queued',snapshot={'enriched':True,'last_incoming':10,'chat_history':'existing'});db.add(b);db.commit()
    assert m.queue_next(db,existing_only=True).id==b.id

def test_daily_usage_keeps_photo_slot_reserved(db):
    j=job(db);j.test=False;j.state='pending';db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='pending',message_id='one',crm_id='one'));db.commit()
    used,channels=m.daily_usage(db,m.now())
    assert used==1 and channels['telegram']==2
    db.add(m.ChannelUsage(id='one',channel='one',transport='telegram',at=m.now()));db.commit()
    assert m.daily_usage(db,m.now())[1]['telegram']==2

def test_pause_is_default_even_after_import(db):
    j=job(db);j.test=False;j.state='queued';db.commit()
    assert not m.production_enabled(db)
    db.add(m.Control(id='production',data={'enabled':True,'source_sha256':'source'}));db.commit()
    assert not m.production_enabled(db)

def test_unsubscribe_overrides_prior_manual_interest(db):
    j=job(db);j.classification='interest';db.add(m.Control(id='classification-'+str(j.id),data={'category':'interest'}));db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='prior-interest'));db.commit()
    m.record_messages(db,{'messages':[{'messageId':'opt-out','isEcho':False,'dateTime':m.now().isoformat(),'channelId':'one','chatType':'telegram','chatId':'79000000001','text':'Не пишите мне'}]})
    assert j.classification=='unsubscribe' and db.get(m.Control,'unsubscribe-'+j.person)


def test_new_contact_uses_configured_channel_with_old_blocked_account(db,monkeypatch):
    from app import reservation_reminders as reminders
    from types import SimpleNamespace
    monkeypatch.setattr(reminders,'ReminderSettings',lambda:SimpleNamespace(telegram_channel='current',max_channel='max',whatsapp_channel='wa'))
    j=job(db);j.test=False;j.contact_id=0;j.state='queued';j.snapshot={**j.snapshot,'phone':'79000000001'};db.commit()
    c=Client((500,))
    c.wz.get=lambda _:httpx.Response(200,json=[{'channelId':'old','transport':'tgapi','state':'blocked'},{'channelId':'current','transport':'tgapi','state':'active'}],request=httpx.Request('GET','https://example.com'))
    m.enrich_one(db,c)
    assert j.snapshot['routes'][0]['channelId']=='current'
    assert j.snapshot['routes'][0]['transport']=='telegram'
    assert not c.wz.posts

def test_queue_overview_filters_search_and_safe_actions(db,monkeypatch):
    j=job(db);j.test=False;j.state='queued';j.contact_id=0
    j.snapshot={**j.snapshot,'name':'Проверка','phone':'79000000001','enriched':True,'chat_history':'existing'}
    db.add(m.Recipient(person=m.key('79000000002'),test=False,segment=2,state='attention',reason='ambiguous_crm_contacts',snapshot={'name':'Другой','matched_contact_ids':[12,34]}))
    db.add(m.Control(id='production',data={'enabled':True,'audit_complete':True,'source_sha256':'source','start_day':'2026-11-11'}));db.commit()
    monkeypatch.setattr(m,'get_session_factory',lambda:lambda:db)
    monkeypatch.setattr(m,'now',lambda:datetime(2026,11,10,18,tzinfo=timezone.utc))
    result=m.queue_status(segment=1,search='Проверка')
    assert result['total']==1 and result['items'][0]['name']=='Проверка'
    assert result['items'][0]['phone_tail']=='0001' and 'phone' not in result['items'][0]
    assert result['items'][0]['can_exclude'] and not result['items'][0]['can_classify']
    assert result['segments'][0]['existing']==1 and result['segments'][1]['attention']==1
    assert result['start_day']=='2026-11-11' and result['next_window']=='2026-11-11T10:00:00+03:00'

def test_queue_sent_includes_replied_and_cannot_exclude_attempted(db,monkeypatch):
    j=job(db);j.test=False;j.state='reply_handoff';j.sent_at=m.now();j.replied_at=m.now()
    db.add(m.Part(recipient_id=j.id,position=0,kind='text',state='sent',crm_id='queue-attempt'));db.commit()
    monkeypatch.setattr(m,'get_session_factory',lambda:lambda:db)
    result=m.queue_status(state='sent')
    assert result['total']==1 and not result['items'][0]['can_exclude'] and result['items'][0]['can_classify']

def test_queue_buyers_excludes_invalidated_purchases(db,monkeypatch):
    j=job(db);j.test=False
    db.add(m.Purchase(id='kassa:invalid',recipient_id=j.id,at=m.now(),amount_kopecks=500,refund_kopecks=0,linked=True,evidence={'active':False}));db.commit()
    monkeypatch.setattr(m,'get_session_factory',lambda:lambda:db)
    assert m.queue_status(state='buyers')['total']==0
