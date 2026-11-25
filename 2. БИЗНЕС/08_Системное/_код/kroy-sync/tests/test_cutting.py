from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select
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


def test_missing_sources_count_as_zero_and_negative_net(setup):
    engine,u,cmd=setup
    p=payload(engine); c=p['cutting']['calculation']
    assert c['rows'][0]['net'] == 0 and c['rows'][0]['recommendation'] == 0
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
    events,missing,_=wb_events([dict(date=dates['end']+'T23:30:00',saleID='R1',barcode='001'),dict(date=dates['end']+'T23:30:00',saleID='R1',barcode='001')],dates['start'],dates['end'],{'001':['s']})
    assert missing==0 and events[0]['kind']=='return'
    data=dict(platform='wb',start=dates['start'],end=dates['end'],at='2026-10-29T00:00:00Z',complete=True,verified=True,covered_items=['s'],events=events)
    store_window(engine,data);store_window(engine,data)
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s']))['rows'][0]
        assert row['platforms']['wb']['returns']==1 and row['net'] == -1 and row['net_known']==-1 and row['recommendation'] == 0 and row['sales_missing']==['ozon']
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


def test_missing_card_uses_the_marketplace_that_is_known(setup):
    from app.sales import store_window
    engine, _, _ = setup
    with Session(engine) as s:
        params = calculate(s, dict(location='own', item_ids=['s', 'm']))['parameters']
    at = datetime.now(MSK).isoformat()
    store_window(engine, dict(platform='wb', start=params['start'], end=params['end'], at=at, complete=True, verified=True, covered_items=['s', 'm'], events=[
        dict(id='wb-s', item_id='s', date=params['end'], kind='sale', quantity=40),
        dict(id='wb-m', item_id='m', date=params['end'], kind='sale', quantity=8)]))
    store_window(engine, dict(platform='ozon', start=params['start'], end=params['end'], at=at, complete=True, verified=True, covered_items=[], events=[]))
    with transaction(engine) as s:
        s.add(Entity(id='mapping:ozon:s', kind='mapping', version=1, data=dict(item_id='s', platform='ozon', state='missing', candidates=[])))
        s.add(Entity(id='mapping:ozon:m', kind='mapping', version=1, data=dict(item_id='m', platform='ozon', state='ambiguous', candidates=[dict(product_id='1'), dict(product_id='2')])))
    with Session(engine) as s:
        rows = {r['item_id']: r for r in calculate(s, dict(location='own', item_ids=['s', 'm']))['rows']}
    assert rows['s']['platforms']['ozon']['absent'] is True and rows['s']['sales_missing'] == [] and rows['s']['net'] == 40 and rows['s']['recommendation'] == 30
    assert rows['m']['net'] == 8 and rows['m']['sales_missing'] == ['ozon'] and rows['m']['recommendation'] == 8
    with transaction(engine) as s:
        for e in s.scalars(select(Entity).where(Entity.kind == 'sales_window', Entity.data['platform'].as_string() == 'ozon')):
            s.delete(e)
    with Session(engine) as s:
        alone = calculate(s, dict(location='own', item_ids=['s']))['rows'][0]
    assert alone['net'] == 40 and alone['sales_missing'] == ['ozon'] and alone['recommendation'] == 30 and not alone['platforms']['ozon'].get('absent')


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
    assert row['net'] == 12 and row['target'] == 12 and row['recommendation'] == 12


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
def test_unknown_or_missing_stock_assumed_zero_only_for_demand(setup,missing):
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
        assert row['stock_assumed_zero'] and row['recommendation']==12 and Decimal(row['free'])==0 and row['target']==12
        b=s.scalar(select(Balance).where(Balance.item_id==iid,Balance.location=='own'))
        assert b is None if missing else b.known is False


def test_purchase_reserves_only_real_cloth_and_cancel_releases_only_owned(setup):
    engine,u,cmd=setup
    p=payload(engine);p['materials'][0]['quantity']='120';p['cutting']['groups'][0]['quantity']='120'
    p['cutting']['allow_purchase']=True
    issued=cmd('task.issue',p)
    assert balance(engine,'cloth')['free']==0
    assert balance(engine,'cloth')['reserved']==Decimal('100.5')
    with Session(engine) as s:
        task=s.get(Entity,issued['id']).data
        assert Decimal(task['purchase_materials'][0]['quantity'])==Decimal('19.5')
        row=calculate(s,dict(location='own',item_ids=['s']))['rows'][0]
        assert row['future']=='0'
    with pytest.raises(DomainError,match='не поступила'):
        cmd('task.confirm',dict(id=issued['id'],version=issued['version'],confirm_actual=True))
    cmd('task.cancel',dict(id=issued['id'],version=issued['version']))
    assert balance(engine,'cloth')['reserved']==0
    assert balance(engine,'cloth')['free']==Decimal('100.5')


def test_purchase_receipt_allocation_and_reversal(setup):
    engine,u,cmd=setup
    p=payload(engine);p['materials'][0]['quantity']='120';p['cutting']['groups'][0]['quantity']='120';p['cutting']['allow_purchase']=True
    issued=cmd('task.issue',p)
    cmd('material.receive',dict(item_id='cloth',location='own',quantity='20'))
    reserved=cmd('task.reserve_materials',dict(id=issued['id'],version=issued['version']))
    fact=cmd('task.confirm',dict(id=issued['id'],version=reserved['version'],confirm_actual=True))
    assert balance(engine,'cloth')['reserved']==0
    assert balance(engine,'cloth')['free']==Decimal('.5')
    rev=cmd('task.reverse_fact',dict(id=issued['id'],version=fact['version'],fact_id=fact['document_id']))
    assert balance(engine,'cloth')['reserved']==120
    cmd('task.cancel',dict(id=issued['id'],version=rev['version']))
    assert balance(engine,'cloth')['free']==Decimal('120.5')


def test_fbo_reduces_demand_without_touching_own_stock(setup):
    from app.sales import store_window
    from app.fbo_stock import store_snapshot
    engine,_,_=setup
    with Session(engine) as s:params=calculate(s,dict(location='own',item_ids=['s']))['parameters']
    for platform in ('wb','ozon'):
        store_window(engine,dict(platform=platform,start=params['start'],end=params['end'],at=datetime.now(MSK).isoformat(),complete=True,verified=True,covered_items=['s'],events=[dict(id=platform,item_id='s',date=params['end'],kind='sale',quantity=30)]))
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))['rows'][0]
        assert row['recommendation']==50 and row['recommendation_partial'] is False and row['fbo_total'] is None and set(row['fbo_missing'])=={'wb','ozon'} and row['fbo_counted']==0
        assert all('основание' not in text for text in calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))['warnings'])
    for platform in ('wb','ozon'):
        store_snapshot(engine,platform,{'s':5},source='verified fixture',data_at=datetime.now(MSK).isoformat())
    with Session(engine) as s:
        result=calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))
        assert result['rows'][0]['recommendation']==40
        fingerprint=result['fingerprint']
    store_snapshot(engine,'wb',{'s':5},source='verified fixture',data_at=datetime.now(MSK).isoformat())
    with Session(engine) as s:
        assert calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))['fingerprint']==fingerprint
    assert balance(engine)['free']==10


def test_purchase_layout_over_thirty_rolls_and_no_fabric(setup):
    from app.planning import grouped_layout_preview
    result=grouped_layout_preview('Лонг скимс',{'Чёрный':{'S':3000}},[{'S':1}],{'Чёрный':0},allow_purchase=True)
    assert result['result']['Чёрный']['rolls']==40
    assert result['result']['Чёрный']['purchase_rolls']==40


def test_purchase_revision_releases_only_allocated_material(setup):
    engine,_,cmd=setup
    p=payload(engine);p['materials'][0]['quantity']='120';p['cutting']['groups'][0]['quantity']='120';p['cutting']['allow_purchase']=True
    task=cmd('task.issue',p)
    revised=cmd('task.revise',dict(id=task['id'],version=task['version'],products=p['products'],materials=[dict(item_id='cloth',quantity=50)]))
    assert balance(engine,'cloth')['reserved']==50
    assert balance(engine,'cloth')['free']==Decimal('50.5')
    cmd('task.cancel',dict(id=task['id'],version=revised['version']))
    assert balance(engine,'cloth')['free']==Decimal('100.5')


def test_ozon_fbo_only_exact_and_complete():
    from app.fbo_stock import normalize_ozon
    catalog={'items':[{'id':1,'sources':[{'sku':11}]},{'id':2,'sources':[{'sku':12}]}]}
    links={'11':['s'],'12':['s']}
    rows=[{'product_id':1,'stocks':[{'type':'fbo','sku':11,'present':10,'reserved':2},{'type':'fbs','sku':11,'present':999,'reserved':0}]}, {'product_id':2,'stocks':[{'type':'fbo','sku':12,'present':5,'reserved':1}]}]
    assert normalize_ozon(rows,catalog,links)=={'s':12}
    assert normalize_ozon(rows[:1],catalog,links)=={}
    with pytest.raises(DomainError):normalize_ozon(rows+rows[:1],catalog,links)
    rows[0]['stocks'][0]['reserved']=11
    with pytest.raises(DomainError):normalize_ozon(rows,catalog,links)


def test_ozon_fbo_cooldown_survives_failure(setup,tmp_path):
    from app.fbo_stock import sync_ozon
    from app.sales import RateLimited
    engine,_,_=setup
    calls=[]
    def denied(*args):calls.append(args[0]);raise RateLimited(7200,'ozon')
    env=dict(OZON_API_KEY='test',OZON_CLIENT_ID='test',KROY_SALES_CACHE=str(tmp_path))
    first=sync_ozon(engine,env,denied)
    assert first['state']=='waiting'
    assert sync_ozon(engine,env,denied)==first
    assert len(calls)==1


def test_ignored_fbo_changes_do_not_invalidate_draft_and_old_source_is_stale(setup):
    from app.fbo_stock import store_snapshot,read_stock
    engine,_,_=setup
    with Session(engine) as s:before=calculate(s,dict(location='own',item_ids=['s']))
    store_snapshot(engine,'ozon',{'s':5},source='fixture',data_at=(datetime.now(MSK)-timedelta(hours=5)).isoformat())
    with Session(engine) as s:
        assert read_stock(s,'s')[0]['ozon']['stale']
        assert calculate(s,dict(location='own',item_ids=['s']))['fingerprint']==before['fingerprint']


def test_tracked_partial_layout_counts_only_secured_remaining(setup):
    engine,_,cmd=setup
    p=payload(engine)
    p['products'][0]['quantity']='20';p['materials'][0]['quantity']='120'
    p['cutting']['quantities']={'s':'20'};p['cutting']['allow_purchase']=True
    group=p['cutting']['groups'][0];group['id']='first'
    p['cutting']['groups'].append({**group,'id':'second','quantity':'118'})
    task=cmd('task.issue',p)
    fact=cmd('task.confirm',dict(id=task['id'],version=task['version'],group_id='first',confirm_actual=True,products=[dict(item_id='s',quantity=4)],materials=[dict(item_id='cloth',quantity=1)]))
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s']))['rows'][0]
        assert row['future']=='10' # Frozen 4 + secured remainder 6; second layout awaits cloth.
    with pytest.raises(DomainError,match='выбранной раскладки'):
        cmd('task.confirm',dict(id=task['id'],version=fact['version'],group_id='first',confirm_actual=True,products=[dict(item_id='s',quantity=8)],materials=[]))
    reversed=cmd('task.reverse_fact',dict(id=task['id'],version=fact['version'],fact_id=fact['document_id']))
    with Session(engine) as s:assert calculate(s,dict(location='own',item_ids=['s']))['rows'][0]['future']=='10'


def test_unfunded_completion_requires_explicit_plan_revision(setup):
    engine,_,cmd=setup
    p=payload(engine);p['materials'][0]['quantity']='120';p['cutting']['groups'][0]['quantity']='120';p['cutting']['allow_purchase']=True
    task=cmd('task.issue',p)
    fact=cmd('task.confirm',dict(id=task['id'],version=task['version'],confirm_actual=True,products=[dict(item_id='s',quantity=1)],materials=[dict(item_id='cloth',quantity=1)]))
    with pytest.raises(DomainError,match='недостающая ткань'):
        cmd('task.confirm',dict(id=task['id'],version=fact['version'],confirm_actual=True,products=[dict(item_id='s',quantity=9)],materials=[]))
    assert balance(engine)['frozen']==1
    revised=cmd('task.revise',dict(id=task['id'],version=fact['version'],products=p['products'],materials=[dict(item_id='cloth',quantity=1)]))
    cmd('task.confirm',dict(id=task['id'],version=revised['version'],confirm_actual=True,products=[dict(item_id='s',quantity=9)],materials=[]))
    assert balance(engine)['frozen']==10


def test_manual_uncertain_never_crosses_target_date(setup):
    engine,_,cmd=setup
    p=payload(engine);p['materials'][0]['quantity']='120';p['cutting']['groups'][0]['quantity']='120';p['cutting']['allow_purchase']=True
    p['cutting']['available_on']=str(datetime.now(MSK).date()+timedelta(days=60))
    task=cmd('task.issue',p)
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s'],include_uncertain=[task['id']]))['rows'][0]
        assert row['future']=='0' and not row['coverage'][0]['included']


def test_versioned_norms_preview_and_issue_recheck(setup):
    from app.api import create_app
    from fastapi.testclient import TestClient
    engine,_,cmd=setup
    client=TestClient(create_app(engine));headers={'Authorization':'Bearer test-only'}
    cmd('norm.set',dict(item_id='s',effective_date=str(datetime.now(MSK).date()),**{'yield':'99.0'},confirmed=True))
    r=client.post('/api/planning/grouped-layout',headers=headers,json=dict(model='Лонг скимс',item_ids=['s'],needs_by_color={'red':{'S':99}},ratios=[{'S':1}],available_by_color={'red':1}))
    assert r.status_code==200,r.text
    assert r.json()['norm']==99 and r.json()['result']['red']['output']['S']==99
    assert client.get('/api/norms',headers=headers).json()['overrides'][0]['yield']=='99.0'
    p=payload(engine);p['cutting']['groups'][0].update(norm='Лонг скимс',norm_snapshot=r.json()['norm_snapshot'])
    cmd('norm.set',dict(item_id='s',effective_date=str(datetime.now(MSK).date()+timedelta(days=1)),**{'yield':150},confirmed=True))
    issued=cmd('task.issue',p) # Future norm cannot replace today's version.
    p=payload(engine);p['cutting']['groups'][0].update(norm='Лонг скимс',norm_snapshot=r.json()['norm_snapshot'])
    cmd('norm.set',dict(item_id='s',effective_date=str(datetime.now(MSK).date()),**{'yield':100},confirmed=True))
    with pytest.raises(DomainError,match='Норма изменилась'):cmd('task.issue',p)
    with Session(engine) as s:assert s.get(Entity,issued['id']).data['cutting']['groups'][0]['norm_value']==99


def test_norm_scope_does_not_leak_to_other_sizes_or_materials(setup):
    from app.norms import resolve
    engine,_,cmd=setup
    cmd('norm.set',dict(item_id='s',material_id='cloth',effective_date=str(datetime.now(MSK).date()),**{'yield':99},confirmed=True))
    with Session(engine) as s:
        assert resolve(s,'Лонг скимс',['s'],{'s':'cloth'})['s']['yield']==99
        assert resolve(s,'Лонг скимс',['s'])['s']['yield']==75
        assert resolve(s,'Лонг скимс',['m'],{'m':'cloth'})['m']['yield']==75
        with pytest.raises(DomainError,match='разные нормы'):resolve(s,'Лонг скимс',['s','m'],{'s':'cloth','m':'cloth'})
    cmd('norm.set',dict(item_id='s',material_id='cloth',effective_date=str(datetime.now(MSK).date()),**{'yield':100},confirmed=False))
    with Session(engine) as s:
        with pytest.raises(DomainError,match='не подтверждена'):resolve(s,'Лонг скимс',['s'],{'s':'cloth'})


def test_empty_refresh_keeps_previous_sales_and_explains_the_gap(setup):
    from app.sales import store_window, wb_events
    engine,_,_=setup
    with Session(engine) as s:params=calculate(s,dict(location='own',item_ids=['s']))['parameters']
    events,_,_=wb_events([dict(date=params['end']+'T12:00:00Z',saleID='S-live',barcode='001')],params['start'],params['end'],{'001':['s']})
    assert events[0]['date']==params['end'] and events[0]['kind']=='sale'
    store_window(engine,dict(platform='wb',start=params['start'],end=params['end'],at='2026-11-01T00:00:00+00:00',complete=True,verified=True,covered_items=['s'],events=events))
    with pytest.raises(DomainError,match='пустая'):
        store_window(engine,dict(platform='wb',start=params['start'],end=params['end'],at='2026-11-25T00:00:00+00:00',complete=True,verified=True,covered_items=['s'],events=[]))
    wider=str(datetime.fromisoformat(params['start']).date()-timedelta(days=5))
    store_window(engine,dict(platform='wb',start=wider,end=params['end'],at='2026-11-25T03:00:00+00:00',complete=True,verified=True,covered_items=['s'],events=[]))
    store_window(engine,dict(platform='ozon',start=params['start'],end=params['end'],at='2026-11-25T03:00:00+00:00',complete=True,verified=True,covered_items=['s'],events=[dict(id='oz',item_id='s',date=params['end'],kind='sale',quantity=2)]))
    with Session(engine) as s:
        row=calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))['rows'][0]
    assert row['platforms']['wb']['sales']==1 and row['kept_previous_sales'] and row['net']==3
    with Session(engine) as s:
        body=calculate(s,dict(location='own',item_ids=['s'],include_fbo=True))
    assert any('пустая' in text for text in body['warnings']) and any('ФБО' in text for text in body['warnings'])
