"""Credentialed, strictly read-only marketplace collector with resumable history."""
import json
import math
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
 'wb':{'/api/v3/orders/new':'GET','/api/v3/orders':'GET','/api/v3/orders/status':'POST','/api/v1/supplier/orders':'GET','/api/v1/supplier/sales':'GET','/api/v1/seller-info':'GET','/content/v2/get/cards/list':'POST'},
 'ozon':{'/v4/posting/fbs/list':'POST','/v4/posting/fbs/unfulfilled/list':'POST','/v3/posting/fbo/list':'POST','/v3/posting/fbs/get':'POST','/v2/posting/fbo/get':'POST','/v1/returns/list':'POST'}}

SKIP=object()

class FeedFailure(RuntimeError):pass
class Paused(FeedFailure):
    def __init__(self,message,retry_at):super().__init__(message);self.retry_at=retry_at


def retry_seconds(headers,at=None,default=180):
    at=at or now();values=[];headers=headers or {}
    for k in ('Retry-After','X-Ratelimit-Retry','X-Ratelimit-Reset'):
        raw=headers.get(k)
        if not raw:continue
        try:
            v=float(raw)
            if not math.isfinite(v):continue
            if v>1_000_000_000:v-=at.timestamp()
        except ValueError:
            try:v=(parsedate_to_datetime(raw)-at).total_seconds()
            except Exception:continue
        if v>0:values.append(v)
    return max(values or [default])


class Client:
    def __init__(self,engine,platform,account,headers,request=None,sleep=time.sleep):
        self.engine,self.platform,self.account,self.headers=engine,platform,account,headers;self.request=request or urllib.request.urlopen;self.sleep=sleep
    def get(self,path,body=None,params=None):
        method=ALLOWED[self.platform].get(path)
        if not method:raise FeedFailure('Метод не входит в список разрешённого чтения')
        key='limit:'+self.platform+':'+self.account+':'+path
        state=sync_state(self.engine).get(key,{})
        own_retry=state.get('retry_at');shared_retry=None;shared_buffered=False
        report=path in ('/api/v1/supplier/orders','/api/v1/supplier/sales')
        if path=='/api/v1/supplier/sales':
            with transaction(self.engine) as s:
                shared=s.get(Entity,'cutting-sales-status');wb_status=shared.data.get('platforms',{}).get('wb',{}) if shared else {};shared_retry=wb_status.get('next_retry_at');shared_buffered=wb_status.get('retry_buffered',False)
        spacing=11100 if path=='/api/v1/supplier/orders' else 7500 if path=='/api/v1/supplier/sales' else 3.1 if self.platform=='wb' else 2.1
        at=now();candidates=[]
        if own_retry:
            # Only the raw 429 deadline needs a margin; shared deadlines already include it.
            candidates.append((datetime.fromisoformat(own_retry)+timedelta(seconds=300 if report else 0),'Площадка ограничила частоту запросов'))
        if shared_retry:candidates.append((datetime.fromisoformat(shared_retry)+timedelta(seconds=300 if report and not shared_buffered else 0),'Площадка ограничила частоту запросов'))
        last=state.get('last_request')
        # Migrated state without outcome: a retry deadline after the last attempt
        # means that attempt was rejected by 429 and did not consume a new slot.
        rejected=bool(report and own_retry and last and last<own_retry and state.get('last_outcome') in (None,'rate_limited'))
        if last and not rejected:candidates.append((datetime.fromisoformat(last)+timedelta(seconds=spacing),'Следующее обновление по расписанию'))
        if state.get('last_success_at'):candidates.append((datetime.fromisoformat(state['last_success_at'])+timedelta(seconds=spacing),'Следующее обновление по расписанию'))
        eligible,reason=max(candidates,key=lambda x:x[0]) if candidates else (at,'')
        delay=max(0,(eligible-at).total_seconds())
        if delay>60:raise Paused(reason,eligible.isoformat())
        if delay:self.sleep(delay)
        host='https://api-seller.ozon.ru' if self.platform=='ozon' else 'https://content-api.wildberries.ru' if path=='/content/v2/get/cards/list' else 'https://common-api.wildberries.ru' if path=='/api/v1/seller-info' else 'https://statistics-api.wildberries.ru' if path in ('/api/v1/supplier/orders','/api/v1/supplier/sales') else 'https://marketplace-api.wildberries.ru'
        req=urllib.request.Request(host+path+('?' + urlencode(params) if params else ''),data=json.dumps(body or {}).encode() if method=='POST' else None,headers={**self.headers,'Content-Type':'application/json'},method=method)
        for attempt in range(3):
            set_sync(self.engine,key,last_request=now().isoformat(),last_outcome='started')
            try:
                with self.request(req,timeout=40) as response:data=json.load(response)
                set_sync(self.engine,key,last_success_at=now().isoformat(),last_outcome='success',retry_at=None)
                return data
            except urllib.error.HTTPError as e:
                if e.code==429:
                    fallback=10800 if path=='/api/v1/supplier/orders' else 7200 if path=='/api/v1/supplier/sales' else 180
                    until=(now()+timedelta(seconds=retry_seconds(e.headers,default=fallback))).isoformat();set_sync(self.engine,key,retry_at=until,last_outcome='rate_limited')
                    safe_until=(datetime.fromisoformat(until)+timedelta(seconds=300 if report else 0)).isoformat()
                    if path=='/api/v1/supplier/sales':
                        update_wb_status(self.engine,state='waiting',reason='Площадка ограничила частоту запросов',next_retry_at=safe_until,retry_buffered=True)
                    raise Paused('Площадка ограничила частоту запросов',safe_until) from None
                if e.code not in (500,502,503,504) or attempt==2 or path in ('/api/v1/supplier/orders','/api/v1/supplier/sales'):
                    message=''
                    if e.code in (400,422):
                        try:message=': '+str(json.loads(e.read()).get('message',''))[:240]
                        except Exception:pass
                    raise FeedFailure('HTTP '+str(e.code)+' при чтении '+path+message) from None
            except (urllib.error.URLError,TimeoutError,OSError):
                if attempt==2 or path in ('/api/v1/supplier/orders','/api/v1/supplier/sales'):raise FeedFailure('Нет связи с площадкой при чтении '+path) from None
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
            if open_only:row['_status_verified']=True
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


def wb_statistics(client,start,path='/api/v1/supplier/orders',identity='srid',page_size=80000,on_page=None,checkpoint=None):
    engine=getattr(client,'engine',None)
    progress=sync_state(engine).get(checkpoint,{}) if engine is not None and checkpoint else {}
    # A paginated report can span cooldowns and restarts. Persist the page before
    # advancing the cursor; never advertise a partial report as complete.
    origin=progress.get('origin',start) if progress.get('pending') and progress.get('origin','')>=start[:4]+'-01-01' else start
    rows=[];seen={};resuming=progress.get('pending') and origin==progress.get('origin')
    cursor=progress.get('cursor',origin) if resuming else origin
    cutoff=progress.get('cutoff_at') if resuming else now().isoformat()
    for _ in range(1000):
        batch=client.get(path,params={'dateFrom':cursor,'flag':0})
        if not isinstance(batch,list):raise FeedFailure('Неполный отчёт заказов ВБ')
        timestamps=[]
        for r in batch:
            key=r.get(identity);changed=r.get('lastChangeDate')
            if not key:raise FeedFailure('Нет идентификатора строки отчёта ВБ')
            if not changed:raise FeedFailure('Нет даты изменения строки отчёта ВБ')
            try:timestamps.append(timestamp(changed,True))
            except (ValueError,TypeError):raise FeedFailure('Некорректная дата изменения строки отчёта ВБ') from None
            if key not in seen or str(changed)>str(seen[key].get('lastChangeDate')):seen[key]=r
        if timestamps!=sorted(timestamps):raise FeedFailure('Нарушен порядок строк отчёта ВБ')
        if on_page:on_page(batch)
        # Stop only at an empty page or once an ordered page has passed the
        # fixed start-of-read watermark. New sales cannot prolong the import forever.
        proof='empty_page' if not batch else 'cutoff' if timestamps[-1]>cutoff else None
        if proof:
            if engine is not None and checkpoint:set_sync(engine,checkpoint,pending=False,origin=origin,cursor=None,cutoff_at=cutoff,proof=proof,completed_at=now().isoformat())
            return list(seen.values())
        nxt=batch[-1]['lastChangeDate']
        if nxt<=cursor:raise FeedFailure('Не продвинулся курсор статистики ВБ')
        cursor=nxt
        if engine is not None and checkpoint:set_sync(engine,checkpoint,pending=True,origin=origin,cursor=cursor,cutoff_at=cutoff)
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


def run(engine,env,history_steps=1,on_current=None,deep=True,reports=None,current=True):
    reports=deep if reports is None else reports
    at=now();state=sync_state(engine);result={}
    identity=state.get('wb-identity',{});fingerprint=hashlib_account(env['WB_TOKEN'])
    if identity.get('token_fingerprint')!=fingerprint or not identity.get('account'):
        try:
            info=Client(engine,'wb','identity',{'Authorization':env['WB_TOKEN']}).get('/api/v1/seller-info')
            if not isinstance(info.get('sid'),str) or not info['sid']:raise FeedFailure('Не подтверждён продавец ВБ')
            identity=dict(account=info['sid'],token_fingerprint=fingerprint,state='ready');set_sync(engine,'wb-identity',**identity)
        except Exception:
            identity={};set_sync(engine,'wb-identity',state='error',error='Не удалось подтвердить кабинет ВБ')
    accounts={'wb':identity.get('account'),'ozon':str(env['OZON_CLIENT_ID'])}
    clients={'wb':Client(engine,'wb',accounts['wb'],{'Authorization':env['WB_TOKEN']}),'ozon':Client(engine,'ozon',accounts['ozon'],{'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']})}
    def phase(key,work):
        set_sync(engine,key,state='running',attempt_at=now().isoformat())
        try:
            if key.startswith('wb-') and not accounts['wb']:raise FeedFailure('Сначала нужно подтвердить кабинет ВБ')
            value=work()
            if value is SKIP:
                set_sync(engine,key,state='ready',error=None,retry_at=None)
                if key=='wb-sales':update_wb_status(engine,state='ready_cached',reason='Оперативные продажи Wildberries; итоговый отчёт не сверён',next_retry_at=None)
                return value
            set_sync(engine,key,state='ready',success_at=now().isoformat(),error=None,retry_at=None)
            if on_current and key in ('wb-new','wb-fbs','ozon-open','ozon-fbs'):on_current()
            return value
        except Exception as e:
            message=str(e) if isinstance(e,FeedFailure) else 'Ошибка проверки данных; предыдущая выгрузка сохранена'
            set_sync(engine,key,state='waiting' if isinstance(e,Paused) else 'error',error=message,retry_at=e.retry_at if isinstance(e,Paused) else None)
            if key=='wb-sales' and isinstance(e,Paused):update_wb_status(engine,state='waiting',reason=message,next_retry_at=e.retry_at,retry_buffered=True)
            return None
    wb=clients['wb'];account=accounts['wb']
    def current_wb():
        data=wb.get('/api/v3/orders/new');rows=data.get('orders')
        if not isinstance(rows,list):raise FeedFailure('Нет массива новых заказов ВБ')
        # Commit brand-new orders before attempting history/status endpoints.
        ingest(engine,'wb','fbs',account,[{**r,'supplierStatus':'new'} for r in rows],'wb-fbs',only_new=True)
        set_sync(engine,'wb-new',received=len(rows))
        return rows
    def catalog_wb():
        prior=state.get('wb-catalog',{})
        if prior.get('success_at') and (at-datetime.fromisoformat(prior['success_at'])).total_seconds()<86400:return SKIP
        from .product_identity import refresh_wb_mappings
        cards=[];seen=set();cursors=set();cursor={'limit':100}
        for _ in range(1000):
            data=wb.get('/content/v2/get/cards/list',{'settings':{'cursor':cursor,'filter':{'withPhoto':-1}}});batch=data.get('cards')
            if not isinstance(batch,list):raise FeedFailure('Неполный каталог ВБ')
            for c in batch:
                if not c.get('nmID') or c['nmID'] in seen:raise FeedFailure('Повтор карточки ВБ')
                seen.add(c['nmID']);cards.append(dict(id=c['nmID'],sizes=[dict(chrtID=r['chrtID'],skus=r.get('skus',[])) for r in c.get('sizes',[])]))
            if len(batch)<100:break
            nxt=data.get('cursor') or {};key=(nxt.get('updatedAt'),nxt.get('nmID'))
            if not all(key) or key in cursors:raise FeedFailure('Не продвинулся курсор карточек ВБ')
            cursors.add(key);cursor={'limit':100,'updatedAt':key[0],'nmID':key[1]}
        else:raise FeedFailure('Лимит страниц карточек ВБ')
        result=refresh_wb_mappings(engine,dict(at=now().isoformat(),cards=cards));set_sync(engine,'wb-catalog',**result)
    fresh=phase('wb-new',current_wb) if current else []
    oz=clients['ozon'];oz_account=accounts['ozon']
    def open_ozon():
        rows=postings(oz,'fbs',(at-timedelta(days=90)).isoformat(),(at+timedelta(days=30)).isoformat(),True)
        ingest(engine,'ozon','fbs',oz_account,rows,'ozon-fbs')
        ids={r['posting_number'] for r in rows}
        with transaction(engine) as s:
            missing=[dict(e.data) for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='ozon',Entity.data['scheme'].as_string()=='fbs')) if e.data['account']==oz_account and e.data['status'] in PENDING and e.data['external_id'] not in ids]
        for d in sorted(missing,key=lambda d:d.get('observed_at',''))[:100]:
            row=oz.get('/v3/posting/fbs/get',{'posting_number':d['external_id'],'with':{'analytics_data':False,'financial_data':False}});row=row.get('result',row)
            if row.get('posting_number')!=d['external_id']:raise FeedFailure('Не подтверждён номер открытого отправления')
            ingest(engine,'ozon','fbs',oz_account,[{**row,'_status_verified':True}],'ozon-fbs')
        set_sync(engine,'ozon-open',received=len(rows),tracked_missing=len(missing))
    if current:phase('ozon-open',open_ozon)
    def fbs():
        prior=state.get('wb-fbs',{});history_due=deep and (not prior.get('history_refreshed_at') or (at-datetime.fromisoformat(prior['history_refreshed_at'])).total_seconds()>=86400)
        rows=wb_history(wb,at-timedelta(days=30),lambda batch:ingest(engine,'wb','fbs',account,batch,'wb-fbs',only_new=True)) if history_due else []
        if history_due:set_sync(engine,'wb-fbs',history_refreshed_at=at.isoformat())
        tracked=[]
        with transaction(engine) as s:
            for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='wb',Entity.data['scheme'].as_string()=='fbs')):
                d=e.data
                if d['source']=='wb-fbs' and d['account']==account and d['status'] not in ('cancelled','returned','delivered'):tracked.append(dict(id=int(d['external_id']),chrtId=d['lines'][0].get('sku') if d['lines'] else None,rid=d['source_ref'],createdAt=d['created_at'],skus=[l['barcode'] for l in d['lines'] if l.get('barcode')],article=d['lines'][0].get('offer_id','') if d['lines'] else ''))
        merged={r['id']:r for r in tracked};merged.update({r['id']:r for r in rows});merged.update({r['id']:r for r in fresh or []})
        statuses=wb_statuses(wb,list(merged))
        ingest(engine,'wb','fbs',account,[{**r,**statuses[r['id']],'_status_verified':True} for r in merged.values()],'wb-fbs')
        set_sync(engine,'wb-fbs',received=len(merged),history_from=(at-timedelta(days=30)).isoformat() if deep else state.get('wb-fbs',{}).get('history_from'),history_to=at.isoformat() if deep else state.get('wb-fbs',{}).get('history_to'))
    if deep:phase('wb-catalog',catalog_wb)
    if current:phase('wb-fbs',fbs)
    def statistics():
        prior=state.get('wb-statistics',{});last=prior.get('success_at');year=max(at.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).date().replace(month=1,day=1).isoformat(),(at-timedelta(days=89)).date().isoformat())
        if last and prior.get('history_from','9999')<=year and (at-datetime.fromisoformat(last)).total_seconds()<86400:return SKIP
        start=year if prior.get('history_from','9999')>year or not last else max(year,(datetime.fromisoformat(last)-timedelta(days=1)).date().isoformat())
        def save_page(batch):
            unknown=sum(r.get('warehouseType') not in ('Склад WB','Склад продавца') for r in batch)
            if unknown:raise FeedFailure('Не определена схема '+str(unknown)+' заказов ВБ')
            for scheme,warehouse in [('fbo','Склад WB'),('fbs','Склад продавца')]:ingest(engine,'wb',scheme,account,[r for r in batch if r.get('warehouseType')==warehouse],'wb-statistics')
        rows=wb_statistics(wb,start,on_page=save_page,checkpoint='report:wb-orders:'+account)
        for scheme,warehouse in [('fbo','Склад WB'),('fbs','Склад продавца')]:ingest(engine,'wb',scheme,account,[r for r in rows if r.get('warehouseType')==warehouse],'wb-statistics')
        unknown=sum(r.get('warehouseType') not in ('Склад WB','Склад продавца') for r in rows)
        if unknown:raise FeedFailure('Не определена схема '+str(unknown)+' заказов ВБ')
        set_sync(engine,'wb-statistics',received=len(rows),history_from=year,bootstrap_partial=False,history_to=sync_state(engine).get('report:wb-orders:'+account,{}).get('cutoff_at',at.isoformat()),window_from=start)
    if reports:phase('wb-statistics',statistics)
    if reports:phase('wb-sales',lambda:sync_wb_sales(engine,wb,account,state.get('wb-sales',{}),at))
    account=oz_account
    if deep:
        for scheme in ('fbs','fbo'):
            def current(scheme=scheme):
                key='ozon-'+scheme;last=state.get(key,{}).get('success_at');start=min(at-timedelta(days=31),datetime.fromisoformat(last)-timedelta(days=1)) if last else at-timedelta(days=31)
                # Never request an unbounded interval after an outage.
                cursor=start
                count=0
                while cursor<at:
                    end=min(cursor+timedelta(days=31),at)
                    rows=postings(oz,scheme,cursor.isoformat(),end.isoformat(),on_page=lambda batch:ingest(engine,'ozon',scheme,account,batch,'ozon-'+scheme,only_new=True))
                    ingest(engine,'ozon',scheme,account,[{**r,'_status_verified':True} for r in rows],'ozon-'+scheme);count+=len(rows);cursor=end
                # Independently follow older nonterminal postings by exact posting number.
                with transaction(engine) as s:
                    tracked=[copy_data(e) for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='ozon',Entity.data['scheme'].as_string()==scheme)) if e.data['account']==account and e.data.get('created_at') and e.data['created_at']<start.isoformat() and e.data['status'] not in ('delivered','cancelled','returned')]
                for d in sorted(tracked,key=lambda d:d.get('observed_at',''))[:100]:
                    data=oz.get('/v3/posting/fbs/get' if scheme=='fbs' else '/v2/posting/fbo/get',{'posting_number':d['external_id'],'with':{'analytics_data':False,'financial_data':False}})
                    data=data.get('result',data)
                    if data.get('posting_number')!=d['external_id']:raise FeedFailure('Не подтверждён номер старого отправления')
                    ingest(engine,'ozon',scheme,account,[{**data,'_status_verified':True}],'ozon-'+scheme)
                set_sync(engine,key,received=count,window_from=start.isoformat(),history_to=at.isoformat(),tracked_old=len(tracked))
            phase('ozon-'+scheme,current)
        phase('ozon-returns',lambda:ingest_returns(engine,account,ozon_returns(oz)))
        # Backfill independently from current orders: completed windows survive failures.
        for scheme in ('fbs','fbo'):
            key='ozon-history-'+scheme
            def history(scheme=scheme,key=key):
                prior=sync_state(engine).get(key,{});cursor=datetime.fromisoformat(prior['cursor']) if prior.get('cursor') else at-timedelta(days=31)
                start=at.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).replace(month=1,day=1,hour=0,minute=0,second=0,microsecond=0)
                cursor=max(cursor,start)
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


def reports_due(state,at):
    for key in ('wb-statistics','wb-sales'):
        value=state.get(key,{})
        if value.get('state')=='waiting' and value.get('retry_at') and datetime.fromisoformat(value['retry_at'])<=at:return True
    return False


def hashlib_account(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:20]

def copy_data(e):return dict(e.data)


def upsert_sales_rows(engine, rows, kind, newest=False, chunk_size=500):
    """Commit bounded batches, so a large report cannot lock inventory for minutes."""
    for offset in range(0,len(rows),chunk_size):
        chunk={}
        for key,data in rows[offset:offset+chunk_size]:
            previous=chunk.get(key)
            if not newest or previous is None or str(data['row'].get('lastChangeDate') or '')>=str(previous['row'].get('lastChangeDate') or ''):
                chunk[key]=data
        with transaction(engine) as s:
            existing={e.id:e for e in s.scalars(select(Entity).where(Entity.id.in_(list(chunk))))}
            for key,data in chunk.items():
                e=existing.get(key)
                if e:
                    if newest and str(data['row'].get('lastChangeDate') or '')<str(e.data['row'].get('lastChangeDate') or ''):continue
                    e.data=data;e.version+=1
                else:s.add(Entity(id=key,kind=kind,version=1,data=data))


def update_wb_status(engine,**fields):
    with transaction(engine) as s:
        e=s.get(Entity,'cutting-sales-status');data=dict(e.data) if e else {};platforms=dict(data.get('platforms',{}));platforms['wb']={**platforms.get('wb',{}),**fields};data['platforms']=platforms
        if e:e.data=data;e.version+=1
        else:s.add(Entity(id='cutting-sales-status',kind='sales_status',version=1,data=data))


def sync_wb_sales(engine,client,account,prior,at):
    # Share the existing sales reader lock, so the two workers cannot race the
    # same report endpoint after a cooldown expires.
    from pathlib import Path
    import fcntl
    root=Path(os.environ.get('KROY_APP_ROOT','/opt/kroy-app-stage'))
    if not root.is_dir():return _sync_wb_sales(engine,client,account,prior,at)
    with (root/'sales-reader.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Paused('Продажи уже сверяет другой читатель',(now()+timedelta(seconds=60)).isoformat()) from None
        return _sync_wb_sales(engine,client,account,prior,at)


def _sync_wb_sales(engine,client,account,prior,at):
    from .sales import wb_events,store_window,mappings,MSK
    from .order_feed import timestamp
    from collections import defaultdict
    import hashlib
    prior={**prior,**sync_state(engine).get('wb-sales',{})}
    year=max(str(at.astimezone(MSK).date().replace(month=1,day=1)),str(at.astimezone(MSK).date()-timedelta(days=89)))
    history_from=year
    if prior.get('raw_complete_from') and prior['raw_complete_from']<=year and prior.get('raw_end')==str(at.astimezone(MSK).date()-timedelta(days=1)) and not prior.get('rebuild_required') and prior.get('raw_at') and (at-datetime.fromisoformat(prior['raw_at'])).total_seconds()<86400:return SKIP
    cached_rebuild=bool(prior.get('rebuild_required') and prior.get('raw_complete_from') and prior['raw_complete_from']<=history_from and prior.get('raw_end'))
    fetched_at=prior.get('raw_at') if cached_rebuild else now().isoformat()
    end=prior['raw_end'] if cached_rebuild else str(at.astimezone(MSK).date()-timedelta(days=1))
    if not cached_rebuild:
        start=history_from if not prior.get('raw_complete_from') or prior['raw_complete_from']>year or prior.get('rebuild_required') else max(year,(datetime.fromisoformat(prior['raw_at'])-timedelta(days=1)).date().isoformat())
        def persist(batch):
          rows=[]
          for r in batch:
            safe={k:r.get(k) for k in ('saleID','date','lastChangeDate','barcode','srid','warehouseType')};key='market-sale-raw:'+hashlib.sha256(json.dumps([account,safe['saleID']]).encode()).hexdigest()
            rows.append((key,dict(account=account,row=safe)))
          upsert_sales_rows(engine,rows,'market_sale_raw',newest=True)
        wb_statistics(client,start,'/api/v1/supplier/sales','saleID',on_page=persist,checkpoint='report:wb-sales:'+account)
        fetched_at=sync_state(engine).get('report:wb-sales:'+account,{}).get('cutoff_at',fetched_at)
    with transaction(engine) as s:raw=[e.data['row'] for e in s.scalars(select(Entity).where(Entity.kind=='market_sale_raw',Entity.data['account'].as_string()==account))]
    codes,_,covered=mappings(engine)
    minimum=min((str(r['date'])[:10] for r in raw),default=history_from)
    events,_=wb_events(raw,minimum,str(at.astimezone(MSK).date()),codes)
    window_events,unmatched=wb_events(raw,history_from,end,codes)
    identities={str(r.get('saleID')):r for r in raw}
    normalized=[]
    for event in events:
        r=identities[event['id']];data={**event,'account':account,'order_ref':str(r.get('srid') or ''),'order_at':timestamp(r.get('date'),True)}
        key='market-sale:'+hashlib.sha256(json.dumps([account,event['id']]).encode()).hexdigest();normalized.append((key,data))
    upsert_sales_rows(engine,normalized,'market_sale')
    with transaction(engine) as s:
        by_ref=defaultdict(list)
        for event in events:
            r=identities[event['id']]
            if r.get('srid'):by_ref[str(r['srid'])].append({**event,'order_at':timestamp(r.get('date'),True)})
        for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()=='wb',Entity.data['account'].as_string()==account)):
            d=e.data;related=by_ref.get(d['source_ref'])
            if not related:continue
            last=max(related,key=lambda r:(r.get('order_at') or r['date'],r['kind']=='return'));state='returned' if last['kind']=='return' else 'delivered'
            if d['status']==state:continue
            history=d.get('history',[])+[dict(at=now().isoformat(),status=state,raw_status=last['kind'],source='wb-sales')]
            e.data={**d,'status':state,'sale_status':state,'history':history[-100:]};e.version+=1
    store_window(engine,dict(platform='wb',start=history_from,end=end,at=fetched_at,complete=True,verified=True,covered_items=list(covered['wb']),events=[e for e in window_events if e['item_id'] in covered['wb']],source='WB supplier/sales',unmatched=unmatched,collection_proof=sync_state(engine).get('report:wb-sales:'+account,{}).get('proof','empty_page')))
    set_sync(engine,'wb-sales',history_from=history_from,received=len(raw),history_to=end,raw_complete_from=history_from,raw_end=end,raw_at=fetched_at,rebuild_required=False,state='ready',success_at=now().isoformat(),error=None,retry_at=None)
    update_wb_status(engine,state='ready_cached' if cached_rebuild else 'ready',at=fetched_at,reason='Оперативные продажи Wildberries; итоговый отчёт не сверён',**({} if cached_rebuild else {'next_retry_at':None}))
