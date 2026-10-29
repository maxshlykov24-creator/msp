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

    def test_norm_display_units_convert_to_stock_units(self):
        r = self.e.save_recipe({'name': 'Test', 'stages': [{'materials': [
            {'unit': 'кг', 'input_unit': 'г', 'input_quantity': 250}]}]})
        self.assertEqual(r['stages'][0]['materials'][0]['quantity'], .25)
        r = self.e.save_recipe(r)
        self.assertEqual(r['stages'][0]['materials'][0]['quantity'], .25)

    def test_norm_rejects_incompatible_units(self):
        with self.assertRaisesRegex(Review, 'Единица'):
            self.e.save_recipe({'approved': True, 'stages': [{'materials': [
                {'unit': 'м', 'input_unit': 'г', 'input_quantity': 250}]}]})

    def test_norm_duplicate_product_is_rejected(self):
        self.e.save_recipe({'name': 'First', 'assortment_id': self.product})
        with self.assertRaisesRegex(Review, 'уже есть норма'):
            self.e.save_recipe({'name': 'Second', 'assortment_id': self.product})

    def test_norm_stale_editor_cannot_overwrite_and_history_retained(self):
        r = self.e.save_recipe({'name': 'First'})
        stale = copy.deepcopy(r)
        r['name'] = 'Changed'
        self.e.save_recipe(r)
        with self.assertRaisesRegex(Review, 'уже изменена'):
            self.e.save_recipe(stale)
        saved = self.e.recipes()[0]
        self.assertEqual(saved['name'], 'Changed')
        self.assertEqual(saved['history'][0]['version'], 1)

    def cache_client(self):
        class CatalogueMS:
            calls = 0
            fail = False
            def get(inner, path, **params):
                inner.calls += 1
                if inner.fail:
                    raise Review('temporary failure')
                kind = path.split('/')[-1]
                return {'meta': {'size': 1}, 'rows': [{'id': self.product,
                    'name': 'Cache item', 'meta': {'type': kind}, 'article': 'abc123',
                    'email': 'must-not-be-cached@example.org'}]}
        return CatalogueMS()

    def test_catalogue_persists_and_search_never_calls_live_api(self):
        m = self.cache_client()
        self.e.client = m
        self.e.refresh_catalogues_now()
        self.assertEqual(m.calls, 4)
        reloaded = Engine(self.tmp.name, m)
        self.assertEqual(len(reloaded.catalogue('assortment', 'ABC123')), 1)
        self.assertEqual(m.calls, 4)
        self.assertNotIn('email', reloaded.catalogue('counterparty')[0])
        self.assertTrue(reloaded.catalogue_snapshot()['ready'])

    def test_failed_catalogue_refresh_keeps_previous_generation(self):
        m = self.cache_client()
        self.e.client = m
        self.e.refresh_catalogues_now()
        before = self.e.catalogue_snapshot()['updated']
        m.fail = True
        with self.assertRaises(Review):
            self.e.refresh_catalogues_now()
        self.assertEqual(self.e.catalogue_snapshot()['updated'], before)
        self.assertEqual(len(self.e.catalogue('assortment')), 1)

    def test_catalogue_refresh_twenty_minutes_and_single_flight(self):
        import threading
        from unittest.mock import patch
        self.e.client = self.cache_client()
        self.e.refresh_catalogues_now()
        updated = self.e.catalogue_snapshot()['updated']
        with patch('production.time.time', return_value=updated+1199):
            self.assertFalse(self.e.start_catalogue_refresh())
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def refresh():
            entered.set()
            release.wait(2)
            finished.set()
        with patch.object(self.e, 'refresh_catalogues_now', refresh):
            with patch('production.time.time', return_value=updated+1201):
                self.assertTrue(self.e.start_catalogue_refresh())
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.e.start_catalogue_refresh(force=True))
                # An in-progress refresh must never block selection.
                self.assertEqual(len(self.e.catalogue('assortment')), 1)
            release.set()
            self.assertTrue(finished.wait(1))

    def test_catalogue_keeps_composition_from_product_for_variant(self):
        class AssortmentMS:
            def get(inner, path, **kwargs):
                product = {'id': self.product, 'name': 'Item', 'attributes': [
                    {'name': 'Состав', 'value': '100% шерсть'},
                    {'name': 'Служебное поле', 'value': 'не отображать'}]}
                row = {'id': self.material, 'name': 'Item (S)',
                    'meta': {'type': 'variant'}, 'product': product,
                    'characteristics': [{'name': 'размер', 'value': 'S'}]}
                return {'meta': {'size': 1}, 'rows': [row]}
        self.e.client = AssortmentMS()
        self.e.refresh_catalogues_now()
        row = self.e.catalogue('assortment')[0]
        self.assertEqual(row['composition'], '100% шерсть')
        self.assertEqual(row['parent_id'], self.product)
        self.assertNotIn('Служебное поле', str(row))

    def group_recipe(self, product, size, quantity):
        return {'name': 'Test '+size, 'model': 'Test', 'color': 'blue', 'size': size,
            'assortment_id': product, 'approved': False, 'zip_checked': True, 'editor_schema': 2,
            'stages': [{'factory': self.factory, 'work_cost': 0, 'materials': [
                {'id': self.material, 'name': 'Fabric', 'type': 'product', 'quantity': quantity},
                {'id': uid(), 'name': 'Zip', 'type': 'product', 'quantity': 1, 'role': 'packaging'}]}]}

    def test_group_applies_two_sizes_atomically(self):
        a = self.e.save_recipe(self.group_recipe(self.product, 'S', 2))
        b = self.e.save_recipe(self.group_recipe(uid(), 'M', 4))
        a['stages'][0]['materials'][0]['quantity'] = 3
        b['stages'][0]['materials'][0]['quantity'] = 6
        self.e.save_recipe_group([a, b])
        rows = {r['size']: r for r in self.e.recipes()}
        self.assertEqual(rows['S']['stages'][0]['materials'][0]['quantity'], 3)
        self.assertEqual(rows['M']['stages'][0]['materials'][0]['quantity'], 6)
        self.assertTrue(rows['S']['approved'] and rows['M']['approved'])

    def test_invalid_second_size_rolls_back_first(self):
        a = self.e.save_recipe(self.group_recipe(self.product, 'S', 2))
        b = self.e.save_recipe(self.group_recipe(uid(), 'M', 4))
        a['stages'][0]['materials'][0]['quantity'] = 3
        b['stages'][0]['materials'][0]['quantity'] = 0
        with self.assertRaisesRegex(Review, 'Размер M'):
            self.e.save_recipe_group([a, b])
        rows = {r['size']: r for r in self.e.recipes()}
        self.assertEqual(rows['S']['version'], 1)
        self.assertEqual(rows['S']['stages'][0]['materials'][0]['quantity'], 2)
        self.assertFalse(rows['S']['approved'])

    def test_stale_size_cannot_partially_apply_group(self):
        a = self.e.save_recipe(self.group_recipe(self.product, 'S', 2))
        b = self.e.save_recipe(self.group_recipe(uid(), 'M', 4))
        stale = copy.deepcopy(b)
        b['name'] = 'Updated elsewhere'
        self.e.save_recipe(b)
        with self.assertRaisesRegex(Review, 'уже изменена'):
            self.e.save_recipe_group([a, stale])
        rows = {r['size']: r for r in self.e.recipes()}
        self.assertFalse(rows['S']['approved'])
        self.assertEqual(rows['S']['version'], 1)

    def test_group_work_cost_propagates_without_approving_untouched_size(self):
        a = self.e.save_recipe(self.group_recipe(self.product, 'S', 2))
        b = self.e.save_recipe(self.group_recipe(uid(), 'M', 4))
        untouched = self.e.save_recipe(self.group_recipe(uid(), 'L', 5))
        a['stages'][0]['work_cost'] = 250
        b['stages'][0]['materials'][0]['quantity'] = 7
        self.e.save_recipe_group([b, a])
        rows = {r['size']: r for r in self.e.recipes()}
        self.assertTrue(rows['S']['approved'] and rows['M']['approved'])
        self.assertFalse(rows['L']['approved'])
        self.assertTrue(all(r['stages'][0]['work_cost'] == 250 for r in rows.values()))
        self.assertEqual(rows['M']['stages'][0]['materials'][0]['quantity'], 7)
        self.assertEqual(rows['L']['id'], untouched['id'])

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
            old_id = uid()
            self.ms.docs[kind][old_id] = {'id': old_id, 'name': '15'}
            value = uid()
            self.ms.docs[kind][value] = {'id': value, 'name': '999'}
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

    def setup_chain(self):
        from production import STATES
        fid1, fid2, agent, s2 = [uid() for _ in range(4)]
        cfg = {'enabled': False, 'company_store': self.company, 'states': {
            k: {label: uid() for label in labels} for k, labels in STATES.items()},
            'reason_attribute': uid(), 'reason_dictionary': uid(), 'reason_production': uid(),
            'factories': [{'id': fid1, 'agent': agent, 'store': self.factory},
                          {'id': fid2, 'agent': uid(), 'store': s2}]}
        self.e.set_config(cfg)
        self.e.save_recipe({'name': 'Изделие', 'assortment_id': self.product, 'approved': True,
            'zip_checked': True, 'stages': [
                {'factory': fid1, 'materials': [{'id': self.material, 'quantity': 2}]},
                {'factory': fid2, 'materials': [{'id': self.material, 'quantity': '.1'}]}]})
        supply_id, order_id = uid(), uid()
        self.ms.docs['supply'][supply_id] = {
            'id': supply_id, 'name': 'Поставка', 'moment': '2026-10-29 10:00:00', 'applicable': True,
            'organization': ref('organization', self.org), 'store': ref('store', self.company),
            'agent': ref('counterparty', agent), 'purchaseOrder': ref('purchaseorder', order_id),
            'positions': [{'assortment': ref('product', self.product), 'quantity': 10, 'price': 10000}]}
        return supply_id, cfg

    def test_full_two_stage_chain_with_partial_manual_return(self):
        supply_id, cfg = self.setup_chain()
        self.e.process_supply(supply_id)
        chain, number = self.e.chain(supply_id)
        oid, group = next(iter(chain['outbound'].items()))
        out = self.ms.docs['move'][oid]
        # Ручной возврат четырёх изделий. Материалы пошива списаны на все десять.
        rid = uid()
        self.ms.docs['move'][rid] = {'id': rid, 'name': out['name'] + '/01', 'applicable': True,
            'moment': '2026-10-29 11:00:00', 'sourceStore': out['targetStore'], 'targetStore': out['sourceStore'],
            'organization': out['organization'],
            'positions': [{'assortment': ref('product', self.product), 'quantity': 4}]}
        self.e.process_move(rid)
        chain, _ = self.e.chain(supply_id)
        self.assertFalse(chain['complete'])
        out['state'] = ref('move/metadata/states', cfg['states']['move']['Доработка принята'])
        self.e.process_move(oid)
        chain, _ = self.e.chain(supply_id)
        self.assertTrue(chain['complete'])
        returns = [d for kind, d in self.ms.created if kind == 'move' and d['name'].startswith(out['name'] + '/')]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]['positions'][0]['quantity'], 6)
        loss = [d for kind, d in self.ms.created if kind == 'loss']
        self.assertEqual(len(loss), 3)
        self.assertEqual(sum(dec['positions'][0]['quantity'] for dec in loss), 21)
        count = len(self.ms.created)
        self.e.process_move(oid)
        self.assertEqual(len(self.ms.created), count)

    def test_supply_change_after_consumption_blocks(self):
        value, _ = self.setup_chain()
        self.e.process_supply(value)
        self.ms.docs['supply'][value]['positions'][0]['quantity'] = 9
        with self.assertRaises(Review): self.e.process_supply(value)

    def test_unconfirmed_native_link_prevents_post(self):
        self.ms.request_original = self.ms.request
        def unlinked(method, path, body):
            if path.endswith('/new'):
                return {'positions': []}
            return self.ms.request_original(method, path, body)
        self.ms.request = unlinked
        with self.assertRaises(Review): self.e.execute('blocked', 'move', self.payload(), basis=uid())
        self.assertFalse(self.ms.created)


if __name__ == '__main__':
    unittest.main()
