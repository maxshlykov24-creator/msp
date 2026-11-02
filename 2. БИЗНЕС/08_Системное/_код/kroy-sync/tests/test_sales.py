from datetime import datetime,timedelta
from copy import deepcopy
from sqlalchemy.orm import Session
import pytest
from app.db import Entity,now,transaction
from app.service import DomainError
from app.sales import ozon_entries,reconcile_ozon,collect_ozon_day,store_window,mappings
from app.cutting import calculate,MSK
from app.sales_jobs import enqueue,claim,finish
from test_app import setup


def accrual(key,quantity=1,price='100',day='2026-09-10',unit='posting',sku=1,delivery=True):
    return dict(accrual_id=key,date=day,unit_number=unit,posting={'products':[dict(sku=sku,quantity=quantity,commission=dict(seller_price={'amount':price},sale_amount={'amount':price}),delivery={} if delivery else None)]})


def test_ozon_counts_only_realization_and_genuine_returns():
    sale=accrual(1,3);ret=accrual(2,2,'-100',delivery=False)
    fee=accrual(3,100);fee['posting']['products'][0]['commission']=None
    rows,blocked,corrections=ozon_entries([sale,ret,fee,sale])
    assert [(r['kind'],r['quantity']) for r in rows]==[('sale',3),('return',2)]
    assert not blocked and not corrections


def test_delivery_reversal_cancels_original_not_customer_return():
    rows,blocked,reversed_units=ozon_entries([accrual(1,3),accrual(2,1,'-100'),accrual(3,1,'-100',delivery=False)])
    assert sum(r['quantity'] for r in rows if r['kind']=='sale')==2
    assert sum(r['quantity'] for r in rows if r['kind']=='return')==1
    assert reversed_units==1 and not blocked
    rows,blocked,_=ozon_entries([accrual(4,1,'-100',unit='missing')])
    assert not rows and blocked=={'1'}


def test_same_day_real_sale_return_are_not_net_deduplicated():
    rows,_,_=ozon_entries([accrual(1),accrual(2,price='-100',delivery=False)])
    assert len(rows)==2 and {r['kind'] for r in rows}=={'sale','return'}


def test_ozon_rejects_uncertain_sign_and_conflicting_duplicate():
    for value in ['0','NaN']:
        with pytest.raises((DomainError,ValueError)):ozon_entries([accrual(1,price=value)])
    with pytest.raises(DomainError,match='Конфликт'):ozon_entries([accrual(1),accrual(1,2)])


def test_reconciliation_requires_each_sku_and_gross_counts():
    report={'result':{'rows':[{'item':{'sku':1},'delivery_commission':{'quantity':2},'return_commission':{'quantity':1}}]}}
    cert=reconcile_ozon([accrual(1,2),accrual(2,price='-100',delivery=False)],report)
    assert cert['verified'] and cert['sales']==2 and cert['returns']==1
    assert not reconcile_ozon([accrual(1)],report)['verified']


def test_pagination_cache_and_complete_boundary(tmp_path):
    calls=[]
    def get(url,headers,body):
        calls.append(body['last_id'])
        return {'accruals':[accrual(1 if not body['last_id'] else 2)],'last_id':'next' if not body['last_id'] else ''}
    d=collect_ozon_day('2026-09-10',{},tmp_path,get)
    assert len(d['accruals'])==2 and calls==['','next']
    collect_ozon_day('2026-09-10',{},tmp_path,lambda *_:pytest.fail('Unnecessary API call'))
    with pytest.raises(DomainError,match='Повтор'):
        collect_ozon_day('2026-09-11',{},tmp_path,lambda *_:{'accruals':[accrual(1,day='2026-09-11')],'last_id':'again'})
    assert not (tmp_path/'ozon-2026-09-11.json').exists()


def test_semantic_retrieval_time_does_not_invalidate_draft(setup):
    engine,u,cmd=setup
    end=str(datetime.now(MSK).date()-timedelta(days=1));start=str(datetime.now(MSK).date()-timedelta(days=14))
    data=dict(platform='wb',start=start,end=end,at=now().isoformat(),complete=True,verified=True,covered_items=['s'],events=[dict(id='sale',item_id='s',date=end,kind='sale',quantity=10)])
    store_window(engine,data)
    with Session(engine) as s:first=calculate(s,dict(location='own',item_ids=['s']))
    data['at']=(now()+timedelta(seconds=5)).isoformat();store_window(engine,data)
    with Session(engine) as s:second=calculate(s,dict(location='own',item_ids=['s']))
    assert first['fingerprint']==second['fingerprint']
    data['events'][0]['quantity']=11;store_window(engine,data)
    with Session(engine) as s:third=calculate(s,dict(location='own',item_ids=['s']))
    assert first['fingerprint']!=third['fingerprint']


def test_refresh_queue_reuses_work_and_keeps_stock_unchanged(setup):
    from test_app import balance
    engine,u,cmd=setup;before=balance(engine)
    raw=dict(start=str(datetime.now(MSK).date()-timedelta(days=14)),end=str(datetime.now(MSK).date()-timedelta(days=1)))
    job=enqueue(engine,u.id,raw);assert enqueue(engine,u.id,raw)['id']==job['id']
    assert claim(engine)['id']==job['id'] and claim(engine) is None
    finish(engine,job['id'],{'ozon':{'state':'ready'}})
    assert enqueue(engine,u.id,raw)['status']=='done'
    assert balance(engine)==before


def test_external_sku_ambiguity_never_produces_known_zero(setup):
    engine,u,cmd=setup
    with transaction(engine) as s:
        for iid in ('s','m'):
            s.add(Entity(id='map:'+iid,kind='mapping',version=1,data=dict(platform='ozon',item_id=iid,state='unique',candidates=[{'skus':['123']}])) )
    _,skus,covered=mappings(engine)
    assert not covered['ozon'] and set(skus['123'])=={'s','m'}


def test_normalized_event_identity_survives_product_reorder():
    row=accrual(1);row['posting']['products'].append(accrual(2,sku=2)['posting']['products'][0])
    first,_,_=ozon_entries([row]);other=deepcopy(row);other['posting']['products'].reverse();second,_,_=ozon_entries([other])
    assert sorted(first,key=lambda e:e['id'])==sorted(second,key=lambda e:e['id'])


def test_full_sync_proves_unlisted_zero_and_combines_platforms(setup,tmp_path):
    import json
    from app.sales import sync
    from test_app import balance
    engine,u,cmd=setup;before=balance(engine)
    end=datetime.now(MSK).date()-timedelta(days=1);start=end-timedelta(days=1)
    with transaction(engine) as s:s.add(Entity(id='sales-reconciliation:ozon:v2',kind='sales_reconciliation',version=1,data={'verified':True,'period':'test-fixture'}))
    store_window(engine,dict(platform='wb',start=str(start),end=str(end),at=now().isoformat(),complete=True,verified=True,covered_items=['s','m'],events=[dict(id='wb-sale',item_id='s',date=str(end),kind='sale',quantity=3),dict(id='wb-return',item_id='s',date=str(end),kind='return',quantity=1)]))
    for day,raw in [(start,accrual(1,7,day=str(start),sku=123)),(end,accrual(2,2,'-100',day=str(end),sku=123,delivery=False))]:
        (tmp_path/('ozon-'+str(day)+'.json')).write_text(json.dumps(dict(at=now().isoformat(),complete=True,date=str(day),accruals=[raw],source_hashes=['test'])))
    (tmp_path/'ozon-catalog.json').write_text(json.dumps(dict(at=now().isoformat(),complete=True,items=[dict(id=1,barcodes=['001'],sources=[{'sku':123}])])))
    result=sync(engine,{'KROY_SALES_CACHE':str(tmp_path),'OZON_API_KEY':'test','OZON_CLIENT_ID':'test'},str(start),str(end))
    assert result['ozon']['state']=='ready' and result['wb']['state']=='ready_cached'
    with Session(engine) as s:
        c=calculate(s,dict(location='own',item_ids=['s','m'],start=str(start),end=str(end)))
    by={r['item_id']:r for r in c['rows']}
    assert by['s']['net']==7 and by['s']['platforms']['ozon']['returns']==2
    assert by['m']['net']==0 and by['m']['platforms']['ozon']['listed'] is False
    assert balance(engine)==before


def test_catalog_requires_full_pages_and_unique_local_identity(setup,tmp_path):
    from app.sales import collect_ozon_catalog,catalog_links
    engine,u,cmd=setup
    def get(url,headers,body):
        if url.endswith('/product/list'):return {'result':{'items':[{'product_id':1}], 'total':1}}
        return {'items':[{'id':1,'barcodes':['001'],'sources':[{'sku':123}]}]}
    catalog=collect_ozon_catalog({},tmp_path,get)
    skus,covered,absent,proof=catalog_links(engine,catalog)
    assert skus=={'123':['s']} and covered=={'s'} and absent=={'m'} and proof['all_products_matched']
    with pytest.raises(DomainError):
        collect_ozon_catalog({},tmp_path/'bad',lambda *_:{'result':{'items':[], 'total':10,'last_id':''}})


def test_refresh_api_permission_and_progress(setup):
    import hashlib
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app.db import User
    engine,u,cmd=setup
    with transaction(engine) as s:s.add(User(id='view',name='Viewer',role='viewer',active=True,token_hash=hashlib.sha256(b'viewer').hexdigest()))
    client=TestClient(create_app(engine));body={'start':str(datetime.now(MSK).date()-timedelta(days=14)),'end':str(datetime.now(MSK).date()-timedelta(days=1))}
    assert client.post('/api/cutting/sales-refresh',json=body,headers={'Authorization':'Bearer viewer'}).status_code==403
    response=client.post('/api/cutting/sales-refresh',json=body,headers={'Authorization':'Bearer test-only'})
    assert response.status_code==200
    job=response.json();assert job['status']=='queued' and 'actor' not in job
    assert client.get('/api/cutting/sales-refresh/'+job['id'],headers={'Authorization':'Bearer test-only'}).json()['status']=='queued'
