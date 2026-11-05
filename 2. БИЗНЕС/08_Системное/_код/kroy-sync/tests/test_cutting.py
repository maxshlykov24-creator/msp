from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from app.cutting import calculate, MSK
from app.db import Entity, User, transaction
from app.service import DomainError, execute
from test_app import setup, balance


def payload(engine):
    with Session(engine) as s:
        calc = calculate(s, {'location':'own', 'item_ids':['s']})
    today = datetime.now(MSK).date()
    return dict(title='Проверка', location='own', products=[{'item_id':'s','quantity':'10'}], materials=[{'item_id':'cloth','quantity':'2'}],
        cutting=dict(schema=1, due_on=str(today+timedelta(days=1)), available_on=str(today+timedelta(days=2)), assignee='owner', technology_confirmed=True,
          calculation=calc, manual_reason='Подтверждённая ручная потребность', quantities={'s':'10'},
          groups=[dict(model='Лонг скимс',ratio='100% S',material='cloth',quantity='2',products={'s':'10'})]))


def test_issue_atomic_retry_and_coverage_after_partial(setup):
    engine,u,cmd=setup
    p=payload(engine); op=str(uuid4())
    issued=cmd('task.issue',p,op)
    assert cmd('task.issue',p,op)==issued
    assert balance(engine,'cloth')['reserved']==2
    fact=cmd('task.confirm',dict(id=issued['id'],version=issued['version'],confirm_actual=True,products=[dict(item_id='s',quantity=4)],materials=[dict(item_id='cloth',quantity=1)]))
    with Session(engine) as s:
        c=calculate(s,dict(location='own',item_ids=['s']))
    assert c['rows'][0]['future']=='10'
    assert len(c['rows'][0]['coverage'])==2
    cmd('task.cancel',dict(id=issued['id'],version=fact['version']))
    assert balance(engine)['frozen']==4
    assert balance(engine,'cloth')['reserved']==0


def test_stale_calculation_and_inconsistent_groups_rejected(setup):
    engine,u,cmd=setup
    p=payload(engine)
    cmd('stock.adjust',dict(item_id='s',location='own',version=balance(engine)['version'],physical=11))
    with pytest.raises(DomainError,match='изменились'):cmd('task.issue',p)
    p=payload(engine);p['cutting']['groups'][0]['products']['s']='11'
    with pytest.raises(DomainError,match='не совпадают'):cmd('task.issue',p)
    assert balance(engine,'cloth')['reserved']==0


def test_missing_sources_never_zero_and_negative_net(setup):
    engine,u,cmd=setup
    p=payload(engine); c=p['cutting']['calculation']
    assert c['rows'][0]['net'] is None and c['rows'][0]['recommendation'] is None
    with transaction(engine) as s:
        for platform in ('wb','ozon'):
            s.add(Entity(id=platform,kind='sales_window',version=1,data=dict(platform=platform,start=c['parameters']['start'],end=c['parameters']['end'],at='2026-10-29T00:00:00Z',complete=True,verified=True,covered_items=['s'],events=[dict(item_id='s',kind='return',quantity=2,date=c['parameters']['end'])])))
    with Session(engine) as s:c=calculate(s,dict(location='own',item_ids=['s']))
    assert c['rows'][0]['net']==-4 and c['rows'][0]['recommendation']==0


def test_cutter_cannot_issue_and_empty_draft_reserves_nothing(setup):
    engine,u,cmd=setup
    with transaction(engine) as s:
        cutter=User(id='cutter',name='Cutter',role='cutter',active=True,token_hash='x');s.add(cutter)
    with pytest.raises(DomainError,match='Нет права'):
        execute(engine,cutter,str(uuid4()),'task.issue',payload(engine),'Test')
    p=payload(engine);p['products']=[];p['materials']=[];p['cutting']['groups']=[]
    cmd('task.save',p)
    assert balance(engine,'cloth')['reserved']==0


def test_unknown_date_not_included_until_explicit_choice(setup):
    engine,u,cmd=setup
    p=payload(engine);p['cutting']['available_on']=''
    issued=cmd('task.issue',p)
    with Session(engine) as s:
        c=calculate(s,dict(location='own',item_ids=['s']))
        assert c['rows'][0]['future']=='0'
        c=calculate(s,dict(location='own',item_ids=['s'],include_uncertain=[issued['id']]))
        assert c['rows'][0]['future']=='10'


def test_sales_import_deduplicates_and_preserves_returns_date(setup):
    from app.sales import store_window, wb_events
    engine,u,cmd=setup
    p=payload(engine);dates=p['cutting']['calculation']['parameters']
    events,missing=wb_events([dict(date=dates['end']+'T23:30:00',saleID='R1',barcode='001'),dict(date=dates['end']+'T23:30:00',saleID='R1',barcode='001')],dates['start'],dates['end'],{'001':['s']})
    assert missing==0 and events[0]['kind']=='return'
    data=dict(platform='wb',start=dates['start'],end=dates['end'],at='2026-10-29T00:00:00Z',complete=True,verified=True,covered_items=['s'],events=events)
    store_window(engine,data);store_window(engine,data)
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s']))['rows'][0]
        assert row['platforms']['wb']['returns']==1 and row['net'] is None
    assert balance(engine)['free']==10


def test_issue_tolerance_and_stable_document_identity(setup):
    engine,u,cmd=setup
    p=payload(engine);p['new_id']=str(uuid4())
    p['cutting']['quantities']['s']='100'
    with pytest.raises(DomainError,match='допуска'):cmd('task.issue',p)
    p['cutting']['quantities']['s']='10'
    cmd('task.issue',p)
    with pytest.raises(DomainError,match='существует'):cmd('task.issue',p)
    assert balance(engine,'cloth')['reserved']==2


def test_confirmed_zero_stock_still_produces_recommendation(setup):
    from app.sales import store_window
    engine, user, cmd = setup
    with Session(engine) as s:
        params = calculate(s, {'location': 'own', 'item_ids': ['m']})['parameters']
    for platform, sales, returns in [('wb', 12, 1), ('ozon', 3, 2)]:
        store_window(engine, dict(platform=platform, start=params['start'], end=params['end'], at=datetime.now(MSK).isoformat(),
            complete=True, verified=True, covered_items=['m'], events=[
                dict(id=platform+'-sale',item_id='m',date=params['end'],kind='sale',quantity=sales),
                dict(id=platform+'-return',item_id='m',date=params['end'],kind='return',quantity=returns)]))
    with Session(engine) as s:
        row = calculate(s, {'location': 'own', 'item_ids': ['m']})['rows'][0]
    assert Decimal(row['free']) == 0
    assert row['net'] == 12 and row['target'] == 24 and row['recommendation'] == 24


def test_catalog_links_do_not_invalidate_same_calculation(setup):
    from app.sales import store_window
    engine, _, _ = setup
    with Session(engine) as s:
        params = calculate(s, {'location':'own','item_ids':['m']})['parameters']
    window=dict(platform='ozon',start=params['start'],end=params['end'],at=datetime.now(MSK).isoformat(),complete=True,verified=True,covered_items=['m'],events=[])
    store_window(engine,window)
    with Session(engine) as s:before=calculate(s, {'location':'own','item_ids':['m']})
    store_window(engine,{**window,'catalog_matches':{'m':[{'product_id':'1','offer_id':'test','skus':['2']}]}})
    with Session(engine) as s:after=calculate(s, {'location':'own','item_ids':['m']})
    assert after['fingerprint']==before['fingerprint']
    assert after['rows'][0]['platforms']['ozon']['catalog_matches'][0]['offer_id']=='test'


@pytest.mark.parametrize('missing',[False,True])
def test_unknown_or_missing_stock_does_not_invent_a_recommendation(setup,missing):
    from app.db import Balance,Item,transaction
    from sqlalchemy import select
    from app.sales import store_window
    engine,_,_=setup
    with transaction(engine) as s:
        if missing:s.add(Item(id='empty',name='Без остатка',model='Тест',color='',size='S',category='',kind='garment',unit='piece',barcodes=['empty'],photo='',source={}))
        else:
            b=s.scalar(select(Balance).where(Balance.item_id=='m',Balance.location=='own'));b.known=False
    iid='empty' if missing else 'm'
    with Session(engine) as s:p=calculate(s,dict(location='own',item_ids=[iid]))['parameters']
    for platform in ('wb','ozon'):
        store_window(engine,dict(platform=platform,start=p['start'],end=p['end'],at=datetime.now(MSK).isoformat(),complete=True,verified=True,covered_items=[iid],events=[dict(id=platform,item_id=iid,date=p['end'],kind='sale',quantity=6)]))
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=[iid]))['rows'][0]
        assert row['stock_assumed_zero'] and row['recommendation'] is None and row['free'] is None and row['target']==24
        b=s.scalar(select(Balance).where(Balance.item_id==iid,Balance.location=='own'))
        assert b is None if missing else b.known is False
