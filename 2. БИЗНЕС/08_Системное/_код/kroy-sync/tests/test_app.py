import hashlib
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.api import create_app
from app.db import Base, Balance, Document, Entity, Item, Movement, Outbox, User, engine_for, transaction
from app.planning import allocate, demand, layout_preview
from app.service import DomainError, execute, get_balance
from app.worker import tick


def test_reverse_fact_restores_cloth_and_requires_original_freeze(setup):
    engine, user, cmd = setup
    t = task(cmd, qty=10, cloth='10')
    fact = cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True,
                              'materials': [{'item_id': 'cloth', 'quantity': '8'}]})
    assert balance(engine, 'cloth')['free'] == Decimal('92.5')
    rev = cmd('task.reverse_fact', {'id': t['id'], 'version': fact['version'], 'fact_id': fact['document_id']})
    assert balance(engine)['frozen'] == 0
    assert balance(engine, 'cloth')['reserved'] == 10
    assert balance(engine, 'cloth')['free'] == Decimal('90.5')
    next_fact = cmd('task.confirm', {'id': t['id'], 'version': rev['version'], 'confirm_actual': True})
    cmd('freeze.release', {'id': next_fact['document_id'] + ':s', 'version': 1, 'quantity': 1})
    with pytest.raises(DomainError, match='разморожен'):
        cmd('task.reverse_fact', {'id': t['id'], 'version': next_fact['version'], 'fact_id': next_fact['document_id']})


def test_order_shortage_can_be_reserved_after_receipt(setup):
    engine, user, cmd = setup
    order = cmd('order.create', {'platform': 'wb', 'account': 'test', 'external_id': 'short',
                                'location': 'own', 'lines': [{'item_id': 's', 'quantity': 15}]})
    b = balance(engine)
    cmd('stock.adjust', {'item_id': 's', 'location': 'own', 'version': b['version'], 'physical': 15})
    result = cmd('order.reserve', {'id': order['id'], 'version': order['version']})
    assert result['status'] == 'reserved'
    assert balance(engine)['reserved'] == 15
    cmd('order.ship', {'id': result['id'], 'version': result['version']})
    assert balance(engine)['reserved'] == 0


def test_draft_without_material_never_posts_output(setup):
    engine, user, cmd = setup
    t = cmd('task.create', {'title': 'Расход уточняется', 'location': 'own',
                           'products': [{'item_id': 's', 'quantity': 10}], 'materials': []})
    with pytest.raises(DomainError, match='расход'):
        cmd('task.approve', {'id': t['id'], 'version': t['version']})
    assert balance(engine)['frozen'] == 0


def test_mapping_covers_missing_and_ambiguous(setup):
    from app.mappings import import_candidates
    engine, user, cmd = setup
    wb = {'at': '2026-10-26T00:00:00Z', 'cards': [{'id': 1, 'sizes': [{'chrtID': 2, 'skus': ['001']}]}]}
    ozon = {'at': wb['at'], 'items': [{'id': 1, 'offer_id': 'a', 'barcodes': ['002']},
                                    {'id': 2, 'offer_id': 'b', 'barcodes': ['002']}]}
    counts = import_candidates(engine, wb, ozon)
    assert counts['wb'] == {'unique': 1, 'missing': 1, 'ambiguous': 0}
    assert counts['ozon'] == {'unique': 0, 'missing': 1, 'ambiguous': 1}
    assert import_candidates(engine, wb, ozon) == counts
    assert balance(engine)['free'] == 10


def test_fbo_discrepancy_remains_in_transit_until_resolved(setup):
    engine, user, cmd = setup
    fbo = cmd('fbo.create', {'platform': 'ozon', 'account': 'test', 'external_id': 'discrepancy',
                          'location': 'own', 'lines': [{'item_id': 's', 'quantity': 10}]})
    sent = cmd('fbo.dispatch', {'id': fbo['id'], 'version': fbo['version']})
    accepted = cmd('fbo.accept', {'id': fbo['id'], 'version': sent['version'], 'lines': [{'item_id': 's', 'quantity': 6}]})
    assert balance(engine, loc='transit:' + fbo['id'])['free'] == 4
    returned = cmd('fbo.resolve', {'id': fbo['id'], 'version': accepted['version'],
                    'resolution': 'returned', 'condition': 'damaged', 'lines': [{'item_id': 's', 'quantity': 2}]})
    assert balance(engine)['damaged'] == 2
    assert balance(engine)['free'] == 0
    lost = cmd('fbo.resolve', {'id': fbo['id'], 'version': returned['version'],
                    'resolution': 'lost', 'lines': [{'item_id': 's', 'quantity': 2}]})
    assert lost['status'] == 'settled'
    assert balance(engine, loc='transit:' + fbo['id'])['free'] == 0
    with pytest.raises(DomainError):
        cmd('fbo.accept', {'id': fbo['id'], 'version': lost['version'], 'lines': [{'item_id': 's', 'quantity': 2}]})


def test_open_order_pagination_and_missing_status_fail_closed():
    from app.connectors import ozon_unfulfilled, wb_order_statuses, ReadFailure
    class Ozon:
        def request(self, path, body):
            assert path == '/v4/posting/fbs/unfulfilled/list'
            assert body['filter']['cutoff_from'] == 'start'
            second = bool(body['cursor'])
            return {'postings': [{'posting_number': 'old' if not second else 'new',
                                 'products': [{'sku': 1, 'quantity': 2}], 'customer': 'not exported'}],
                    'cursor': 'next', 'has_next': not second}
    rows = ozon_unfulfilled(Ozon(), 'start', 'end')
    assert len(rows) == 2 and all('customer' not in r for r in rows)
    class WB:
        def request(self, path, body):
            return {'orders': [{'id': 1, 'supplierStatus': 'new'}]}
    with pytest.raises(ReadFailure):
        wb_order_statuses(WB(), [1, 2])


@pytest.fixture
def setup(tmp_path):
    import os
    url = os.environ.get('KROY_TEST_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db'))
    engine = engine_for(url)
    # Dedicated test schema; never drop application tables.
    if engine.dialect.name == 'postgresql':
        from sqlalchemy import text
        schema = 'test_' + uuid4().hex
        with engine.begin() as c:
            c.execute(text('CREATE SCHEMA ' + schema))
        engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    user = User(id='owner', name='Test', role='owner', active=True, token_hash=hashlib.sha256(b'test-only').hexdigest())
    with transaction(engine) as s:
        s.add(user)
        s.add_all([Item(id='cloth', name='Ткань', model='Ткань', kind='material', unit='meter', barcodes=[]),
                   Item(id='s', name='Лонг скимс S', model='Лонг скимс', kind='garment', unit='piece', size='S', barcodes=['001']),
                   Item(id='m', name='Лонг скимс M', model='Лонг скимс', kind='garment', unit='piece', size='M', barcodes=['002'])])
    def cmd(command, payload, op=None, actor=user):
        return execute(engine, actor, op or str(uuid4()), command, payload, 'Контрольный пример')
    for item_id, qty in [('cloth', '100.5'), ('s', 10), ('m', 0)]:
        cmd('stock.adjust', {'item_id': item_id, 'location': 'own', 'version': 0, 'physical': qty})
    return engine, user, cmd


def balance(engine, item='s', loc='own'):
    with Session(engine) as s:
        b = s.scalar(select(Balance).where(Balance.item_id == item, Balance.location == loc))
        return {k: getattr(b, k) for k in ('free', 'frozen', 'reserved', 'damaged', 'version')}


def task(cmd, qty=75, cloth='10.5'):
    t = cmd('task.create', {'title': 'Лонг скимс', 'location': 'own', 'products': [{'item_id': 's', 'quantity': qty}],
                          'materials': [{'item_id': 'cloth', 'quantity': cloth}]})
    return cmd('task.approve', {'id': t['id'], 'version': t['version']})


def test_cut_retry_and_two_identical_batches(setup):
    engine, user, cmd = setup
    for _ in range(2):
        t = task(cmd)
        op = str(uuid4())
        p = {'id': t['id'], 'version': t['version'], 'confirm_actual': True}
        first = cmd('task.confirm', p, op)
        assert cmd('task.confirm', p, op) == first
    assert balance(engine)['frozen'] == 150
    assert balance(engine, 'cloth')['free'] == Decimal('79.5')
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Document).where(Document.kind == 'task.confirm')) == 2


def test_id_reuse_different_content(setup):
    engine, user, cmd = setup
    op = str(uuid4())
    p = {'item_id': 's', 'location': 'own', 'physical': 11, 'version': 1}
    cmd('stock.adjust', p, op)
    with pytest.raises(DomainError):
        cmd('stock.adjust', {**p, 'physical': 12}, op)


def test_atomic_rollback_cut_shortage(setup):
    engine, user, cmd = setup
    t = task(cmd)
    before = balance(engine, 'cloth')
    with pytest.raises(DomainError):
        cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True,
                            'products': [{'item_id': 's', 'quantity': 76}]})
    assert balance(engine, 'cloth') == before
    assert balance(engine)['frozen'] == 0


def test_partial_cut_cancel_and_release(setup):
    engine, user, cmd = setup
    t = task(cmd)
    t = cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True,
                            'products': [{'item_id': 's', 'quantity': 30}], 'materials': [{'item_id': 'cloth', 'quantity': 4}]})
    cmd('task.cancel', {'id': t['id'], 'version': t['version']})
    assert balance(engine, 'cloth')['free'] == Decimal('96.5')
    with Session(engine) as s:
        freeze = s.scalar(select(Entity).where(Entity.kind == 'freeze'))
        p = {'id': freeze.id, 'version': freeze.version, 'quantity': 12}
    cmd('freeze.release', p)
    assert balance(engine)['frozen'] == 18
    assert balance(engine)['free'] == 22


def test_order_ship_cancel_return_no_second_deduction(setup):
    engine, user, cmd = setup
    o = cmd('order.create', {'platform': 'wb', 'account': 'a', 'external_id': '1', 'location': 'own', 'lines': [{'item_id': 's', 'quantity': 2}]})
    assert balance(engine)['free'] == 8
    o = cmd('order.ship', {'id': o['id'], 'version': o['version']})
    assert balance(engine)['free'] == 8
    o = cmd('order.cancel', {'id': o['id'], 'version': o['version']})
    assert balance(engine)['free'] == 8
    cmd('return.receive', {'id': o['id'], 'version': o['version'], 'condition': 'damaged', 'lines': [{'item_id': 's', 'quantity': 2}]})
    assert balance(engine)['damaged'] == 2
    assert balance(engine)['free'] == 8


def test_shortage_order_preserved(setup):
    engine, user, cmd = setup
    o = cmd('order.create', {'platform': 'ozon', 'account': 'a', 'external_id': '1', 'location': 'own', 'lines': [{'item_id': 's', 'quantity': 12}]})
    assert o['shortages'][0]['quantity'] == '2.000'
    with pytest.raises(DomainError):
        cmd('order.ship', {'id': o['id'], 'version': o['version']})
    cmd('order.cancel', {'id': o['id'], 'version': o['version']})
    assert balance(engine)['free'] == 10


def test_fbo_partial_stays_in_transit(setup):
    engine, user, cmd = setup
    f = cmd('fbo.create', {'platform': 'wb', 'account': 'a', 'external_id': 'f1', 'location': 'own', 'lines': [{'item_id': 's', 'quantity': 8}]})
    f = cmd('fbo.dispatch', {'id': f['id'], 'version': f['version']})
    p = {'id': f['id'], 'version': f['version'], 'lines': [{'item_id': 's', 'quantity': 5}]}
    op = str(uuid4())
    accepted = cmd('fbo.accept', p, op)
    assert cmd('fbo.accept', p, op) == accepted
    assert balance(engine)['free'] == 2
    assert balance(engine, loc='transit:' + f['id'])['free'] == 3
    assert balance(engine, loc='marketplace:wb:a')['free'] == 5


def test_count_scan_retries_and_reservation_conflict(setup):
    engine, user, cmd = setup
    c = cmd('count.create', {'location': 'own', 'item_ids': ['s']})
    p = {'id': c['id'], 'version': c['version'], 'barcode': '001'}
    op = str(uuid4())
    c = cmd('count.scan', p, op)
    assert cmd('count.scan', p, op) == c
    c = cmd('count.scan', {'id': c['id'], 'version': c['version'], 'barcode': '001'})
    cmd('freeze.create', {'item_id': 's', 'location': 'own', 'version': 1, 'quantity': 1})
    with pytest.raises(DomainError):
        cmd('count.post', {'id': c['id'], 'version': c['version'], 'confirm_zeroes': True})
    assert balance(engine)['free'] == 9


def test_count_scope_no_other_zero(setup):
    engine, user, cmd = setup
    c = cmd('count.create', {'location': 'own', 'item_ids': ['m']})
    cmd('count.post', {'id': c['id'], 'version': c['version'], 'confirm_zeroes': True})
    assert balance(engine)['free'] == 10


def test_concurrent_freeze_and_order(setup):
    engine, user, cmd = setup
    def freeze():
        try:
            return cmd('freeze.create', {'item_id': 's', 'location': 'own', 'version': 1, 'quantity': 8})
        except DomainError:
            return 'conflict'
    def order():
        return cmd('order.create', {'platform': 'wb', 'account': 'a', 'external_id': 'race', 'location': 'own', 'lines': [{'item_id': 's', 'quantity': 8}]})
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(freeze), pool.submit(order)
        a.result(), b.result()
    values = balance(engine)
    assert values['free'] + values['reserved'] + values['frozen'] == 10
    assert values['free'] >= 0


def test_api_permissions_revocation_finance(setup):
    engine, user, cmd = setup
    with transaction(engine) as s:
        s.add(User(id='v', name='viewer', role='viewer', active=True, token_hash=hashlib.sha256(b'view').hexdigest()))
    client = TestClient(create_app(engine))
    assert client.get('/api/catalog').status_code == 401
    h = {'Authorization': 'Bearer view'}
    assert client.get('/api/catalog', headers=h).status_code == 200
    assert client.get('/api/entities/finance', headers=h).status_code == 403
    response = client.post('/api/commands', headers=h, json={'operation_id': str(uuid4()), 'command': 'stock.adjust',
                     'reason': 'test', 'payload': {'item_id': 's', 'location': 'own', 'version': 1, 'physical': 20}})
    assert response.status_code == 403
    with transaction(engine) as s:
        s.get(User, 'v').active = False
    assert client.get('/api/catalog', headers=h).status_code == 401


def test_expiry_reminder_never_auto_releases(setup):
    engine, user, cmd = setup
    cmd('freeze.create', {'item_id': 's', 'location': 'own', 'version': 1, 'quantity': 5, 'until': '2020-01-01T00:00:00Z'})
    assert tick(engine) == 1
    assert tick(engine) == 0
    assert balance(engine)['frozen'] == 5


def test_invalid_numeric_rolls_not_meters(setup):
    engine, user, cmd = setup
    for value in ['NaN', 'Infinity', '0.5', True, '-1']:
        with pytest.raises(DomainError):
            cmd('freeze.create', {'item_id': 's', 'location': 'own', 'version': 1, 'quantity': value})


def test_planning_known_client_counterexamples():
    assert allocate(75, {'S': 50, 'M': 50}) == {'S': 37, 'M': 38}
    assert layout_preview('Лонг скимс', {'S': 100, 'M': 100}, [{'S': 50, 'M': 50}], 2)['result'] is None
    assert layout_preview('Футболка Сердца', {'S': 50}, [{'S': 1}], 1)['result'] is None
    assert layout_preview('Костюм двойка Поло', {'S': 25}, [{'S': 1}], 1)['result']['output'] == {'S': 27}
    assert layout_preview('Лонг скимс', {'S': 0, 'M': 75}, [{'S': 1, 'M': 1}], 1)['result'] is None
    assert demand(100, 14, 28, 20, 30)['need'] == 150


def test_ledger_outbox_consistency(setup):
    engine, user, cmd = setup
    t = task(cmd)
    cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True})
    with Session(engine) as s:
        for b in s.scalars(select(Balance)):
            for bucket in ('free', 'frozen', 'reserved', 'damaged'):
                value = s.scalar(select(func.sum(Movement.quantity)).where(Movement.item_id == b.item_id, Movement.location == b.location, Movement.bucket == bucket)) or 0
                assert getattr(b, bucket) == value
        assert all(s.get(Document, o.document_id) for o in s.scalars(select(Outbox)))


def test_publication_stale_and_quota_transfer():
    from app.publication import plan_publication
    from app.db import now
    p = dict(available=10, safety_stock=0, desired={'wb': 5, 'ozon': 5}, confirmed={'wb': 10, 'ozon': 0},
             orders_read_at=now().isoformat(), max_age_seconds=60, mappings_verified=True,
             single_writer=True, reductions_confirmed=False)
    with pytest.raises(DomainError):
        plan_publication(**p)
    assert plan_publication(**{**p, 'reductions_confirmed': True})['publication_enabled'] is False
    with pytest.raises(DomainError):
        plan_publication(**{**p, 'reductions_confirmed': True, 'orders_read_at': '2020-01-01T00:00:00Z'})
    with pytest.raises(DomainError):
        plan_publication(**{**p, 'desired': {'wb': 10, 'ozon': 10}})


def test_connector_pagination_and_no_personal_data():
    from app.connectors import ozon_postings, ReadFailure, ReadClient
    class Fake:
        def __init__(self, repeat=False):
            self.calls, self.repeat = 0, repeat
        def request(self, path, body):
            self.calls += 1
            return {'postings': [{'posting_number': str(self.calls), 'status': 'awaiting_packaging', 'customer': {'name': 'must-not-leak'}, 'products': []}],
                    'has_next': self.calls == 1 or self.repeat, 'cursor': 'next'}
    assert len(ozon_postings(Fake(), 'start', 'end')) == 2
    assert 'customer' not in ozon_postings(Fake(), 'start', 'end')[0]
    with pytest.raises(ReadFailure):
        ozon_postings(Fake(True), 'start', 'end')
    with pytest.raises(ReadFailure):
        ReadClient('ozon', {}).request('/v2/products/stocks', {'stocks': []})


def test_auto_freeze_requires_explicit_policy_and_no_double_release(setup):
    engine, user, cmd = setup
    f = cmd('freeze.create', {'item_id': 's', 'location': 'own', 'version': 1, 'quantity': 4, 'until': '2020-01-01T00:00:00Z'})
    cmd('freeze.configure', {'id': f['id'], 'version': f['version'], 'mode': 'auto'})
    assert tick(engine) == 1
    assert tick(engine) == 0
    assert balance(engine)['free'] == 10
    assert balance(engine)['frozen'] == 0


def test_unknown_scan_resolution_auditable(setup):
    engine, user, cmd = setup
    c = cmd('count.create', {'location': 'own', 'item_ids': ['s']})
    c = cmd('count.scan', {'id': c['id'], 'version': c['version'], 'barcode': 'unknown'})
    with pytest.raises(DomainError):
        cmd('count.post', {'id': c['id'], 'version': c['version'], 'confirm_zeroes': True})
    c = cmd('count.resolve', {'id': c['id'], 'version': c['version'], 'index': 0, 'item_id': 's'})
    cmd('count.post', {'id': c['id'], 'version': c['version'], 'confirm_zeroes': True})
    assert balance(engine)['free'] == 1


def test_grouped_layout_respects_each_color_and_size():
    from app.planning import grouped_layout_preview
    result = grouped_layout_preview('Лонг скимс', {'black': {'S': 75, 'M': 75}, 'red': {'S': 38, 'M': 37}},
                                    [{'S': 1, 'M': 0}, {'S': 0, 'M': 1}, {'S': 1, 'M': 1}], {'black': 2, 'red': 1})
    assert result['patterns_used'] == [2]
    assert sum(result['result']['black']['output'].values()) == 150
    assert sum(result['result']['red']['output'].values()) == 75


def test_second_partial_fact_after_cloth_consumed(setup):
    engine, user, cmd = setup
    t = task(cmd)
    t = cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True,
                            'products': [{'item_id': 's', 'quantity': 30}]})
    t = cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True})
    assert t['status'] == 'complete'
    assert balance(engine)['frozen'] == 75
    assert balance(engine, 'cloth')['free'] == 90


def test_task_revision_preserves_fact_and_reallocates(setup):
    engine, user, cmd = setup
    t = task(cmd)
    t = cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True,
                            'products': [{'item_id': 's', 'quantity': 30}], 'materials': [{'item_id': 'cloth', 'quantity': 4}]})
    t = cmd('task.revise', {'id': t['id'], 'version': t['version'], 'products': [{'item_id': 's', 'quantity': 50}],
                           'materials': [{'item_id': 'cloth', 'quantity': 8}]})
    assert balance(engine, 'cloth')['reserved'] == 4
    assert balance(engine, 'cloth')['free'] == Decimal('92.5')
    with pytest.raises(DomainError):
        cmd('task.revise', {'id': t['id'], 'version': t['version'], 'products': [{'item_id': 's', 'quantity': 29}],
                           'materials': [{'item_id': 'cloth', 'quantity': 8}]})
    cmd('task.confirm', {'id': t['id'], 'version': t['version'], 'confirm_actual': True})
    assert balance(engine)['frozen'] == 50


def test_staff_rights_and_owner_guard(setup):
    engine, user, cmd = setup
    created = cmd('user.create', {'id': 'cutter', 'name': 'Закройщик', 'role': 'cutter', 'token_hash': hashlib.sha256(b'new-secret').hexdigest()})
    with Session(engine) as s:
        cutter = s.get(User, 'cutter');s.expunge(cutter)
    with pytest.raises(DomainError):
        cmd('stock.adjust', {'item_id': 's','location':'own','version':1,'physical':100}, actor=cutter)
    with pytest.raises(DomainError):
        cmd('user.update', {'id':'owner','version':1,'role':'viewer','active':True})
    cmd('user.update', {'id':'cutter','version':1,'role':'cutter','active':False})
    with pytest.raises(DomainError):
        cmd('task.create', {'title':'test','location':'own','products':[{'item_id':'s','quantity':1}],'materials':[{'item_id':'cloth','quantity':1}]}, actor=cutter)


def test_catalog_update_keeps_id_unit_and_history(setup):
    engine, user, cmd = setup
    cmd('item.update', {'id':'s','version':1,'name':'Новое название','model':'Модель','barcodes':['001']})
    with Session(engine) as s:
        assert s.get(Item,'s').unit=='piece'
        assert s.get(Item,'s').source['version']==2
        assert s.scalar(select(Entity).where(Entity.kind=='item_revision')) is not None
    with pytest.raises(DomainError):
        cmd('item.update', {'id':'m','version':1,'name':'M','model':'Модель','barcodes':['001']})
