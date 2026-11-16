"""Validated marketplace snapshots; never writes the own warehouse ledger."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from .db import Entity, now, transaction
from .service import require


def store_snapshot(engine, platform, quantities, *, source, data_at, interval_seconds=3600):
    require(platform in ('wb','ozon') and source, 'Неизвестный источник ФБО', 422)
    require(interval_seconds >= 60, 'Некорректный интервал источника', 422)
    parsed = datetime.fromisoformat(data_at)
    require(parsed.tzinfo is not None, 'Укажи часовой пояс данных', 422)
    require((parsed-now()).total_seconds() <= 300, 'Время данных ФБО находится в будущем', 422)
    values = {}
    for iid,q in quantities.items():
        require(type(q) is int and q >= 0, 'Некорректный доступный остаток ФБО', 422)
        values[iid] = q
    digest = hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        from .db import Item
        require(all((i:=s.get(Item,iid)) is not None and i.kind=='garment' for iid in values), 'Нет точного сопоставления ФБО', 422)
        key='fbo-stock:'+platform
        e=s.get(Entity,key)
        data=dict(platform=platform,quantities=values,source=source,data_at=data_at,at=now().isoformat(),interval_seconds=interval_seconds,digest=digest)
        if e:
            if e.data.get('digest')!=digest:e.version+=1
            e.data=data
        else:s.add(Entity(id=key,kind='fbo_stock_snapshot',version=1,data=data))


def read_stock(s, item_id):
    result={}
    for platform in ('wb','ozon'):
        e=s.get(Entity,'fbo-stock:'+platform)
        d=e.data if e else {}
        known=item_id in d.get('quantities',{})
        stale=not d.get('at') or (now()-datetime.fromisoformat(d['at'])).total_seconds()>2*d.get('interval_seconds',3600)
        stale = stale or not d.get('data_at') or (now()-datetime.fromisoformat(d['data_at'])).total_seconds()>2*d.get('interval_seconds',3600)
        result[platform]=dict(known=known,quantity=d.get('quantities',{}).get(item_id),stale=stale,source=d.get('source'),at=d.get('at'),data_at=d.get('data_at'),version=e.version if e else None)
    total=sum(v['quantity'] for v in result.values()) if all(v['known'] for v in result.values()) else None
    return result,total


def normalize_ozon(rows, catalog, sku_links):
    """FBO present minus reserved; FBS never enters production coverage.

    Identity is exact catalog SKU mapping, never a name or a fuzzy offer match.
    A missing product/stock group remains unknown, even after full pagination.
    """
    expected, resolved, invalid = {}, {}, set()
    for product in catalog['items']:
        ids = {iid for source in product.get('sources') or [] for iid in sku_links.get(str(source.get('sku')), [])}
        if len(ids) != 1:
            invalid.update(ids)
            continue
        iid = next(iter(ids))
        expected.setdefault(iid, set()).add(str(product['id']))
        resolved[str(product['id'])] = iid
    quantities, observed, seen = {}, {}, set()
    for row in rows:
        pid = str(row['product_id'])
        require(pid not in seen, 'Повтор товара в остатках Ozon', 422)
        seen.add(pid)
        iid = resolved.get(pid)
        if not iid: continue
        fbo = [v for v in row.get('stocks', []) if v.get('type') == 'fbo']
        if not fbo: continue
        keys = set()
        total = 0
        for stock in fbo:
            key = str(stock.get('sku'))
            require(key not in keys and key not in ('None',''), 'Неоднозначный остаток Ozon', 422)
            keys.add(key)
            require(sku_links.get(key) == [iid], 'Остаток Ozon не сопоставлен точно', 422)
            present, reserved = stock.get('present'), stock.get('reserved')
            require(type(present) is int and type(reserved) is int and 0 <= reserved <= present, 'Некорректный остаток Ozon', 422)
            total += present-reserved
        quantities[iid] = quantities.get(iid, 0)+total
        observed.setdefault(iid,set()).add(pid)
    return {iid:q for iid,q in quantities.items() if iid not in invalid and observed[iid] == expected[iid]}


def sync_ozon(engine, env, get=None):
    """Called under the existing sales-reader lock. At most once per hour.

    Cooldown is saved before networking, so crashes/restarts cannot hammer Ozon.
    Failures retain the previous snapshot and its original freshness timestamp.
    """
    from datetime import timedelta
    from .sales import get_json, collect_ozon_catalog, catalog_links, RateLimited
    from .connectors import ozon_stocks
    get = get or get_json
    key = 'fbo-stock-status:ozon'
    with transaction(engine) as s:
        e = s.get(Entity,key)
        prior = dict(e.data) if e else {}
        if prior.get('next_retry_at') and datetime.fromisoformat(prior['next_retry_at']) > now():
            return prior
        state = dict(state='loading',next_retry_at=(now()+timedelta(hours=1)).isoformat())
        if e: e.data=state
        else: s.add(Entity(id=key,kind='fbo_stock_status',version=1,data=state))
    try:
        headers={'Api-Key':env['OZON_API_KEY'],'Client-Id':env['OZON_CLIENT_ID']}
        class Client:
            def request(self,path,body):
                require(path=='/v4/product/info/stocks','Недопустимый метод ФБО',422)
                return get('https://api-seller.ozon.ru'+path,headers,body)
        catalog=collect_ozon_catalog(headers,env.get('KROY_SALES_CACHE','.runtime/sales-cache'),get)
        links,_,_,_=catalog_links(engine,catalog)
        rows=ozon_stocks(Client())
        quantities=normalize_ozon(rows,catalog,links)
        at=now().isoformat()
        store_snapshot(engine,'ozon',quantities,source='Ozon /v4/product/info/stocks: fbo present-reserved',data_at=at)
        state.update(state='ready',at=at,matched_items=len(quantities),products=len(rows))
    except RateLimited as exc:
        state.update(state='waiting',reason='Ozon ограничил частоту запросов',next_retry_at=(now()+timedelta(seconds=max(3600,exc.seconds+60))).isoformat())
    except Exception:
        state.update(state='unavailable',reason='Не удалось полностью загрузить и сопоставить остатки ФБО Ozon')
    with transaction(engine) as s:
        e=s.get(Entity,key);e.data=state;e.version+=1
    return state
