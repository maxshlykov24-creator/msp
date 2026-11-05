"""Credentialed, strictly read-only marketplace collector with resumable history."""
import json
import os
import time
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from sqlalchemy import select
from .db import Entity, now, transaction
from .order_feed import ingest,ingest_returns,sync_state,set_sync,timestamp,PENDING

ALLOWED={
 'wb':{'/api/v3/orders/new':'GET','/api/v3/orders':'GET','/api/v3/orders/status':'POST','/api/v1/supplier/orders':'GET'},
 'ozon':{'/v4/posting/fbs/list':'POST','/v4/posting/fbs/unfulfilled/list':'POST','/v3/posting/fbo/list':'POST','/v3/posting/fbs/get':'POST','/v2/posting/fbo/get':'POST','/v1/returns/list':'POST'}}

class FeedFailure(RuntimeError):pass
class Paused(FeedFailure):pass


def retry_seconds(headers,at=None):
    at=at or now();values=[]
    for k in ('Retry-After','X-Ratelimit-Retry','X-Ratelimit-Reset'):
        raw=headers.get(k)
        if not raw:continue
        try:
            v=float(raw)
            if v>1_000_000_000:v-=at.timestamp()
        except ValueError:
            try:v=(parsedate_to_datetime(raw)-at).total_seconds()
            except Exception:continue
        if v>0:values.append(v)
    return max(values or [180])


class Client:
    def __init__(self,engine,platform,account,headers,request=None,sleep=time.sleep):
        self.engine,self.platform,self.account,self.headers=engine,platform,account,headers;self.request=request or urllib.request.urlopen;self.sleep=sleep
    def get(self,path,body=None,params=None):
        method=ALLOWED[self.platform].get(path)
        if not method:raise FeedFailure('Метод не входит в список разрешённого чтения')
        key='limit:'+self.platform+':'+self.account+':'+path
        state=sync_state(self.engine).get(key,{})
        retry=state.get('retry_at')
        if retry and datetime.fromisoformat(retry)>now():raise Paused('Пауза площадки до '+retry)
        last=state.get('last_request');spacing=61 if path=='/api/v1/supplier/orders' else 3.1 if self.platform=='wb' else 2.1
        delay=max(0,spacing-(now()-datetime.fromisoformat(last)).total_seconds()) if last else 0
        if delay:self.sleep(delay)
        host='https://api-seller.ozon.ru' if self.platform=='ozon' else 'https://statistics-api.wildberries.ru' if path=='/api/v1/supplier/orders' else 'https://marketplace-api.wildberries.ru'
        req=urllib.request.Request(host+path+('?' + urlencode(params) if params else ''),data=json.dumps(body or {}).encode() if method=='POST' else None,headers={**self.headers,'Content-Type':'application/json'},method=method)
        for attempt in range(3):
            set_sync(self.engine,key,last_request=now().isoformat())
            try:
                with self.request(req,timeout=40) as response:return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code==429:
                    until=(now()+timedelta(seconds=retry_seconds(e.headers))).isoformat();set_sync(self.engine,key,retry_at=until);raise Paused('Ограничение площадки; повтор после '+until) from None
                if e.code not in (500,502,503,504) or attempt==2:raise FeedFailure('HTTP '+str(e.code)+' при чтении '+path) from None
            except (urllib.error.URLError,TimeoutError,OSError):
                if attempt==2:raise FeedFailure('Нет связи с площадкой при чтении '+path) from None
            self.sleep(2**attempt)
        raise FeedFailure('Чтение не завершено')


def postings(client,scheme,start,end,open_only=False,on_page=None):
    path='/v4/posting/fbs/unfulfilled/list' if open_only else '/v4/posting/fbs/list' if scheme=='fbs' else '/v3/posting/fbo/list'
    rows=[];seen=set();cursors={''};cursor=''
    for _ in range(10000):
        filters={'cutoff_from':start,'cutoff_to':end} if open_only else {'since':start,'to':end}
        data=client.get(path,{'dir':'ASC','filter':filters,'limit':100,'cursor':cursor,'with':{'analytics_data':False,'financial_data':False}})
        data=data.get('result',data)
        batch=data.get('postings');more=data.get('has_next')
        if not isinstance(batch,list) or type(more) is not bool:raise FeedFailure('Неполный ответ отправлений '+scheme)
        for row in batch:
            key=row.get('posting_number')
            if not key or key in seen:raise FeedFailure('Повтор или отсутствие номера отправления')
            seen.add(key)
        if on_page:on_page(batch)
        rows.extend(batch)
        if not more:return rows
        nxt=data.get('cursor')
        if not batch or not isinstance(nxt,str) or not nxt or nxt in cursors:raise FeedFailure('Не продвинулся курсор отправлений')
        cursors.add(nxt);cursor=nxt
    raise FeedFailure('Лимит страниц отправлений')


def wb_history(client,start,on_page=None):
    rows=[];seen=set();cursors={0};cursor=0
    for _ in range(10000):
        data=client.get('/api/v3/orders',params={'limit':1000,'next':cursor,'dateFrom':int(start.timestamp())})
        batch=data.get('orders')
        if not isinstance(batch,list) or type(data.get('next')) is not int:raise FeedFailure('Неполная история ВБ')
        for row in batch:
            key=row.get('id')
            if not key or key in seen:raise FeedFailure('Повтор номера сборочного задания ВБ')
            seen.add(key)
        if on_page:on_page(batch)
        rows.extend(batch);nxt=data['next']
        if not batch or not nxt:return rows
        if nxt in cursors:raise FeedFailure('Повтор курсора ВБ')
        cursors.add(nxt);cursor=nxt
    raise FeedFailure('Лимит страниц ВБ')


def wb_statuses(client,ids):
    out={};ids=list(dict.fromkeys(ids))
    for offset in range(0,len(ids),1000):
        batch=ids[offset:offset+1000];data=client.get('/api/v3/orders/status',{'orders':batch}).get('orders')
        if not isinstance(data,list) or len(data)!=len(batch) or {r.get('id') for r in data}!=set(batch):raise FeedFailure('Статусы ВБ не покрывают запрошенные задания')
        out.update({r['id']:r for r in data})
    return out


def wb_statistics(client,start):
    rows=[];seen={};cursor=start
    for _ in range(1000):
        batch=client.get('/api/v1/supplier/orders',params={'dateFrom':cursor,'flag':0})
        if not isinstance(batch,list):raise FeedFailure('Неполный отчёт заказов ВБ')
        for r in batch:
            key=r.get('srid')
            if not key:raise FeedFailure('Нет srid в статистике ВБ')
            # Boundary repeats are allowed; retain the latest source revision.
            if key not in seen or str(r.get('lastChangeDate'))>str(seen[key].get('lastChangeDate')):seen[key]=r
        if len(batch)<80000:return list(seen.values())
        nxt=batch[-1].get('lastChangeDate')
        if not nxt or nxt<=cursor:raise FeedFailure('Не продвинулся курсор статистики ВБ')
        cursor=nxt
    raise FeedFailure('Лимит страниц статистики ВБ')


def ozon_returns(client):
    rows=[];seen=set();cursor=0
    for _ in range(10000):
        d=client.get('/v1/returns/list',{'filter':{},'limit':500,'last_id':cursor});batch=d.get('returns');more=d.get('has_next')
        if not isinstance(batch,list) or type(more) is not bool:raise FeedFailure('Неполный список возвратов Ozon')
        for r in batch:
            if not r.get('id') or r['id'] in seen:raise FeedFailure('Повтор или отсутствие номера возврата')
            seen.add(r['id'])
        rows.extend(batch)
        if not more:return rows
        nxt=batch[-1]['id'] if batch else None
        if not nxt or nxt==cursor:raise FeedFailure('Не продвинулся курсор возвратов')
        cursor=nxt
    raise FeedFailure('Лимит страниц возвратов')


def run(engine,env,history_steps=1):
    at=now();state=sync_state(engine);result={}
    accounts={'wb':hashlib_account(env['WB_TOKEN']),'ozon':str(env['OZON_CLIENT_ID'])}
    clients={'wb':Client(engine,'wb',accounts['wb'],{'Authorization':env['WB_TOKEN']}),'ozon':Client(engine,'ozon',accounts['ozon'],{'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']})}
    def phase(key,work):
        set_sync(engine,key,state='running',attempt_at=now().isoformat())
        try:
            value=work();set_sync(engine,key,state='ready',success_at=now().isoformat(),error=None);return value
        except Exception as e:
            message=str(e) if isinstance(e,FeedFailure) else 'Ошибка проверки данных; предыдущая выгрузка сохранена'
            set_sync(engine,key,state='waiting' if isinstance(e,Paused) else 'error',error=message);return None
    wb=clients['wb'];account=accounts['wb']
    def current_wb():
        data=wb.get('/api/v3/orders/new');rows=data.get('orders')
        if not isinstance(rows,list):raise FeedFailure('Нет массива новых заказов ВБ')
        # Commit brand-new orders before attempting history/status endpoints.
        ingest(engine,'wb','fbs',account,[{**r,'supplierStatus':'new'} for r in rows],'wb-fbs',only_new=True)
        set_sync(engine,'wb-new',received=len(rows))
        return rows
    fresh=phase('wb-new',current_wb)
    def fbs():
        rows=wb_history(wb,at-timedelta(days=30),lambda batch:ingest(engine,'wb','fbs',account,batch,'wb-fbs',only_new=True))
        tracked=[]
        with transaction(engine) as s:
            for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='wb',Entity.data['scheme'].as_string()=='fbs')):
                d=e.data
                if d['source']=='wb-fbs' and d['account']==account and (d['status'] not in ('cancelled','returned','delivered') or d.get('created_at','')>=str(at-timedelta(days=30))):tracked.append(dict(id=int(d['external_id']),rid=d['source_ref'],createdAt=d['created_at'],skus=[l['barcode'] for l in d['lines'] if l.get('barcode')],article=d['lines'][0].get('offer_id','') if d['lines'] else ''))
        merged={r['id']:r for r in tracked};merged.update({r['id']:r for r in rows});merged.update({r['id']:r for r in fresh or []})
        statuses=wb_statuses(wb,list(merged))
        ingest(engine,'wb','fbs',account,[{**r,**statuses[r['id']]} for r in merged.values()],'wb-fbs')
        set_sync(engine,'wb-fbs',received=len(merged),history_from=(at-timedelta(days=30)).isoformat(),history_to=at.isoformat())
    phase('wb-fbs',fbs)
    def statistics():
        last=state.get('wb-statistics',{}).get('success_at');start=(at-timedelta(days=90)).isoformat() if not last else min(at-timedelta(days=7),datetime.fromisoformat(last)-timedelta(days=1)).isoformat()
        rows=wb_statistics(wb,start)
        for scheme,warehouse in [('fbo','Склад WB'),('fbs','Склад продавца')]:ingest(engine,'wb',scheme,account,[r for r in rows if r.get('warehouseType')==warehouse],'wb-statistics')
        unknown=sum(r.get('warehouseType') not in ('Склад WB','Склад продавца') for r in rows)
        if unknown:raise FeedFailure('Не определена схема '+str(unknown)+' заказов ВБ')
        set_sync(engine,'wb-statistics',received=len(rows),history_from=state.get('wb-statistics',{}).get('history_from') or start,history_to=at.isoformat(),window_from=start)
    phase('wb-statistics',statistics)
    oz=clients['ozon'];account=accounts['ozon']
    phase('ozon-open',lambda:ingest(engine,'ozon','fbs',account,postings(oz,'fbs',(at-timedelta(days=90)).isoformat(),(at+timedelta(days=30)).isoformat(),True),'ozon-fbs'))
    for scheme in ('fbs','fbo'):
        def current(scheme=scheme):
            key='ozon-'+scheme;last=state.get(key,{}).get('success_at');start=min(at-timedelta(days=31),datetime.fromisoformat(last)-timedelta(days=1)) if last else at-timedelta(days=31)
            # Never request an unbounded interval after an outage.
            cursor=start
            count=0
            while cursor<at:
                end=min(cursor+timedelta(days=31),at)
                rows=postings(oz,scheme,cursor.isoformat(),end.isoformat(),on_page=lambda batch:ingest(engine,'ozon',scheme,account,batch,'ozon-'+scheme,only_new=True))
                ingest(engine,'ozon',scheme,account,rows,'ozon-'+scheme);count+=len(rows);cursor=end
            # Independently follow older nonterminal postings by exact posting number.
            with transaction(engine) as s:
                tracked=[copy_data(e) for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='ozon',Entity.data['scheme'].as_string()==scheme)) if e.data['account']==account and e.data.get('created_at') and e.data['created_at']<start.isoformat() and e.data['status'] not in ('delivered','cancelled','returned')]
            for d in sorted(tracked,key=lambda d:d.get('observed_at',''))[:100]:
                data=oz.get('/v3/posting/fbs/get' if scheme=='fbs' else '/v2/posting/fbo/get',{'posting_number':d['external_id'],'with':{'analytics_data':False,'financial_data':False}})
                data=data.get('result',data)
                if data.get('posting_number')!=d['external_id']:raise FeedFailure('Не подтверждён номер старого отправления')
                ingest(engine,'ozon',scheme,account,[data],'ozon-'+scheme)
            set_sync(engine,key,received=count,window_from=start.isoformat(),history_to=at.isoformat(),tracked_old=len(tracked))
        phase('ozon-'+scheme,current)
    phase('ozon-returns',lambda:ingest_returns(engine,account,ozon_returns(oz)))
    # Backfill independently from current orders: completed windows survive failures.
    for scheme in ('fbs','fbo'):
        key='ozon-history-'+scheme
        def history(scheme=scheme,key=key):
            prior=sync_state(engine).get(key,{});cursor=datetime.fromisoformat(prior['cursor']) if prior.get('cursor') else at-timedelta(days=31)
            start=datetime.fromisoformat(prior['start']) if prior.get('start') else at-timedelta(days=365)
            for _ in range(history_steps):
                if cursor<=start:break
                since=max(start,cursor-timedelta(days=31))
                rows=postings(oz,scheme,since.isoformat(),cursor.isoformat(),on_page=lambda batch:ingest(engine,'ozon',scheme,account,batch,'ozon-'+scheme,only_new=True))
                ingest(engine,'ozon',scheme,account,rows,'ozon-'+scheme);cursor=since
                set_sync(engine,key,cursor=cursor.isoformat(),start=start.isoformat(),received=len(rows),complete=cursor<=start,history_to=at.isoformat())
            return cursor
        phase(key,history)
    set_sync(engine,'cycle',finished_at=now().isoformat())
    return {k:v for k,v in sync_state(engine).items() if not k.startswith('limit:')}


def hashlib_account(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:20]

def copy_data(e):return dict(e.data)
