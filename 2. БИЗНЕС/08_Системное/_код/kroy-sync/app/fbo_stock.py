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
        result[platform]=dict(known=known,quantity=d.get('quantities',{}).get(item_id),stale=stale,source=d.get('source'),at=d.get('at'),data_at=d.get('data_at'),version=e.version if e else None)
    total=sum(v['quantity'] for v in result.values()) if all(v['known'] for v in result.values()) else None
    return result,total
