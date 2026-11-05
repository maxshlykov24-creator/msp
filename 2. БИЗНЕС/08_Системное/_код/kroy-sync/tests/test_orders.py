import copy
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.db import Entity,User,Document,Movement,now,transaction
from app.order_feed import ingest,ingest_returns,list_orders,stable_id
from app.order_sync import Client,FeedFailure,postings,wb_history,wb_statuses,retry_seconds
from app.order_accounting import reconcile
from test_app import setup,balance


def wb(status='new',state='new',qty='001'):
 return dict(id=123,rid='stable-rid',createdAt=now().isoformat(),skus=[qty],supplierStatus=status,wbStatus=state)


def market(engine):
 with Session(engine) as s:return s.scalar(select(Entity).where(Entity.kind=='market_order'))


def count(engine,model):
 with Session(engine) as s:return s.scalar(select(func.count()).select_from(model))


def test_import_is_idempotent_and_statistics_never_erase_status(setup):
 engine,_,_=setup;r=wb('complete','sold');before=count(engine,Movement)
 ingest(engine,'wb','fbs','seller',[r],'wb-fbs')
 original=copy.deepcopy(market(engine).data)
 ingest(engine,'wb','fbs','seller',[r],'wb-fbs')
 assert len(market(engine).data['history'])==1
 stats=dict(srid='stable-rid',date=now().isoformat(),barcode='001',isCancel=False)
 ingest(engine,'wb','fbs','seller',[stats],'wb-statistics')
 assert market(engine).data['status']=='delivered'
 assert market(engine).data['source']=='wb-fbs'
 assert count(engine,Movement)==before


def test_pending_reserves_once_handover_does_not_deduct_twice(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs')
 reconcile(engine,'own');assert balance(engine)['reserved']==1 and balance(engine)['free']==9
 before=count(engine,Document);reconcile(engine,'own');assert count(engine,Document)==before
 ingest(engine,'wb','fbs','seller',[wb('complete','ready_for_pickup')],'wb-fbs')
 reconcile(engine,'own');assert balance(engine)['reserved']==0 and balance(engine)['free']==9
 reconcile(engine,'own');assert balance(engine)['free']==9


def test_cancel_before_handover_releases_reserve_after_does_not_receipt(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs');reconcile(engine,'own')
 ingest(engine,'wb','fbs','seller',[wb('cancel','canceled')],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==10 and balance(engine)['reserved']==0
 # Second identity is shipped before cancellation.
 r={**wb(),'id':124,'rid':'other'}
 ingest(engine,'wb','fbs','seller',[r],'wb-fbs');reconcile(engine,'own')
 ingest(engine,'wb','fbs','seller',[{**r,'supplierStatus':'complete','wbStatus':'sold'}],'wb-fbs');reconcile(engine,'own')
 ingest(engine,'wb','fbs','seller',[{**r,'supplierStatus':'complete','wbStatus':'canceled'}],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==9 and balance(engine)['reserved']==0
 with Session(engine) as s:
  e=s.get(Entity,stable_id('wb','seller','fbs','other'));assert e.data['accounting_state']=='return_pending'


def test_historical_and_fbo_orders_do_not_create_warehouse_documents(setup):
 engine,_,_=setup;before=count(engine,Document)
 ingest(engine,'wb','fbs','seller',[wb('complete','sold')],'wb-fbs')
 ingest(engine,'wb','fbo','seller',[dict(srid='fbw',date=now().isoformat(),barcode='001',isCancel=False)],'wb-statistics')
 reconcile(engine,'own')
 assert count(engine,Document)==before and balance(engine)['free']==10


def test_unknown_mapping_saved_but_never_reserved(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb(qty='unknown')],'wb-fbs');reconcile(engine,'own')
 e=market(engine);assert e.data['needs_mapping'] and e.data['accounting_state']=='needs_mapping'
 assert balance(engine)['free']==10


def test_shortage_then_new_balance_reserves_automatically(setup):
 engine,_,cmd=setup
 ingest(engine,'wb','fbs','seller',[wb(qty='002')],'wb-fbs');reconcile(engine,'own')
 assert market(engine).data['accounting_state']=='shortage'
 cmd('stock.adjust',dict(item_id='m',location='own',version=balance(engine,'m')['version'],physical=3))
 reconcile(engine,'own');assert balance(engine,'m')['reserved']==1 and balance(engine,'m')['free']==2


def test_composition_change_releases_previous_and_reserves_new_atomically(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs');reconcile(engine,'own')
 ingest(engine,'wb','fbs','seller',[wb(qty='002')],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==10 and balance(engine)['reserved']==0
 assert market(engine).data['accounting_state']=='shortage'


def test_no_cross_account_dedup_and_stale_observation_ignored(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','one',[wb()],'wb-fbs',now().isoformat())
 ingest(engine,'wb','fbs','two',[wb()],'wb-fbs',now().isoformat())
 ingest(engine,'wb','fbs','one',[wb('cancel','canceled')],'wb-fbs',(now()-timedelta(days=1)).isoformat())
 with Session(engine) as s:
  assert len(list(s.scalars(select(Entity).where(Entity.kind=='market_order'))))==2
  assert s.get(Entity,stable_id('wb','one','fbs','stable-rid')).data['status']=='new'


def test_unknown_return_does_not_create_receipt(setup):
 engine,_,_=setup;before=count(engine,Movement)
 ingest_returns(engine,'account',[dict(id=1,schema='Fbs',posting_number='123-1',product={'sku':999,'quantity':1},visual={'status':{'sys_name':'ReturnedToOzon','display_name':'На складе Ozon'},'change_moment':now().isoformat()})])
 assert count(engine,Movement)==before
 with Session(engine) as s:
  r=s.scalar(select(Entity).where(Entity.kind=='market_return'));assert r.data['item_id'] is None


def test_pagination_repeated_cursor_and_incomplete_status_rejected():
 class Fake:
  def get(self,path,body=None,params=None):return {'postings':[{'posting_number':'p'}],'has_next':True,'cursor':'same'}
 with pytest.raises(FeedFailure):postings(Fake(),'fbs',now().isoformat(),now().isoformat())
 class Missing:
  def get(self,*args,**kwargs):return {'orders':[]}
 with pytest.raises(FeedFailure):wb_statuses(Missing(),[1])
 with pytest.raises(FeedFailure):wb_history(Missing(),now())


def test_new_page_is_saved_before_later_history_failure(setup):
 engine,_,_=setup
 class Fake:
  def __init__(self):self.n=0
  def get(self,*args,**kwargs):
   self.n+=1
   if self.n==1:return {'postings':[dict(posting_number='kept',status='awaiting_packaging',in_process_at=now().isoformat(),products=[])],'has_next':True,'cursor':'1'}
   raise FeedFailure('interrupted')
 with pytest.raises(FeedFailure):postings(Fake(),'fbs','start','end',on_page=lambda rows:ingest(engine,'ozon','fbs','seller',rows,'ozon-fbs',only_new=True))
 assert market(engine).data['external_id']=='kept'


def test_read_allowlist_and_rate_header():
 assert retry_seconds({'Retry-After':'300','X-Ratelimit-Retry':'600'})==600
 c=Client(None,'ozon','x',{})
 with pytest.raises(FeedFailure):c.get('/v1/product/import',{})


def test_order_api_filters_and_refresh_permission(setup):
 import hashlib
 from fastapi.testclient import TestClient
 from app.api import create_app
 engine,u,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs')
 with transaction(engine) as s:s.add(User(id='view-orders',name='Viewer',role='viewer',active=True,token_hash=hashlib.sha256(b'view-orders').hexdigest()))
 c=TestClient(create_app(engine));headers={'Authorization':'Bearer test-only'}
 assert c.get('/api/orders',headers=headers).json()['total']==1
 assert c.get('/api/orders?platform=ozon',headers=headers).json()['total']==0
 assert c.get('/api/orders?q=001',headers=headers).json()['total']==1
 assert c.get('/api/orders?q=%25',headers=headers).json()['total']==0
 assert c.get('/api/orders?start=wrong',headers=headers).status_code==422
 assert c.post('/api/orders/refresh',headers={'Authorization':'Bearer view-orders'},json={}).status_code==403
 assert c.post('/api/orders/refresh',headers=headers,json={}).status_code==200


def test_unverified_historical_pending_waits_for_exact_current_check(setup):
 engine,_,_=setup
 with transaction(engine) as s:s.add(Entity(id='oz-map',kind='mapping',version=1,data=dict(platform='ozon',item_id='s',state='unique',candidates=[{'skus':['123']}])) )
 raw=dict(posting_number='old-pending',status='awaiting_packaging',in_process_at=(now()-timedelta(days=180)).isoformat(),products=[dict(sku=123,offer_id='s',quantity=2)])
 before=count(engine,Document)
 ingest(engine,'ozon','fbs','seller',[raw],'ozon-fbs');reconcile(engine,'own')
 assert count(engine,Document)==before and balance(engine)['reserved']==0
 ingest(engine,'ozon','fbs','seller',[{**raw,'_status_verified':True}],'ozon-fbs');reconcile(engine,'own')
 assert balance(engine)['reserved']==2 and balance(engine)['free']==8


def test_concurrent_workers_do_not_reserve_twice(setup):
 from concurrent.futures import ThreadPoolExecutor
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb(),{**wb(),'id':124,'rid':'second'}],'wb-fbs')
 with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:reconcile(engine,'own'),range(2)))
 assert balance(engine)['reserved']==2 and balance(engine)['free']==8


def test_return_status_proves_handover_but_not_physical_receipt(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs');reconcile(engine,'own')
 with transaction(engine) as s:
  e=s.get(Entity,market(engine).id);e.data={**e.data,'status':'returned'};e.version+=1
 reconcile(engine,'own')
 assert balance(engine)['free']==9 and balance(engine)['reserved']==0
 assert market(engine).data['accounting_state']=='return_pending'


def test_date_filters_use_moscow_calendar(setup):
 engine,_,_=setup
 raw={**wb(),'createdAt':'2026-11-04T21:30:00+00:00'}
 ingest(engine,'wb','fbs','seller',[raw],'wb-fbs')
 with Session(engine) as s:
  assert list_orders(s,start='2026-11-05',end='2026-11-05')['total']==1
  assert list_orders(s,start='2026-11-04',end='2026-11-04')['total']==0


def test_partial_import_keeps_new_and_retry_state_is_per_account(setup):
 import urllib.error,io
 from app.order_feed import sync_state
 from app.order_sync import Paused
 engine,_,_=setup
 def failure(request,timeout):raise urllib.error.HTTPError(request.full_url,429,'limit',{'Retry-After':'300'},io.BytesIO(b''))
 c=Client(engine,'wb','one',{},request=failure,sleep=lambda _:None)
 with pytest.raises(Paused):c.get('/api/v3/orders/new')
 class Response:
  def __enter__(self):return io.BytesIO(b'{"orders":[]}')
  def __exit__(self,*a):pass
 other=Client(engine,'wb','two',{},request=lambda *a,**kw:Response(),sleep=lambda _:None)
 assert other.get('/api/v3/orders/new')=={'orders':[]}
 assert sync_state(engine)['limit:wb:one:/api/v3/orders/new']['retry_at']


def test_mapping_arriving_later_resolves_same_order(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb(qty='later')],'wb-fbs');original=market(engine).id;reconcile(engine,'own')
 with transaction(engine) as s:
  from app.db import Item
  s.get(Item,'s').barcodes=['001','later']
 ingest(engine,'wb','fbs','seller',[wb(qty='later')],'wb-fbs');reconcile(engine,'own')
 assert market(engine).id==original and balance(engine)['reserved']==1


def test_first_count_after_handover_is_not_deducted_again(setup):
 from app.db import Item
 engine,_,cmd=setup
 with transaction(engine) as s:s.add(Item(id='not-initialized',name='Новая позиция',model='Новая',kind='garment',unit='piece',barcodes=['003']))
 ingest(engine,'wb','fbs','seller',[wb(qty='003')],'wb-fbs');reconcile(engine,'own')
 assert market(engine).data['accounting_state']=='waiting_stock'
 ingest(engine,'wb','fbs','seller',[wb('complete','sold',qty='003')],'wb-fbs');reconcile(engine,'own')
 cmd('stock.adjust',dict(item_id='not-initialized',location='own',version=0,physical=5))
 reconcile(engine,'own')
 assert balance(engine,'not-initialized')['free']==5
 assert market(engine).data['accounting_state']=='covered_by_count'
 reconcile(engine,'own');assert balance(engine,'not-initialized')['free']==5


def test_existing_exact_manual_order_is_linked_without_double_reserve(setup):
 engine,_,cmd=setup
 own=cmd('order.create',dict(platform='wb',account='seller',external_id='123',location='own',lines=[dict(item_id='s',quantity=1)]))
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==9 and balance(engine)['reserved']==1
 assert market(engine).data['ledger_id']==own['id']


def test_wb_srid_cannot_duplicate_between_fbs_and_fbw(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb('complete','sold')],'wb-fbs')
 ingest(engine,'wb','fbo','seller',[dict(srid='stable-rid',date=now().isoformat(),barcode='001',isCancel=False)],'wb-statistics')
 with Session(engine) as s:assert s.scalar(select(func.count()).select_from(Entity).where(Entity.kind=='market_order'))==1
 assert market(engine).data['scheme']=='fbs'


def test_cancellation_with_unmapped_or_missing_composition_still_releases_reserve(setup):
 engine,_,_=setup
 ingest(engine,'wb','fbs','seller',[wb()],'wb-fbs');reconcile(engine,'own')
 ingest(engine,'wb','fbs','seller',[wb('cancel','canceled',qty='no-barcode')],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==10 and balance(engine)['reserved']==0
 assert market(engine).data['accounting_state']=='cancelled'


def test_fast_cycle_does_not_wait_for_historical_pages(setup,monkeypatch):
 import app.order_sync as sync
 engine,_,_=setup;paths=[]
 def get(self,path,body=None,params=None):
  paths.append(path)
  if path=='/api/v1/seller-info':return {'sid':'seller'}
  if path=='/api/v3/orders/new':return {'orders':[]}
  if path=='/v4/posting/fbs/unfulfilled/list':return {'postings':[],'has_next':False}
  raise AssertionError('Fast cycle requested historical data: '+path)
 monkeypatch.setattr(sync.Client,'get',get)
 sync.run(engine,{'WB_TOKEN':'fixture','OZON_CLIENT_ID':'1','OZON_API_KEY':'fixture'},deep=False)
 assert '/v4/posting/fbs/list' not in paths and '/api/v1/supplier/orders' not in paths


def test_order_missed_during_outage_is_counted_after_activation_only(setup):
 from app.order_feed import set_sync
 engine,_,_=setup
 set_sync(engine,'accounting-start',at=(now()-timedelta(days=1)).isoformat())
 raw={**wb('complete','sold'),'_status_verified':True}
 ingest(engine,'wb','fbs','seller',[raw],'wb-fbs');reconcile(engine,'own')
 assert balance(engine)['free']==9 and balance(engine)['reserved']==0
 assert market(engine).data['accounting_state']=='shipped'
 reconcile(engine,'own');assert balance(engine)['free']==9


def test_alternative_gtin_is_linked_by_exact_wb_variant_and_scans_as_same_item(setup):
 from app.product_identity import refresh_wb_mappings
 engine,_,cmd=setup
 catalog=dict(at=now().isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001','04670249981666'])])])
 result=refresh_wb_mappings(engine,catalog);assert result['counts']['unique']==1
 ingest(engine,'wb','fbo','seller',[dict(srid='gtin-order',date=now().isoformat(),barcode='04670249981666',isCancel=False)],'wb-statistics')
 assert market(engine).data['lines'][0]['item_id']=='s' and not market(engine).data['needs_mapping']
 c=cmd('count.create',dict(location='own',mode='scanned',barcode='04670249981666'))
 with Session(engine) as s:assert s.get(Entity,c['id']).data['items']['s']['count']=='1'
 from app.sales import mappings
 codes,_,covered=mappings(engine)
 assert codes['04670249981666']==['s'] and codes['001']==['s'] and covered['wb']=={'s'}


def test_conflicting_marketplace_barcode_is_never_guessed(setup):
 from app.product_identity import refresh_wb_mappings,barcode_matches
 engine,_,_=setup
 refresh_wb_mappings(engine,dict(at=now().isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001','shared']),dict(chrtID=11,skus=['002','shared'])])]))
 with Session(engine) as s:
  rows=list(s.scalars(select(Entity).where(Entity.kind=='mapping')))
  assert all(r.data['state']=='ambiguous' for r in rows)
  assert not barcode_matches(s,'shared')


def test_owner_can_bind_unmapped_identity_once_without_moving_stock(setup):
 engine,_,cmd=setup
 ingest(engine,'wb','fbo','seller',[dict(srid='a',date=now().isoformat(),barcode='new-alias',isCancel=False),dict(srid='b',date=now().isoformat(),barcode='new-alias',isCancel=False)],'wb-statistics')
 e=market(engine);before=count(engine,Movement);op=str(uuid4());payload=dict(id=e.id,version=e.version,line=0,item_id='s')
 result=cmd('market.order.map',payload,op);assert result['changed_orders']==2
 assert cmd('market.order.map',payload,op)==result
 assert count(engine,Movement)==before
 with Session(engine) as s:assert not any(r.data['needs_mapping'] for r in s.scalars(select(Entity).where(Entity.kind=='market_order')))
 ingest(engine,'wb','fbo','seller',[dict(srid='c',date=now().isoformat(),barcode='new-alias',isCancel=False)],'wb-statistics')
 with Session(engine) as s:
  e=s.get(Entity,stable_id('wb','seller','fbo','c'));assert e.data['lines'][0]['item_id']=='s'


def test_manual_ozon_identity_is_also_used_for_normalized_sales(setup):
 from app.sales import catalog_links
 engine,_,cmd=setup
 ingest(engine,'ozon','fbs','seller',[dict(posting_number='manual',status='delivered',in_process_at=now().isoformat(),products=[dict(sku=999,quantity=1,offer_id='external')])],'ozon-fbs')
 e=market(engine);cmd('market.order.map',dict(id=e.id,version=e.version,line=0,item_id='m'))
 skus,covered,absent,proof=catalog_links(engine,dict(at=now().isoformat(),complete=True,items=[dict(id=10,barcodes=['unknown'],sources=[{'sku':999}])]))
 assert skus['999']==['m'] and 'm' in covered and 'm' not in absent


def test_saved_sales_make_a_recommendation_while_the_report_is_still_limited(setup):
    from app.cutting import calculate
    from app.db import Balance
    from app.order_sync import Paused, sync_wb_sales
    from app.product_identity import refresh_wb_mappings
    from app.sales import MSK, store_window
    engine,_,_=setup
    refresh_wb_mappings(engine,dict(at=now().isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001']),dict(chrtID=11,skus=['002'])])]))
    day=str(now().astimezone(MSK).date()-timedelta(days=1))
    with transaction(engine) as s:
        s.add(Entity(id='market-sale-raw:saved',kind='market_sale_raw',version=1,data=dict(account='seller',row=dict(saleID='S-saved',date=day+'T12:00:00',lastChangeDate=day+'T12:00:00',barcode='001',srid='saved'))))
        for item in ('s','m'):
            balance=s.scalar(select(Balance).where(Balance.item_id==item,Balance.location=='own'))
            balance.known=False
        s.add(Entity(id='order-sync:wb-sales',kind='order_sync',version=1,data=dict(rebuild_required=True,state='waiting')))
    class Limited:
        def get(self,*args,**kwargs):raise Paused('Площадка ограничила частоту запросов','2099-01-01T00:00:00+00:00')
    with pytest.raises(Paused):sync_wb_sales(engine,Limited(),'seller',{},now())
    with Session(engine) as s:
        window=s.scalar(select(Entity).where(Entity.kind=='sales_window',Entity.data['platform'].as_string()=='wb',Entity.data['collection_proof'].as_string()=='saved_rows'))
        store_window(engine,dict(platform='ozon',start=window.data['start'],end=window.data['end'],at=now().isoformat(),complete=True,verified=True,covered_items=['s','m'],events=[]))
    with Session(engine) as s:
        rows={r['item_id']:r for r in calculate(s,dict(location='own',item_ids=['s','m']))['rows']}
    assert rows['s']['platforms']['wb']['known'] and rows['s']['platforms']['wb']['sales']==1
    assert rows['s']['stock_assumed_zero'] and rows['s']['recommendation'] is None and rows['s']['target']==2
    assert rows['m']['platforms']['wb']['sales']==0 and rows['m']['stock_assumed_zero'] and rows['m']['recommendation'] is None


def test_raw_sales_can_be_rebuilt_after_alias_change_without_an_api_call(setup):
 from app.product_identity import refresh_wb_mappings
 from app.order_sync import sync_wb_sales
 from app.order_feed import sync_state
 from app.cutting import calculate
 from app.sales import MSK
 engine,_,_=setup
 refresh_wb_mappings(engine,dict(at=now().isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001'])])]))
 day=str(now().astimezone(MSK).date()-timedelta(days=1))
 class First:
  def get(self,*a,**kw):return [] if 'T' in kw['params']['dateFrom'] else [dict(saleID='S-source',date=day+'T12:00:00',lastChangeDate=day+'T12:00:00',barcode='alternative',srid='purchase')]
 sync_wb_sales(engine,First(),'seller',{},now())
 refresh_wb_mappings(engine,dict(at=now().isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001','alternative'])])]))
 with Session(engine) as s:
  window=s.scalar(select(Entity).where(Entity.kind=='sales_window'));assert 's' in window.data['identity_pending']
 class NoNetwork:
  def get(self,*a,**kw):pytest.fail('An alias rebuild must use the complete raw snapshot')
 sync_wb_sales(engine,NoNetwork(),'seller',sync_state(engine)['wb-sales'],now())
 with Session(engine) as s:
  window=s.scalar(select(Entity).where(Entity.kind=='sales_window'));assert sum(e['quantity'] for e in window.data['events'])==1
  assert 's' in window.data['covered_items'] and not window.data.get('identity_pending')


def test_sales_cooldown_is_shared_with_the_existing_sales_worker(setup):
 from app.order_sync import Client,Paused,update_wb_status
 engine,_,_=setup
 update_wb_status(engine,next_retry_at=(now()+timedelta(hours=1)).isoformat())
 client=Client(engine,'wb','seller',{},request=lambda *a,**kw:pytest.fail('Shared cooldown must block network'))
 with pytest.raises(Paused):client.get('/api/v1/supplier/sales',params={'dateFrom':'2026-11-01'})


def test_order_sales_reader_respects_existing_report_lock(setup,tmp_path,monkeypatch):
 import fcntl
 from app.order_sync import sync_wb_sales,Paused
 engine,_,_=setup;monkeypatch.setenv('KROY_APP_ROOT',str(tmp_path))
 with (tmp_path/'sales-reader.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with pytest.raises(Paused,match='другой читатель'):sync_wb_sales(engine,None,'seller',{},now())


def test_report_spacing_blocks_locally_without_network(setup):
 from app.order_feed import set_sync
 from app.order_sync import Paused
 engine,_,_=setup
 for path,elapsed in [('/api/v1/supplier/sales',7201),('/api/v1/supplier/orders',10801)]:
  set_sync(engine,'limit:wb:seller:'+path,last_request=(now()-timedelta(seconds=elapsed)).isoformat())
  c=Client(engine,'wb','seller',{},request=lambda *_a,**_k:pytest.fail('Unexpected network request'),sleep=lambda _:pytest.fail('Long blocking sleep'))
  with pytest.raises(Paused,match='расписанию'):c.get(path)


def test_complete_sales_snapshot_is_reused_for_multiple_calculations(setup):
 from app.order_sync import sync_wb_sales
 from app.order_feed import sync_state
 engine,_,_=setup;day=str(now().date()-timedelta(days=1));calls=[]
 class Source:
  def get(self,path,params):
   calls.append(params['dateFrom']);return [] if 'T' in params['dateFrom'] else [dict(saleID='S-a',date=day+'T12:00:00',lastChangeDate=day+'T12:00:00',barcode='001',srid='a')]
 sync_wb_sales(engine,Source(),'seller',{},now())
 sync_wb_sales(engine,Source(),'seller',sync_state(engine)['wb-sales'],now())
 assert len(calls)==2


def test_report_checkpoint_survives_pause_without_rereading_first_page(setup):
 from app.order_sync import wb_statistics,Paused
 from app.order_feed import sync_state
 engine,_,_=setup;pages=[];calls=[]
 class Source:
  def __init__(self):self.engine=engine
  def get(self,path,params):
   calls.append(params['dateFrom'])
   if len(calls)==1:return [dict(srid='a',lastChangeDate='2026-10-01T10:00:00')]
   if len(calls)==2:raise Paused('Wait',(now()+timedelta(hours=3)).isoformat())
   return []
 c=Source()
 with pytest.raises(Paused):wb_statistics(c,'2026-09-01',page_size=1,on_page=lambda p:pages.extend(p),checkpoint='test-report')
 assert sync_state(engine)['test-report']['pending']
 wb_statistics(c,'2026-09-01',page_size=1,on_page=lambda p:pages.extend(p),checkpoint='test-report')
 assert calls==['2026-09-01','2026-10-01T10:00:00','2026-10-01T10:00:00'] and len(pages)==1
 assert not sync_state(engine)['test-report']['pending']


def test_bereza_status_grouping_is_consistent_with_filter(setup):
 engine,_,_=setup
 rows=[dict(posting_number='group-new',status='awaiting_packaging',in_process_at=now().isoformat(),products=[dict(sku=999,quantity=1)]),dict(posting_number='group-ship',status='arbitration',in_process_at=now().isoformat(),products=[dict(sku=999,quantity=1)])]
 ingest(engine,'ozon','fbs','seller',rows,'ozon-fbs')
 with Session(engine) as s:
  new=list_orders(s,status='new')['rows'];shipped=list_orders(s,status='shipping')['rows']
  assert [(x['external_id'],x['display_status']) for x in new]==[('group-new','new')]
  assert [(x['external_id'],x['display_status']) for x in shipped]==[('group-ship','shipping')]


def test_429_uses_retry_deadline_without_counting_rejected_request_as_success(setup,monkeypatch):
 from datetime import datetime,timezone
 import io
 import app.order_sync as sync
 from app.order_feed import set_sync,sync_state
 engine,_,_=setup;clock=[datetime(2026,11,5,11,16,tzinfo=timezone.utc)];monkeypatch.setattr(sync,'now',lambda:clock[0])
 path='/api/v1/supplier/sales';key='limit:wb:seller:'+path
 rejected=(clock[0]-timedelta(minutes=20)).isoformat();deadline=(clock[0]+timedelta(minutes=13)).isoformat()
 set_sync(engine,key,last_request=rejected,retry_at=deadline,last_outcome='rate_limited')
 set_sync(engine,'wb-sales',state='waiting')
 with transaction(engine) as s:s.add(Entity(id='cutting-sales-status',kind='sales_status',version=1,data={'platforms':{'wb':{'next_retry_at':deadline}}}))
 calls=[]
 def fetch(*_a,**_k):calls.append(1);return io.BytesIO(b'[]')
 client=sync.Client(engine,'wb','seller',{},request=fetch,sleep=lambda _:None)
 with pytest.raises(sync.Paused) as paused:client.get(path)
 assert paused.value.retry_at==(clock[0]+timedelta(minutes=18)).isoformat() and not calls
 # The shared status can contain the computed deadline; it must not gain another five minutes on every check.
 with transaction(engine) as s:s.get(Entity,'cutting-sales-status').data={'platforms':{'wb':{'next_retry_at':paused.value.retry_at,'retry_buffered':True}}}
 with pytest.raises(sync.Paused) as repeated:client.get(path)
 assert repeated.value.retry_at==paused.value.retry_at
 clock[0]+=timedelta(minutes=18,seconds=1)
 assert client.get(path)==[] and len(calls)==1
 assert sync_state(engine)[key]['last_outcome']=='success'


def test_reports_resume_only_after_their_due_time_without_current_order_reads(setup,monkeypatch):
 import app.order_sync as sync
 from app.order_feed import set_sync
 engine,_,_=setup;now_at=now();state={'wb-sales':{'state':'waiting','retry_at':(now_at+timedelta(seconds=30)).isoformat()}}
 assert not sync.reports_due(state,now_at) and sync.reports_due(state,now_at+timedelta(seconds=31))
 assert not sync.reports_due({'wb-sales':{'state':'error','retry_at':now_at.isoformat()}},now_at)
 paths=[];set_sync(engine,'wb-identity',account='seller',token_fingerprint=sync.hashlib_account('fixture'))
 def get(self,path,body=None,params=None):
  paths.append(path)
  if path=='/api/v1/supplier/orders':return []
  if path=='/api/v1/supplier/sales':return []
  raise AssertionError('Unexpected current-order request '+path)
 monkeypatch.setattr(sync.Client,'get',get)
 sync.run(engine,{'WB_TOKEN':'fixture','OZON_CLIENT_ID':'1','OZON_API_KEY':'fixture'},deep=False,reports=True,current=False)
 assert set(paths)=={'/api/v1/supplier/orders','/api/v1/supplier/sales'}


def test_short_statistics_page_is_not_treated_as_the_end(setup):
 from app.order_sync import wb_statistics
 engine,_,_=setup;received=[]
 class Source:
  def __init__(self):self.engine=engine;self.calls=0
  def get(self,path,params):
   self.calls+=1
   if self.calls==1:return [dict(srid='first',lastChangeDate='2026-10-01T10:00:00')]
   if self.calls==2:return [dict(srid='second',lastChangeDate='2026-10-01T11:00:00')]
   return []
 source=Source();rows=wb_statistics(source,'2026-09-01',on_page=lambda p:received.extend(p),checkpoint='short-page')
 assert source.calls==3 and {r['srid'] for r in rows}=={'first','second'} and len(received)==2


def test_statistics_finishes_at_fixed_watermark_even_if_new_events_keep_arriving(setup,monkeypatch):
 import app.order_sync as sync
 from app.order_feed import sync_state
 from datetime import datetime,timezone
 engine,_,_=setup;instant=datetime(2026,11,5,11,0,tzinfo=timezone.utc);monkeypatch.setattr(sync,'now',lambda:instant)
 class Source:
  def __init__(self):self.engine=engine;self.calls=0
  def get(self,path,params):
   self.calls+=1
   if self.calls==1:return [dict(srid='old',lastChangeDate='2026-11-05T13:59:00')]
   if self.calls==2:return [dict(srid='new',lastChangeDate='2026-11-05T14:01:00')]
   pytest.fail('Should stop once the fixed watermark is passed')
 source=Source();rows=sync.wb_statistics(source,'2026-11-01',checkpoint='watermark')
 assert len(rows)==2 and source.calls==2 and sync_state(engine)['watermark']['proof']=='cutoff'


def test_paginated_wb_sales_resume_after_cooldown_and_feed_recommendation(setup,monkeypatch):
 import io,json
 import app.order_sync as sync
 from app.product_identity import refresh_wb_mappings
 from app.sales import store_window,MSK
 from app.cutting import calculate
 engine,_,_=setup;clock=[now()];monkeypatch.setattr(sync,'now',lambda:clock[0])
 refresh_wb_mappings(engine,dict(at=clock[0].isoformat(),cards=[dict(id=1,sizes=[dict(chrtID=10,skus=['001'])])]))
 day=str(clock[0].astimezone(MSK).date()-timedelta(days=1));calls=[]
 def fetch(req,timeout):
  calls.append(1);changed=(clock[0].astimezone(MSK)-timedelta(minutes=1) if len(calls)==1 else clock[0].astimezone(MSK)).replace(tzinfo=None).isoformat()
  return io.BytesIO(json.dumps([dict(saleID='S-'+str(len(calls)),date=day+'T12:00:00',lastChangeDate=changed,barcode='001',srid='r'+str(len(calls)))]).encode())
 client=sync.Client(engine,'wb','seller',{},request=fetch,sleep=lambda _:None)
 with pytest.raises(sync.Paused):sync.sync_wb_sales(engine,client,'seller',{},clock[0])
 assert len(calls)==1
 with Session(engine) as s:assert not list(s.scalars(select(Entity).where(Entity.kind=='sales_window')))
 cutoff=clock[0].isoformat();clock[0]+=timedelta(seconds=7501)
 sync.sync_wb_sales(engine,client,'seller',{},clock[0]);assert len(calls)==2
 with Session(engine) as s:
  window=s.scalar(select(Entity).where(Entity.kind=='sales_window'));assert window.data['collection_proof']=='cutoff' and window.data['at']==cutoff
  params=calculate(s,dict(location='own',item_ids=['s']))['parameters']
 store_window(engine,dict(platform='ozon',start=params['start'],end=params['end'],at=cutoff,complete=True,verified=True,covered_items=['s'],events=[]))
 from app.db import Balance
 with transaction(engine) as s:s.scalar(select(Balance).where(Balance.item_id=='s',Balance.location=='own')).known=False
 with Session(engine) as s:
  row=calculate(s,dict(location='own',item_ids=['s']))['rows'][0];assert row['net']==2 and row['stock_assumed_zero'] and row['recommendation'] is None and row['target']==4


def test_wb_retry_without_headers_uses_conservative_method_limit(setup):
 import urllib.error
 from app.order_sync import Client,Paused
 engine,_,_=setup
 def blocked(req,timeout):raise urllib.error.HTTPError(req.full_url,429,'limited',{},None)
 for path,minimum in [('/api/v1/supplier/sales',7500),('/api/v1/supplier/orders',11100)]:
  with pytest.raises(Paused) as caught:Client(engine,'wb','seller',{},request=blocked).get(path)
  from datetime import datetime
  assert (datetime.fromisoformat(caught.value.retry_at)-now()).total_seconds()>=minimum-2


def test_wb_sales_batches_are_idempotent_and_keep_newest_revision(setup):
 from app.order_sync import upsert_sales_rows
 engine,_,_=setup
 rows=[('raw:'+str(i),{'row':{'lastChangeDate':'2026-11-05T12:00:00','value':i}}) for i in range(1001)]
 upsert_sales_rows(engine,rows,'market_sale_raw',newest=True)
 upsert_sales_rows(engine,[('raw:0',{'row':{'lastChangeDate':'2026-11-06T12:00:00','value':'new'}}),('raw:0',{'row':{'lastChangeDate':'2026-11-04T12:00:00','value':'old'}})],'market_sale_raw',newest=True)
 upsert_sales_rows(engine,rows,'market_sale_raw',newest=True)
 with Session(engine) as s:
  assert s.scalar(select(func.count()).select_from(Entity).where(Entity.kind=='market_sale_raw'))==1001
  assert s.get(Entity,'raw:0').data['row']['value']=='new'
