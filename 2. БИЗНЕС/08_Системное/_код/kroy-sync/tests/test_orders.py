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
 ingest(engine,'ozon','fbs','seller',[{**raw,'_open_verified':True}],'ozon-fbs');reconcile(engine,'own')
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
