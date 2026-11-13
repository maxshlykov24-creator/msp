import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
import webhook_server as w

OID = '8d966037-dda1-11f1-0a80-1cfb005db55e'
AID = '8da4b043-dda1-11f1-0a80-1cfb005db570'
NEW = '11111111-1111-1111-1111-111111111111'

def ref(kind, id):
    return {'meta': {'href': w.lib.MS_BASE + '/' + kind + '/' + id}}

def order():
    return {'id': OID, 'created': '2026-11-11 10:28:55.992', 'updated': '2026-11-11 12:05:47.182',
            'name': '02444', 'sum': 2890000, 'agent': ref('entity/counterparty', AID)}

def creation():
    return {'eventType': 'create', 'source': 'remap-1.2', 'audit': ref('audit', AID)}

INFO = {'additionalInfo': '(1.2.3.4, WordPress/7.1.4; https://2mymoods.ru)'}

class FakeAPI:
    def __init__(self):
        self.order = order()
        self.events = [creation()]
        self.writes = 0
        self.lose_response = False
        self.change_card = False
    def request(self, path, params=None, body=None):
        if body is not None:
            self.writes += 1
            self.order.update(body)
            self.events.insert(0, {'eventType': 'update', 'source': 'remap-1.2',
                'audit': ref('audit', NEW), 'diff': {'salesChannel': {'newValue': body['salesChannel']}}})
            if self.lose_response:
                raise w.SiteDeferred('ms_transport_TimeoutError')
            return self.order.copy()
        if path == '/entity/saleschannel/' + w.SITE_ID:
            return {'id': w.SITE_ID, 'name': 'Сайт'}
        if path == '/audit/' + AID:
            return {'id': AID, 'entityType': 'customerorder', 'eventType': 'create', 'info': INFO}
        if path == '/audit/' + NEW:
            return {'info': {'additionalInfo': '(1.2.3.4, ' + w.SITE_UA + ')'}}
        result = self.order.copy()
        if self.change_card:
            self.order['sum'] += 1
        return result
    def rows(self, path, params=None):
        return list(self.events) if path.endswith('/audit') else [self.order.copy()]

class Decisions(unittest.TestCase):
    def test_source_exact(self):
        self.assertTrue(w.site_source(INFO))
        for text in ['(x, WordPress/1; https://2mymoods.ru.evil.test)',
                     '(x, WordPress/1; https://evil.test/2mymoods.ru)',
                     '(x, Other/1; https://2mymoods.ru)',
                     '(x, WordPress/1; https://2mymoods.ru@evil.test)']:
            self.assertFalse(w.site_source({'additionalInfo': text}))
    def test_eligible(self):
        self.assertEqual(w.site_decision(order(), [creation()], INFO), 'eligible')
    def test_manual_clear_and_api_changes(self):
        for source in ['app', 'remap-1.2', 'scriptor']:
            e = {'eventType': 'update', 'source': source, 'diff': {'salesChannel': {'newValue': None}}}
            self.assertEqual(w.site_decision(order(), [creation(), e], INFO), 'skip_prior_channel_change')
    def test_missing_creation(self):
        with self.assertRaises(w.SiteDeferred):
            w.site_decision(order(), [], INFO)
    def test_non_site_creation(self):
        c = creation(); c['source'] = 'app'
        self.assertEqual(w.site_decision(order(), [c], INFO), 'skip_other_source')
    def test_extra_channel(self):
        o = order(); o['attributes'] = [{'id': w.SITE_ATTR, 'value': {'name': 'Шоурум'}}]
        self.assertEqual(w.site_decision(o, [creation()], INFO), 'conflict_extra_channel')
    def test_reference_hash_and_list_order(self):
        a = order(); b = order(); b['agent'] = ref('entity/counterparty', NEW)
        self.assertNotEqual(w.site_core(a), w.site_core(b))
        self.assertEqual(w.site_hash([1, 2]), w.site_hash([2, 1]))
    def test_ambiguous_diff(self):
        with self.assertRaises(w.SiteDeferred):
            w.site_decision(order(), [creation(), {'eventType': 'update'}], INFO)

class Pages(unittest.TestCase):
    def test_partial_history_is_error(self):
        api = w.SiteAPI.__new__(w.SiteAPI)
        pages = iter([{'meta': {'size': 2}, 'rows': [{'id': 'a'}]}, {'meta': {'size': 2}, 'rows': []}])
        api.request = lambda *a, **kw: next(pages)
        with self.assertRaises(w.SiteDeferred): api.rows('/audit')
    def test_duplicate_page_is_error(self):
        api = w.SiteAPI.__new__(w.SiteAPI)
        api.request = lambda *a, **kw: {'meta': {'size': 2}, 'rows': [{'id': 'a'}]}
        with self.assertRaises(w.SiteDeferred): api.rows('/audit')

class Worker(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = FakeAPI()
        self.worker = w.SiteWorker(self.temp.name, self.api)
        self.worker.enqueue([order()])
    def tearDown(self):
        self.worker.db.close(); self.worker.lock.close(); self.temp.cleanup()
    def job(self):
        return self.worker.db.execute('SELECT * FROM jobs WHERE id=?', (OID,)).fetchone()
    def test_dry_run(self):
        self.worker.process(self.job())
        self.assertEqual(self.api.writes, 0)
        self.assertEqual(self.job()['reason'], 'dry_run_eligible')
    def test_timeout_not_retried(self):
        self.api.lose_response = True
        with self.assertRaises(w.SiteDeferred): self.worker.process(self.job(), True)
        self.assertEqual(self.job()['state'], 'intent')
        self.worker.process(self.job(), True)
        self.assertEqual(self.api.writes, 1)
        self.assertEqual(self.job()['state'], 'verifying')
    def test_changed_card_no_write(self):
        self.api.change_card = True
        with self.assertRaises(w.SiteDeferred): self.worker.process(self.job(), True)
        self.assertEqual(self.api.writes, 0)
    def test_manual_change_after_write(self):
        self.worker.process(self.job(), True)
        self.api.events.insert(0, {'eventType': 'update', 'source': 'app', 'audit': ref('audit', '22222222-2222-2222-2222-222222222222'),
                                  'diff': {'salesChannel': {'newValue': self.api.order['salesChannel']}}})
        self.worker.verify(self.job())
        self.assertEqual(self.job()['state'], 'conflict')
        self.assertEqual(self.api.writes, 1)
    def test_finished_order_never_requeued(self):
        self.worker.mark(OID, 'done', 'test')
        self.worker.enqueue([order()])
        self.worker.cycle(True, [OID])
        self.assertEqual(self.api.writes, 0)
    def test_cursor_not_advanced_on_failed_discovery(self):
        self.api.rows = lambda *a, **kw: (_ for _ in ()).throw(w.SiteDeferred('incomplete'))
        with self.assertRaises(w.SiteDeferred): self.worker.discover()
        self.assertEqual(self.worker.setting('cursor'), '')
    def test_single_worker(self):
        with self.assertRaises(w.SiteFatal): w.SiteWorker(self.temp.name, self.api)
    def test_write_over_racing_nonempty_value_is_conflict(self):
        self.worker.process(self.job(), True)
        self.api.events[0]['diff']['salesChannel']['oldValue'] = ref('entity/saleschannel', AID)
        self.worker.verify(self.job())
        self.assertEqual(self.job()['state'], 'conflict')
    def test_restart_after_uncertain_write(self):
        self.api.lose_response = True
        with self.assertRaises(w.SiteDeferred): self.worker.process(self.job(), True)
        self.worker.db.close(); self.worker.lock.close()
        self.worker = w.SiteWorker(self.temp.name, self.api)
        self.worker.process(self.job(), True)
        self.assertEqual(self.api.writes, 1)
        self.assertEqual(self.job()['state'], 'verifying')
    def test_filled_discovery_preserved(self):
        o = order(); o['id'] = NEW; o['salesChannel'] = ref('entity/saleschannel', AID)
        self.worker.enqueue([o])
        row = self.worker.db.execute('SELECT state FROM jobs WHERE id=?', (NEW,)).fetchone()
        self.assertEqual(row[0], 'skipped')
    def test_unknown_write_not_retried(self):
        with self.worker.db:
            self.worker.db.execute("UPDATE jobs SET state='intent',intent_at=?,before_hash=?,audit_before='[]'",
                                   (time.time()-400, w.site_core(order())))
        self.worker.process(self.job(), True)
        self.assertEqual(self.api.writes, 0)
        self.assertEqual(self.job()['state'], 'conflict')
    def test_cooldown_prevents_requests(self):
        self.worker.set('cooldown_until', time.time()+120)
        self.api.request = lambda *a, **kw: self.fail('request during cooldown')
        self.worker.cycle(True)
    def test_halt_persisted(self):
        self.worker.set('halt', 'ms_access_403')
        self.api.request = lambda *a, **kw: self.fail('request after halt')
        with self.assertRaises(w.SiteFatal): self.worker.cycle(True)
    def test_delayed_success(self):
        self.worker.process(self.job(), True)
        with self.worker.db:
            self.worker.db.execute('UPDATE jobs SET intent_at=?', (time.time()-130,))
        self.worker.verify(self.job())
        self.assertEqual(self.job()['state'], 'done')

if __name__ == '__main__': unittest.main()
