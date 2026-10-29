import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import production
import dashboard_gate


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = production.Engine(self.tmp.name)
        self.env = patch.dict(os.environ, {'DASHBOARD_BASIC_USER': 'test', 'DASHBOARD_COOKIE_SECRET': 'test-secret',
                                          'PRODUCTION_WEBHOOK_TOKEN': 'hook-test'})
        self.env.start()
        self.service_patch = patch.object(production, 'engine', return_value=self.engine)
        self.service_patch.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_gate.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.cookie = dashboard_gate.COOKIE + '=' + dashboard_gate.sign('test', int(time.time()) + 60)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def req(self, path, body=None, auth=False, headers=None):
        hdr = dict(headers or {})
        if auth: hdr['Cookie'] = self.cookie
        if body is not None: hdr['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + path, headers=hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_requires_session(self):
        self.assertEqual(self.req('/api/production')[0], 401)

    def test_authenticated_status(self):
        code, body = self.req('/api/production', auth=True)
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)['config']['enabled'])

    def test_write_requires_csrf_header(self):
        code, _ = self.req('/api/production/recipe', {'name': 'test'}, auth=True)
        self.assertEqual(code, 422)
        self.assertFalse(self.engine.recipes())

    def test_webhook_authentication(self):
        self.assertEqual(self.req('/hooks/production/move', {'id': str(uuid.uuid4())})[0], 403)

    def test_webhook_queues_without_stock_mutation(self):
        code, body = self.req('/hooks/production/move', {'id': str(uuid.uuid4())},
                              headers={'X-Production-Token': 'hook-test'})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['queued'], 1)
        self.assertEqual(self.engine.status()['jobs'][0]['status'], 'pending')

    def test_invalid_uuid_is_rejected(self):
        code, _ = self.req('/hooks/production/move', {'id': '../../secret'},
                           headers={'X-Production-Token': 'hook-test'})
        self.assertEqual(code, 422)

    def test_recipe_draft_can_be_saved(self):
        code, _ = self.req('/api/production/recipe', {'name': 'Черновик', 'stages': []},
                           auth=True, headers={'X-Production-Request': '1'})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.engine.recipes()), 1)


if __name__ == '__main__':
    unittest.main()
