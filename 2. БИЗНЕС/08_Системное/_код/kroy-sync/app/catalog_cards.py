"""Locally observed card variants available for explicit catalog linking."""
from sqlalchemy import select
from .db import Entity
from .service import require


def remember(s, platform, at, variants):
    key = 'card-catalog:' + platform
    data = {'at': at, 'variants': variants}
    entry = s.get(Entity, key)
    if entry is None:
        s.add(Entity(id=key, kind='card_catalog', version=1, data=data))
    elif at >= entry.data['at'] and data != entry.data:
        entry.data = data
        entry.version += 1


def wb_variants(cards):
    return [dict(product_id=str(c['id']), variant_id=str(v['chrtID']),
                 market_barcodes=list(v.get('skus') or []), size=v.get('techSize') or '',
                 title=c.get('vendorCode') or '') for c in cards for v in c['sizes']]


def ozon_variants(products):
    return [dict(product_id=str(p['id']), offer_id=p.get('offer_id'), title=p.get('name') or '',
                 barcodes=list(p.get('barcodes') or []),
                 skus=[str(v['sku']) for v in p.get('sources') or [] if v.get('sku')])
            for p in products if not p.get('is_archived') and not p.get('is_autoarchived')]


def link_card(s, item, platform, identifier, doc):
    from .order_feed import index
    require(platform in ('wb', 'ozon') and isinstance(identifier,str) and identifier.strip(), 'Укажи площадку и идентификатор', 422)
    identifier = identifier.strip()
    snapshot = s.get(Entity, 'card-catalog:' + platform)
    require(snapshot, 'Каталог площадки ещё не загружен')
    rows = snapshot.data['variants']
    matches = [c for c in rows if identifier in (
        [c['variant_id'], *c['market_barcodes']] if platform == 'wb' else
        [c['product_id'], c['offer_id'], *c['skus'], *c['barcodes']])]
    require(len(matches)==1, 'В сохранённом каталоге нет однозначного совпадения. Проверь идентификатор размера.')
    card = matches[0]
    codes, skus, variants = index(s)
    barcodes = card.get('market_barcodes', card.get('barcodes', []))
    claims = [codes.get(str(b), set()) for b in barcodes]
    if platform == 'wb':
        claims.append(variants.get(card['variant_id'], set()))
    else:
        require(card['skus'], 'В карточке Ozon отсутствует SKU')
        claims.extend(skus.get(v,set()) for v in card['skus'])
    for e in s.scalars(select(Entity).where(Entity.kind=='mapping',Entity.data['platform'].as_string()==platform)):
        if e.data['item_id']==item.id:continue
        for c in e.data.get('candidates', []):
            field = 'variant_id' if platform=='wb' else 'product_id'
            require(str(c.get(field)) != card[field], 'Карточка уже относится к другой позиции или требует разбора конфликта')
    require(not (set().union(*claims)-{item.id}), 'Этот вариант или его штрихкод уже связан с другим товаром')
    key = 'mapping:' + platform + ':' + item.id
    previous = s.get(Entity, key)
    require(not previous or previous.data['state']=='missing', 'Для этой позиции уже есть сопоставление или конфликт')
    data = dict(item_id=item.id, platform=platform, state='unique', candidates=[card], approved=False,
                local_conflicts=[], observed_at=snapshot.data['at'], document_id=doc, manual_card=card)
    if previous:
        previous.data=data
        previous.version+=1
    else:
        s.add(Entity(id=key, kind='mapping', version=1, data=data))
