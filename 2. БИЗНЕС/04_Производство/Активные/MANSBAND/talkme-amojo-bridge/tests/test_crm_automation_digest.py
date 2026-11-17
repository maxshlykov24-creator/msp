from datetime import datetime,timedelta
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
import pytest
from app import crm_automation_digest as d,crm_automation as a

@pytest.fixture
def db():
    engine=create_engine('sqlite://');d.Base.metadata.create_all(engine,tables=d.TABLES)
    with Session(engine) as db:yield db

class Telegram:
    def __init__(self):self.sent=[];self.failure=False;self.denied=False
    def check(self,chat):
        if self.denied:raise ValueError('no_access')
    def send(self,chat,body):
        self.sent.append((chat,body))
        if self.failure:raise TimeoutError()
        return 7


def test_delivery_is_once_per_group_and_day(db):
    at=datetime(2026,11,17,12,tzinfo=a.MSK);tg=Telegram()
    assert d.deliver(db,tg,-1,at,'summary',preview=True)=='preview'
    assert tg.sent==[]
    assert d.deliver(db,tg,-1,at,'fresh summary',preview=False)=='sent'
    assert d.deliver(db,tg,-1,at,'second summary',preview=False)=='sent'
    assert tg.sent==[(-1,'fresh summary')]
    assert d.deliver(db,tg,-1,at+timedelta(days=1),'tomorrow',preview=False)=='sent'
    assert len(tg.sent)==2


def test_unknown_delivery_is_never_blindly_retried(db):
    at=datetime(2026,11,17,12,tzinfo=a.MSK);tg=Telegram();tg.failure=True
    assert d.deliver(db,tg,-1,at,'summary',preview=False)=='unknown'
    assert d.deliver(db,tg,-1,at,'summary',preview=False)=='unknown'
    assert len(tg.sent)==1
    job=db.scalar(select(d.Digest));assert job.reason=='send_result_not_confirmed'


def test_missing_access_leaves_retryable_unclaimed_job(db):
    at=datetime(2026,11,17,12,tzinfo=a.MSK);tg=Telegram();tg.denied=True
    with pytest.raises(ValueError):d.deliver(db,tg,-1,at,'summary',preview=False)
    assert db.scalar(select(d.Digest)).state=='planned'
    assert tg.sent==[]


@pytest.mark.parametrize('stage,date_field',[('Дано обещание',3815335),('Сертификат оплачен',3169911)])
def test_collect_preserves_closed_lead_tasks_and_excludes_tests_migration(stage,date_field):
    at=datetime(2026,11,17,12,tzinfo=a.MSK);yesterday=int((at-timedelta(days=1)).timestamp())
    def lead(i,status=11,fields=None,tags=None):
        return {'id':i,'pipeline_id':a.SALES,'status_id':status,'custom_fields_values':fields or [],'_embedded':{'tags':tags or []}}
    actual={'field_id':date_field,'values':[{'value':int(at.timestamp())}]}
    stale={'field_id':3279145,'values':[{'value':yesterday}]}
    leads={1:lead(1,142),2:lead(2,tags=[{'name':'ТЕСТ НЕ ОТПРАВЛЯТЬ'}]),3:lead(3),4:lead(4,fields=[actual,stale]),5:lead(5,fields=[stale])}
    class Client:
        def get(self,path,**params):
            if path=='/api/v4/leads/pipelines':return {'_embedded':{'pipelines':[{'id':pid,'name':str(pid),'_embedded':{'statuses':[{'id':11,'name':stage}]}} for pid in a.PIPELINES]}}
            if path=='/api/v4/tasks':return {'_embedded':{'tasks':[{'id':i,'entity_id':i,'entity_type':'leads','is_completed':False,'complete_till':yesterday} for i in (1,2,3)]}}
            if path=='/api/v4/leads':return {'_embedded':{'leads':[leads[4],leads[5]] if params['filter[statuses][0][pipeline_id]']==a.SALES else []}}
            return leads[int(path.rsplit('/',1)[1])]
    result=d.collect(Client(),at,held={3})
    assert [x['lead_id'] for x in result['tasks']]==[1]
    assert result['stages']==[]  # Actual date today wins; missing actual date does not use stale technical SLA.
    assert result['missing_dates']==[5]
    body=d.render(result,at)
    assert 'Задачи: 1.' in body and '/leads/detail/1' in body


def test_digest_never_runs_outside_noon_window(monkeypatch):
    monkeypatch.setattr(d,'collect',lambda *_:pytest.fail('unexpected scan'))
    d.tick(d.Settings(mode='live',chat_id=-1),datetime(2026,11,17,11,59,tzinfo=a.MSK))
    d.tick(d.Settings(mode='live',chat_id=-1),datetime(2026,11,17,13,tzinfo=a.MSK))
