import copy
import json
import sys
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from production import Engine, Review, quantities, ref, remaining, fingerprint


def uid():
    return str(uuid.uuid4())


class FakeMS:
    def __init__(self):
        self.docs = {'move': {}, 'loss': {}, 'supply': {}, 'purchaseorder': {}}
        self.created = []
        self.timeout_after_create = False
        self.stock = {}

    def get(self, path, **params):
        _, _, kind, value = path.split('/')
        return copy.deepcopy(self.docs[kind][value])

    def rows(self, path, **params):
        if path.startswith('/report'):
            return [{'id': k, 'stock': v} for k, v in self.stock.items()]
        kind = path.split('/')[2]
        docs = list(self.docs[kind].values())
        query = params.get('filter', '')
        if query.startswith('syncId='):
            docs = [d for d in docs if d.get('syncId') == query.split('=', 1)[1]]
        if query.startswith('name~='):
            docs = [d for d in docs if query[6:] in d.get('name', '')]
        if query.startswith('purchaseOrder='):
            docs = [d for d in docs if d.get('purchaseOrder', {}).get('meta', {}).get('href') == query.split('=', 1)[1]]
        return copy.deepcopy(docs)

    def positions(self, kind, value):
        return copy.deepcopy(self.docs[kind][value]['positions'])

    def request(self, method, path, body):
        kind = path.split('/')[2]
        if path.endswith('/new'):
            return copy.deepcopy(body)
        if method == 'POST':
            doc = dict(copy.deepcopy(body), id=uid())
            self.docs[kind][doc['id']] = doc
            self.created.append((kind, doc))
            if self.timeout_after_create:
                self.timeout_after_create = False
                raise Review('Неопределённый ответ')
            return copy.deepcopy(doc)
        value = path.split('/')[-1]
        self.docs[kind][value].update(copy.deepcopy(body))
        return copy.deepcopy(self.docs[kind][value])


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ms = FakeMS()
        self.e = Engine(self.tmp.name, self.ms)
        self.product, self.material, self.company, self.factory, self.org = [uid() for _ in range(5)]
        self.ms.stock = {self.product: 100, self.material: 100}

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self):
        return {'name': 'МТ-П-1', 'organization': ref('organization', self.org), 'applicable': True,
                'sourceStore': ref('store', self.factory), 'targetStore': ref('store', self.company),
                'positions': [{'assortment': ref('product', self.material), 'quantity': 3}]}

    def test_partial_return_remainder(self):
        self.assertEqual(remaining({'a': Decimal(10)}, {'a': Decimal(4)}), {'a': Decimal(6)})

    def test_excess_and_foreign_return_blocked(self):
        for returned in ({'a': Decimal(11)}, {'b': Decimal(1)}):
            with self.assertRaises(Review): remaining({'a': Decimal(10)}, returned)

    def test_quantities_aggregate_duplicate_lines(self):
        p = ref('product', self.product)
        self.assertEqual(quantities([{'assortment': p, 'quantity': 1}, {'assortment': p, 'quantity': 2}]),
                         {self.product: Decimal(3)})

    def test_post_timeout_recovers_without_duplicate(self):
        self.ms.timeout_after_create = True
        with self.assertRaises(Review): self.e.execute('same', 'move', self.payload())
        result = self.e.execute('same', 'move', self.payload())
        self.assertEqual(len(self.ms.created), 1)
        self.assertEqual(result['id'], self.ms.created[0][1]['id'])

    def test_duplicate_event_does_not_create(self):
        self.e.execute('same', 'move', self.payload())
        self.e.execute('same', 'move', self.payload())
        self.assertEqual(len(self.ms.created), 1)

    def test_edited_generated_document_blocks(self):
        doc = self.e.execute('same', 'move', self.payload())
        self.ms.docs['move'][doc['id']]['positions'][0]['quantity'] = 4
        with self.assertRaises(Review): self.e.execute('same', 'move', self.payload())

    def test_short_stock_prevents_post(self):
        self.ms.stock[self.material] = 2
        with self.assertRaises(Review): self.e.execute('same', 'move', self.payload())
        self.assertFalse(self.ms.created)

    def test_factory_store_edit_blocks_recovery(self):
        doc = self.e.execute('same', 'move', self.payload())
        self.ms.docs['move'][doc['id']]['sourceStore'] = ref('store', uid())
        with self.assertRaises(Review): self.e.execute('same', 'move', self.payload())

    def test_disabled_worker_does_not_write(self):
        self.e.enqueue('move', uid())
        self.assertFalse(self.e.run_one())
        self.assertFalse(self.ms.created)

    def test_ordinary_number_series_independent(self):
        for kind in ('move', 'loss'):
            value = uid()
            self.ms.docs[kind][value] = {'id': value, 'name': '15'}
            self.e.number_other(kind, self.ms.docs[kind][value])
            self.assertEqual(self.ms.docs[kind][value]['name'], '16')
            self.e.number_other(kind, self.ms.docs[kind][value])
            with self.e.db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM names WHERE kind=?', (kind,)).fetchone()[0], 1)

    def test_non_numeric_production_prefix_not_renumbered(self):
        with self.assertRaises(Review): self.e.number_other('move', {'id': uid(), 'name': 'ВШ-100/01'})

    def test_approval_requires_material_mapping_and_zip(self):
        with self.assertRaises(Review): self.e.save_recipe({'approved': True})
        draft = self.e.save_recipe({'name': 'Черновик', 'approved': False, 'stages': []})
        self.assertEqual(draft['version'], 1)
        self.assertEqual(self.e.save_recipe(draft)['version'], 2)

    def test_metadata_types(self):
        self.assertEqual(ref('move/metadata/states', uid())['meta']['type'], 'state')
        self.assertEqual(ref('customentity/' + uid(), uid())['meta']['type'], 'customentity')

    def test_source_fingerprint_tracks_quantity_not_status(self):
        doc = {'applicable': True, 'moment': '2026-10-29 12:00:00'}
        positions = self.payload()['positions']
        f = fingerprint(doc, positions)
        doc['state'] = ref('move/metadata/states', uid())
        self.assertEqual(f, fingerprint(doc, positions))
        positions[0]['quantity'] = 2
        self.assertNotEqual(f, fingerprint(doc, positions))

    def test_material_norm_multiplies_quantity(self):
        lines = [{'quantity': 3, 'recipe': {'stages': [{'materials': [
            {'id': self.material, 'quantity': '0.09'}]}]}}]
        self.assertAlmostEqual(self.e.material_lines(lines, 0)[0]['quantity'], .27)

    def test_nonfinite_quantity_rejected(self):
        with self.assertRaises(Review): quantities([{'assortment': ref('product', self.product), 'quantity': float('nan')}])


if __name__ == '__main__':
    unittest.main()
