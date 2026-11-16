"""Effective, item-scoped cutting norms. Historical snapshots are immutable."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Entity, Document, Item
from .planning import NORMS, NORM_SOURCE
from .service import require


def current(s, item_ids=None):
    today=str(datetime.now(ZoneInfo('Europe/Moscow')).date())
    rows={}
    for e,doc in s.execute(select(Entity,Document).join(Document,Document.id==Entity.data['document_id'].as_string()).where(Entity.kind=='norm')):
        d=e.data
        if d['effective_date']>today or item_ids is not None and d['item_id'] not in item_ids:continue
        key=(d['item_id'],d.get('material_id',''))
        rank=(d['effective_date'],doc.created_at.isoformat(),e.id)
        if key not in rows or rank>rows[key][0]:rows[key]=(rank,dict(d,id=e.id,version=e.version))
    return [r[1] for r in rows.values()]


def resolve(s, label, item_ids, material_by_item=None):
    material_by_item=material_by_item or {}
    norms=current(s,set(item_ids))
    result={}
    for iid in sorted(set(item_ids)):
        item=s.get(Item,iid)
        require(item is not None and item.kind=='garment','Неизвестное изделие для нормы',422)
        candidates=[n for n in norms if n['item_id']==iid and (not n.get('material_id') or n['material_id']==material_by_item.get(iid))]
        candidates.sort(key=lambda n:bool(n.get('material_id')),reverse=True)
        if candidates:
            n=candidates[0]
            require(n['confirmed'] is True,'Новая норма ещё не подтверждена для '+item.name,422)
            result[iid]={'id':n['id'],'version':n['version'],'yield':int(Decimal(n['yield'])),'effective_date':n['effective_date'],'material_id':n.get('material_id',''),'confirmed':True}
        else:
            require(label in NORMS,'Для изделия нет нормы',422)
            result[iid]={'id':'preliminary:'+label,'version':1,'yield':NORMS[label],'confirmed':False}
    require(result,'Выбери изделия для применения норм',422)
    require(len({n['yield'] for n in result.values()})==1,'Для выбранных изделий действуют разные нормы. Подбери раскладки отдельно',422)
    return result


def prepare(s, raw):
    body=dict(raw)
    ids=body.pop('item_ids',[])
    materials=body.pop('material_by_item',{})
    # Old callers may omit identity only when no applicable overrides exist.
    if not ids:
        ids=list(s.scalars(select(Item.id).where(Item.kind=='garment',Item.model==body['model'])))
    snapshot=resolve(s,body['model'],ids,materials)
    body['_norm']=next(iter(snapshot.values()))['yield']
    body.pop('_budget',None)
    return body,snapshot
