"""Barcode-backed candidates for the entire garment catalog, including gaps."""
import hashlib
import json
from sqlalchemy import select
from .db import Entity, Item, transaction


def import_candidates(engine, wb, ozon):
    digest = hashlib.sha256(json.dumps([wb, ozon], sort_keys=True).encode()).hexdigest()
    with transaction(engine) as s:
        existing = s.get(Entity, 'mapping-source:' + digest)
        if existing:
            return existing.data['counts']
        counts = {p: {'unique': 0, 'missing': 0, 'ambiguous': 0} for p in ('wb', 'ozon')}
        for item in s.scalars(select(Item).where(Item.kind == 'garment')):
            codes = set(item.barcodes)
            candidates = {'wb': [], 'ozon': []}
            for card in wb['cards']:
                for size in card['sizes']:
                    matched = sorted(codes.intersection(size.get('skus', [])))
                    if matched:
                        candidates['wb'].append({'product_id': str(card['id']), 'variant_id': str(size['chrtID']), 'barcodes': matched})
            for product in ozon['items']:
                matched = sorted(codes.intersection(product.get('barcodes') or []))
                if matched and not product.get('is_archived'):
                    candidates['ozon'].append({'product_id': str(product['id']), 'offer_id': product['offer_id'],
                        'skus': [str(x['sku']) for x in product.get('sources', [])], 'barcodes': matched})
            for platform, rows in candidates.items():
                # Same barcode duplicated in the local catalog makes both sides ambiguous.
                other_items = [i.id for i in s.scalars(select(Item).where(Item.id != item.id)) if codes.intersection(i.barcodes)]
                state = 'unique' if len(rows) == 1 and not other_items else 'ambiguous' if rows else 'missing'
                counts[platform][state] += 1
                key = 'mapping:' + platform + ':' + item.id
                e = s.get(Entity, key)
                data = {'item_id': item.id, 'platform': platform, 'state': state, 'candidates': rows,
                        'local_conflicts': other_items, 'source_sha256': digest, 'approved': False,
                        'observed_at': wb['at'] if platform == 'wb' else ozon['at']}
                if e is None:
                    s.add(Entity(id=key, kind='mapping', version=1, data=data))
                else:
                    if e.data.get('candidates') == rows and e.data.get('state') == state:
                        data['approved'] = e.data.get('approved', False)
                    e.data = data
                    e.version += 1
        s.add(Entity(id='mapping-source:' + digest, kind='mapping_source', version=1,
                     data={'counts': counts, 'sha256': digest, 'scope': 'all_garments'}))
        return counts
