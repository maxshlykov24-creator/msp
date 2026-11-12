import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from production import Engine, Review, ident, quantities, ref, remaining, fingerprint


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
        for row in self.docs[kind][value]['positions']:
            row.setdefault('id', uid())
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
        if '/positions/' in path:
            value, pid = path.split('/')[3], path.split('/')[-1]
            row = next(p for p in self.docs[kind][value]['positions'] if p['id'] == pid)
            row.update(copy.deepcopy(body))
            return copy.deepcopy(row)
        value = path.split('/')[-1]
        update = copy.deepcopy(body)
        if 'attributes' in update:
            attrs = {a['meta']['href']: a for a in self.docs[kind][value].get('attributes', [])}
            attrs.update({a['meta']['href']: a for a in update['attributes']})
            update['attributes'] = list(attrs.values())
        self.docs[kind][value].update(update)
        return copy.deepcopy(self.docs[kind][value])


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.pause = patch("production.time.sleep")
        self.pause.start()
        self.addCleanup(self.pause.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.ms = FakeMS()
        self.e = Engine(self.tmp.name, self.ms)
        self.product, self.material, self.company, self.factory, self.org = [uid() for _ in range(5)]
        self.ms.stock = {self.product: 100, self.material: 100}
        # Legacy scenarios now also have real-shaped catalogue/card fixtures.
        save = self.e.save_recipe
        def fixture_save(recipe, db=None, propagate=True, actor=None):
            key = recipe.get('assortment_id')
            if key and db is None:
                with self.e.db() as connection:
                    row = connection.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
                    items = json.loads(row['body']) if row else []
                    card = next((x for x in items if x['id'] == key), None)
                    if card is None:
                        card = dict(ref('product', key), id=key, name=recipe.get('assortment_name') or recipe.get('name', 'Test'),
                                    characteristics=[{'name': 'Размер', 'value': recipe.get('size', '')}])
                        items.append(card)
                        connection.execute("INSERT OR REPLACE INTO catalogues VALUES('assortment',0,?)", (json.dumps(items),))
                    self.ms.docs.setdefault(card['meta']['type'], {}).setdefault(key, copy.deepcopy(card))
            return save(recipe, db=db, propagate=propagate, actor=actor)
        self.e.save_recipe = fixture_save

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self):
        return {'name': 'МТ-П-1', 'organization': ref('organization', self.org), 'applicable': True,
                'sourceStore': ref('store', self.factory), 'targetStore': ref('store', self.company),
                'positions': [{'assortment': ref('product', self.material), 'quantity': 3}]}

    def test_duplicate_webhook_does_not_postpone_pending_job(self):
        self.e.set_config({'enabled': False, 'delay': 1})
        with patch('production.time.time', return_value=100):
            self.e.enqueue('supply', self.product)
        with patch('production.time.time', return_value=100.8):
            self.e.enqueue('supply', self.product)
        with self.e.db() as db:
            row = db.execute('SELECT due,status FROM jobs').fetchone()
            self.assertEqual(row['due'], 101)
            self.assertEqual(row['status'], 'pending')
            db.execute("UPDATE jobs SET status='done'")
        with patch('production.time.time', return_value=200):
            self.e.enqueue('supply', self.product)
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT due FROM jobs').fetchone()[0], 201)

    def test_analytics_cache_separates_stores_and_strips_query_from_id(self):
        calls = []
        def get(path, **params):
            calls.append(params)
            return {'meta': {'size': 1}, 'rows': [{'meta': {'href': 'https://example/entity/product/' + self.product + '?expand=supplier'},
                    'stock': 2, 'reserve': 1, 'price': 12345, 'salePrice': 20000, 'supplier': {'secret': 'private'}}]}
        self.ms.get = get
        first = self.e.analytics(self.company)
        self.assertEqual(first['rows'][0]['id'], self.product)
        self.assertNotIn('cost', first['rows'][0])
        self.assertNotIn('supplier', first['rows'][0])
        self.assertEqual(self.e.analytics(self.company), first)
        self.assertEqual(len(calls), 1)
        self.e.analytics(self.factory)
        self.assertEqual(len(calls), 2)
        self.assertIn(self.factory, calls[-1]['filter'])
        with self.assertRaises(Review):
            self.e.analytics('invalid-store')

    def test_analytics_incomplete_refresh_does_not_replace_cache(self):
        self.ms.get = lambda *a, **kw: {'meta': {'size': 0}, 'rows': []}
        first = self.e.analytics()
        self.ms.get = lambda *a, **kw: {'meta': {'size': 2}, 'rows': []}
        with self.assertRaises(Review):
            self.e.analytics(force=True)
        self.assertEqual(self.e.analytics(), first)

    def fifo_layer(self, stock, price, moment='2026-11-01 00:00:00', kind='supply'):
        return {'stock': stock, 'costPerUnit': price, 'moment': moment,
                'operation': ref(kind, uid()), 'store': ref('store', self.company)}

    def cost_recipe(self):
        return {'id': uid(), 'assortment_id': self.product, 'approved': True, 'zip_checked': True,
                'version': 1, 'stages': [
                    {'work_cost': 100, 'materials': [{'id': self.material, 'quantity': .3},
                     {'id': self.factory, 'quantity': 1, 'role': 'packaging'}]},
                    {'work_cost': 50, 'materials': [{'id': self.material, 'quantity': .2}]}]}

    def test_fifo_splits_old_and_new_layers_and_reprices_after_depletion(self):
        old = self.fifo_layer(.2, 10000)
        new = self.fifo_layer(1, 20000, '2026-11-02 00:00:00')
        total, trace = self.e.fifo_cost([new, old], '.5')
        self.assertEqual(total, Decimal('80'))
        self.assertEqual([r['quantity'] for r in trace], [.2, .3])
        self.assertEqual(self.e.fifo_cost([new], '.5')[0], Decimal('100'))

    def test_transferred_fifo_uses_report_cost_not_move_price(self):
        layer=self.fifo_layer(10,560,kind='move')
        for move_price in (0,99999900):
            layer['price']=move_price
            total,trace=self.e.fifo_cost([layer],2)
            self.assertEqual(total,Decimal('11.2'))
            self.assertEqual(trace[0]['operation_type'],'move')
        for cost in (None,0):
            layer['costPerUnit']=cost
            with self.assertRaises(Review):self.e.fifo_cost([layer],1)

    def test_fifo_unknown_price_origin_shortage_negative_are_not_zero(self):
        for rows in [[self.fifo_layer(1, None)], [self.fifo_layer(1, 100, kind='enter')],
                     [self.fifo_layer(.1, 100)], [self.fifo_layer(-1, 100)]]:
            with self.assertRaises(Review):
                self.e.fifo_cost(rows, 1)

    def test_production_cost_adds_all_work_and_packaging_once(self):
        recipe = self.cost_recipe()
        self.e.recipes = lambda: [recipe]
        calls = []
        def layers(key, kind, force):
            calls.append(key)
            return [self.fifo_layer(10, 10000 if key == self.material else 1000)]
        self.e.material_fifo_layers = layers
        result = self.e.production_costs()[self.product]
        self.assertEqual(result['cost'], 210)  # .5*100 + 100+50 + 1*10
        self.assertEqual(calls.count(self.material), 1)
        self.assertEqual(recipe['stages'][0]['materials'][0]['quantity'], .3)
        recipe['approved'] = False
        self.assertIsNone(self.e.production_costs()[self.product]['cost'])

    def test_opening_inventory_is_allowed_only_when_registered_and_unchanged(self):
        oid = uid()
        doc = {'id': oid, 'applicable': True, 'moment': '2026-10-31 23:59:00',
               'store': ref('store', self.factory), 'organization': ref('organization', self.org),
               'positions': [{'assortment': ref('product', self.material), 'quantity': 10, 'price': 560}]}
        self.ms.docs['enter'] = {oid: doc}
        layer = self.fifo_layer(10, 560, doc['moment'], 'enter')
        layer['operation'] = ref('enter', oid)
        self.assertEqual(self.e.verified_opening_balances([layer], {}), set())
        cfg = self.e.config()
        cfg['opening_balance_documents'] = {oid: fingerprint(doc, doc['positions'])}
        self.e.set_config(cfg)
        allowed = self.e.verified_opening_balances([layer], {})
        self.assertEqual(self.e.fifo_cost([layer], 2, allowed)[0], Decimal('11.2'))
        doc['positions'][0]['price'] = 600
        with self.assertRaisesRegex(Review, 'Начальные остатки'):
            self.e.verified_opening_balances([layer], {})

    def test_production_unknown_material_makes_whole_cost_unknown(self):
        recipe = self.cost_recipe()
        self.e.recipes = lambda: [recipe]
        self.e.material_fifo_layers = lambda *a: []
        result = self.e.production_costs()[self.product]
        self.assertIsNone(result['cost'])
        self.assertIn('FIFO', result['issue'])

    def test_fifo_cache_force_refresh_and_incomplete_preserves_previous(self):
        calls = []
        def get(*a, **kw):
            calls.append(kw)
            return {'meta': {'size': 1}, 'rows': [self.fifo_layer(1, 100)]}
        self.ms.get = get
        first = self.e.material_fifo_layers(self.material)
        self.assertEqual(self.e.material_fifo_layers(self.material), first)
        self.assertEqual(len(calls), 1)
        first = self.e.material_fifo_layers(self.material, force=True)
        self.assertEqual(len(calls), 2)
        self.ms.get = lambda *a, **kw: {'meta': {'size': 1}, 'rows': []}
        with self.assertRaises(Review):
            self.e.material_fifo_layers(self.material, force=True)
        self.assertEqual(self.e.material_fifo_layers(self.material), first)

    def test_stock_events_invalidate_fifo_cache_without_waiting_20_minutes(self):
        calls = []
        def report(*a, **kw):
            calls.append(kw)
            return {'meta': {'size': 1}, 'rows': [self.fifo_layer(100, len(calls)*100)]}
        self.ms.get = report
        self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], 100)
        for index, kind in enumerate(('supply','move','loss'), 2):
            self.e.enqueue(kind, uid())
            self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], index*100)
            self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], index*100)
        self.assertEqual(len(calls), 4)

    def test_event_during_fifo_fetch_cannot_cache_old_layers_as_fresh(self):
        calls=[]
        def report(*a, **kw):
            calls.append(kw)
            if len(calls)==1: self.e.enqueue('move', uid())
            return {'meta': {'size': 1}, 'rows': [self.fifo_layer(100, len(calls)*100)]}
        self.ms.get = report
        self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], 100)
        self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], 200)
        self.assertEqual(self.e.material_fifo_layers(self.material)[0]['costPerUnit'], 200)
        self.assertEqual(len(calls), 2)

    def test_own_writeoff_invalidates_cached_pre_consumption_layers(self):
        with self.e.db() as db:
            before=db.execute("SELECT value FROM kv WHERE key='fifo_generation'").fetchone()
        self.e.execute('own/loss', 'loss', {'organization':ref('organization',self.org),
            'store':ref('store',self.factory),'applicable':True,
            'positions':[{'assortment':ref('product',self.material),'quantity':1}]})
        with self.e.db() as db:
            generation=int(db.execute("SELECT value FROM kv WHERE key='fifo_generation'").fetchone()[0])
        self.assertEqual(generation, (int(before[0]) if before else 0)+1)
        self.e.execute('own/loss', 'loss', {})
        with self.e.db() as db:
            self.assertEqual(int(db.execute("SELECT value FROM kv WHERE key='fifo_generation'").fetchone()[0]),generation)

    def test_positive_fifo_batch_adds_work_once_and_ignores_finished_receipt_price(self):
        value,cfg=self.setup_direct()
        recipe=self.e.recipes()[0]
        recipe['stages'][0]['work_cost']=100
        recipe['stages'][1]['work_cost']=50
        self.e.save_recipe(recipe)
        self.ms.docs['supply'][value]['positions'][0]['price']=99999900
        a,b=cfg['factories'][0]['store'],cfg['factories'][1]['store']
        old=dict(self.fifo_layer(5,200,'2026-11-01 00:00:00'),store=ref('store',a))
        new=dict(self.fifo_layer(100,300,'2026-11-02 00:00:00'),store=ref('store',a))
        second=dict(self.fifo_layer(100,400),store=ref('store',b))
        self.e.material_fifo_layers=lambda *args: [old,new,second]
        plan=self.e.plan_supply(value);groups=self.e.direct_material_groups(plan)
        snapshot=self.e.snapshot_direct_cost(plan,groups)
        self.assertEqual(snapshot['total'],1559) # 10*150 work + (5*2+15*3) + 1*4
        self.assertEqual([r['quantity'] for r in snapshot['materials'][0]['layers']],[5,15])
        old['stock']=0
        self.assertEqual(self.e.snapshot_direct_cost(plan,groups)['total'],1564)

    def test_legacy_fifo_cache_migration_does_not_reuse_pre_event_layers(self):
        import sqlite3,time
        with tempfile.TemporaryDirectory() as folder:
            with sqlite3.connect(Path(folder)/'production.sqlite3') as db:
                db.execute('CREATE TABLE material_fifo_cache(id TEXT PRIMARY KEY,updated REAL,body TEXT NOT NULL)')
                db.execute('INSERT INTO material_fifo_cache VALUES(?,?,?)',(self.material,time.time(),json.dumps([self.fifo_layer(10,100)])))
            client=FakeMS()
            client.get=lambda *a,**kw: {'meta':{'size':1},'rows':[self.fifo_layer(10,200)]}
            migrated=Engine(folder,client)
            self.assertEqual(migrated.material_fifo_layers(self.material)[0]['costPerUnit'],200)

    def test_norm_display_units_convert_to_stock_units(self):
        r = self.e.save_recipe({'name': 'Test', 'stages': [{'materials': [
            {'unit': 'кг', 'input_unit': 'г', 'input_quantity': 250}]}]})
        self.assertEqual(r['stages'][0]['materials'][0]['quantity'], .25)
        r = self.e.save_recipe(r)
        self.assertEqual(r['stages'][0]['materials'][0]['quantity'], .25)

    def test_norm_rejects_incompatible_units(self):
        with self.assertRaisesRegex(Review, 'Этап 1: единица материала'):
            self.e.save_recipe({'approved': True, 'stages': [{'materials': [
                {'id': self.material, 'name': 'Ткань', 'unit': 'м', 'input_unit': 'г', 'input_quantity': 250}]}]})

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

    def test_apply_without_optional_packaging(self):
        recipe = self.group_recipe(self.product, 'S', 2)
        recipe.update(approved=True, zip_checked=False)
        recipe['stages'][0]['materials'] = recipe['stages'][0]['materials'][:1]
        self.e.save_recipe(recipe)
        self.assertTrue(self.e.recipes()[0]['approved'])
        self.e.material_fifo_layers = lambda *args: [self.fifo_layer(10, 10000)]
        self.assertEqual(self.e.production_costs()[self.product]['cost'], 200)

    def test_optional_packaging_still_rejects_duplicate_and_zero(self):
        recipe = self.group_recipe(self.product, 'S', 2)
        recipe['approved'] = True
        recipe['stages'][0]['materials'].append(dict(recipe['stages'][0]['materials'][-1]))
        with self.assertRaises(Review):
            self.e.save_recipe(recipe)
        recipe['stages'][0]['materials'].pop()
        recipe['stages'][0]['materials'][-1]['quantity'] = 0
        with self.assertRaises(Review):
            self.e.save_recipe(recipe)

    def test_history_records_changes_and_account_with_shared_work(self):
        a = self.group_recipe(self.product, 'S', 2)
        a['assortment_name'] = 'Карточка S'
        a = self.e.save_recipe(a, actor='Общий аккаунт')
        b = self.e.save_recipe(self.group_recipe(uid(), 'M', 4))
        a['stages'][0]['work_cost'] = 250
        a['stages'][0]['materials'][0]['quantity'] = 3
        self.e.save_recipe_group([a], actor='Общий аккаунт')
        rows = {r['size']: r for r in self.e.recipes()}
        events = rows['S']['history']
        self.assertEqual(events[0]['actor'], 'Общий аккаунт')
        self.assertTrue(any('работа, ₽: 0 → 250' in c for c in events[0]['changes']))
        self.assertTrue(any('Fabric: 2 шт → 3 шт' in c for c in events[0]['changes']) or
                        any('Fabric: 2 не указано → 3 не указано' in c for c in events[0]['changes']))
        self.assertTrue(any('Карточка S' in c for c in events[-1]['changes']))
        self.assertEqual(rows['M']['history'][0]['actor'], 'Общий аккаунт')
        self.assertTrue(any('работа, ₽: 0 → 250' in c for c in rows['M']['history'][0]['changes']))

    def test_factories_from_moysklad_group_and_store(self):
        factory_id, store_id, other_id = uid(), uid(), uid()
        self.e.set_config({'enabled': False, 'factories': [{'id': factory_id, 'name': 'Factory', 'agent': '', 'store': ''}]})
        rows = [{'id': self.product, 'name': 'Factory', 'tags': ['производство'], 'companyType': 'individual', 'production_store': store_id},
                {'id': other_id, 'name': 'Other', 'tags': [], 'companyType': 'legal', 'production_store': ''}]
        with self.e.db() as db:
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('counterparty', 1, json.dumps(rows)))
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('store', 1, json.dumps([{'id': store_id, 'name': 'Склад'}])))
        f = self.e.config()['factories']
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]['id'], f[0]['agent'], f[0]['store'], f[0]['store_ok']), (factory_id, self.product, store_id, True))
        cfg = self.e.config()
        cfg['factories'] = []
        self.e.set_config(cfg)
        self.assertEqual(self.e.config()['factories'][0]['id'], factory_id)

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

    def test_async_scenario_redirect_blocks_before_dependent_document(self):
        def redirect(_):
            doc = next(iter(self.ms.docs['move'].values()))
            doc['targetStore'] = ref('store', uid())
        with patch('production.time.sleep', side_effect=redirect):
            with self.assertRaisesRegex(Review, 'склад'):
                self.e.execute('scenario-race', 'move', self.payload())
        with self.e.db() as db:
            operation = db.execute("SELECT * FROM operations WHERE key='scenario-race'").fetchone()
        self.assertIsNotNone(operation['doc_id'])
        self.assertNotEqual(operation['status'], 'done')
        self.assertEqual(len(self.ms.created), 1)

    def test_recovery_checks_native_link(self):
        basis = uid()
        doc = self.e.execute('linked', 'move', self.payload(), basis=basis)
        self.ms.docs['move'][doc['id']].pop('supply')
        with self.assertRaisesRegex(Review, 'связь'):
            self.e.execute('linked', 'move', self.payload(), basis=basis)
        self.assertEqual(len(self.ms.created), 1)

    def test_disabled_worker_does_not_write(self):
        self.e.enqueue('move', uid())
        self.assertFalse(self.e.run_one())
        self.assertFalse(self.ms.created)

    def test_ordinary_number_series_independent(self):
        for kind in ('supply', 'move', 'loss'):
            old_id = uid()
            self.ms.docs[kind][old_id] = {'id': old_id, 'name': '15'}
            value = uid()
            self.ms.docs[kind][value] = {'id': value, 'name': '999'}
            self.e.number_other(kind, self.ms.docs[kind][value])
            self.assertEqual(self.ms.docs[kind][value]['name'], '16')
            self.e.number_other(kind, self.ms.docs[kind][value])
            with self.e.db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM names WHERE kind=?', (kind,)).fetchone()[0], 1)

    def test_ordinary_receipt_ignores_old_production_numeric_names(self):
        production, cfg = self.setup_direct()
        self.ms.docs['supply'][production]['name'] = '9999'
        old, new, tracked = uid(), uid(), uid()
        self.ms.docs['supply'][old] = {'id': old, 'name': '15'}
        self.ms.docs['supply'][tracked] = {'id': tracked, 'name': '8888'}
        with self.e.db() as db:
            db.execute('INSERT INTO receipt_numbers VALUES(?,?,?,?)', (tracked, uid(), 1, 'ПР-0042-01'))
        self.ms.docs['supply'][new] = {'id': new, 'name': '10000'}
        self.e.number_other('supply', self.ms.docs['supply'][new])
        self.assertEqual(self.ms.docs['supply'][new]['name'], '16')
        self.assertEqual(self.ms.docs['supply'][production]['name'], '9999')
        self.assertEqual(self.ms.docs['supply'][tracked]['name'], '8888')

    def test_unlinked_receipt_with_production_name_and_factory_uses_plain_number(self):
        _, cfg = self.setup_direct()
        value = uid()
        doc = {'id': value, 'name': 'ПР-0001-02', 'agent': ref('counterparty', cfg['factories'][0]['agent']),
               'applicable': False, 'positions': []}
        self.ms.docs['supply'][value] = doc
        self.assertFalse(self.e.is_production_document('supply', doc))
        self.e.number_other('supply', doc)
        self.assertEqual(doc['name'], '1')
        self.assertNotIn('state', doc)
        self.assertFalse(self.e.chain(value)[0])

    def test_tracked_receipt_remains_production_when_order_removed(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        doc = self.ms.docs['supply'][value]
        doc.pop('purchaseOrder')
        self.assertTrue(self.e.is_production_document('supply', doc))
        with self.assertRaisesRegex(Review, 'Производственная приёмка'):
            self.e.number_other('supply', doc)

    def test_project_is_added_to_generated_losses_and_existing_document(self):
        value, cfg = self.setup_direct()
        cfg['production_project'] = uid()
        self.e.set_config(cfg)
        self.e.process_supply(value)
        self.assertTrue(self.ms.created)
        for kind, doc in self.ms.created:
            self.assertEqual(ident(doc['project']), cfg['production_project'])
        doc = self.ms.docs['supply'][value]
        before = fingerprint(doc, self.ms.positions('supply', value))
        result = self.e.stamp_production_project('supply', doc)
        self.assertEqual(ident(result['project']), cfg['production_project'])
        self.assertEqual(fingerprint(result, self.ms.positions('supply', value)), before)

    def test_project_scope_requires_document_links_not_factory_stores(self):
        value, cfg = self.setup_direct()
        receipt = self.ms.docs['supply'][value]
        self.assertTrue(self.e.needs_production_project('supply', receipt))
        ordinary = dict(receipt, id=uid(), name='ПР-0001-02')
        ordinary.pop('purchaseOrder')
        self.assertFalse(self.e.needs_production_project('supply', ordinary))
        self.assertFalse(self.e.needs_production_project('move', {'id': uid(),
            'sourceStore': ref('store', cfg['factories'][0]['store'])}))
        self.assertFalse(self.e.needs_production_project('loss', {'id': uid(), 'store': ref('store', uid())}))

    def test_production_project_removed_from_unlinked_prefixed_loss(self):
        value, cfg = self.setup_direct()
        cfg['production_project'] = uid()
        self.e.set_config(cfg)
        loss = {'id': uid(), 'name': 'СП-0013-01-03', 'project': ref('project', cfg['production_project']),
                'store': ref('store', cfg['factories'][0]['store']), 'positions': [], 'description': 'Комментарий сотрудника',
                'applicable': True}
        self.ms.docs['loss'][loss['id']] = copy.deepcopy(loss)
        self.assertFalse(self.e.needs_production_project('loss', loss))
        saved = self.e.stamp_production_project('loss', loss)
        self.assertIsNone(saved['project'])
        self.assertEqual(saved['description'], loss['description'])
        self.assertEqual(fingerprint(loss, []), fingerprint(saved, []))
        self.e.stamp_production_project('loss', saved)
        self.assertFalse(self.ms.created)

    def test_linked_loss_gets_project_but_lookalike_name_does_not(self):
        value, cfg = self.setup_direct()
        cfg['production_project'] = uid()
        self.e.set_config(cfg)
        doc = {'id': uid(), 'name': '123', 'store': ref('store', self.company), 'attributes': [
            {'meta': {'href': 'https://example/attributes/' + cfg['loss_receipt_attribute']},
             'value': 'https://online.moysklad.ru/app/#supply/edit?id=' + value}]}
        self.assertTrue(self.e.needs_production_project('loss', doc))
        self.assertFalse(self.e.needs_production_project('loss', dict(doc, name='СП-0009-01-01', attributes=[])))
        self.assertFalse(self.e.needs_production_project('loss', dict(doc, attributes=[], externalCode='2my-prod-copy')))
        self.ms.docs['supply'][value].pop('purchaseOrder')
        self.assertFalse(self.e.needs_production_project('loss', doc))

    def test_unrelated_project_is_preserved_on_ordinary_loss(self):
        value, cfg = self.setup_direct()
        cfg['production_project'] = uid()
        self.e.set_config(cfg)
        project = uid()
        doc = {'id': uid(), 'name': '1', 'project': ref('project', project)}
        self.assertEqual(self.e.stamp_production_project('loss', doc), doc)

    def test_ordinary_loss_ignores_numeric_production_and_prefixes(self):
        old, prod, tracked, labeled, new = [uid() for _ in range(5)]
        self.ms.docs['loss'] = {old: {'id': old, 'name': '15'},
            prod: {'id': prod, 'name': '9000', 'externalCode': '2my-prod-old'},
            tracked: {'id': tracked, 'name': '8000'},
            labeled: {'id': labeled, 'name': 'СП-9999-01'}, new: {'id': new, 'name': '99999'}}
        with self.e.db() as db:
            db.execute("INSERT INTO operations(key,kind,doc_id,body,status) VALUES('old','loss',?,'{}','done')", (tracked,))
        self.e.number_other('loss', self.ms.docs['loss'][new])
        self.assertEqual(self.ms.docs['loss'][new]['name'], '16')

    def test_copied_loss_worker_assigns_next_number_and_preserves_stock(self):
        _, cfg = self.setup_direct()
        cfg.update(enabled=True, start_at='2026-11-01 00:00:00', production_project=uid())
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}): self.e.set_config(cfg)
        old, value = uid(), uid()
        self.ms.docs['loss'][old] = {'id': old, 'name': '134'}
        before = {'id': value, 'name': 'СП-0013-01-03', 'created': '2026-11-02 17:59:32',
                  'applicable': True, 'description': 'Комментарий сотрудника',
                  'store': ref('store', self.company),
                  'positions': [{'id': uid(), 'assortment': ref('product', self.product), 'quantity': 1, 'price': 2890000}]}
        self.ms.docs['loss'][value] = copy.deepcopy(before)
        self.e.enqueue('loss', value)
        with self.e.db() as db: db.execute('UPDATE jobs SET due=0')
        self.e.run_one()
        self.assertEqual(self.ms.docs['loss'][value], dict(before, name='135'))
        with self.e.db() as db:
            job=db.execute("SELECT status,error FROM jobs WHERE kind='loss' AND id=?", (value,)).fetchone()
            self.assertEqual((job['status'],job['error']), ('done',''))
        self.e.number_other('loss', self.ms.docs['loss'][value])
        self.assertEqual(self.ms.docs['loss'][value]['name'], '135')
        self.assertFalse(self.ms.created)

    def test_linked_manual_loss_keeps_production_number(self):
        value, cfg = self.setup_direct()
        loss={'id': uid(), 'name': 'СП-0009-01-01', 'attributes': [
            {'meta': {'href': 'https://example/attributes/' + cfg['loss_receipt_attribute']},
             'value': 'https://online.moysklad.ru/app/#supply/edit?id=' + value}]}
        self.assertTrue(self.e.is_production_document('loss',loss))
        with self.assertRaises(Review): self.e.number_other('loss',loss)

    def test_ordinary_supply_worker_changes_only_name_and_preserves_state(self):
        _, cfg = self.setup_direct()
        cfg.update(enabled=True, start_at='2026-11-01 00:00:00')
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}): self.e.set_config(cfg)
        old, value = uid(), uid()
        self.ms.docs['supply'][old] = {'id': old, 'name': '20'}
        before = {'id': value, 'name': '500', 'created': '2026-11-02 10:00:00',
                  'applicable': False, 'agent': ref('counterparty', uid()),
                  'description': 'Комментарий сотрудника', 'state': ref('supply/metadata/states', uid())}
        self.ms.docs['supply'][value] = copy.deepcopy(before)
        self.e.enqueue('supply', value)
        with self.e.db() as db: db.execute('UPDATE jobs SET due=0')
        self.e.run_one()
        self.assertEqual(self.ms.docs['supply'][value], dict(before, name='21'))
        self.assertFalse(self.ms.created)
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (value,)).fetchone()[0], 'done')

    def test_ordinary_number_survives_timeout_and_manual_renaming(self):
        old, value = uid(), uid()
        self.ms.docs['supply'][old] = {'id': old, 'name': '15'}
        self.ms.docs['supply'][value] = {'id': value, 'name': '999'}
        original = self.ms.request
        def timeout(method, path, body):
            original(method, path, body)
            raise Review('Нет подтверждения ответа')
        self.ms.request = timeout
        with self.assertRaises(Review): self.e.number_other('supply', copy.deepcopy(self.ms.docs['supply'][value]))
        self.ms.request = original
        self.e.number_other('supply', copy.deepcopy(self.ms.docs['supply'][value]))
        self.ms.docs['supply'][value]['name'] = '2000'
        self.e.number_other('supply', copy.deepcopy(self.ms.docs['supply'][value]))
        self.assertEqual(self.ms.docs['supply'][value]['name'], '16')
        with self.e.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM names WHERE kind='supply'").fetchone()[0], 1)

    def test_concurrent_ordinary_allocations_are_unique_and_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor
        old, a, b = uid(), uid(), uid()
        self.ms.docs['loss'][old] = {'id': old, 'name': '15'}
        self.ms.docs['loss'][a] = {'id': a, 'name': 'auto', 'created': '2026-11-02 10:00:00'}
        self.ms.docs['loss'][b] = {'id': b, 'name': 'auto', 'created': '2026-11-02 10:00:01'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda key: self.e.number_other('loss', copy.deepcopy(self.ms.docs['loss'][key])), [a, a, b, b]))
        self.assertEqual({self.ms.docs['loss'][x]['name'] for x in (a,b)}, {'16','17'})
        with self.e.db() as db: self.assertEqual(db.execute("SELECT count(*) FROM names WHERE kind='loss'").fetchone()[0], 2)

    def test_ordinary_counter_does_not_follow_moysklad_temporary_numbers(self):
        for kind in ('supply', 'loss'):
            old, a, b, future = [uid() for _ in range(4)]
            self.ms.docs[kind][old] = {'id': old, 'name': '15'}
            self.ms.docs[kind][a] = {'id': a, 'name': '9999'}
            self.e.number_other(kind, self.ms.docs[kind][a])
            self.ms.docs[kind][future] = {'id': future, 'name': '10000'}
            self.ms.docs[kind][b] = {'id': b, 'name': '10001'}
            self.e.number_other(kind, self.ms.docs[kind][b])
            self.assertEqual(self.ms.docs[kind][a]['name'], '16')
            self.assertEqual(self.ms.docs[kind][b]['name'], '17')

    def test_copied_production_prefix_without_basis_gets_ordinary_number(self):
        for kind, name in [('move', 'ВШ-100/01'), ('loss', 'СП-0013-01-03')]:
            doc = {'id': uid(), 'name': name}
            self.ms.docs[kind][doc['id']] = doc
            self.e.number_other(kind, copy.deepcopy(doc))
            self.assertEqual(doc['name'], '1')
            self.e.number_other(kind, copy.deepcopy(doc))
            self.assertEqual(doc['name'], '1')

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
        self.ms.docs['purchaseorder'][order_id] = {
            'id': order_id, 'name': '0009', 'applicable': True, 'agent': ref('counterparty', agent),
            'supplies': [ref('supply', supply_id)],
            'organization': ref('organization', self.org),
            'state': ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['В работе']),
            'positions': copy.deepcopy(self.ms.docs['supply'][supply_id]['positions'])}
        return supply_id, cfg

    def setup_direct(self):
        value, cfg = self.setup_chain()
        cfg['workflow'] = 'finished_receipt'
        cfg['loss_receipt_attribute'] = uid()
        self.e.set_config(cfg)
        self.ms.docs['supply'][value]['state'] = ref('supply/metadata/states', cfg['states']['supply']['Ожидает обработки'])
        fields = [{'id': uid(), 'meta': {'href': 'https://example/entity/supply/metadata/attributes/' + uid(),
                                       'type': 'attributemetadata'}} for _ in range(2)]
        for f in fields:
            f['id'] = f['meta']['href'].split('/')[-1]
        self.e.receipt_link_fields = lambda count=2: fields[:count]
        self.e.material_fifo_layers = lambda *args: [dict(self.fifo_layer(100, 560), store=ref('store', f['store']))
                                                    for f in cfg['factories']]
        return value, cfg

    def test_receipt_prices_written_once_manual_override_survives_order_check(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        row = self.ms.docs['supply'][value]['positions'][0]
        self.assertEqual(row['price'], 1176)
        row['price'] = 54321
        self.e.process_supply(value)
        self.e.process_order(ident(self.ms.docs['supply'][value]['purchaseOrder']))
        self.assertEqual(row['price'], 54321)
        self.assertEqual(len(self.ms.created), 2)

    def test_receipt_price_timeout_reconciles_without_second_write(self):
        value, cfg = self.setup_direct()
        original = self.ms.request
        calls = []
        def timeout(method, path, body):
            result = original(method, path, body)
            if '/positions/' in path:
                calls.append(path)
                raise Review('Неопределённый ответ')
            return result
        self.ms.request = timeout
        with self.assertRaisesRegex(Review, 'Неопределённый ответ'):
            self.e.process_supply(value)
        self.ms.request = original
        self.e.process_supply(value)
        chain, _ = self.e.chain(value)
        self.assertEqual(chain['cost_snapshot']['prices'][0]['status'], 'done')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.ms.created), 2)

    def test_two_receipt_rows_share_fifo_without_reusing_first_layer(self):
        value, cfg = self.setup_direct()
        recipe = self.e.recipes()[0]
        recipe['stages'] = [recipe['stages'][0]]
        recipe['stages'][0]['materials'][0]['quantity'] = 1
        recipe['stages'][0]['work_cost'] = 10
        self.e.save_recipe(recipe)
        self.ms.docs['supply'][value]['positions'] = [
            {'id': uid(), 'assortment': ref('product', self.product), 'quantity': 2, 'price': 0},
            {'id': uid(), 'assortment': ref('product', self.product), 'quantity': 3, 'price': 0}]
        store = cfg['factories'][0]['store']
        self.e.material_fifo_layers = lambda *args: [
            dict(self.fifo_layer(3, 200, '2026-01-01'), store=ref('store', store)),
            dict(self.fifo_layer(10, 500, '2026-02-01'), store=ref('store', store))]
        self.e.process_supply(value)
        rows = self.ms.docs['supply'][value]['positions']
        self.assertEqual([r['price'] for r in rows], [1200, 1400])
        chain, _ = self.e.chain(value)
        self.assertEqual(chain['cost_snapshot']['total'], 66)

    def test_unknown_fifo_does_not_write_zero_price(self):
        value, cfg = self.setup_direct()
        self.ms.docs['supply'][value]['state'] = ref('supply/metadata/states', cfg['states']['supply']['Завершена'])
        self.e.material_fifo_layers = lambda *args: []
        before = self.ms.docs['supply'][value]['positions'][0]['price']
        self.e.process_supply(value)
        self.assertEqual(self.ms.docs['supply'][value]['positions'][0]['price'], before)
        chain, _ = self.e.chain(value)
        self.assertIsNone(chain['cost_snapshot']['total'])
        self.assertIn('Себестоимость пока не рассчитана', self.ms.docs['supply'][value]['description'])

    def test_quantity_change_still_blocks_after_price_write(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        self.ms.docs['supply'][value]['positions'][0]['quantity'] += 1
        with self.assertRaisesRegex(Review, 'Приёмка изменена'):
            self.e.process_supply(value)

    def test_historical_chain_is_not_repriced(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        chain, number = self.e.chain(value)
        chain.pop('price_write_once')
        self.ms.docs['supply'][value]['positions'][0]['price'] = 45678
        chain['fingerprint'] = fingerprint(self.ms.docs['supply'][value], self.ms.positions('supply', value))
        self.e.save_chain(value, chain, number)
        self.e.process_supply(value)
        self.assertEqual(self.ms.docs['supply'][value]['positions'][0]['price'], 45678)

    def test_manual_restore_original_after_timeout_is_not_overwritten(self):
        value, cfg = self.setup_direct()
        original = self.ms.request
        before = self.ms.docs['supply'][value]['positions'][0]['price']
        def timeout(method, path, body):
            result = original(method, path, body)
            if '/positions/' in path:
                raise Review('Неопределённый ответ')
            return result
        self.ms.request = timeout
        with self.assertRaises(Review): self.e.process_supply(value)
        self.ms.request = original
        self.ms.docs['supply'][value]['positions'][0]['price'] = before
        with self.assertRaisesRegex(Review, 'Запись цены не подтверждена'):
            self.e.process_supply(value)
        self.assertEqual(self.ms.docs['supply'][value]['positions'][0]['price'], before)

    def test_discount_blocks_before_writeoffs(self):
        value, cfg = self.setup_direct()
        self.ms.docs['supply'][value]['positions'][0]['discount'] = 5
        with self.assertRaisesRegex(Review, 'Убери скидку'):
            self.e.process_supply(value)
        self.assertFalse(self.ms.created)

    def test_empty_material_row_identifies_stage(self):
        recipe = {'assortment_id': self.product, 'approved': True, 'stages': [
            {'factory': self.factory, 'materials': []},
            {'factory': self.factory, 'materials': [{'input_quantity': 0}]}]}
        with self.assertRaisesRegex(Review, 'Этап 2: выбери материал'):
            self.e.save_recipe(recipe)

    def test_work_only_receipt_completes_without_loss_and_costs_work(self):
        value, cfg = self.setup_direct()
        recipe = self.e.recipes()[0]
        for stage, cost in zip(recipe['stages'], [100, 50]):
            stage['materials'] = []
            stage['work_cost'] = cost
        self.e.save_recipe(recipe)
        self.assertEqual(self.e.production_costs()[self.product]['cost'], 150)
        self.e.process_supply(value)
        chain, _ = self.e.chain(value)
        self.assertTrue(chain['complete'])
        self.assertEqual(chain['cost_snapshot']['total'], 1500)
        self.assertEqual(chain['material_groups'], [])
        self.assertEqual(self.ms.created, [])
        self.assertEqual(self.ms.docs['supply'][value]['state'], ref('supply/metadata/states', cfg['states']['supply']['Завершена']))
        self.e.process_supply(value)
        self.assertEqual(self.ms.created, [])

    def test_material_free_stage_does_not_create_empty_loss(self):
        value, cfg = self.setup_direct()
        recipe = self.e.recipes()[0]
        recipe['stages'][0]['materials'] = []
        recipe['stages'][0]['work_cost'] = 100
        recipe['stages'][1]['work_cost'] = 50
        self.e.save_recipe(recipe)
        self.e.process_supply(value)
        chain, _ = self.e.chain(value)
        self.assertEqual(len(self.ms.created), 1)
        self.assertEqual(self.ms.created[0][1]['store'], ref('store', cfg['factories'][1]['store']))
        self.assertAlmostEqual(chain['cost_snapshot']['total'], 1505.6)

    def test_supplier_sync_uses_parent_card_and_first_factory(self):
        agent, variant = uid(), uid()
        self.e.set_config({'factories': [{'id': self.factory, 'agent': agent}]})
        catalogue = [{'id': variant, 'name': 'Размер', 'parent_id': self.product, 'meta': {'type': 'variant'}}]
        self.ms.docs['variant'] = {variant: copy.deepcopy(catalogue[0])}
        with self.e.db() as db:
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('assortment', 1, json.dumps(catalogue)))
        recipe = {'assortment_id': variant, 'name': 'Размер', 'stages': [
            {'factory': self.factory, 'work_cost': 100, 'materials': []}]}
        self.e.save_recipe_group([recipe])
        doc = {'supplier': None, 'name': 'Карточка', 'code': 'не менять'}
        calls = []
        self.ms.get = lambda path, **kw: copy.deepcopy(doc)
        def put(method, path, body):
            calls.append((method,path,copy.deepcopy(body)))
            doc.update(body)
        self.ms.request = put
        self.assertTrue(self.e.run_supplier_one())
        self.assertEqual(calls, [('PUT', '/entity/product/' + self.product, {'supplier': ref('counterparty', agent)})])
        self.assertEqual(doc['code'], 'не менять')
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM supplier_jobs').fetchone()[0], 'done')
        with self.e.db() as db:
            self.e.enqueue_supplier(db, self.product)
        self.e.run_supplier_one()
        self.assertEqual(len(calls), 1)

    def test_supplier_conflict_does_not_overwrite_card(self):
        agent1, agent2, factory2, v1, v2 = [uid() for _ in range(5)]
        self.e.set_config({'factories': [{'id': self.factory, 'agent': agent1}, {'id': factory2, 'agent': agent2}]})
        catalogue = [{'id': v, 'name': str(v), 'parent_id': self.product, 'meta': {'type': 'variant'}} for v in (v1,v2)]
        self.ms.docs['variant'] = {x['id']: copy.deepcopy(x) for x in catalogue}
        with self.e.db() as db:
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('assortment', 1, json.dumps(catalogue)))
        recipes = [{'assortment_id': v, 'name': str(v), 'stages': [{'factory': f, 'materials': []}]} for v,f in [(v1,self.factory),(v2,factory2)]]
        self.e.save_recipe_group(recipes)
        self.ms.request = lambda *args: self.fail('Конфликт не должен менять поставщика')
        self.e.run_supplier_one()
        with self.e.db() as db:
            row = db.execute('SELECT status,error FROM supplier_jobs').fetchone()
        self.assertEqual(row['status'], 'review')
        self.assertIn('разные фабрики', row['error'])

    def test_direct_receipt_two_losses_links_no_moves_idempotent(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        chain, _ = self.e.chain(value)
        self.assertTrue(chain['complete'])
        self.assertEqual(len(self.ms.created), 2)
        self.assertTrue(all(kind == 'loss' for kind, _ in self.ms.created))
        self.assertEqual({ident_doc['store']['meta']['href'] for _, ident_doc in self.ms.created},
                         {ref('store', f['store'])['meta']['href'] for f in cfg['factories']})
        self.assertEqual(len(self.ms.docs['supply'][value]['attributes']), 2)
        self.assertAlmostEqual(chain['cost_snapshot']['total'], 21 * 5.6)
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)
        self.e.process_order(chain['source']['purchaseOrder']['meta']['href'].split('/')[-1])

    def test_order_receipt_and_two_loss_numbers(self):
        value, _ = self.setup_direct()
        self.e.process_supply(value)
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'ПР-0009-01')
        self.assertEqual([d['name'] for _, d in self.ms.created], ['СП-0009-01-01', 'СП-0009-01-02'])
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)

    def test_single_loss_has_no_stage_suffix(self):
        value, _ = self.setup_direct()
        recipe = self.e.recipes()[0]
        recipe['stages'] = recipe['stages'][:1]
        self.e.save_recipe(recipe)
        self.e.process_supply(value)
        self.assertEqual([d['name'] for _, d in self.ms.created], ['СП-0009-01'])

    def test_receipt_order_not_webhook_order_and_restart(self):
        value, _ = self.setup_direct()
        second = uid()
        first = self.ms.docs['supply'][value]
        first['created'] = '2026-11-02 10:00:00'
        self.ms.docs['supply'][second] = dict(copy.deepcopy(first), id=second, created='2026-11-02 10:01:00')
        order = self.ms.docs['purchaseorder'][first['purchaseOrder']['meta']['href'].split('/')[-1]]
        order['supplies'].append(ref('supply', second))
        self.e.number_receipt(self.ms.docs['supply'][second])
        self.assertEqual(self.ms.docs['supply'][second]['name'], 'ПР-0009-02')
        restarted = Engine(self.tmp.name, self.ms)
        restarted.number_receipt(first)
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'ПР-0009-01')
        third = uid()
        self.ms.docs['supply'][third] = dict(copy.deepcopy(first), id=third)
        order['supplies'].append(ref('supply', third))
        restarted.number_receipt(self.ms.docs['supply'][third])
        self.assertEqual(self.ms.docs['supply'][third]['name'], 'ПР-0009-03')

    def test_number_assigned_even_if_materials_missing(self):
        value, _ = self.setup_direct()
        self.ms.stock = {}
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'ПР-0009-01')
        self.assertFalse(self.ms.created)

    def test_old_losses_are_renamed_without_reposting_or_changing_stock(self):
        value, _ = self.setup_direct()
        self.e.process_supply(value)
        previous = copy.deepcopy(self.ms.docs['loss'])
        for doc in self.ms.docs['loss'].values():
            doc['name'] = 'СП-3-1'
        self.e.process_supply(value)
        self.assertEqual(self.ms.docs['loss'], previous)
        self.assertEqual(len(self.ms.created), 2)

    def test_concurrent_receipt_numbers_are_unique(self):
        from concurrent.futures import ThreadPoolExecutor
        value, _ = self.setup_direct()
        first = self.ms.docs['supply'][value]
        ids = [value] + [uid() for _ in range(3)]
        order_id = first['purchaseOrder']['meta']['href'].split('/')[-1]
        for i, key in enumerate(ids):
            self.ms.docs['supply'][key] = dict(copy.deepcopy(first), id=key, created=f'2026-11-02 10:00:0{i}')
        self.ms.docs['purchaseorder'][order_id]['supplies'] = [ref('supply', key) for key in ids]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda key: self.e.number_receipt(copy.deepcopy(self.ms.docs['supply'][key])), reversed(ids)))
        self.assertEqual([self.ms.docs['supply'][key]['name'] for key in ids],
                         ['ПР-0009-01', 'ПР-0009-02', 'ПР-0009-03', 'ПР-0009-04'])

    def test_number_timeout_recovers_the_same_reservation(self):
        value, _ = self.setup_direct()
        original = self.ms.request
        first = [True]
        def timeout_after_put(method, path, body):
            result = original(method, path, body)
            if first[0] and method == 'PUT' and path == '/entity/supply/' + value:
                first[0] = False
                raise Review('Нет подтверждения ответа')
            return result
        self.ms.request = timeout_after_put
        with self.assertRaises(Review):
            self.e.number_receipt(copy.deepcopy(self.ms.docs['supply'][value]))
        self.e.number_receipt(copy.deepcopy(self.ms.docs['supply'][value]))
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'ПР-0009-01')
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM receipt_numbers').fetchone()[0], 1)

    def test_number_does_not_follow_changed_order_silently(self):
        value, _ = self.setup_direct()
        self.e.number_receipt(copy.deepcopy(self.ms.docs['supply'][value]))
        doc = self.ms.docs['supply'][value]
        doc['purchaseOrder'] = ref('purchaseorder', uid())
        with self.assertRaisesRegex(Review, 'Заказ приёмки изменён'):
            self.e.number_receipt(copy.deepcopy(doc))
        self.assertEqual(doc['name'], 'ПР-0009-01')

    def test_numbering_unposted_old_documents_does_not_repost_them(self):
        value, _ = self.setup_direct()
        self.e.process_supply(value)
        self.ms.docs['supply'][value]['applicable'] = False
        for doc in self.ms.docs['loss'].values():
            doc.update(name='СП-3-1', applicable=False)
        with self.e.db() as db:
            for row in db.execute('SELECT key,body FROM operations').fetchall():
                body = json.loads(row['body']); body['name'] = 'СП-3-1'
                db.execute('UPDATE operations SET body=? WHERE key=?', (json.dumps(body), row['key']))
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(sorted(d['name'] for d in self.ms.docs['loss'].values()), ['СП-0009-01-01', 'СП-0009-01-02'])
        self.assertTrue(all(not d['applicable'] for d in self.ms.docs['loss'].values()))
        self.assertEqual(len(self.ms.created), 2)

    def test_deleted_document_is_not_renamed(self):
        value, _ = self.setup_direct()
        doc = copy.deepcopy(self.ms.docs['supply'][value])
        doc['deleted'] = '2026-11-02 10:00:00'
        with self.assertRaisesRegex(Review, 'находится в корзине'):
            self.e.rename_document('supply', doc, 'ПР-0009-01')
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'Поставка')

    def test_worker_ignores_deleted_production_receipt(self):
        value, cfg = self.setup_direct()
        cfg.update(enabled=True, start_at='2026-11-01 00:00:00')
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}):
            self.e.set_config(cfg)
        self.ms.docs['supply'][value].update(deleted='2026-11-02 10:00:00',created='2026-11-02 09:00:00')
        self.e.enqueue('supply', value)
        with self.e.db() as db: db.execute('UPDATE jobs SET due=0')
        self.e.run_one()
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (value,)).fetchone()[0], 'ignored')
        self.assertFalse(self.ms.created)
        self.assertEqual(self.ms.docs['supply'][value]['name'], 'Поставка')

    def test_chain_numbers_skip_existing_moysklad_production_losses(self):
        loss = uid()
        self.ms.docs['loss'][loss] = {'id': loss, 'name': 'СП-27-1'}
        self.assertEqual(self.e.save_chain(uid(), {}), 28)

    def test_completed_receipt_allows_shortage_without_faking_cost(self):
        value, cfg = self.setup_direct()
        self.ms.stock = {}
        self.e.material_fifo_layers = lambda *args: []
        self.ms.docs['supply'][value]['state'] = ref('supply/metadata/states', cfg['states']['supply']['Завершена'])
        self.e.process_supply(value)
        chain, _ = self.e.chain(value)
        self.assertTrue(chain['complete'])
        self.assertTrue(chain['allow_negative'])
        self.assertIsNone(chain['cost_snapshot']['total'])
        self.assertEqual(len(self.ms.created), 2)
        comment = self.ms.docs['supply'][value]['description']
        self.assertIn('Материалы списаны в минус.', comment)
        self.assertIn('Себестоимость пока не рассчитана.', comment)
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)
        self.assertEqual(self.ms.docs['supply'][value]['description'], comment)

    def test_force_does_not_bypass_changed_or_unposted_loss(self):
        value, cfg = self.setup_direct()
        self.ms.docs['supply'][value]['state'] = ref('supply/metadata/states', cfg['states']['supply']['Завершена'])
        self.e.process_supply(value)
        loss = self.ms.created[0][1]
        self.ms.docs['loss'][loss['id']]['applicable'] = False
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)

    def test_force_does_not_bypass_missing_material_price(self):
        value, cfg = self.setup_direct()
        self.ms.docs['supply'][value]['state'] = ref('supply/metadata/states', cfg['states']['supply']['Завершена'])
        self.e.material_fifo_layers = lambda *args: [dict(self.fifo_layer(100, None), store=ref('store', f['store']))
                                                     for f in cfg['factories']]
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(self.ms.created, [])

    def test_stock_message_names_material_store_unit_and_shortage(self):
        with self.e.db() as db:
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('assortment', 1, json.dumps([
                {'id': self.material, 'name': 'Нить голубая', 'uom': {'name': 'г'}}])))
            db.execute('INSERT INTO catalogues VALUES(?,?,?)', ('store', 1, json.dumps([
                {'id': self.factory, 'name': 'Производство | Фабрика'}])))
        self.ms.stock[self.material] = 2
        problems = self.e.stock_shortages(self.factory, [{'assortment': ref('product', self.material), 'quantity': 5}])
        self.assertEqual(problems, ['Нить голубая — 3 г (Фабрика)'])

    def test_receipt_problem_comment_preserves_employee_text_no_duplicates(self):
        value, _ = self.setup_direct()
        self.ms.docs['supply'][value]['description'] = 'Комментарий сотрудника'
        self.e.mark_chain_review('supply', value, 'Нить голубая: не хватает 3 г')
        first = self.ms.docs['supply'][value]['description']
        self.e.mark_chain_review('supply', value, 'Нить голубая: не хватает 3 г')
        self.assertEqual(self.ms.docs['supply'][value]['description'], first)
        self.assertIn('Комментарий сотрудника', first)
        self.e.receipt_note(value, 'Нить голубая: не хватает 1 г')
        self.assertNotIn('не хватает 3 г', self.ms.docs['supply'][value]['description'])
        self.e.receipt_note(value)
        self.assertEqual(self.ms.docs['supply'][value]['description'], 'Комментарий сотрудника')

    def test_partial_direct_processing_publishes_first_link_and_recovers(self):
        value, _ = self.setup_direct()
        original = self.ms.request
        count = 0
        def fail_second(method, path, body):
            nonlocal count
            if method == 'POST' and path == '/entity/loss':
                count += 1
                if count == 2:
                    raise Review('Не удалось создать второе списание')
            return original(method, path, body)
        self.ms.request = fail_second
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(len(self.ms.docs['supply'][value]['attributes']), 1)
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)
        self.assertEqual(len(self.ms.docs['supply'][value]['attributes']), 2)

    def test_review_reopens_completed_order_and_preserves_no_duplicate_losses(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        order_id = self.ms.docs['supply'][value]['purchaseOrder']['meta']['href'].split('/')[-1]
        self.e.process_order(order_id)
        self.assertEqual(self.ms.docs['purchaseorder'][order_id]['state'],
                         ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['Завершён']))
        loss_id = self.ms.created[0][1]['id']
        self.ms.docs['loss'][loss_id]['applicable'] = False
        self.e.mark_chain_review('loss', loss_id, 'Списание распроведено')
        self.assertFalse(self.e.chain(value)[0]['complete'])
        self.assertEqual(self.ms.docs['purchaseorder'][order_id]['state'],
                         ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['В работе']))
        self.ms.docs['loss'][loss_id]['applicable'] = True
        self.e.process_supply(value)
        self.e.process_order(order_id)
        self.assertTrue(self.e.chain(value)[0]['complete'])
        self.assertEqual(len(self.ms.created), 2)

    def test_loss_receipt_field_is_clickable_without_generated_comment(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        for _, loss in self.ms.created:
            saved = self.ms.docs['loss'][loss['id']]
            fields = {a['meta']['href'].rsplit('/', 1)[-1]: a['value'] for a in saved['attributes']}
            self.assertEqual(fields[cfg['loss_receipt_attribute']], 'https://online.moysklad.ru/app/#supply/edit?id=' + value)
            self.assertFalse(saved.get('description'))
            self.assertIn(cfg['reason_attribute'], fields)

    def test_existing_generated_loss_comment_preserves_employee_note(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        loss = self.ms.created[0][1]
        saved = self.ms.docs['loss'][loss['id']]
        saved['attributes'] = [a for a in saved['attributes'] if a['meta']['href'].rsplit('/', 1)[-1] != cfg['loss_receipt_attribute']]
        saved['description'] = ('Готовая продукция; приёмка №1; этапы 1; '
            'https://online.moysklad.ru/app/#supply/edit?id=' + value + '\nТекст сотрудника')
        self.e.loss_receipt_link(loss['id'], value)
        self.assertEqual(self.ms.docs['loss'][loss['id']]['description'], 'Текст сотрудника')
        self.e.loss_receipt_link(loss['id'], value)
        self.assertEqual(self.ms.docs['loss'][loss['id']]['description'], 'Текст сотрудника')

    def test_different_existing_receipt_link_is_not_overwritten(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        loss = self.ms.created[0][1]
        fields = self.ms.docs['loss'][loss['id']]['attributes']
        target = next(a for a in fields if a['meta']['href'].endswith(cfg['loss_receipt_attribute']))
        target['value'] = 'https://online.moysklad.ru/app/#supply/edit?id=' + uid()
        with self.assertRaises(Review):
            self.e.loss_receipt_link(loss['id'], value)
        self.assertNotEqual(target['value'], 'https://online.moysklad.ru/app/#supply/edit?id=' + value)

    def test_direct_analytics_uses_each_factory_fifo(self):
        value, cfg = self.setup_direct()
        recipe = self.e.recipes()[0]
        for stage in recipe['stages']:
            stage['work_cost'] = 0
        self.e.recipes = lambda: [recipe]
        self.e.material_fifo_layers = lambda *args: [
            dict(self.fifo_layer(100, 100), store=ref('store', cfg['factories'][0]['store'])),
            dict(self.fifo_layer(100, 1000), store=ref('store', cfg['factories'][1]['store']))]
        self.assertEqual(self.e.production_costs()[self.product]['cost'], 3)

    def test_direct_all_materials_preflight_before_writes(self):
        value, _ = self.setup_direct()
        self.e.material_fifo_layers = lambda *args: []
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(self.ms.created, [])
        self.assertIsNone(self.e.chain(value)[0])

    def test_direct_recovers_after_unknown_create_response(self):
        value, _ = self.setup_direct()
        self.ms.timeout_after_create = True
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)
        self.assertTrue(self.e.chain(value)[0]['complete'])

    def test_direct_unposted_loss_and_changed_receipt_block_completion(self):
        value, cfg = self.setup_direct()
        self.e.process_supply(value)
        loss = self.ms.created[0][1]
        self.ms.docs['loss'][loss['id']]['applicable'] = False
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.e.mark_chain_review('loss', loss['id'])
        self.assertEqual(self.ms.docs['supply'][value]['state'],
                         ref('supply/metadata/states', cfg['states']['supply']['Требует проверки']))
        self.ms.docs['supply'][value]['positions'][0]['quantity'] = 9
        with self.assertRaises(Review):
            self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 2)

    def test_direct_groups_stages_on_same_warehouse(self):
        value, cfg = self.setup_direct()
        cfg['factories'][1]['store'] = cfg['factories'][0]['store']
        # set_config deliberately preserves persisted factory mappings.
        with self.e.db() as db:
            db.execute("UPDATE kv SET value=? WHERE key='config'", (json.dumps(cfg),))
        self.e.process_supply(value)
        self.assertEqual(len(self.ms.created), 1)
        self.assertEqual(self.ms.created[0][1]['positions'][0]['quantity'], 21)

    def test_cancelled_or_unconducted_order_never_reopens(self):
        value, cfg = self.setup_chain()
        for cancelled in (True, False):
            oid = self.ms.docs['supply'][value]['purchaseOrder']['meta']['href'].rsplit('/', 1)[-1]
            order = self.ms.docs['purchaseorder'][oid]
            order['state'] = ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['В работе'])
            order['applicable'] = True
            if cancelled:
                order['state'] = ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['Отменён'])
            else:
                order['applicable'] = False
            before = copy.deepcopy(order)
            with self.assertRaises(Review): self.e.process_supply(value)
            with self.assertRaises(Review): self.e.process_order(order['id'])
            self.assertEqual(order, before)
            self.assertFalse(self.ms.created)

    def test_changed_order_factory_blocks_receipt(self):
        value, _ = self.setup_chain()
        oid = self.ms.docs['supply'][value]['purchaseOrder']['meta']['href'].rsplit('/', 1)[-1]
        self.ms.docs['purchaseorder'][oid]['agent'] = ref('counterparty', uid())
        with self.assertRaisesRegex(Review, 'разные организацию/фабрику'):
            self.e.process_supply(value)
        self.assertFalse(self.ms.created)

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
        previous_returns = copy.deepcopy(chain['outbound'])
        self.e.process_supply(supply_id)
        recovered, _ = self.e.chain(supply_id)
        self.assertTrue(recovered['complete'])
        self.assertEqual(recovered['outbound'], previous_returns)
        self.assertEqual(len(self.ms.created), count)

    def test_outbound_warehouse_edit_blocks_return(self):
        sid, cfg = self.setup_chain()
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        self.ms.docs['move'][oid]['targetStore'] = ref('store', uid())
        before = len(self.ms.created)
        with self.assertRaisesRegex(Review, 'склад'):
            self.e.process_move(oid)
        self.assertEqual(before, len(self.ms.created))

    def test_order_does_not_close_after_receipt_quantity_edit(self):
        sid, cfg = self.setup_chain()
        self.e.process_supply(sid)
        self.ms.docs['supply'][sid]['positions'][0]['quantity'] = 1
        oid = self.ms.docs['supply'][sid]['purchaseOrder']['meta']['href'].rsplit('/', 1)[-1]
        with self.assertRaisesRegex(Review, 'Приёмка изменена'):
            self.e.process_order(oid)

    def test_duplicate_receipt_detects_unposted_finished_return(self):
        sid, cfg = self.setup_chain()
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        self.ms.docs['move'][oid]['state'] = ref('move/metadata/states', cfg['states']['move']['Доработка принята'])
        self.e.process_move(oid)
        chain, _ = self.e.chain(sid)
        rid = next(iter(chain['outbound'][oid]['returns']))
        self.ms.docs['move'][rid]['applicable'] = False
        with self.assertRaisesRegex(Review, 'возврат'):
            self.e.process_supply(sid)

    def test_manual_return_legacy_redirect_requires_audit_proof(self):
        sid, cfg = self.setup_chain()
        cfg.update(operation_dictionary=uid(), operation_attributes={'move': uid()},
                   operation_values={name: uid() for name in ['Материалы на производство', 'Изделия на доработку',
                                                              'Возврат с доработки', 'Расход материалов']})
        self.e.set_config(cfg)
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        out = self.ms.docs['move'][oid]
        rid, transit = uid(), uid()
        manual = {'id': rid, 'name': out['name']+'/01', 'applicable': True,
                  'moment': '2026-10-29 11:00:00', 'organization': out['organization'],
                  'sourceStore': out['targetStore'], 'targetStore': ref('store', transit),
                  'attributes': [{'name': 'Склад получатель', 'meta': {'href': '/attr/'+uid()},
                                  'value': out['sourceStore']}],
                  'positions': [{'assortment': ref('product', self.product), 'quantity': 4}]}
        self.ms.docs['move'][rid] = manual
        history = [{'moment': '2026-10-29 11:00:01', 'source': 'scriptor', 'diff': {
            'targetStore': {'oldValue': out['sourceStore'], 'newValue': dict(ref('store', transit), name='В пути')},
            'Склад получатель': {'newValue': out['sourceStore']}}}]
        original = self.ms.rows
        self.ms.rows = lambda path, **kw: copy.deepcopy(history) if path.endswith('/audit') else original(path, **kw)
        history[0]['source'] = 'web'
        with self.assertRaisesRegex(Review, 'направление'):
            self.e.process_move(rid)
        self.assertEqual(manual['targetStore'], ref('store', transit))
        history[0]['source'] = 'scriptor'
        self.e.process_move(rid)
        self.assertEqual(manual['targetStore'], out['sourceStore'])
        self.assertIn(rid, self.e.chain(sid)[0]['outbound'][oid]['returns'])

    def test_order_closes_from_native_supplies_without_unsupported_filter(self):
        sid, cfg = self.setup_chain()
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        self.ms.docs['move'][oid]['state'] = ref('move/metadata/states', cfg['states']['move']['Доработка принята'])
        self.e.process_move(oid)
        order_id = self.ms.docs['supply'][sid]['purchaseOrder']['meta']['href'].rsplit('/', 1)[-1]
        original = self.ms.rows
        def rows(path, **params):
            self.assertFalse(params.get('filter', '').startswith('purchaseOrder='))
            return original(path, **params)
        self.ms.rows = rows
        self.e.process_order(order_id)
        self.assertEqual(self.ms.docs['purchaseorder'][order_id]['state'],
                         ref('purchaseorder/metadata/states', cfg['states']['purchaseorder']['Завершён']))

    def test_event_during_processing_remains_pending(self):
        sid, cfg = self.setup_chain()
        cfg.update(enabled=True, start_at='2026-10-01 00:00:00', delay=1)
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}):
            self.e.set_config(cfg)
        self.ms.docs['supply'][sid]['created'] = '2026-11-02 10:00:00'
        self.e.enqueue('supply', sid)
        with self.e.db() as db:
            db.execute('UPDATE jobs SET due=0')
        self.e.process_supply = lambda value: self.e.enqueue('supply', value)
        self.assertTrue(self.e.run_one())
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (sid,)).fetchone()[0], 'pending')

    def test_new_event_during_failed_processing_is_not_lost(self):
        sid, cfg = self.setup_chain()
        cfg.update(enabled=True, start_at='2026-10-01 00:00:00', delay=1)
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}):
            self.e.set_config(cfg)
        self.ms.docs['supply'][sid]['created'] = '2026-11-02 10:00:00'
        self.e.enqueue('supply', sid)
        with self.e.db() as db:
            db.execute('UPDATE jobs SET due=0')
        def changed_during_failure(value):
            self.e.enqueue('supply', value)
            raise Review('Ошибка предыдущего события')
        self.e.process_supply = changed_during_failure
        self.assertTrue(self.e.run_one())
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (sid,)).fetchone()[0], 'pending')

    def test_worker_ignores_prelaunch_documents(self):
        sid, cfg = self.setup_chain()
        cfg.update(enabled=True, start_at='2026-11-01 00:00:00')
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}):
            self.e.set_config(cfg)
        self.ms.docs['supply'][sid]['created'] = '2026-10-31 23:59:59'
        self.e.enqueue('supply', sid)
        with self.e.db() as db:
            db.execute('UPDATE jobs SET due=0')
        self.e.run_one()
        with self.e.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0], 'ignored')
        self.assertFalse(self.ms.created)

    def test_automatic_return_fingerprint_uses_api_positions(self):
        sid, cfg = self.setup_chain()
        original = self.ms.request
        def api_numbers(method, path, body):
            result = original(method, path, body)
            if method == 'POST':
                kind = path.split('/')[2]
                for line in self.ms.docs[kind][result['id']]['positions']:
                    line['price'] = float(line.get('price', 0))
                result = copy.deepcopy(self.ms.docs[kind][result['id']])
            return result
        self.ms.request = api_numbers
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        self.ms.docs['move'][oid]['state'] = ref('move/metadata/states', cfg['states']['move']['Доработка принята'])
        self.e.process_move(oid)
        self.e.process_move(oid)
        self.e.process_supply(sid)
        self.assertTrue(self.e.chain(sid)[0]['complete'])

    def test_worker_marks_receipt_for_review_after_outbound_edit(self):
        sid, cfg = self.setup_chain()
        self.e.process_supply(sid)
        chain, _ = self.e.chain(sid)
        oid = next(iter(chain['outbound']))
        self.ms.docs['move'][oid].update(created='2026-11-01 12:00:00', targetStore=ref('store', uid()))
        cfg.update(enabled=True, start_at='2026-11-01 00:00:00')
        with patch.dict('os.environ', {'PRODUCTION_ALLOW_WRITES': '1'}):
            self.e.set_config(cfg)
        self.e.enqueue('move', oid)
        with self.e.db() as db:
            db.execute("UPDATE jobs SET due=0 WHERE kind='move'")
        self.e.run_one()
        self.assertEqual(self.ms.docs['supply'][sid]['state'],
                         ref('supply/metadata/states', cfg['states']['supply']['Требует проверки']))

    def test_csv_materials_share_card_across_stores_and_keep_unknowns(self):
        from production_setup import read_material_csv
        source = Path(self.tmp.name) / 'materials.csv'
        source.write_text('артикул производителя,цвет,состав,единица измерения,Контрагент,категория,склад,"цена,руб",остаток на 31.10.2026\n'
                          'Пряжа HA5001,Молочный,шерсть,грамм,Поставщик,пряжа,Фабрика,"5,6",10\n'
                          'Пряжа HA5001,молочный,шерсть,грамм,Поставщик,пряжа,Компания,,\n')
        items = read_material_csv(source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['rows'], [2, 3])
        self.assertEqual(items[0]['unit'], 'г')
        self.assertEqual(items[0]['locations'][0]['price'], '5.6')
        self.assertIsNone(items[0]['locations'][1]['quantity'])
        self.assertIsNone(items[0]['locations'][1]['price'])

    def test_inventory_alignment_moves_existing_stock_before_opening(self):
        from production_setup import plan_balance_alignment
        plan = plan_balance_alignment({'old': 9575}, [{'store': 'moscow', 'quantity': 4980, 'price': '5.6'}])
        self.assertEqual(plan['moves'], [{'source': 'old', 'target': 'moscow', 'quantity': '4980'}])
        self.assertEqual(plan['losses'], [{'store': 'old', 'quantity': '4595'}])
        self.assertEqual(plan['enters'], [])
        plan = plan_balance_alignment({'old': 2087}, [{'store': 'factory', 'quantity': 12000, 'price': '7.6'}])
        self.assertEqual(plan['enters'], [{'store': 'factory', 'quantity': '9913', 'price': '7.6'}])
        with self.assertRaises(Review):
            plan_balance_alignment({}, [{'store': 'moscow', 'quantity': None, 'price': 1}])
        with self.assertRaises(Review):
            plan_balance_alignment({'old': -1}, [])

    def test_material_matching_requires_sku_and_color(self):
        from production_setup import material_candidates
        existing = [{'name': 'Пряжа Альпака HA5001 молочный', 'meta': {'type': 'product'}},
                    {'name': 'Пряжа Альпака HA5002 молочный', 'meta': {'type': 'product'}}]
        item = {'source_name': 'Альпака HA5001 1/5 5NM молочный', 'color': 'Молочный'}
        self.assertEqual(material_candidates(item, existing), existing[:1])
        item['color'] = 'Черный'
        self.assertEqual(material_candidates(item, existing), [])

    def test_wrong_receipt_store_blocks_before_any_document(self):
        value, _ = self.setup_chain()
        self.ms.docs['supply'][value]['store'] = ref('store', uid())
        with self.assertRaisesRegex(Review, 'Склад Москва'):
            self.e.process_supply(value)
        self.assertFalse(self.ms.created)
        self.assertEqual(self.e.chain(value), (None, None))

    def test_material_move_and_loss_have_distinct_operations(self):
        cfg = self.e.config()
        labels = ['Материалы на производство', 'Расход материалов']
        cfg.update(operation_dictionary=uid(), operation_values={s: uid() for s in labels},
                   operation_attributes={'move': uid(), 'loss': uid()})
        self.e.set_config(cfg)
        for kind, key, label in [('move', 'batch/materials', labels[0]), ('loss', 'batch/loss', labels[1])]:
            payload = self.payload()
            if kind == 'loss':
                payload['store'] = payload.pop('sourceStore')
                payload.pop('targetStore')
            doc = self.e.execute(key, kind, payload)
            self.assertEqual(doc['attributes'][0]['value'],
                ref('customentity/' + cfg['operation_dictionary'], cfg['operation_values'][label]))

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

class SizeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ms = FakeMS()
        self.e = Engine(self.tmp.name, self.ms)
        self.key, self.material, self.trim, self.factory = [uid() for _ in range(4)]
        self.card = dict(ref('variant', self.key), id=self.key, name='Топ S-M', parent_id=uid(),
                         characteristics=[{'name': 'Размер', 'value': 'S-M'}])
        self.ms.docs['variant'] = {self.key: self.card}
        with self.e.db() as db:
            db.execute("INSERT INTO catalogues VALUES('assortment',1,?)", (json.dumps([self.card]),))
        self.recipe = self.e.save_recipe({'assortment_id': self.key, 'size': 'S-M', 'approved': True,
            'stages': [{'factory': self.factory, 'work_cost': 1400, 'materials': [
                {'id': self.material, 'name': 'Пряжа', 'quantity': .34, 'input_quantity': 340, 'input_unit': 'г', 'unit': 'кг', 'size_dependent': True},
                {'id': self.trim, 'name': 'Кольцо', 'quantity': 2, 'unit': 'шт', 'size_dependent': False},
                {'id': uid(), 'name': 'Пакет', 'quantity': 1, 'unit': 'шт', 'role': 'packaging'}]}]})

    def change(self, size='XS-S'):
        self.card['characteristics'][0]['value'] = size
        self.e.verify_live_sizes([self.key])
        return self.e.recipes()[0]

    def test_change_clears_only_primary_and_is_idempotent(self):
        r = self.change()
        self.assertFalse(r['approved'])
        self.assertEqual(r['norm_review']['old_size'], 'S-M')
        self.assertIsNone(r['stages'][0]['materials'][0]['quantity'])
        self.assertIsNone(r['stages'][0]['materials'][0]['input_quantity'])
        self.assertEqual(r['stages'][0]['materials'][1:], self.recipe['stages'][0]['materials'][1:])
        self.assertEqual(r['stages'][0]['work_cost'], 1400)
        self.assertEqual(self.change()['version'], r['version'])
        with self.e.db() as db:
            old = json.loads(db.execute('SELECT body FROM recipe_history WHERE id=? AND version=?', (r['id'], self.recipe['version'])).fetchone()[0])
        self.assertEqual(old['stages'][0]['materials'][0]['input_quantity'], 340)

    def test_cosmetic_change_preserves_approval_and_values(self):
        r = self.change(' s – m ')
        self.assertTrue(r['approved'])
        self.assertEqual(r['version'], self.recipe['version'])
        self.assertEqual(r['size'], 'S-M')

    def test_stale_save_and_missing_confirmation_blocked(self):
        r = self.change()
        with self.assertRaisesRegex(Review, 'уже изменена'):
            self.e.save_recipe(self.recipe)
        r['approved'] = True
        r['stages'][0]['materials'][0]['input_quantity'] = 360
        with self.assertRaisesRegex(Review, 'Подтверди'):
            self.e.save_recipe(r)
        r['confirm_size_review'] = True
        r = self.e.save_recipe(r)
        self.assertTrue(r['approved'])
        self.assertNotIn('norm_review', r)
        self.assertAlmostEqual(r['stages'][0]['materials'][0]['quantity'], .36)

    def test_client_cannot_remove_review_metadata(self):
        r = self.change()
        r.pop('norm_review')
        r['approved'] = False
        saved = self.e.save_recipe(r)
        self.assertIn('norm_review', saved)

    def test_cannot_approve_blank_quantity(self):
        r = self.change()
        r.update(approved=True, confirm_size_review=True)
        with self.assertRaises(Review):
            self.e.save_recipe(r)
        self.assertFalse(self.e.recipes()[0]['approved'])

    def test_unknown_roles_preserved_but_require_classification(self):
        self.recipe['stages'][0]['materials'][0].pop('size_dependent')
        self.recipe = self.e.save_recipe(self.recipe)
        r = self.change()
        self.assertEqual(r['stages'][0]['materials'][0]['input_quantity'], 340)
        self.assertTrue(r['norm_review']['needs_primary'])
        r.update(approved=True, confirm_size_review=True)
        with self.assertRaisesRegex(Review, 'назначение'):
            self.e.save_recipe(r)

    def test_incomplete_chain_stops_even_after_new_norm_confirmed(self):
        chain = {'lines': [{'assortment': ref('variant', self.key), 'recipe': copy.deepcopy(self.recipe)}]}
        r = self.change()
        with self.assertRaisesRegex(Review, 'приостановлена'):
            self.e.guard_chain_sizes(chain)
        r.update(approved=True, confirm_size_review=True)
        r['stages'][0]['materials'][0]['input_quantity'] = 360
        self.e.save_recipe(r)
        with self.assertRaisesRegex(Review, 'приостановлена'):
            self.e.guard_chain_sizes(chain)
        chain['complete'] = True
        self.e.guard_chain_sizes(chain)
        self.assertEqual(self.ms.created, [])

    def test_failed_live_read_does_not_change_norm(self):
        before = self.e.recipes()
        self.ms.get = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline'))
        with self.assertRaisesRegex(Review, 'Не удалось проверить'):
            self.e.verify_live_sizes([self.key])
        self.assertEqual(self.e.recipes(), before)

    def test_missing_card_is_visible_review_and_repeat_is_noop(self):
        with self.e.db() as db:
            self.e.reconcile_recipe_sizes(db, {}, complete=True)
        r = self.e.recipes()[0]
        self.assertEqual(r['norm_review']['reason'], 'card_unavailable')
        with self.e.db() as db:
            self.e.reconcile_recipe_sizes(db, {}, complete=True)
        self.assertEqual(self.e.recipes()[0]['version'], r['version'])

    def test_changed_back_still_requires_review(self):
        first = self.change()
        second = self.change('S-M')
        self.assertGreater(second['size_watch']['revision'], first['size_watch']['revision'])
        self.assertFalse(second['approved'])

    def test_cost_not_zero_for_pending_review(self):
        self.change()
        result = self.e.production_costs()[self.key]
        self.assertIsNone(result.get('cost'))
        self.assertIn('требует уточнения', result['issue'])

    def test_baseline_detects_existing_mismatch(self):
        with self.e.db() as db:
            r = copy.deepcopy(self.recipe)
            r.pop('size_watch')
            r['size'] = 'XS'
            db.execute('UPDATE recipes SET body=?', (json.dumps(r),))
            self.e.reconcile_recipe_sizes(db, {self.key: self.card}, complete=True)
        self.assertEqual(self.e.recipes()[0]['norm_review']['old_size'], 'XS')


    def test_multiple_primary_materials_reset_across_stages(self):
        r = copy.deepcopy(self.recipe)
        r['stages'].append(copy.deepcopy(r['stages'][0]))
        self.e.save_recipe(r)
        changed = self.change()
        for st in changed['stages']:
            self.assertIsNone(st['materials'][0]['input_quantity'])
            self.assertEqual(st['materials'][1]['quantity'], 2)

    def test_group_confirmation_uses_same_server_guard(self):
        r = self.change()
        r['stages'][0]['materials'][0]['input_quantity'] = 360
        with self.assertRaisesRegex(Review, 'Подтверди'):
            self.e.save_recipe_group([copy.deepcopy(r)])
        r['confirm_size_review'] = True
        saved = self.e.save_recipe_group([r])[0]
        self.assertTrue(saved['approved'])
        self.assertNotIn('norm_review', saved)

    def test_legacy_chain_cannot_resume_after_size_changed_and_returned(self):
        legacy = copy.deepcopy(self.recipe)
        legacy.pop('size_watch')
        self.change()
        r = self.change('S-M')
        r.update(approved=True, confirm_size_review=True)
        r['stages'][0]['materials'][0]['input_quantity'] = 340
        self.e.save_recipe(r)
        with self.assertRaisesRegex(Review, 'приостановлена'):
            self.e.guard_chain_sizes({'lines': [{'assortment': ref('variant', self.key), 'recipe': legacy}]})
