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
        if old and old.data.get('events') and not data['events']:
            require(False,'Новая выгрузка продаж пустая. Предыдущие продажи сохранены',422)
        if old:
            # Updated retrieval time is not a change of sales quantities.
            if old.data.get('digest')!=digest:old.version+=1
            old.data={**data,'digest':digest}
        else:s.add(Entity(id=key,kind='sales_window',version=1,data={**data,'digest':digest}))


def wb_events(raw,start,end,barcodes):
    require(isinstance(raw,list),'Неполная выгрузка ВБ',422)
    events=[];unmatched=0;by_day=defaultdict(int)
    for row in raw:
        raw_date=row.get('date');require(isinstance(raw_date,str),'Некорректная дата продажи ВБ',422)
        dt=datetime.fromisoformat(raw_date.replace('Z','+00:00'));day=str(dt.astimezone(MSK).date() if dt.tzinfo else dt.date())
        if not start<=day<=end:continue
        code=str(row['saleID']);require(code.startswith(('S','R')),'Неизвестный тип события ВБ',422)
        ids=barcodes.get(str(row['barcode']),[])
        if len(ids)!=1:unmatched+=1;by_day[day]+=1;continue
        events.append(dict(id=code,item_id=ids[0],date=day,kind='sale' if code[0]=='S' else 'return',quantity=1))
    return events,unmatched,dict(by_day)


def unmatched_between(data,start,end):
    """Rows without one barcode, sliced by day. None means the window has no day split yet."""
    by_day=data.get('unmatched_by_day')
    if not isinstance(by_day,dict):return None
    return sum(int(count) for day,count in by_day.items() if start<=str(day)<=end)


def _offer(offers,srid,barcode):
    lines=offers.get(srid) or []
    for code,offer in lines:
        if code and code==barcode and offer:return offer
    for _,offer in lines:
        if offer:return offer
    return ''


def wb_unmatched_rows(raw,start,end,barcodes,offers):
    """Unmatched sales grouped by day and seller article. The barcode stays out of the result."""
    require(isinstance(raw,list),'Неполная выгрузка ВБ',422)
    bucket=defaultdict(lambda:[0,0])
    for row in raw:
        raw_date=row.get('date');require(isinstance(raw_date,str),'Некорректная дата продажи ВБ',422)
        dt=datetime.fromisoformat(raw_date.replace('Z','+00:00'));day=str(dt.astimezone(MSK).date() if dt.tzinfo else dt.date())
        if not start<=day<=end:continue
        code=str(row['saleID']);require(code.startswith(('S','R')),'Неизвестный тип события ВБ',422)
        if len(barcodes.get(str(row['barcode']),[]))==1:continue
        offer=_offer(offers,str(row.get('srid') or ''),str(row['barcode']))
        bucket[(day,offer)][0 if code[0]=='S' else 1]+=1
    return [dict(date=day,offer_id=offer,sales=sales,returns=returns) for (day,offer),(sales,returns) in sorted(bucket.items())]


def _ru_noun(count,one,few,many):
    count=abs(int(count))
    if count%10==1 and count%100!=11:return one
    if 2<=count%10<=4 and not 12<=count%100<=14:return few
    return many


def wb_movement(sales,returns):
    parts=[]
    if sales:parts.append(str(sales)+' '+_ru_noun(sales,'продажа','продажи','продаж'))
    if returns:parts.append(str(returns)+' '+_ru_noun(returns,'возврат','возврата','возвратов'))
    return ', '.join(parts)


def wb_gap_rows(data,start,end):
    """Article lines for one period. None means this window has no article split yet."""
    source=data.get('unmatched_rows')
    if not isinstance(source,list):return None
    totals=defaultdict(lambda:[0,0])
    for row in source:
        day=str(row.get('date') or '')
        if not start<=day<=end:continue
        offer=str(row.get('offer_id') or '').strip() or 'Артикул в заказе не найден'
        totals[offer][0]+=int(row.get('sales') or 0);totals[offer][1]+=int(row.get('returns') or 0)
    rows=[dict(offer_id=offer,sales=sales,returns=returns,units=sales+returns,detail=wb_movement(sales,returns)) for offer,(sales,returns) in totals.items() if sales or returns]
    rows.sort(key=lambda row:(-row['units'],row['offer_id']))
    return rows


def wb_gap_text(data,start,end):
    count=unmatched_between(data,start,end)
    if count is None:
        if not data.get('unmatched'):return None
        return 'Wildberries: '+str(data['unmatched'])+' строк выгрузки с '+str(data.get('start'))+' по '+str(data.get('end'))+' без однозначного штрихкода не вошли в расчёт.'
    if not count:return None
    if wb_gap_rows(data,start,end) is not None:
        return 'Wildberries: '+str(count)+' '+_ru_noun(count,'строка','строки','строк')+' с '+start+' по '+end+' не сопоставлены с товаром.'
    return 'Wildberries: '+str(count)+' строк с '+start+' по '+end+' без однозначного штрихкода не вошли в расчёт.'


def wb_gap_lines(data,start,end):
    text=wb_gap_text(data,start,end)
    if not text:return []
    rows=wb_gap_rows(data,start,end) or []
    return [text]+[row['offer_id']+': '+row['detail'] for row in rows]


def ozon_gap_cards(catalog,skus,normalized,limit=40):
    """Cards that are not one local product. Names stay outside the sales digest."""
    units=defaultdict(int)
    for event in normalized:
        if len(skus.get(event['sku'],[]))!=1:units[event['sku']]+=int(event['quantity'])
    seen=set();rows=[]
    for product in catalog.get('items') or []:
        product_skus=[str(v.get('sku')) for v in product.get('sources') or [] if v.get('sku')]
        matches=set()
        for sku in product_skus:matches.update(skus.get(sku,[]))
        if product_skus and len(matches)==1:
            seen.update(product_skus);continue
        sold=sum(units.get(sku,0) for sku in product_skus);seen.update(product_skus)
        rows.append(dict(offer_id=str(product.get('offer_id') or '')[:80],name=str(product.get('name') or '')[:180],units=int(sold)))
    for sku,sold in units.items():
        if sku not in seen and sold:rows.append(dict(offer_id=str(sku)[:80],name='',units=int(sold)))
    rows.sort(key=lambda r:(-r['units'],r['offer_id'],r['name']))
    return rows[:limit]


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


def _window_headers(engine):
    """Window selection must not materialize every historical sale row."""
    from sqlalchemy import text
    if engine.dialect.name=='postgresql':
        query=text("SELECT (data::jsonb - 'events' - 'catalog_matches' - 'source_hashes' - 'reconciliation')::text FROM entities WHERE kind='sales_window'")
    else:
        query=text("SELECT json_remove(data, '$.events', '$.catalog_matches', '$.source_hashes', '$.reconciliation') FROM entities WHERE kind='sales_window'")
    with engine.connect() as connection:
        rows=connection.execute(query).all()
    headers=[]
    for (raw,) in rows:
        if not raw:continue
        meta=json.loads(raw) if isinstance(raw,str) else raw
        if isinstance(meta,str):meta=json.loads(meta)
        if isinstance(meta,dict):headers.append(meta)
    return headers


def latest_window(engine,platform,start,end):
    windows=[d for d in _window_headers(engine) if d.get('platform')==platform and d.get('start')<=start and d.get('end')>=end and d.get('verified') and d.get('complete')]
    return copy.deepcopy(max(windows,key=lambda d:(not bool(d.get('identity_pending')),d.get('at') or ''),default=None))


def assemble_ozon_window(engine, catalog, raw, hashes, start, end, certificate):
    """Attach barcodes that already sit on a linked size, then build the sales window."""
    from .catalog_cards import ozon_variants, remember
    from .product_identity import adopt_barcodes_from_linked_sizes
    require(certificate and certificate.get('verified'), 'Классификация Ozon ещё не сверена с отчётом', 422)
    with transaction(engine) as s:
        if catalog.get('complete'):
            remember(s, 'ozon', catalog['at'], ozon_variants(catalog['items']))
        adopted = adopt_barcodes_from_linked_sizes(s)
    skus, ozon_covered, not_listed, catalog_proof = catalog_links(engine, catalog)
    normalized, blocked, reversed_units = ozon_entries(raw)
    unmatched = 0
    events = []
    catalog_matches = defaultdict(list)
    for product in catalog['items']:
        ids = {iid for source in product.get('sources') or [] for iid in skus.get(str(source.get('sku')), [])}
        if len(ids) == 1:
            catalog_matches[next(iter(ids))].append(dict(product_id=str(product['id']), offer_id=product.get('offer_id'), skus=[str(v['sku']) for v in product.get('sources') or [] if v.get('sku')], archived=bool(product.get('is_archived') or product.get('is_autoarchived'))))
    blocked_items = {iid: 'Не найдена исходная продажа для отмены начисления Ozon' for sku in blocked for iid in skus.get(sku, [])}
    allowed = ozon_covered - set(blocked_items)
    unknown_skus = {r['sku'] for r in normalized if len(skus.get(r['sku'], [])) != 1} | {sku for sku in blocked if len(skus.get(sku, [])) != 1}
    proven_not_listed = not_listed if catalog_proof['all_products_matched'] and not unknown_skus else set()
    allowed |= proven_not_listed
    for event in normalized:
        matches = skus.get(event['sku'], [])
        if len(matches) != 1 or matches[0] not in allowed:
            unmatched += event['quantity']
            continue
        events.append({k: v for k, v in event.items() if k != 'sku'} | {'item_id': matches[0]})
    gaps = ozon_gap_cards(catalog, skus, normalized)
    data = dict(platform='ozon', start=start, end=end, at=now().isoformat(), complete=True, verified=True, covered_items=list(allowed), blocked_items=blocked_items, not_listed_items=sorted(proven_not_listed), catalog_matches=dict(catalog_matches), catalog_proof={**catalog_proof, 'unmatched_financial_skus': len(unknown_skus)}, events=events, source=OZON_SOURCE, unmatched=unmatched, gaps=gaps, reconciliation=certificate, reversed_deliveries=reversed_units, source_hashes=hashes)
    return data, adopted


def reattribute_cached_ozon_window(engine, cache_dir, start, end):
    """Rebuild one saved Ozon window from complete cached days. Does not call Ozon."""
    cache = Path(cache_dir)
    catalog = json.loads((cache / 'ozon-catalog.json').read_text())
    require(catalog.get('catalog_schema') == 2 and catalog.get('complete') and catalog.get('items'), 'Каталог Ozon в кэше неполный', 422)
    raw, hashes = [], []
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        path = cache / ('ozon-' + str(day) + '.json')
        require(path.exists(), 'Нет сохранённого дня Ozon ' + str(day), 422)
        chunk = json.loads(path.read_text())
        require(chunk.get('complete') and chunk.get('date') == str(day), 'Неполный день Ozon ' + str(day), 422)
        raw.extend(chunk.get('accruals') or [])
        hashes.extend(chunk.get('source_hashes') or [])
        day += timedelta(days=1)
    with transaction(engine) as s:
        cert_row = s.get(Entity, 'sales-reconciliation:ozon:v2')
        certificate = copy.deepcopy(cert_row.data) if cert_row else None
        old = s.get(Entity, 'sales:ozon:' + start + ':' + end)
        old_events = len(old.data.get('events') or []) if old else 0
        old_unmatched = int(old.data.get('unmatched') or 0) if old else 0
    data, adopted = assemble_ozon_window(engine, catalog, raw, hashes, start, end, certificate)
    require(len(data['events']) >= old_events, 'Новое окно потеряло продажи', 422)
    require(int(data['unmatched']) <= old_unmatched, 'Несопоставленных продаж стало больше', 422)
    store_window(engine, data)
    return dict(events=len(data['events']), unmatched=data['unmatched'], gaps=len(data['gaps']), adopted=len(adopted), covered=len(data['covered_items']), previous_events=old_events, previous_unmatched=old_unmatched)


def sync(engine, env, start=None, end=None):
    today=datetime.now(MSK).date();start=start or str(today-timedelta(days=30));end=end or str(today-timedelta(days=1))
    require(date.fromisoformat(start)<=date.fromisoformat(end)<today and (date.fromisoformat(end)-date.fromisoformat(start)).days<90,'Проверь период продаж',422)
    codes,skus,covered=mappings(engine);results={}
    with transaction(engine) as s:
        previous=s.get(Entity,'cutting-sales-status');previous=copy.deepcopy(previous.data.get('platforms',{})) if previous else {}
        certificate=s.get(Entity,'sales-reconciliation:ozon:v2');certificate=copy.deepcopy(certificate.data) if certificate else None
    def publish(partial):
        with transaction(engine) as s:
            key='cutting-sales-status';e=s.get(Entity,key)
            platforms=dict(e.data.get('platforms',{})) if e else {}
            platforms.update(partial)
            data=dict(at=now().isoformat(),platforms=platforms)
            if e:e.data=data;e.version+=1
            else:s.add(Entity(id=key,kind='sales_status',version=1,data=data))
    for platform in ('ozon','wb'):
        cached=latest_window(engine,platform,start,end);prior=previous.get(platform,{})
        try:
            retry=prior.get('next_retry_at')
            if platform!='wb' and retry and datetime.fromisoformat(retry)>now():
                results[platform]={**prior,'state':'ready_cached' if cached and not cached.get('identity_pending') else 'waiting','at':cached['at'] if cached else None};continue
            if platform=='wb':
                if cached and not cached.get('identity_pending') and cached.get('covered_items') is not None and cached.get('end')>=end:
                    results[platform]=dict(state='ready_cached',at=cached.get('at'),reason='Оперативные продажи Wildberries; итоговый отчёт не сверён',start=cached.get('start'),end=cached.get('end'));continue
                from .order_sync import Client,_sync_wb_sales,Paused,FeedFailure
                from .order_feed import sync_state
                state=sync_state(engine);account=state.get('wb-identity',{}).get('account')
                require(account,'Ожидается подтверждение кабинета ВБ',422)
                try:_sync_wb_sales(engine,Client(engine,'wb',account,{'Authorization':env['WB_TOKEN']}),account,state.get('wb-sales',{}),now())
                except Paused as e:
                    cached=latest_window(engine,'wb',start,end)
                    results[platform]=dict(state='ready_cached' if cached and not cached.get('identity_pending') else 'waiting',at=cached['at'] if cached else None,reason='Оперативные продажи Wildberries; итоговый отчёт не сверён' if cached and not cached.get('identity_pending') else str(e),next_retry_at=e.retry_at,retry_buffered=True,**({'start':cached['start'],'end':cached['end']} if cached else {}));continue
                cached=latest_window(engine,'wb',start,end)
                results[platform]=dict(state='ready_cached' if cached and not cached.get('identity_pending') else 'waiting',at=cached['at'] if cached else None,reason='Оперативные продажи Wildberries; итоговый отчёт не сверён',**({'start':cached['start'],'end':cached['end']} if cached else {}))
                continue
            else:
                require(certificate and certificate.get('verified'),'Классификация Ozon ещё не сверена с отчётом',422)
                headers={'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']};raw=[];hashes=[]
                for offset in range((date.fromisoformat(end)-date.fromisoformat(start)).days+1):
                    day=str(date.fromisoformat(start)+timedelta(days=offset));chunk=collect_ozon_day(day,headers,env.get('KROY_SALES_CACHE','.runtime/sales-cache'));raw.extend(chunk['accruals']);hashes.extend(chunk['source_hashes'])
                catalog=collect_ozon_catalog(headers,env.get('KROY_SALES_CACHE','.runtime/sales-cache'))
                # Known zero is justified only when the entire account catalog AND every
                # financial sale/return SKU are identified. Missing mapping alone is never zero.
                data,_adopted=assemble_ozon_window(engine,catalog,raw,hashes,start,end,certificate)
            store_window(engine,data)
            results[platform]=dict(state='ready',at=data['at'],start=start,end=end,events=len(data['events']),covered=len(data['covered_items']),unmatched=data['unmatched'])
        except RateLimited as e:
            results[platform]=dict(state='ready_cached' if cached and not cached.get('identity_pending') else 'waiting',at=cached['at'] if cached else None,reason=str(e),next_retry_at=(now()+timedelta(seconds=e.seconds)).isoformat(),**({'start':cached['start'],'end':cached['end']} if cached else {}))
        except MemoryError:
            results[platform]=dict(state='ready_cached' if cached and not cached.get('identity_pending') else 'unavailable',at=cached['at'] if cached else None,reason='Не хватило памяти при сверке. Предыдущие данные сохранены',**({'start':cached['start'],'end':cached['end']} if cached else {}))
        except Exception as e:
            from .service import DomainError
            reason=(e.message if isinstance(e,DomainError) else str(e)) or 'Ошибка проверки выгрузки'
            results[platform]=dict(state='ready_cached' if cached and not cached.get('identity_pending') else 'unavailable',at=cached['at'] if cached else None,reason=reason[:300],**( {'start':cached['start'],'end':cached['end']} if cached else {}))
        finally:
            if platform in results:publish({platform:results[platform]})
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
            result['total']=result.get('total_items',result.get('total'))
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
        from .catalog_cards import remember, ozon_variants
        if catalog.get('complete'):
            remember(s, 'ozon', catalog['at'], ozon_variants(catalog['items']))
        for sku,ids in manual.items():
            skus[sku]|=ids
            if len(ids)==1:covered|=ids
        for e in s.scalars(select(Entity).where(Entity.kind=='mapping',Entity.data['platform'].as_string()=='ozon')):
            if e.data.get('manual_card') and e.data.get('state')=='unique':
                for card in e.data['candidates']:
                    for sku in card.get('skus',[]):manual[sku].add(e.data['item_id'])
        for product in catalog['items']:
            product_skus={str(v['sku']) for v in product.get('sources')or[] if v.get('sku')}
            matches=set().union(*(codes.get(str(c),set()) for c in product.get('barcodes')or[]))|set().union(*(manual.get(sku,set()) for sku in product_skus))
            if len(matches)!=1 or not product_skus:continue
            iid=next(iter(matches));matched_products+=1;covered.add(iid)
            for sku in product_skus:skus[sku].add(iid)
        ambiguous={iid for ids in skus.values() if len(ids)!=1 for iid in ids};covered-=ambiguous
        return {k:list(v) for k,v in skus.items()},covered,{i.id for i in items}-covered,dict(at=catalog['at'],all_products_matched=catalog.get('complete') and matched_products==len(catalog['items']) and not ambiguous,catalog_products=len(catalog['items']),matched_products=matched_products,visibility_scopes=catalog.get('visibility_scopes',[]))


def invalidate_unverified_wb_windows(engine):
    """Do not use old windows as complete when no terminal page was checked."""
    changed=0
    with transaction(engine) as s:
        for e in s.scalars(select(Entity).where(Entity.kind=='sales_window',Entity.data['platform'].as_string()=='wb')):
            d=dict(e.data)
            if d.get('collection_proof') in ('empty_page','cutoff'):continue
            affected=set(d.get('covered_items',[]))
            if not affected:continue
            blocked=dict(d.get('blocked_items',{}));blocked.update({iid:'Проверяем полноту выгрузки Wildberries' for iid in affected})
            d.update(covered_items=[],blocked_items=blocked,identity_pending=sorted(set(d.get('identity_pending',[]))|affected),digest='incomplete:'+str(d.get('digest','')))
            e.data=d;e.version+=1;changed+=1
    return changed


def window_refs(engine):
    """Headers only. Event bodies stay in the database."""
    from sqlalchemy import text
    if engine.dialect.name=='postgresql':
        query=text("SELECT id, (data::jsonb - 'events' - 'catalog_matches' - 'source_hashes' - 'reconciliation')::text FROM entities WHERE kind='sales_window'")
    else:
        query=text("SELECT id, json_remove(data, '$.events', '$.catalog_matches', '$.source_hashes', '$.reconciliation') FROM entities WHERE kind='sales_window'")
    refs=[]
    with engine.connect() as connection:
        for row_id,raw in connection.execute(query):
            if not raw:continue
            meta=json.loads(raw) if isinstance(raw,str) else raw
            if isinstance(meta,str):meta=json.loads(meta)
            if isinstance(meta,dict):
                meta=dict(meta);meta['_id']=row_id;refs.append(meta)
    return refs


def attach_window_fields(engine,row_id,extra):
    """Add a note to a saved window without changing its sales digest or version."""
    with transaction(engine) as s:
        e=s.get(Entity,row_id)
        if e is None:return False
        if all(e.data.get(key)==value for key,value in extra.items()):return False
        e.data={**e.data,**extra}
        return True


def begin_sales_run(engine,source):
    require(source in ('timer','manual'),'Неизвестный запуск сверки',422)
    with transaction(engine) as s:
        data=dict(state='running',source=source,started_at=now().isoformat(),finished_at=None,error=None)
        e=s.get(Entity,'sales-run')
        if e:e.data=data;e.version+=1
        else:s.add(Entity(id='sales-run',kind='sales_run',version=1,data=data))


def finish_sales_run(engine,error=None):
    with transaction(engine) as s:
        e=s.get(Entity,'sales-run')
        if e is None:return
        message=(error or '')[:300] or None
        e.data={**e.data,'state':'error' if message else 'done','finished_at':now().isoformat(),'error':message}
        e.version+=1


def _days(start,end):
    cursor,last=date.fromisoformat(start),date.fromisoformat(end)
    while cursor<=last:
        yield str(cursor);cursor+=timedelta(days=1)


def _raw_sale_keys(engine):
    from sqlalchemy import text
    if engine.dialect.name=='postgresql':
        query=text("SELECT (data::jsonb)#>>'{row,date}', (data::jsonb)#>>'{row,barcode}', (data::jsonb)#>>'{row,saleID}' FROM entities WHERE kind='market_sale_raw'")
    else:
        query=text("SELECT json_extract(data, '$.row.date'), json_extract(data, '$.row.barcode'), json_extract(data, '$.row.saleID') FROM entities WHERE kind='market_sale_raw'")
    with engine.connect() as connection:
        return connection.execute(query).all()


def annotate_saved_gaps(engine,cache_dir):
    """Fill names and day counts already on disk. Does not call a marketplace."""
    return dict(ozon=_annotate_ozon_gaps(engine,Path(cache_dir)),wb=_annotate_wb_days(engine))


def _annotate_ozon_gaps(engine,cache):
    catalog_path=cache/'ozon-catalog.json'
    if not catalog_path.exists():return 0
    try:catalog=json.loads(catalog_path.read_text())
    except (OSError,json.JSONDecodeError):return 0
    if not catalog.get('complete') or not catalog.get('items'):return 0
    try:skus,_,_,_=catalog_links(engine,catalog)
    except Exception:return 0
    changed=0
    for meta in window_refs(engine):
        if meta.get('platform')!='ozon' or not meta.get('complete') or not meta.get('verified') or 'gaps' in meta:continue
        start,end=meta.get('start'),meta.get('end')
        if not start or not end:continue
        try:
            if (date.fromisoformat(end)-date.fromisoformat(start)).days>=90:continue
        except ValueError:continue
        paths=[cache/('ozon-'+day+'.json') for day in _days(start,end)]
        if not all(path.exists() for path in paths):continue
        try:
            raw=[]
            for path in paths:raw.extend(json.loads(path.read_text()).get('accruals') or [])
            normalized,_,_=ozon_entries(raw)
        except Exception:continue
        if attach_window_fields(engine,meta['_id'],dict(gaps=ozon_gap_cards(catalog,skus,normalized))):changed+=1
    return changed


def _annotate_wb_days(engine):
    refs=[meta for meta in window_refs(engine) if meta.get('platform')=='wb' and meta.get('complete') and meta.get('verified') and 'unmatched_by_day' not in meta]
    if not refs:return 0
    codes,_,_=mappings(engine)
    counts=defaultdict(int);saw=False
    for raw_date,barcode,sale_id in _raw_sale_keys(engine):
        if not isinstance(raw_date,str) or not isinstance(sale_id,str) or not sale_id.startswith(('S','R')):continue
        saw=True
        try:dt=datetime.fromisoformat(raw_date.replace('Z','+00:00'))
        except ValueError:continue
        day=str(dt.astimezone(MSK).date() if dt.tzinfo else dt.date())
        if len(codes.get(str(barcode or ''),[]))!=1:counts[day]+=1
    if not saw:return 0
    changed=0
    for meta in refs:
        start,end=meta.get('start'),meta.get('end')
        if not start or not end:continue
        by_day={day:count for day,count in counts.items() if start<=day<=end}
        total=sum(by_day.values())
        if meta.get('unmatched') is not None and total!=int(meta['unmatched']):continue
        if attach_window_fields(engine,meta['_id'],dict(unmatched_by_day=by_day)):changed+=1
    return changed
