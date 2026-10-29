"""Read-only marketplace sales. No stock or order endpoints are used here."""
import hashlib
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Entity, Item, now, transaction
from .service import require
MSK = ZoneInfo('Europe/Moscow')


class RateLimited(RuntimeError):
    def __init__(self, seconds):
        self.seconds = max(60, seconds)
        super().__init__('Лимит запросов ВБ; повтор после паузы')



def store_window(engine, data):
    require(data['platform'] in ('wb','ozon'), 'Неизвестная площадка',422)
    start,end=date.fromisoformat(data['start']),date.fromisoformat(data['end'])
    require(start<=end, 'Неверные даты',422)
    events={}
    for event in data['events']:
        require(event['kind'] in ('sale','return') and type(event['quantity']) is int and event['quantity']>0, 'Неверное событие продаж',422)
        require(start<=date.fromisoformat(event['date'])<=end, 'Событие вне периода',422)
        key=event['id']
        require(key not in events or events[key]==event, 'Конфликт повторного события',422)
        events[key]=event
    data={**data,'events':list(events.values())}
    digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        for iid in data['covered_items']:
            item=s.get(Item,iid);require(item is not None and item.kind=='garment','Неизвестный товар',422)
        require(all(e['item_id'] in data['covered_items'] for e in data['events']), 'Событие без сопоставления',422)
        key='sales:'+data['platform']+':'+data['start']+':'+data['end']
        old=s.get(Entity,key)
        if old:
            if old.data.get('digest')==digest:return
            old.data={**data,'digest':digest};old.version+=1
        else:s.add(Entity(id=key,kind='sales_window',version=1,data={**data,'digest':digest}))


def get_json(url,headers,body=None):
    req=urllib.request.Request(url,headers={**headers,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None,method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raw = e.headers.get('Retry-After', e.headers.get('X-Ratelimit-Retry', '60'))
            raise RateLimited(int(raw) if str(raw).isdigit() else 60) from None
        # Only status, never headers or response with private account data.
        raise RuntimeError('HTTP '+str(e.code)) from None


def wb_events(raw,start,end,barcodes):
    require(isinstance(raw,list) and len(raw)<80000,'Неполная выгрузка ВБ',422)
    events=[];unmatched=0
    for row in raw:
        dt=datetime.fromisoformat(row['date'])
        day=str(dt.astimezone(MSK).date() if dt.tzinfo else dt.date())
        if not start<=day<=end:continue
        code=str(row['saleID']);require(code.startswith(('S','R')),'Неизвестный тип события ВБ',422)
        ids=barcodes.get(str(row['barcode']),[])
        if len(ids)!=1:unmatched+=1;continue
        events.append(dict(id=code,item_id=ids[0],date=day,kind='sale' if code[0]=='S' else 'return',quantity=1))
    return events,unmatched


def sync(engine, env):
    today=datetime.now(MSK).date();start=str(today-timedelta(days=30));end=str(today-timedelta(days=1))
    results={}
    try:
        with transaction(engine) as s:
            previous=s.get(Entity,'cutting-sales-status')
            retry=(previous.data.get('platforms',{}).get('wb',{}).get('next_retry_at') if previous else None)
        if retry and datetime.fromisoformat(retry)>now():
            raise RateLimited(int((datetime.fromisoformat(retry)-now()).total_seconds())+1)
        raw=get_json('https://statistics-api.wildberries.ru/api/v1/supplier/sales?dateFrom='+start,{'Authorization':env['WB_TOKEN']})
        with transaction(engine) as s:
            codes={}
            for item in s.scalars(select(Item).where(Item.kind=='garment')):
                for code in item.barcodes:codes.setdefault(code,[]).append(item.id)
            covered=[e.data['item_id'] for e in s.scalars(select(Entity).where(Entity.kind=='mapping')) if e.data.get('platform')=='wb' and e.data.get('state')=='unique']
        events,unmatched=wb_events(raw,start,end,codes)
        events=[e for e in events if e['item_id'] in covered]
        store_window(engine,dict(platform='wb',start=start,end=end,at=now().isoformat(),complete=True,verified=True,covered_items=covered,events=events,source='WB supplier/sales',unmatched=unmatched))
        results['wb']=dict(state='ready',events=len(events),unmatched=unmatched)
    except RateLimited as e:results['wb']=dict(state='unavailable',reason=str(e),next_retry_at=(now()+timedelta(seconds=e.seconds)).isoformat())
    except Exception as e:results['wb']=dict(state='unavailable',reason=str(e) if isinstance(e,RuntimeError) else 'Не удалось проверить выгрузку')
    # The official daily realization report excludes cancellations/non-redemptions.
    # Validate access before selecting this source; do not substitute deprecated analytics or orders.
    try:
        d=today-timedelta(days=2)
        get_json('https://api-seller.ozon.ru/v1/finance/realization/by-day',{'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']},dict(day=d.day,month=d.month,year=d.year))
        results['ozon']=dict(state='needs_reconciliation',reason='Доступ есть; требуется сверка структуры и количества с отчётом')
    except RuntimeError as e:
        results['ozon']=dict(state='unavailable',reason='Дневной отчёт недоступен для текущего доступа Ozon' if str(e)=='HTTP 403' else str(e))
    except Exception:results['ozon']=dict(state='unavailable',reason='Не удалось проверить дневной отчёт')
    with transaction(engine) as s:
        key='cutting-sales-status';e=s.get(Entity,key);data=dict(at=now().isoformat(),platforms=results)
        if e:e.data=data;e.version+=1
        else:s.add(Entity(id=key,kind='sales_status',version=1,data=data))
    return results
