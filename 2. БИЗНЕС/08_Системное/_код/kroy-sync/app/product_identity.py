"""Exact marketplace variant identities and their verified barcode aliases."""
import hashlib,json
from collections import defaultdict
from sqlalchemy import select
from .db import Entity,Item,transaction


def refresh_wb_mappings(engine,catalog):
    digest=hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        from .catalog_cards import remember, wb_variants
        remember(s, "wb", catalog["at"], wb_variants(catalog["cards"]))
        items=list(s.scalars(select(Item).where(Item.kind=='garment')));codes=defaultdict(set);previous={};proposals=defaultdict(list)
        for i in items:
            for c in i.barcodes:codes[str(c)].add(i.id)
        for e in s.scalars(select(Entity).where(Entity.kind=='market_identity')):
            if e.data['platform']=='wb' and e.data['identity_kind']=='barcode':codes[e.data['value']].add(e.data['item_id'])
        old={e.data['item_id']:e for e in s.scalars(select(Entity).where(Entity.kind=='mapping',Entity.data['platform'].as_string()=='wb'))}
        for iid,e in old.items():
            manual=e.data.get('manual_card')
            if manual:previous.setdefault((manual['product_id'],manual['variant_id']),set()).add(iid)
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
        changed=0;counts=defaultdict(int);aliases_added=set()
        for i in items:
            rows=proposals[i.id];conflicts=set().union(*(owners[c]-{i.id} for row in rows for c in row['market_barcodes']))
            state='unique' if len(rows)==1 and not conflicts else 'ambiguous' if rows else 'missing';counts[state]+=1
            data=dict(item_id=i.id,platform='wb',state=state,candidates=rows,local_conflicts=sorted(conflicts),source_sha256=digest,observed_at=catalog['at'],approved=False)
            e=old.get(i.id)
            previous_aliases={code for c in (e.data.get('candidates',[]) if e else []) for code in c.get('market_barcodes',[])}|set(i.barcodes)
            if state=='unique' and any(set(c['market_barcodes'])-previous_aliases for c in rows):aliases_added.add(i.id)
            if e:
                if e.data.get('manual_card'):
                    data.update(manual_card=e.data['manual_card'],document_id=e.data['document_id'])
                if e.data.get('candidates')==rows and e.data.get('state')==state:data['approved']=e.data.get('approved',False)
                if any(e.data.get(k)!=data.get(k) for k in ('candidates','state','local_conflicts')):e.version+=1;changed+=1
                e.data=data
            else:s.add(Entity(id='mapping:wb:'+i.id,kind='mapping',version=1,data=data));changed+=1
        if aliases_added:
            for w in s.scalars(select(Entity).where(Entity.kind=='sales_window',Entity.data['platform'].as_string()=='wb')):
                if not w.data.get('unmatched'):continue
                affected=aliases_added & set(w.data.get('covered_items',[]))
                if not affected:continue
                data=dict(w.data);blocked=dict(data.get('blocked_items',{}));blocked.update({iid:'Обновлены штрихкоды ВБ. Ожидается повторная сверка продаж' for iid in affected})
                data.update(covered_items=sorted(set(data['covered_items'])-affected),blocked_items=blocked,identity_pending=sorted(set(data.get('identity_pending',[]))|affected),digest='identity-pending:'+data.get('digest',''));w.data=data;w.version+=1
            key='order-sync:wb-sales';e=s.get(Entity,key);data={**(e.data if e else {}),'rebuild_required':True}
            if e:e.data=data;e.version+=1
            else:s.add(Entity(id=key,kind='order_sync',version=1,data=data))
            e=s.get(Entity,'cutting-sales-status')
            if e:
                data=dict(e.data);platforms=dict(data.get('platforms',{}));platforms['wb']={**platforms.get('wb',{}),'state':'waiting','reason':'Обновлены штрихкоды. Продажи ВБ будут пересверены'};data['platforms']=platforms;e.data=data;e.version+=1
        return dict(counts=counts,changed=changed,source_sha256=digest,aliases_added_for=sorted(aliases_added))


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


def planned_barcode_adoptions(s):
    """Ozon card whose barcodes all sit on one unique Wildberries size.

    Several cards for one garment, a barcode owned by another garment, or an
    Ozon card already linked elsewhere are left untouched.
    """
    catalog = s.get(Entity, 'card-catalog:ozon')
    if catalog is None:
        return []
    items = {i.id: i for i in s.scalars(select(Item).where(Item.kind == 'garment'))}
    stored = defaultdict(set)
    for item in items.values():
        for code in item.barcodes or []:
            stored[str(code)].add(item.id)
    on_size = defaultdict(set)
    for entry in s.scalars(select(Entity).where(Entity.kind == 'mapping', Entity.data['platform'].as_string() == 'wb')):
        data = entry.data
        rows = data.get('candidates') or []
        if data.get('state') != 'unique' or len(rows) != 1:
            continue
        for code in rows[0].get('market_barcodes') or []:
            on_size[str(code)].add(data['item_id'])
    ozon = {}
    for entry in s.scalars(select(Entity).where(Entity.kind == 'mapping', Entity.data['platform'].as_string() == 'ozon')):
        ozon[entry.data['item_id']] = entry.data
    found = []
    for variant in catalog.data.get('variants') or []:
        codes = [str(code).strip() for code in variant.get('barcodes') or [] if str(code).strip()]
        if not codes or not variant.get('product_id'):
            continue
        owners = set()
        for code in codes:
            matches = on_size.get(code) or set()
            if len(matches) != 1:
                owners = set()
                break
            owners |= matches
        if len(owners) != 1:
            continue
        item_id = next(iter(owners))
        if any(stored.get(code, set()) - {item_id} for code in codes):
            continue
        current = ozon.get(item_id)
        if current and current.get('state') not in ('missing', 'unique'):
            continue
        if current and current.get('state') == 'unique':
            linked = {str(card.get('product_id')) for card in current.get('candidates') or []}
            if str(variant['product_id']) not in linked:
                continue
        item = items.get(item_id)
        if item is None:
            continue
        have = {str(code) for code in item.barcodes or []}
        missing = [code for code in codes if code not in have]
        needs_link = current is None or current.get('state') == 'missing'
        if not missing and not needs_link:
            continue
        found.append(dict(item_id=item_id, product_id=str(variant['product_id']), offer_id=str(variant.get('offer_id') or ''), barcodes=missing, needs_link=needs_link))
    grouped = defaultdict(list)
    for row in found:
        grouped[row['item_id']].append(row)
    return [rows[0] for rows in grouped.values() if len(rows) == 1]


def adopt_barcodes_from_linked_sizes(s):
    """Write the planned barcode onto the garment and link its Ozon card."""
    from uuid import uuid4
    from .catalog_cards import link_card
    from .db import Document, User
    from .service import require
    plans = planned_barcode_adoptions(s)
    if not plans:
        return []
    owner = s.scalar(select(User).where(User.role == 'owner', User.active.is_(True)).order_by(User.id))
    require(owner is not None, 'Нет владельца для записи штрихкода', 422)
    applied = []
    for plan in plans:
        item = s.get(Item, plan['item_id'])
        doc = str(uuid4())
        if plan['barcodes']:
            version = int((item.source or {}).get('version') or 1)
            previous = {key: getattr(item, key) for key in ('name', 'model', 'color', 'size', 'category', 'barcodes', 'photo')}
            previous['barcodes'] = list(item.barcodes or [])
            s.add(Entity(id='item-revision:' + doc, kind='item_revision', version=version, data={'item_id': item.id, 'previous': previous}))
            item.barcodes = list(item.barcodes or []) + plan['barcodes']
            item.source = {**(item.source or {}), 'version': version + 1, 'last_document': doc}
        s.add(Document(id=doc, kind='item.update' if plan['barcodes'] else 'catalog.link', actor=owner.id, reason='Штрихкод карточки на уже связанном размере Wildberries', payload={'item_id': item.id, 'barcodes': plan['barcodes'], 'ozon_product_id': plan['product_id'], 'offer_id': plan['offer_id']}))
        if plan['needs_link']:
            link_card(s, item, 'ozon', plan['product_id'], doc)
        applied.append(plan)
    return applied


GEISHA_OFFERS = {
    'ФТБ-ГША-ЧЕРН-S': ('Чёрный', 'S'),
    'ФТБ-ГША-ЧЕРН-M': ('Чёрный', 'M'),
    'ФТБ-ГША-Т.КОРИЧ-S': ('Тёмно-коричневый', 'S'),
    'ФТБ-ГША-Т.КОРИЧ-M': ('Тёмно-коричневый', 'M'),
}


def create_approved_geisha(engine, user, products):
    """Create the four approved Geisha garments. Any other offer is ignored."""
    from uuid import uuid4
    from sqlalchemy.orm import Session
    from .service import execute, require
    specs = []
    with Session(engine) as s:
        existing = list(s.scalars(select(Item).where(Item.kind == 'garment')))
    by_variant = {(item.model, item.color, item.size): item for item in existing}
    by_code = defaultdict(set)
    for item in existing:
        for code in item.barcodes or []:
            by_code[str(code)].add(item.id)
    for product in products:
        offer = str(product.get('offer_id') or '')
        if offer not in GEISHA_OFFERS:
            continue
        color, size = GEISHA_OFFERS[offer]
        codes = [str(code).strip() for code in product.get('barcodes') or [] if str(code).strip()]
        skus = [str(source.get('sku')) for source in product.get('sources') or [] if source.get('sku')]
        require(not product.get('is_archived') and not product.get('is_autoarchived'), 'Карточка Гейши в архиве: ' + offer, 422)
        require(len(codes) == 1 and len(skus) == 1 and product.get('id'), 'Карточка Гейши не однозначна: ' + offer, 422)
        current = by_variant.get(('Футболка Гейша', color, size))
        owners = by_code.get(codes[0], set())
        if current is not None:
            require(not owners or owners == {current.id}, 'Штрихкод Гейши уже у другого товара', 422)
            continue
        require(not owners, 'Штрихкод Гейши уже у другого товара', 422)
        specs.append(dict(offer=offer, color=color, size=size, code=codes[0], product_id=str(product['id'])))
    created = []
    for spec in specs:
        op = str(uuid4())
        execute(engine, user, op, 'item.create', dict(id=op, name='Футболка Гейша \u2014 ' + spec['color'] + ' (' + spec['size'] + ')', model='Футболка Гейша', color=spec['color'], size=spec['size'], category='YUJI', kind='garment', unit='piece', barcodes=[spec['code']], cards={'ozon': spec['product_id']}), 'Новая модель Футболка Гейша по артикулу Ozon')
        created.append(dict(id=op, offer=spec['offer'], color=spec['color'], size=spec['size']))
    return created
