from datetime import datetime,timedelta
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import crm_automation as a,crm_automation_messages as n,message_templates as m

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    a.Base.metadata.create_all(engine,tables=[*a.TABLES,*n.TABLES,m.Document.__table__,m.Job.__table__])
    with Session(engine) as db:yield db

def stamp(hour,minute=0,day=17):return datetime(2026,11,day,hour,minute,tzinfo=a.MSK)

class Client:
    def __init__(self):self.events=[];self.lead={'id':55,'pipeline_id':a.SALES,'status_id':11,'_embedded':{'contacts':[{'id':9,'is_main':True}]}}
    def get(self,path,**params):
        if path=='/api/v4/events':return {'_embedded':{'events':self.events}}
        return self.lead

def prepare(db,at):
    for direction in ('incoming','outgoing'):
        db.add(n.ChatCursor(direction=direction,through=int(at.timestamp()),activated_at=1))
    db.add(n.ChatEvent(id='first',lead_id=55,contact_id=9,at=int(at.timestamp()),direction='incoming',message_id='one',origin='com.wazzup24-1'))
    db.commit()

def test_night_boundaries_and_midnight_share_one_window():
    assert n.night_key(stamp(22,59)) is None
    assert n.night_key(stamp(23))==n.night_key(stamp(0,day=18))==n.night_key(stamp(9,59,day=18))
    assert n.night_key(stamp(10,day=18)) is None

def test_chat_collection_validates_all_rows_before_advancing(db):
    c=Client();c.events=[{'id':'a','entity_id':55,'entity_type':'lead','type':'incoming_chat_message','created_at':100,'linked_talk_contact_id':9,'value_after':[{'message':{'id':'provider-one'}}]}]
    n.collect_chats(db,c,1,120,'incoming');n.collect_chats(db,c,1,120,'incoming')
    assert len(list(db.scalars(select(n.ChatEvent))))==1
    c.events.append({**c.events[0],'id':'bad','created_at':300})
    with pytest.raises(ValueError):n.collect_chats(db,c,1,150,'incoming')
    assert db.get(n.ChatCursor,'incoming').through==120

def test_preview_never_queues_message(db):
    at=stamp(23);prepare(db,at)
    n.plan_night(db,Client(),at,preview=True)
    assert db.get(n.ChatEvent,'first').state=='preview'
    assert list(db.scalars(select(m.Job)))==[]

def test_already_answered_and_secondary_contacts_are_excluded(db):
    at=stamp(23);prepare(db,at)
    db.add(n.ChatEvent(id='reply',lead_id=55,contact_id=9,at=int(at.timestamp()),direction='outgoing',message_id='two'));db.commit()
    n.plan_night(db,Client(),at)
    assert db.get(n.ChatEvent,'first').state=='answered'
    event=db.get(n.ChatEvent,'first');event.state='received';event.contact_id=10;db.commit()
    n.plan_night(db,Client(),at)
    assert event.state=='contact_requires_review'

def test_live_queue_deduplicates_night_and_checks_late_send(db,monkeypatch):
    at=stamp(23);prepare(db,at);c=Client()
    rule=m.Rule(name='Ночной ответ',enabled=True,pipelines=[a.SALES],content=n.NIGHT_TEXT,
        trigger=m.Trigger(type='webhook'),window_start=0,window_end=24,late_policy='same_day')
    db.add(m.Document(id=n.NIGHT_ID,kind='rule',version=1,published_version=1,draft=rule.model_dump(),active=rule.model_dump()))
    monkeypatch.setattr(m,'snapshot',lambda *_:{'rule':rule.model_dump(),'routing':{},'version':1})
    monkeypatch.setattr(m,'make_plan',lambda *_:([{'transport':'telegram','recipient':{'phone':'test'}}],'identity'))
    db.commit();n.plan_night(db,c,at,preview=False)
    db.add(n.ChatEvent(id='again',lead_id=55,contact_id=9,at=int(at.timestamp()),direction='incoming',message_id='three',origin='com.wazzup24-1'));db.commit()
    n.plan_night(db,c,at,preview=False)
    jobs=list(db.scalars(select(m.Job)));assert len(jobs)==1
    assert n.night_send_allowed(db,jobs[0],c.lead,at,c)
    assert not n.night_send_allowed(db,jobs[0],c.lead,at+timedelta(minutes=6),c)
    c.lead['_embedded']['contacts']=[{'id':10,'is_main':True}]
    assert not n.night_send_allowed(db,jobs[0],c.lead,at,c)


def test_fresh_reply_blocks_send_before_worker_cursor(db):
    from types import SimpleNamespace
    at=stamp(23);c=Client()
    job=SimpleNamespace(source_at=at,occurrence=n.night_key(at),snapshot={'trigger_contact_id':9},lead_id=55)
    c.events=[{'type':'outgoing_chat_message','entity_type':'lead','entity_id':55,
               'linked_talk_contact_id':9,'created_at':int(at.timestamp())+1}]
    assert not n.night_send_allowed(db,job,c.lead,at+timedelta(seconds=2),c)
    c.events[0].pop('linked_talk_contact_id')
    assert not n.night_send_allowed(db,job,c.lead,at+timedelta(seconds=2),c)


def test_contact_filter_and_unbound_events_do_not_stall_intake(db):
    class RealShape(Client):
        def get(self,path,**params):
            assert params['filter[entity]']=='contact'
            return {'_embedded':{'events':[{'type':'incoming_chat_message','entity_type':'contact'}]}}
    n.collect_chats(db,RealShape(),1,100,'incoming')
    assert db.get(n.ChatCursor,'incoming').through==100
    assert list(db.scalars(select(n.ChatEvent)))==[]


def test_live_event_contact_resolved_only_through_exact_talk(db):
    class RealShape(Client):
        def get(self,path,**params):
            if path=='/api/v4/talks/44':
                return {'talk_id':44,'entity_type':'lead','entity_id':55,'contact_id':9}
            return {'_embedded':{'events':[{'id':'a','entity_id':55,'entity_type':'lead',
                'type':'incoming_chat_message','created_at':100,
                'value_after':[{'message':{'id':'one','talk_id':44,'origin':'com.wazzup24-1'}}]}]}}
    n.collect_chats(db,RealShape(),1,120,'incoming')
    event=db.get(n.ChatEvent,'a')
    assert (event.contact_id,event.talk_id,event.origin)==(9,44,'com.wazzup24-1')


def test_unknown_inbound_channel_never_falls_back_to_phone(db):
    at=stamp(23);prepare(db,at)
    event=db.get(n.ChatEvent,'first');event.origin='unknown';db.commit()
    n.plan_night(db,Client(),at,preview=False)
    assert event.state=='channel_requires_review'
    assert list(db.scalars(select(m.Job)))==[]
