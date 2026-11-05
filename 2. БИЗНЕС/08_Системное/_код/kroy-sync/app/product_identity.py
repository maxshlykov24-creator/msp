"""Exact marketplace variant identities and their verified barcode aliases."""
import hashlib,json
from collections import defaultdict
from sqlalchemy import select
from .db import Entity,Item,transaction


def refresh_wb_mappings(engine,catalog):
    digest=hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        items=list(s.scalars(select(Item).where(Item.kind=='garment')));codes=defaultdict(set);previous={};proposals=defaultdict(list)
        for i in items:
            for c in i.barcodes:codes[str(c)].add(i.id)
        for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
            if e.data['platform']=='wb' and e.data['identity_kind']=='barcode':codes[e.data['value']].add(e.data['item_id'])
        old={e.data['item_id']:e for e in s.scalars(select(Entity).where(Entity.kind=='mapping',Entity.data['platform'].as_string()=='wb'))}
        for iid,e in old.items():
            if e.data.get('state')=='unique':
                for c in e.data['candidates']:previous.setdefault((str(c['product_id']),str(c['variant_id'])),set()).add(iid)
        for card in catalog['cards']:
            for size in card['sizes']:
                aliases=sorted(set(str(c) for c in size.get('skus',[]) if c));key=(str(card['id']),str(size['chrtID']))
                matches=set().union(*(codes.get(c,set()) for c in aliases))|previous.get(key,set())
                candidate=dict(product_id=key[0],variant_id=key[1],barcodes=sorted(c for c in aliases if codes.get(c)),market_barcodes=aliases)
                for iid in matches:proposals[iid].append(candidate)
        owners=defaultdict(set)
        for iid,rows in proposals.items():
            for c in rows:
                for code in c['market_barcodes']:owners[code].add(iid)
        changed=0;counts=defaultdict(int)
        for i in items:
            rows=proposals[i.id];conflicts=set().union(*(owners[c]-{i.id} for row in rows for c in row['market_barcodes']))
            state='unique' if len(rows)==1 and not conflicts else 'ambiguous' if rows else 'missing';counts[state]+=1
            data=dict(item_id=i.id,platform='wb',state=state,candidates=rows,local_conflicts=sorted(conflicts),source_sha256=digest,observed_at=catalog['at'],approved=False)
            e=old.get(i.id)
            if e:
                if e.data.get('candidates')==rows and e.data.get('state')==state:data['approved']=e.data.get('approved',False)
                if any(e.data.get(k)!=data.get(k) for k in ('candidates','state','local_conflicts')):e.version+=1;changed+=1
                e.data=data
            else:s.add(Entity(id='mapping:wb:'+i.id,kind='mapping',version=1,data=data));changed+=1
        return dict(counts=counts,changed=changed,source_sha256=digest)


def barcode_matches(s,code):
    items=list(s.scalars(select(Item)));ids={i.id for i in items if code in i.barcodes}
    for e in s.scalars(select(Entity).where(Entity.kind=='mapping')):
        if e.data.get('state')!='unique':continue
        if any(code in c.get('market_barcodes',[]) for c in e.data.get('candidates',[])):ids.add(e.data['item_id'])
    for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
        if e.data['identity_kind']=='barcode' and e.data['value']==code:ids.add(e.data['item_id'])
    return [i for i in items if i.id in ids]


def bind_order_item(s,doc,p):
    from .service import entity,require,get_item,save
    order=entity(s,p['id'],'market_order',p['version']);d=dict(order.data);lines=[dict(r) for r in d['lines']]
    n=p.get('line');require(type(n) is int and 0<=n<len(lines),'Строка заказа не найдена',422)
    row=lines[n];item=get_item(s,p['item_id']);require(item.kind=='garment','Выбери изделие',422)
    require(row['mapping']!='matched' or row['item_id']==item.id,'Строка уже однозначно сопоставлена')
    if d['platform']=='ozon':kind='ozon_sku';value=row.get('sku')
    elif row.get('barcode'):kind='barcode';value=row['barcode']
    else:kind='wb_variant';value=row.get('sku')
    require(isinstance(value,str) and value,'Нет точного идентификатора площадки',422)
    if kind!='barcode':
        from .order_feed import index
        lookups=index(s);matches=lookups[1 if kind=='ozon_sku' else 2].get(value,set())
        require(not matches or matches=={item.id},'Идентификатор уже относится к другой позиции; нужна отдельная сверка')
    if kind=='barcode':
        matches=barcode_matches(s,value);require(not matches or {i.id for i in matches}=={item.id},'Штрихкод уже относится к другой позиции; нужна отдельная сверка')
    key='market-identity:'+hashlib.sha256(json.dumps([d['platform'],d['account'],kind,value]).encode()).hexdigest()
    old=s.get(Entity,key);require(old is None or old.data['item_id']==item.id,'Идентификатор уже связан с другой позицией')
    if old is None:s.add(Entity(id=key,kind='market_identity',version=1,data=dict(platform=d['platform'],account=d['account'],identity_kind=kind,value=value,item_id=item.id,document_id=doc)))
    changed=0
    for e in s.scalars(select(Entity).where(Entity.kind=='market_order',Entity.data['platform'].as_string()==d['platform'],Entity.data['account'].as_string()==d['account'])):
        data=dict(e.data);updated=False;new=[]
        for r in data['lines']:
            r=dict(r);candidate=r.get('barcode') if kind=='barcode' else r.get('sku')
            if candidate==value and (r['mapping']!='matched' or r['item_id']==item.id):r.update(item_id=item.id,mapping='matched');updated=True
            new.append(r)
        if updated:data.update(lines=new,needs_mapping=not new or any(r['mapping']!='matched' or r['quantity'] is None for r in new));save(e,data);changed+=1
    return {'id':order.id,'version':order.version,'changed_orders':changed}
