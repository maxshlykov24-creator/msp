"""Read-only sales/returns feeds. Never changes warehouse or marketplace stock."""
import copy
import hashlib
import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Entity, Item, now, transaction
from .service import require
MSK = ZoneInfo('Europe/Moscow')
OZON_SOURCE = 'Ozon finance/accrual/by-day; commission quantities, delivery reversals excluded; v2'


class RateLimited(RuntimeError):
    def __init__(self, seconds, platform='wb'):
        self.seconds = max(1, seconds)
        self.platform = platform
        super().__init__('Лимит запросов '+('ВБ' if platform=='wb' else 'Ozon')+'; повтор после паузы')


def get_json(url, headers, body=None):
    req=urllib.request.Request(url,headers={**headers,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None,method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code==429:
            raw=e.headers.get('Retry-After',e.headers.get('X-Ratelimit-Retry','60'))
            raise RateLimited(int(raw) if str(raw).isdigit() else 60,'ozon' if 'ozon.ru' in url else 'wb') from None
        raise RuntimeError('HTTP '+str(e.code)) from None


def store_window(engine, data):
    require(data['platform'] in ('wb','ozon'),'Неизвестная площадка',422)
    start,end=date.fromisoformat(data['start']),date.fromisoformat(data['end'])
    require(start<=end,'Неверные даты',422)
    events={}
    for event in data['events']:
        require(event['kind'] in ('sale','return') and type(event['quantity']) is int and event['quantity']>0,'Неверное событие продаж',422)
        require(start<=date.fromisoformat(event['date'])<=end,'Событие вне периода',422)
        key=event['id'];require(key not in events or events[key]==event,'Конфликт повторного события',422);events[key]=event
    data={**data,'events':sorted(events.values(),key=lambda e:e['id']),'covered_items':sorted(set(data['covered_items']))}
    semantic={k:data.get(k) for k in ('platform','start','end','events','covered_items','blocked_items','not_listed_items','complete','verified','source')}
    digest=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        for iid in data['covered_items']:
            item=s.get(Item,iid);require(item is not None and item.kind=='garment','Неизвестный товар',422)
        require(all(e['item_id'] in data['covered_items'] for e in data['events']),'Событие без сопоставления',422)
        key='sales:'+data['platform']+':'+data['start']+':'+data['end'];old=s.get(Entity,key)
        if old:
            # Updated retrieval time is not a change of sales quantities.
            if old.data.get('digest')!=digest:old.version+=1
            old.data={**data,'digest':digest}
        else:s.add(Entity(id=key,kind='sales_window',version=1,data={**data,'digest':digest}))


def wb_events(raw,start,end,barcodes):
    require(isinstance(raw,list) and len(raw)<80000,'Неполная выгрузка ВБ',422)
    events=[];unmatched=0
    for row in raw:
        dt=datetime.fromisoformat(row['date']);day=str(dt.astimezone(MSK).date() if dt.tzinfo else dt.date())
        if not start<=day<=end:continue
        code=str(row['saleID']);require(code.startswith(('S','R')),'Неизвестный тип события ВБ',422)
        ids=barcodes.get(str(row['barcode']),[])
        if len(ids)!=1:unmatched+=1;continue
        events.append(dict(id=code,item_id=ids[0],date=day,kind='sale' if code[0]=='S' else 'return',quantity=1))
    return events,unmatched


def ozon_entries(raw):
    """Commission holds sale quantities; delivery-only rows are fees, not units sold."""
    unique={};entries_by_id={}
    for row in raw:
        key=str(row['accrual_id'])
        require(key not in unique or unique[key]==row,'Конфликт начисления Ozon',422)
        if key in unique:continue
        unique[key]=row
        for p in (row.get('posting') or {}).get('products',[]):
            c=p.get('commission')
            if not c:continue
            q=p['quantity'];seller=Decimal(c['seller_price']['amount']);amount=Decimal(c['sale_amount']['amount'])
            require(type(q) is int and q>0 and seller.is_finite() and amount.is_finite() and seller!=0,'Неоднозначное количество или знак реализации Ozon',422)
            require(not amount or (seller>0)==(amount>0),'Противоречивые знаки реализации Ozon',422)
            day=str(row['date']);date.fromisoformat(day)
            correction=seller<0 and p.get('delivery') is not None
            identity=hashlib.sha256(json.dumps([str(p['sku']),str(seller.normalize()),correction]).encode()).hexdigest()[:24]
            eid='ozon:'+key+':'+identity
            if eid in entries_by_id:entries_by_id[eid]['quantity']+=q
            else:entries_by_id[eid]=dict(id=eid,sku=str(p['sku']),unit=str(row['unit_number']),date=day,quantity=q,price=seller,correction=correction)
    entries=list(entries_by_id.values())
    # A reversed delivery is not a customer return. Remove it AND its original sale.
    # Genuine customer returns (no delivery reversal block) remain, including same-day returns.
    remaining={r['id']:r['quantity'] for r in entries};blocked=set();corrections=0
    for r in sorted((e for e in entries if e['correction']),key=lambda e:(e['date'],e['id'])):
        candidates=sorted((p for p in entries if p['price']>0 and p['unit']==r['unit'] and p['sku']==r['sku'] and p['price']==-r['price'] and p['date']<=r['date']),key=lambda e:(e['date'],e['id']),reverse=True)
        need=r['quantity']
        for p in candidates:
            used=min(remaining[p['id']],need);remaining[p['id']]-=used;need-=used
            if not need:break
        if need:blocked.add(r['sku'])
        remaining[r['id']]=0;corrections+=r['quantity']
    return [dict(id=r['id'],sku=r['sku'],date=r['date'],kind='sale' if r['price']>0 else 'return',quantity=remaining[r['id']]) for r in entries if remaining[r['id']]>0 and r['sku'] not in blocked],blocked,corrections


def reconcile_ozon(raw, report):
    rows,blocked,corrections=ozon_entries(raw);expected=defaultdict(lambda:[0,0]);actual=defaultdict(lambda:[0,0])
    for r in report['result']['rows']:
        for i,k in enumerate(('delivery_commission','return_commission')):expected[str(r['item']['sku'])][i]+=int((r.get(k) or {}).get('quantity',0))
    for r in rows:actual[r['sku']][0 if r['kind']=='sale' else 1]+=r['quantity']
    differences=[sku for sku in set(expected)|set(actual) if expected[sku]!=actual[sku]]
    return dict(verified=not differences and not blocked,sku_count=len(set(expected)|set(actual)),mismatched_skus=len(differences),blocked_skus=len(blocked),sales=sum(v[0] for v in actual.values()),returns=sum(v[1] for v in actual.values()),reversed_deliveries=corrections,report_sha256=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest())


def collect_ozon_day(day,headers,cache_dir,get=get_json):
    path=Path(cache_dir)/('ozon-'+day+'.json');path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        cached=json.loads(path.read_text());age=(now()-datetime.fromisoformat(cached['at'])).total_seconds()
        recent=(datetime.now(MSK).date()-date.fromisoformat(day)).days<=3
        if cached.get('complete') and age<(1800 if recent else 86400):return cached
    rows=[];seen=set();cursor='';source_hashes=[]
    for _ in range(1000):
        data=get('https://api-seller.ozon.ru/v1/finance/accrual/by-day',headers,dict(date=day,last_id=cursor))
        require(isinstance(data.get('accruals'),list),'Неполная выгрузка начислений Ozon',422)
        source_hashes.append(hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest())
        for row in data['accruals']:
            require(str(row['accrual_id']) not in seen,'Повтор страницы начислений Ozon',422);seen.add(str(row['accrual_id']))
            require(row['date']==day,'Начисление Ozon вне запрошенного дня',422)
            # Keep only data needed for units, mapping and reversal reconciliation. No prices of fees, contacts or addresses.
            products=[]
            for p in (row.get('posting') or {}).get('products',[]):
                if p.get('commission'):products.append(dict(sku=p['sku'],quantity=p['quantity'],commission={k:p['commission'][k] for k in ('seller_price','sale_amount')},delivery={} if p.get('delivery') is not None else None))
            if products:rows.append(dict(accrual_id=row['accrual_id'],date=day,unit_number=row['unit_number'],posting={'products':products}))
        next_cursor=data.get('last_id','')
        if not next_cursor:break
        require(next_cursor!=cursor and data['accruals'],'Курсор Ozon не продвинулся',422);cursor=next_cursor
    else:raise RuntimeError('Слишком много страниц Ozon')
    result=dict(at=now().isoformat(),date=day,complete=True,accruals=rows,source_hashes=source_hashes)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.chmod(0o600);tmp.replace(path)
    return result


def mappings(engine):
    codes={};skus=defaultdict(set);covered={'wb':set(),'ozon':set()}
    with transaction(engine) as s:
        for item in s.scalars(select(Item).where(Item.kind=='garment')):
            for code in item.barcodes:codes.setdefault(code,[]).append(item.id)
        for e in s.scalars(select(Entity).where(Entity.kind=='mapping')):
            d=e.data
            if d.get('state')!='unique':continue
            if d['platform']=='ozon' and not any(c.get('skus') for c in d['candidates']):continue
            covered[d['platform']].add(d['item_id'])
            if d['platform']=='wb':
                for candidate in d['candidates']:
                    for code in candidate.get('market_barcodes',[]):
                        if d['item_id'] not in codes.setdefault(str(code),[]):codes[str(code)].append(d['item_id'])
            if d['platform']=='ozon':
                for candidate in d['candidates']:
                    for sku in candidate.get('skus',[]):skus[str(sku)].add(d['item_id'])
        for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
            d=e.data
            if d['identity_kind']=='barcode':
                if d['item_id'] not in codes.setdefault(d['value'],[]):codes[d['value']].append(d['item_id'])
                if d['platform']=='wb':covered['wb'].add(d['item_id'])
            elif d['identity_kind']=='ozon_sku':skus[d['value']].add(d['item_id']);covered['ozon'].add(d['item_id'])
    # One external SKU cannot be assigned to multiple local variants.
    ambiguous={iid for values in skus.values() if len(values)!=1 for iid in values}
    covered['ozon']-=ambiguous
    return codes,{k:list(v) for k,v in skus.items()},covered


def latest_window(engine,platform,start,end):
    with transaction(engine) as s:
        windows=[e.data for e in s.scalars(select(Entity).where(Entity.kind=='sales_window')) if e.data.get('platform')==platform and e.data['start']<=start and e.data['end']>=end and e.data.get('verified') and e.data.get('complete')]
        return copy.deepcopy(max(windows,key=lambda d:d['at'],default=None))


def sync(engine, env, start=None, end=None):
    today=datetime.now(MSK).date();start=start or str(today-timedelta(days=30));end=end or str(today-timedelta(days=1))
    require(date.fromisoformat(start)<=date.fromisoformat(end)<today and (date.fromisoformat(end)-date.fromisoformat(start)).days<90,'Проверь период продаж',422)
    codes,skus,covered=mappings(engine);results={}
    with transaction(engine) as s:
        previous=s.get(Entity,'cutting-sales-status');previous=copy.deepcopy(previous.data.get('platforms',{})) if previous else {}
        certificate=s.get(Entity,'sales-reconciliation:ozon:v2');certificate=copy.deepcopy(certificate.data) if certificate else None
    for platform in ('wb','ozon'):
        cached=latest_window(engine,platform,start,end);prior=previous.get(platform,{})
        try:
            retry=prior.get('next_retry_at')
            if retry and datetime.fromisoformat(retry)>now():
                results[platform]={**prior,'state':'ready_cached' if cached else 'waiting','at':cached['at'] if cached else None};continue
            if platform=='wb':
                if cached and (now()-datetime.fromisoformat(cached['at'])).total_seconds()<3600:
                    results[platform]=dict(state='ready_cached',at=cached['at'],reason='Используется свежая полная выгрузка');continue
                raw=get_json('https://statistics-api.wildberries.ru/api/v1/supplier/sales?dateFrom='+start,{'Authorization':env['WB_TOKEN']})
                events,unmatched=wb_events(raw,start,end,codes);events=[e for e in events if e['item_id'] in covered['wb']]
                data=dict(platform='wb',start=start,end=end,at=now().isoformat(),complete=True,verified=True,covered_items=list(covered['wb']),events=events,source='WB supplier/sales',unmatched=unmatched)
            else:
                require(certificate and certificate.get('verified'),'Классификация Ozon ещё не сверена с отчётом',422)
                headers={'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']};raw=[];hashes=[]
                for offset in range((date.fromisoformat(end)-date.fromisoformat(start)).days+1):
                    day=str(date.fromisoformat(start)+timedelta(days=offset));chunk=collect_ozon_day(day,headers,env.get('KROY_SALES_CACHE','.runtime/sales-cache'));raw.extend(chunk['accruals']);hashes.extend(chunk['source_hashes'])
                catalog=collect_ozon_catalog(headers,env.get('KROY_SALES_CACHE','.runtime/sales-cache'))
                skus,ozon_covered,not_listed,catalog_proof=catalog_links(engine,catalog)
                normalized,blocked,reversed_units=ozon_entries(raw);unmatched=0;events=[]
                catalog_matches=defaultdict(list)
                for product in catalog['items']:
                    ids={iid for source in product.get('sources') or [] for iid in skus.get(str(source.get('sku')),[])}
                    if len(ids)==1:
                        catalog_matches[next(iter(ids))].append(dict(product_id=str(product['id']),offer_id=product.get('offer_id'),skus=[str(v['sku']) for v in product.get('sources') or [] if v.get('sku')],archived=bool(product.get('is_archived') or product.get('is_autoarchived'))))
                blocked_items={iid:'Не найдена исходная продажа для отмены начисления Ozon' for sku in blocked for iid in skus.get(sku,[])}
                allowed=ozon_covered-set(blocked_items)
                unknown_skus={r['sku'] for r in normalized if len(skus.get(r['sku'],[]))!=1}|{sku for sku in blocked if len(skus.get(sku,[]))!=1}
                # Known zero is justified only when the entire account catalog AND every
                # financial sale/return SKU are identified. Missing mapping alone is never zero.
                proven_not_listed=not_listed if catalog_proof['all_products_matched'] and not unknown_skus else set()
                allowed|=proven_not_listed
                for event in normalized:
                    matches=skus.get(event['sku'],[])
                    if len(matches)!=1 or matches[0] not in allowed:unmatched+=event['quantity'];continue
                    events.append({k:v for k,v in event.items() if k!='sku'}|{'item_id':matches[0]})
                data=dict(platform='ozon',start=start,end=end,at=now().isoformat(),complete=True,verified=True,covered_items=list(allowed),blocked_items=blocked_items,not_listed_items=sorted(proven_not_listed),catalog_matches=dict(catalog_matches),catalog_proof={**catalog_proof,'unmatched_financial_skus':len(unknown_skus)},events=events,source=OZON_SOURCE,unmatched=unmatched,reconciliation=certificate,reversed_deliveries=reversed_units,source_hashes=hashes)
            store_window(engine,data)
            results[platform]=dict(state='ready',at=data['at'],start=start,end=end,events=len(data['events']),covered=len(data['covered_items']),unmatched=data['unmatched'])
        except RateLimited as e:
            results[platform]=dict(state='ready_cached' if cached else 'waiting',at=cached['at'] if cached else None,reason=str(e),next_retry_at=(now()+timedelta(seconds=e.seconds)).isoformat())
        except Exception as e:
            from .service import DomainError
            reason=e.message if isinstance(e,DomainError) else str(e) if isinstance(e,RuntimeError) else 'Ошибка проверки выгрузки'
            results[platform]=dict(state='ready_cached' if cached else 'unavailable',at=cached['at'] if cached else None,reason=reason)
    with transaction(engine) as s:
        key='cutting-sales-status';e=s.get(Entity,key);data=dict(at=now().isoformat(),platforms=results)
        if e:e.data=data;e.version+=1
        else:s.add(Entity(id=key,kind='sales_status',version=1,data=data))
    return results


def collect_ozon_catalog(headers, cache_dir, get=get_json):
    """Only identity metadata: no marketplace stock quantities are requested."""
    path=Path(cache_dir)/'ozon-catalog.json';path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        cached=json.loads(path.read_text())
        if cached.get('catalog_schema')==2 and cached.get('complete') and (now()-datetime.fromisoformat(cached['at'])).total_seconds()<21600:return cached
    listed=[];all_ids=set()
    # ALL may omit archived products; inspect the archive separately as well.
    for visibility in ('ALL', 'ARCHIVED'):
        seen=set();cursor='';total=None
        for _ in range(1000):
            result=get('https://api-seller.ozon.ru/v3/product/list',headers,dict(filter={'visibility':visibility},limit=1000,last_id=cursor)).get('result',{})
            require(isinstance(result.get('items'),list) and type(result.get('total')) is int,'Неполный каталог Ozon',422)
            require(total is None or total==result['total'],'Каталог Ozon изменился во время загрузки',422);total=result['total']
            for row in result['items']:
                pid=str(row['product_id']);require(pid not in seen,'Повтор страницы каталога Ozon',422);seen.add(pid)
                if pid not in all_ids:all_ids.add(pid);listed.append(pid)
            require(len(seen)<=total,'Неверный итог каталога Ozon',422)
            if len(seen)==total:break
            nxt=result.get('last_id','');require(nxt and nxt!=cursor and result['items'],'Курсор каталога Ozon не продвинулся',422);cursor=nxt
        else:raise RuntimeError('Лимит страниц каталога Ozon')
    items=[]
    for offset in range(0,len(listed),1000):
        batch=listed[offset:offset+1000]
        result=get('https://api-seller.ozon.ru/v3/product/info/list',headers,{'product_id':batch})
        require(isinstance(result.get('items'),list) and {str(r['id']) for r in result['items']}==set(batch) and len(result['items'])==len(batch),'Неполные карточки Ozon',422)
        items.extend({k:r.get(k) for k in ('id','offer_id','name','barcodes','sources','is_archived','is_autoarchived')} for r in result['items'])
    data=dict(at=now().isoformat(),complete=True,catalog_schema=2,visibility_scopes=['ALL','ARCHIVED'],items=items)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);tmp.replace(path);return data


def catalog_links(engine, catalog):
    with transaction(engine) as s:
        items=list(s.scalars(select(Item).where(Item.kind=='garment')))
        codes=defaultdict(set)
        for item in items:
            for code in item.barcodes:codes[str(code)].add(item.id)
        skus=defaultdict(set);covered=set();matched_products=0;manual=defaultdict(set)
        for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
            if e.data['identity_kind']=='ozon_sku':manual[e.data['value']].add(e.data['item_id'])
        for sku,ids in manual.items():
            skus[sku]|=ids
            if len(ids)==1:covered|=ids
        for product in catalog['items']:
            product_skus={str(v['sku']) for v in product.get('sources')or[] if v.get('sku')}
            matches=set().union(*(codes.get(str(c),set()) for c in product.get('barcodes')or[]))|set().union(*(manual.get(sku,set()) for sku in product_skus))
            if len(matches)!=1 or not product_skus:continue
            iid=next(iter(matches));matched_products+=1;covered.add(iid)
            for sku in product_skus:skus[sku].add(iid)
        ambiguous={iid for ids in skus.values() if len(ids)!=1 for iid in ids};covered-=ambiguous
        return {k:list(v) for k,v in skus.items()},covered,{i.id for i in items}-covered,dict(at=catalog['at'],all_products_matched=catalog.get('complete') and matched_products==len(catalog['items']) and not ambiguous,catalog_products=len(catalog['items']),matched_products=matched_products,visibility_scopes=catalog.get('visibility_scopes',[]))
