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
