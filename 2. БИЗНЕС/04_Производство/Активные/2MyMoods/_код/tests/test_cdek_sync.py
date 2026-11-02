import copy
import importlib.util
import json
import tempfile
import time
import unittest
import uuid
import io
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cdek_sync', Path(__file__).resolve().parents[1] / 'cdek_sync.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def order(track='', legacy='', cost=None, status=None):
    attrs = [{'id': 'unrelated', 'value': 'preserve'}]
    for key, val in [(c.TRACK, track), (c.LEGACY, legacy), (c.COST, cost), (c.STATUS, status)]:
        if val is not None and val != '':
            attrs.append({'id': key, 'value': val})
    return {'id': str(uuid.uuid4()), 'name': 'CONTROL', 'attributes': attrs,
            'moment': '2026-06-01 12:00:00.000',
            'sum': 12345, 'applicable': True, 'state': {'meta': {'href': 'unchanged'}},
            'positions': {'meta': {'size': 2}}}


def body(track='00123', cost=690.75, statuses=None):
    return {'entity': {'uuid': 'carrier-id', 'cdek_number': track,
                      'delivery_detail': {'total_sum': cost, 'delivery_sum': 690, 'payment_sum': 9900},
                      'services': [{'total_sum': .75}],
                      'statuses': statuses if statuses is not None else [
                          {'code': 'DELIVERED', 'name': 'Вручен', 'date_time': '2026-10-31T12:00:00+03:00'}]}}


class FakeAPI:
    def __init__(self, orders):
        self.docs = {o['id']: copy.deepcopy(o) for o in orders}
        self.writes, self.calls = [], []
        self.response = body()
        self.error = None
        self.write_error_after = False
        self.on_write = None

    def orders(self):
        yield from copy.deepcopy(list(self.docs.values()))

    def order(self, oid):
        if oid not in self.docs:
            raise c.SyncError('ms_http_404', False)
        return copy.deepcopy(self.docs[oid])

    def peers(self, track):
        return [self.order(oid) for oid, doc in self.docs.items() if c.tracking(doc) == track]

    def write(self, oid, changes):
        self.writes.append((oid, copy.deepcopy(changes)))
        doc = self.docs[oid]
        attrs = c.values(doc)
        attrs.update(changes)
        doc['attributes'] = [{'id': k, 'value': v} for k, v in attrs.items() if v is not None]
        if self.on_write:
            self.on_write(oid, changes)
        if self.write_error_after:
            raise c.SyncError('network_or_json_error')

    def carrier(self, track):
        self.calls.append(track)
        if self.error:
            raise self.error
        result = c.carrier_result(self.response)
        result['track'] = track
        return result


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = c.Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, docs, costs=True):
        api = FakeAPI(docs)
        return c.Engine(self.s, api, costs, 2026), api

    def test_total_is_not_tariff_plus_services_twice(self):
        r = c.carrier_result(body())
        self.assertEqual(r['cost'], 690.75)

    def test_zero_and_missing_are_distinct(self):
        self.assertEqual(c.carrier_result(body(cost=0))['cost'], 0)
        self.assertIsNone(c.carrier_result(body(cost=None))['cost'])
        self.assertEqual(c.carrier_result(body(cost=None))['cost_error'], 'missing_cost')

    def test_nonfinite_negative_and_bool_cost(self):
        for value in ['NaN', 'Infinity', -1, True]:
            self.assertEqual(c.carrier_result(body(cost=value))['cost_error'], 'invalid_cost')

    def test_deleted_latest_is_ignored_and_timezone_sorting(self):
        statuses = [
            {'code': 'DONE', 'name': 'Отменённый', 'date_time': '2026-11-01T12:00:00+03:00', 'deleted': True},
            {'code': 'A', 'name': 'Старый', 'date_time': '2026-10-31T15:00:00+03:00'},
            {'code': 'B', 'name': 'Актуальный', 'date_time': '2026-10-31T13:00:00+00:00'}]
        self.assertEqual(c.carrier_result(body(statuses=statuses))['status']['name'], 'Актуальный')

    def test_tied_disagreeing_statuses_are_ambiguous(self):
        statuses = [{'code': x, 'name': x, 'date_time': '2026-10-31T12:00:00+03:00'} for x in ['A', 'B']]
        self.assertEqual(c.carrier_result(body(statuses=statuses))['status_error'], 'ambiguous_status')

    def test_invalid_timestamp_is_not_guessed(self):
        with self.assertRaises(c.SyncError):
            c.carrier_result(body(statuses=[{'name': 'x', 'date_time': 'yesterday'}]))

    def test_migration_preserves_legacy_leading_zero_and_other_fields(self):
        doc = order(legacy=' 00123 ')
        engine, api = self.engine([doc])
        before = c.protected(api.order(doc['id']))
        result = engine.migrate(True)
        after = api.order(doc['id'])
        self.assertEqual(c.tracking(after), '00123')
        self.assertEqual(c.values(after)[c.LEGACY], ' 00123 ')
        self.assertEqual(c.protected(after), before)
        self.assertEqual(result['copied'], 1)
        self.assertEqual(engine.migrate(True), {'already_complete': True})

    def test_migration_conflict_no_overwrite(self):
        doc = order(track='002', legacy='001')
        engine, api = self.engine([doc])
        self.assertEqual(engine.migrate(True)['conflicts'], 1)
        self.assertEqual(c.tracking(api.order(doc['id'])), '002')
        self.assertFalse(api.writes)

    def test_migration_dry_run_no_writes(self):
        engine, api = self.engine([order(legacy='00123')])
        engine.migrate(False)
        self.assertFalse(api.writes)
        self.assertIsNone(self.s.setting('migration_complete'))

    def test_partial_scan_cannot_start_migration(self):
        engine, api = self.engine([order(legacy='00123')])
        def partial():
            yield from api.docs.values()
            raise c.SyncError('network')
        api.orders = partial
        with self.assertRaises(c.SyncError):
            engine.migrate(True)
        self.assertFalse(api.writes)

    def test_new_track_gets_status_cost_and_preserves_main_document(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        actual = api.order(doc['id'])
        self.assertEqual(c.values(actual)[c.COST], 690.75)
        self.assertEqual(c.values(actual)[c.STATUS], 'Вручен')
        self.assertEqual(c.protected(actual), c.protected(doc))

    def test_cost_gate_status_only(self):
        doc = order('00123')
        engine, api = self.engine([doc], False)
        engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))
        self.assertEqual(c.values(api.order(doc['id']))[c.STATUS], 'Вручен')

    def test_cost_current_year_only_status_all_years(self):
        doc = order('00123')
        doc['moment'] = '2025-12-31 23:59:59.000'
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))
        self.assertEqual(c.values(api.order(doc['id']))[c.STATUS], 'Вручен')
        self.assertTrue(c.cost_year_matches({'moment': '2025-12-31T22:00:00+00:00'}, 2026))
        with patch.object(c, 'current_year', return_value=2027):
            self.assertTrue(c.cost_year_matches({'moment': '2027-01-01 00:00:00.000'}, None))
            self.assertFalse(c.cost_year_matches({'moment': '2026-12-31 23:59:59.000'}, None))

    def test_concurrent_queue_claim_prevents_double_processing(self):
        import threading
        doc = order('00123')
        engine, api = self.engine([doc])
        self.s.observe(doc)
        entered, release = threading.Event(), threading.Event()
        original = engine.process
        def slow(oid):
            entered.set()
            release.wait(3)
            original(oid)
        engine.process = slow
        t = threading.Thread(target=engine.one)
        t.start()
        self.assertTrue(entered.wait(3))
        self.assertFalse(engine.one())
        release.set()
        t.join(3)
        self.assertEqual(len(api.calls), 1)

    def test_duplicate_status_both_no_cost_and_one_carrier_call(self):
        docs = [order('00123'), order('00123')]
        engine, api = self.engine(docs)
        for doc in docs:
            engine.process(doc['id'])
            self.assertNotIn(c.COST, c.values(api.order(doc['id'])))
            self.assertEqual(c.values(api.order(doc['id']))[c.STATUS], 'Вручен')
            self.assertEqual(self.s.row(doc['id'])['error'], 'duplicate_track_cost_blocked')
        self.assertEqual(len(api.calls), 1)

    def test_new_duplicate_removes_owned_cost(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        duplicate = order('00123')
        api.docs[duplicate['id']] = duplicate
        engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))

    def test_new_duplicate_prioritizes_cleanup_of_previous_order(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        with self.s.db() as db:
            db.execute('DELETE FROM jobs')
        duplicate = order('00123')
        api.docs[duplicate['id']] = duplicate
        engine.process(duplicate['id'])
        with self.s.db() as db:
            queued = db.execute('SELECT priority FROM jobs WHERE id=?', (doc['id'],)).fetchone()
        self.assertIsNotNone(queued)
        self.assertEqual(queued['priority'], 0)

    def test_duplicate_does_not_remove_unowned_cost(self):
        docs = [order('00123', cost=500), order('00123')]
        engine, api = self.engine(docs)
        engine.process(docs[0]['id'])
        self.assertEqual(c.values(api.order(docs[0]['id']))[c.COST], 500)

    def test_clear_clears_owned_results_and_does_not_restore_legacy(self):
        doc = order('00123', legacy='00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        api.write(doc['id'], {c.TRACK: None})
        engine.process(doc['id'])
        attrs = c.values(api.order(doc['id']))
        for key in (c.TRACK, c.COST, c.STATUS):
            self.assertNotIn(key, attrs)
        self.assertEqual(attrs[c.LEGACY], '00123')

    def test_change_clears_old_results_even_when_new_carrier_errors(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        api.write(doc['id'], {c.TRACK: '00999'})
        api.error = c.SyncError('network')
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))
        self.assertNotIn(c.STATUS, c.values(api.order(doc['id'])))

    def test_late_old_result_queues_new_version(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        fired = []
        def change(oid, changes):
            if not fired and c.STATUS in changes:
                fired.append(True)
                api.docs[oid]['attributes'] = [a for a in api.docs[oid]['attributes'] if a['id'] != c.TRACK]
                api.docs[oid]['attributes'].append({'id': c.TRACK, 'value': '00999'})
        api.on_write = change
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        api.on_write = None
        api.error = c.SyncError('network')
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))
        self.assertNotIn(c.STATUS, c.values(api.order(doc['id'])))

    def test_transient_error_preserves_known_results(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        engine.process(doc['id'])
        with self.s.db() as db:
            db.execute('DELETE FROM cache')
        before = api.order(doc['id'])
        api.error = c.SyncError('cdek_http_429')
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        self.assertEqual(api.order(doc['id']), before)

    def test_timeout_after_success_is_read_back(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        api.write_error_after = True
        engine.process(doc['id'])
        self.assertEqual(c.values(api.order(doc['id']))[c.COST], 690.75)

    def test_pending_write_recovers_ownership_then_clears(self):
        doc = order('00999', cost=500, status='Old')
        engine, api = self.engine([doc])
        self.s.observe(doc)
        with self.s.db() as db:
            db.execute('INSERT INTO journal(order_id,at,track,before_json,changes_json,state) VALUES(?,?,?,?,?,?)',
                       (doc['id'], time.time(), '00123', '{}', json.dumps({c.COST: 500, c.STATUS: 'Old'}), 'pending'))
        api.error = c.SyncError('network')
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        self.assertNotIn(c.COST, c.values(api.order(doc['id'])))

    def test_mass_event_and_invalid_external_href(self):
        ids = [str(uuid.uuid4()) for _ in range(300)]
        event = {'events': [{'meta': {'href': c.MS_BASE + '/entity/customerorder/' + oid}} for oid in ids]}
        self.assertEqual(set(c.event_ids(event)), set(ids))
        with self.assertRaises(ValueError):
            c.event_ids({'events': [{'meta': {'href': 'https://evil.test/' + ids[0]}}]})

    def test_durable_queue_coalesces_and_prioritizes_events(self):
        a, b = order('00123'), order('00234')
        engine, api = self.engine([a, b])
        self.s.observe(a, 10)
        self.s.observe(b, 0)
        self.s.enqueue(a['id'], 10)
        restored = c.Store(self.tmp.name)
        self.assertEqual(restored.summary()['queued'], 2)
        engine.one()
        self.assertEqual(api.calls[0], '00234')

    def test_scan_failure_does_not_clear_existing_rows(self):
        doc = order('00123')
        engine, api = self.engine([doc])
        self.s.observe(doc)
        def partial():
            raise c.SyncError('network')
            yield
        api.orders = partial
        with self.assertRaises(c.SyncError):
            engine.scan()
        self.assertEqual(self.s.row(doc['id'])['track'], '00123')

    def test_cycle_invalidates_cache_once_for_all_peers(self):
        docs = [order('00123'), order('00123')]
        engine, api = self.engine(docs)
        for doc in docs:
            engine.process(doc['id'])
        self.s.setting('cycle_started', time.time())
        for doc in docs:
            engine.process(doc['id'])
        self.assertEqual(len(api.calls), 2)

    def test_bad_track_is_preserved_not_guessed(self):
        doc = order('tracking: 123')
        engine, api = self.engine([doc])
        with self.assertRaises(c.SyncError):
            engine.process(doc['id'])
        self.assertFalse(api.calls)
        self.assertEqual(c.tracking(api.order(doc['id'])), 'tracking: 123')

    def test_http_auth_method_and_payload_checks(self):
        class Handler:
            path = c.WEBHOOK_PATH
            headers = {}
            rfile = io.BytesIO(b'{}')
            def _json(self, code, body):
                self.code, self.body = code, body
            def _authed(self):
                return False
        h = Handler()
        with patch.dict(c.os.environ, {'CDEK_WEBHOOK_TOKEN': 'test'}):
            self.assertTrue(c.route(h, 'POST'))
            self.assertEqual(h.code, 403)
            h.headers = {'X-Cdek-Webhook-Token': 'test', 'Content-Length': '2'}
            c.route(h, 'POST')
            self.assertEqual(h.code, 400)
            c.route(h, 'GET')
            self.assertEqual(h.code, 405)
        h.path = '/api/cdek/status'
        c.route(h, 'GET')
        self.assertEqual(h.code, 401)

    def test_http_ack_only_after_durable_enqueue_and_mass_delivery(self):
        ids = [str(uuid.uuid4()) for _ in range(300)]
        raw = json.dumps({'events': [{'meta': {'href': c.MS_BASE + '/entity/customerorder/' + oid}}
                                      for oid in ids]}).encode()
        class Handler:
            path = c.WEBHOOK_PATH
            headers = {'X-Cdek-Webhook-Token': 'test', 'Content-Length': str(len(raw))}
            rfile = io.BytesIO(raw)
            def _json(inner, code, body):
                self.assertEqual(self.s.summary()['queued'], 300)
                inner.code = code
        h = Handler()
        with patch.dict(c.os.environ, {'CDEK_WEBHOOK_TOKEN': 'test'}), patch.object(c, 'Store', return_value=self.s):
            c.route(h, 'POST')
        self.assertEqual(h.code, 202)

    def test_http_queue_failure_is_not_acknowledged(self):
        oid = str(uuid.uuid4())
        raw = json.dumps({'id': oid}).encode()
        class Handler:
            path = c.WEBHOOK_PATH
            headers = {'X-Cdek-Webhook-Token': 'test', 'Content-Length': str(len(raw))}
            rfile = io.BytesIO(raw)
            def _json(inner, code, body):
                inner.code = code
        with patch.dict(c.os.environ, {'CDEK_WEBHOOK_TOKEN': 'test'}), \
             patch.object(self.s, 'enqueue', side_effect=c.sqlite3.OperationalError()), \
             patch.object(c, 'Store', return_value=self.s):
            h = Handler()
            c.route(h, 'POST')
            self.assertEqual(h.code, 503)

    def test_scenario_basic_auth_with_document_body(self):
        raw = json.dumps({'id': str(uuid.uuid4())}).encode()
        class Handler:
            path = c.WEBHOOK_PATH
            headers = {'Authorization': 'Basic ' + c.base64.b64encode(b'cdek:test').decode(),
                       'Content-Length': str(len(raw))}
            rfile = io.BytesIO(raw)
            def _json(inner, code, body):
                inner.code = code
        with patch.dict(c.os.environ, {'CDEK_WEBHOOK_TOKEN': 'test'}), patch.object(c, 'Store', return_value=self.s):
            h = Handler()
            c.route(h, 'POST')
            self.assertEqual(h.code, 202)

    def test_parallel_duplicate_uses_one_carrier_request(self):
        from concurrent.futures import ThreadPoolExecutor
        docs = [order('00123'), order('00123')]
        engine, api = self.engine(docs)
        original = api.carrier
        def slow(track):
            time.sleep(.03)
            return original(track)
        api.carrier = slow
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda doc: engine.process(doc['id']), docs))
        self.assertEqual(api.calls, ['00123'])

    def test_carrier_401_refreshes_token_once(self):
        with patch.dict(c.os.environ, {'MOYSKLAD_TOKEN': 'fake', 'CDEK_CLIENT_ID': 'fake',
                                       'CDEK_CLIENT_SECRET': 'fake'}):
            api = c.API()
        with patch.object(api, 'request', side_effect=[
                {'access_token': 'first', 'expires_in': 3600},
                c.SyncError('cdek_http_401'),
                {'access_token': 'second', 'expires_in': 3600}, body()]) as request:
            self.assertEqual(api.carrier('00123')['cost'], 690.75)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(api.token, 'second')

    def test_migration_resumes_and_does_not_duplicate_remote_write(self):
        docs = [order(legacy='00123'), order(legacy='00234')]
        engine, api = self.engine(docs)
        original = api.write
        failed = []
        def fail_once(oid, changes):
            if oid == docs[1]['id'] and not failed:
                failed.append(True)
                raise c.SyncError('network')
            original(oid, changes)
        api.write = fail_once
        self.assertEqual(engine.migrate(True)['failures'], 1)
        self.assertIsNone(self.s.setting('migration_complete'))
        self.assertEqual(engine.migrate(True)['failures'], 0)
        self.assertEqual(len(api.writes), 2)
        self.assertEqual(self.s.setting('migration_complete'), '1')

    def test_rollback_restores_own_fields_and_preserves_employee_change(self):
        doc = order(legacy='00123')
        engine, api = self.engine([doc])
        engine.migrate(True)
        engine.process(doc['id'])
        api.write(doc['id'], {c.COST: 777})
        before = api.order(doc['id'])
        preview = engine.rollback(False)
        self.assertEqual(api.order(doc['id']), before)
        self.assertEqual(preview['skipped_fields'], 1)
        result = engine.rollback(True)
        actual = c.values(api.order(doc['id']))
        self.assertEqual(result['failures'], 0)
        self.assertNotIn(c.TRACK, actual)
        self.assertNotIn(c.STATUS, actual)
        self.assertEqual(actual[c.COST], 777)
        self.assertEqual(actual[c.LEGACY], '00123')

    def test_pagination_visits_every_page_and_rejects_truncation(self):
        api = c.API.__new__(c.API)
        calls = []
        def ms(method, path, body=None):
            calls.append(path)
            if 'offset=0&' in path:
                return {'rows': [order() for _ in range(1000)], 'meta': {'size': 1003}}
            return {'rows': [order() for _ in range(3)], 'meta': {'size': 1003}}
        api.ms = ms
        self.assertEqual(len(list(api.orders())), 1003)
        self.assertEqual(len(calls), 2)
        api.ms = lambda *args: {'rows': [], 'meta': {'size': 100}}
        with self.assertRaises(c.SyncError):
            list(api.orders())


if __name__ == '__main__':
    unittest.main()
