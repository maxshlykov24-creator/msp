"""Marketplace identities and observations, independent of warehouse operations.

No customer names, contacts, addresses, prices or marking codes are retained.
"""
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import select, func, cast, Text, case, and_
from .db import Entity, Item, Balance, now, transaction
from .service import require

STATES={'ordered','new','assembling','ready','shipping','pickup','delivered','cancelled','returned','unknown'}
PENDING={'new','assembling','ready'}
WB_CANCEL={'canceled','canceled_by_client','canceled_by_missed_call','declined_by_client','cancel','cancelled'}
OZON_STATES={'awaiting_registration':'new','acceptance_in_progress':'new','awaiting_packaging':'assembling','awaiting_approve':'new','awaiting_deliver':'ready','driver_pickup':'shipping','delivering':'shipping','delivered':'delivered','cancelled':'cancelled','not_accepted':'cancelled','arbitration':'unknown','client_arbitration':'unknown','sent_by_seller':'shipping'}


def timestamp(value, moscow=False):
    if not value: return None
    require(isinstance(value,str), 'Некорректная дата площадки',422)
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        dt=dt.replace(tzinfo=ZoneInfo('Europe/Moscow') if moscow else timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def stable_id(platform, account, scheme, external):
    return 'market-order:'+hashlib.sha256(json.dumps([platform,account,'' if platform=='wb' else scheme,str(external)],ensure_ascii=False).encode()).hexdigest()


def index(s):
    codes=defaultdict(set);skus=defaultdict(set);variants=defaultdict(set)
    for i in s.scalars(select(Item).where(Item.kind=='garment')):
        for code in i.barcodes: codes[str(code)].add(i.id)
    for e in s.scalars(select(Entity).where(Entity.kind=='mapping')):
        d=e.data
        if d.get('state')!='unique':continue
        for c in d.get('candidates',[]):
            if d.get('platform')=='ozon':
                for sku in c.get('skus',[]):skus[str(sku)].add(d['item_id'])
            else:
                variants[str(c.get('variant_id'))].add(d['item_id'])
                for code in c.get('market_barcodes',[]):codes[str(code)].add(d['item_id'])
    for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
        d=e.data;target=codes if d['identity_kind']=='barcode' else skus if d['identity_kind']=='ozon_sku' else variants
        target[d['value']].add(d['item_id'])
    # Fresh sales-verified identities include archived SKUs.
    for e in s.scalars(select(Entity).where(Entity.kind=='sales_window')):
        if e.data.get('platform')!='ozon' or not e.data.get('verified'):continue
        for iid,matches in e.data.get('catalog_matches',{}).items():
            for c in matches:
                for sku in c.get('skus',[]):skus[str(sku)].add(iid)
    return codes,skus,variants


def normalized(platform,scheme,raw,account,lookups,source):
    codes,skus,variants=lookups
    if platform=='ozon':
        external=str(raw['posting_number']);ref=external;created=raw.get('in_process_at') or raw.get('created_at');status=OZON_STATES.get(raw.get('status'),'unknown')
        lines=[]
        for p in raw.get('products') or []:
            matches=skus.get(str(p.get('sku')),set());q=p.get('quantity')
            lines.append(dict(item_id=next(iter(matches)) if len(matches)==1 else None,sku=str(p.get('sku') or ''),barcode='',offer_id=str(p.get('offer_id') or ''),quantity=q if type(q) is int and q>0 else None,mapping='matched' if len(matches)==1 else 'ambiguous' if matches else 'missing'))
        due=raw.get('shipment_date');handed=raw.get('delivering_date');raw_status=raw.get('status') or '';supplier_status=''
        if raw.get('substatus') in ('posting_in_pickup_point','in_pickup_point'):status='pickup'
    elif source=='wb-statistics':
        external=str(raw['srid']);ref=external;created=raw.get('date');status='cancelled' if raw.get('isCancel') is True else 'ordered'
        matches=codes.get(str(raw.get('barcode')),set())
        lines=[dict(item_id=next(iter(matches)) if len(matches)==1 else None,barcode=str(raw.get('barcode') or ''),sku='',offer_id=str(raw.get('supplierArticle') or ''),quantity=1,mapping='matched' if len(matches)==1 else 'ambiguous' if matches else 'missing')]
        due=handed=None;raw_status='isCancel' if raw.get('isCancel') else 'ordered';supplier_status=''
    else:
        external=str(raw['id']);ref=raw.get('rid') or external;created=raw.get('createdAt');raw_status=raw.get('wbStatus') or '';supplier_status=raw.get('supplierStatus') or ''
        if raw_status in WB_CANCEL or supplier_status in WB_CANCEL:status='cancelled'
        elif raw_status=='sold':status='delivered'
        elif raw_status=='ready_for_pickup':status='pickup'
        elif raw_status in ('ready_for_pickup','sorted','on_way_to_client','accepted_by_client') or supplier_status=='complete':status='shipping'
        elif supplier_status=='confirm':status='assembling'
        elif supplier_status=='new':status='new'
        else:status='unknown'
        matches=set().union(*(codes.get(str(c),set()) for c in raw.get('skus') or []));vm=variants.get(str(raw.get('chrtId',raw.get('chrtID'))),set())
        if not matches:matches=vm
        elif vm and matches!=vm:matches|=vm
        lines=[dict(item_id=next(iter(matches)) if len(matches)==1 else None,barcode=str((raw.get('skus') or [''])[0]),sku=str(raw.get('chrtId',raw.get('chrtID')) or ''),offer_id=str(raw.get('article') or ''),quantity=1,mapping='matched' if len(matches)==1 else 'ambiguous' if matches else 'missing')]
        due=handed=None
    require(external and len(external)<=200,'Нет номера заказа площадки',422)
    return dict(platform=platform,account=account,scheme=scheme,external_id=external,source_ref=str(ref),created_at=timestamp(created,platform=='wb' and source=='wb-statistics'),shipment_due=timestamp(due),handed_at=timestamp(handed),status=status,raw_status=raw_status,supplier_status=supplier_status,lines=lines,source=source,parent=raw.get('parent_posting_number') if platform=='ozon' else None,order_number=str(raw.get('order_number') or raw.get('gNumber') or ''),order_id=str(raw.get('order_id') or raw.get('orderUid') or ''),substatus=raw.get('substatus'))


def ingest(engine,platform,scheme,account,rows,source,observed_at=None,only_new=False):
    at=timestamp(observed_at) if observed_at else now().isoformat();changed=created=0
    with transaction(engine) as s:
        lookups=index(s)
        for raw in rows:
            d=normalized(platform,scheme,raw,account,lookups,source)
            identity=d['source_ref'] if platform=='wb' else d['external_id']
            key=stable_id(platform,account,scheme,identity);e=s.get(Entity,key)
            if e and only_new:continue
            old=copy.deepcopy(e.data) if e else {}
            if old.get('observed_at','')>at:continue
            # Statistics never overwrite richer assembly statuses or content.
            if old.get('source')=='wb-fbs' and source=='wb-statistics':continue
            if old.get('status') in ('delivered','returned','cancelled') and d['status'] in PENDING and old.get('source')==d['source']:
                d['status']=old['status'];d['raw_status']=old.get('raw_status');d['status_warning']='Площадка вернула более ранний статус; нужна сверка'
            if source=='wb-statistics' and old.get('sale_status') and not raw.get('isCancel'):d['status']=old['sale_status']
            for field in ('created_at','parent','order_number','order_id','handed_at'):
                if not d.get(field) and old.get(field):d[field]=old[field]
            history=list(old.get('history',[]))
            if not old or any(old.get(k)!=d.get(k) for k in ('status','raw_status','lines','scheme')):
                history.append(dict(at=at,status=d['status'],raw_status=d['raw_status'],source=source));history=history[-100:]
            data={**old,**d,'status_verified':bool(raw.get('_status_verified') or platform=='wb' and source=='wb-fbs' and raw.get('supplierStatus') in ('new','confirm') or old.get('status_verified')),'observed_at':at,'first_seen':old.get('first_seen',at),'history':history}
            data['needs_mapping']=not d['lines'] or any(l['mapping']!='matched' or l['quantity'] is None for l in d['lines'])
            data['accounting_state']=old.get('accounting_state','unprocessed')
            if e:
                if {k:v for k,v in data.items() if k not in ('observed_at','history')}!={k:v for k,v in old.items() if k not in ('observed_at','history')}:e.version+=1;changed+=1
                e.data=data
            else:s.add(Entity(id=key,kind='market_order',version=1,data=data));created+=1
    return dict(created=created,changed=changed,received=len(rows))


def ingest_returns(engine, account, rows, observed_at=None):
    at=timestamp(observed_at) if observed_at else now().isoformat()
    with transaction(engine) as s:
        _,skus,_=index(s)
        for r in rows:
            p=r.get('product') or {};visual=r.get('visual') or {};status=visual.get('status') or {};ids=skus.get(str(p.get('sku')),set());rid=str(r['id']);schema=str(r.get('schema','')).lower()
            require(schema in ('fbs','fbo'),'Неизвестная схема возврата',422)
            key='market-return:'+hashlib.sha256(json.dumps([account,rid]).encode()).hexdigest();e=s.get(Entity,key)
            d=dict(account=account,external_id=rid,platform='ozon',scheme=schema,posting_number=r.get('posting_number'),item_id=next(iter(ids)) if len(ids)==1 else None,sku=str(p.get('sku') or ''),quantity=p.get('quantity'),status=str(status.get('sys_name') or ''),status_label=str(status.get('display_name') or ''),changed_at=timestamp(visual.get('change_moment')),return_at=timestamp((r.get('logistic') or {}).get('return_date')),observed_at=at)
            if e:
                if e.data.get('observed_at','')>at:continue
                if e.data.get('changed_at') and d['changed_at'] and d['changed_at']<e.data['changed_at']:continue
                e.data=d;e.version+=1
            else:s.add(Entity(id=key,kind='market_return',version=1,data=d))


def set_sync(engine,key,**values):
    with transaction(engine) as s:
        eid='order-sync:'+key;e=s.get(Entity,eid);d={**(e.data if e else {}),**values}
        if e:e.data=d;e.version+=1
        else:s.add(Entity(id=eid,kind='order_sync',version=1,data=d))


def sync_state(engine):
    with transaction(engine) as s:return {e.id.split(':',1)[1]:dict(e.data) for e in s.scalars(select(Entity).where(Entity.kind=='order_sync'))}


def list_orders(s,platform='',scheme='',status='',query='',start='',end='',page=1,per_page=50,attention=False):
    q=select(Entity).where(Entity.kind=='market_order')
    for key,value in [('platform',platform),('scheme',scheme)]:
        if value:q=q.where(Entity.data[key].as_string()==value)
    if status:
        grouped=case((and_(Entity.data['platform'].as_string()=='ozon',Entity.data['raw_status'].as_string()=='awaiting_packaging'),'new'),(and_(Entity.data['platform'].as_string()=='ozon',Entity.data['raw_status'].as_string().in_(['arbitration','client_arbitration'])),'shipping'),else_=Entity.data['status'].as_string())
        if status=='open':q=q.where(grouped.in_(['new','assembling','ready']))
        elif status=='closed':q=q.where(grouped.in_(['cancelled','returned']))
        else:q=q.where(grouped==status)
    if start:q=q.where(Entity.data['created_at'].as_string()>=timestamp(start+'T00:00:00',True))
    if end:
        from datetime import date,timedelta
        q=q.where(Entity.data['created_at'].as_string()<timestamp(str(date.fromisoformat(end)+timedelta(days=1))+'T00:00:00',True))
    if query:q=q.where(cast(Entity.data,Text).ilike('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    if attention:q=q.where((Entity.data['needs_mapping'].as_boolean()==True)|(Entity.data['accounting_state'].as_string().in_(['shortage','needs_review','waiting_stock'])))
    total=s.scalar(select(func.count()).select_from(q.subquery()))
    result=[]
    for e in s.scalars(q.order_by(Entity.data['created_at'].as_string().desc(),Entity.id).offset((page-1)*per_page).limit(per_page)):
        result.append(public_order(s,e))
    return dict(rows=result,total=total,page=page,per_page=per_page)


def public_order(s,e):
    d=copy.deepcopy(e.data);ledger=s.get(Entity,d['ledger_id']) if d.get('ledger_id') else None
    returns=[{'id':r.id,**r.data} for r in s.scalars(select(Entity).where(Entity.kind=='market_return',Entity.data['account'].as_string()==d['account'],Entity.data['posting_number'].as_string()==d['external_id']))]
    if d['platform']=='wb':
        returns=[{'id':r.id,**r.data,'status_label':'Возврат зарегистрирован ВБ','changed_at':r.data.get('order_at')} for r in s.scalars(select(Entity).where(Entity.kind=='market_sale',Entity.data['account'].as_string()==d['account'],Entity.data['order_ref'].as_string()==d['source_ref'],Entity.data['kind'].as_string()=='return'))]
    group='new' if d.get('platform')=='ozon' and d.get('raw_status')=='awaiting_packaging' else 'shipping' if d.get('platform')=='ozon' and d.get('raw_status') in ('arbitration','client_arbitration') else d['status']
    return {'id':e.id,'version':e.version,**d,'display_status':group,'returns':returns,'ledger':{'id':ledger.id,'version':ledger.version,**ledger.data} if ledger else None}
