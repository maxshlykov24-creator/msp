#!/usr/bin/env python3
"""2MY CDEK: durable order queue, legacy migration and read-only carrier API.

Only customerorder tracking/status/cost attributes may be written. Costs are
disabled until an operator records financial verification in the service env.
No shipment creation, CRM calls or primary document status changes.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import decimal
import fcntl
import gzip
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MS_BASE = 'https://api.moysklad.ru/api/remap/1.2'
CDEK_BASE = 'https://api.cdek.ru/v2'
LEGACY = 'c6feb69e-de2a-11ef-0a80-0d360015ef35'
TRACK = '62fb5fd9-d687-11f1-0a80-0cda004104fc'
COST = '80514094-d687-11f1-0a80-18b400405108'
STATUS = '6fb78ca1-d688-11f1-0a80-0cda0041551a'
INTERVAL = 900
WEBHOOK_PATH = '/integrations/cdek/moysklad'


class SyncError(Exception):
    def __init__(self, code, retry=True):
        self.code, self.retry = code, retry
        super().__init__(code)


def values(order):
    return {a['id']: a.get('value') for a in order.get('attributes', [])}


def tracking(order):
    return str(values(order).get(TRACK) or '').strip()


def valid_track(track):
    return bool(re.fullmatch(r'[0-9]+', track))


def payload(changes):
    types = {TRACK: 'string', STATUS: 'string', COST: 'double'}
    return {'attributes': [
        {'meta': {'href': MS_BASE + '/entity/customerorder/metadata/attributes/' + key,
                  'type': 'attributemetadata', 'mediaType': 'application/json'},
         'id': key, 'type': types[key], 'value': value}
        for key, value in changes.items()]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def protected(order):
    # updated/meta describe API transport and necessarily change after a write.
    out = {k: v for k, v in order.items() if k not in ('meta', 'updated', 'attributes')}
    out['attributes'] = sorted([a for a in order.get('attributes', [])
                                if a['id'] not in (TRACK, COST, STATUS)], key=lambda a: a['id'])
    return out


def carrier_result(body):
    entity = body.get('entity')
    if not isinstance(entity, dict) or not entity:
        raise SyncError('cdek_order_unavailable', False)
    entries = []
    for s in entity.get('statuses') or []:
        if s.get('deleted') is True:
            continue
        try:
            stamp = dt.datetime.fromisoformat(s['date_time'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise SyncError('ambiguous_status_time', False)
        entries.append((stamp.timestamp(), s))
    status = None
    status_error = None
    if entries:
        latest = max(t for t, _ in entries)
        latest_rows = [s for t, s in entries if t == latest]
        if len({(s.get('code'), s.get('name')) for s in latest_rows}) != 1:
            status_error = 'ambiguous_status'
        elif not latest_rows[0].get('name'):
            status_error = 'missing_status_name'
        else:
            status = latest_rows[0]
    else:
        status_error = 'missing_status'
    raw_cost = (entity.get('delivery_detail') or {}).get('total_sum')
    cost, cost_error = None, None
    if raw_cost is not None:
        try:
            if isinstance(raw_cost, bool):
                raise ValueError()
            n = decimal.Decimal(str(raw_cost))
            if not n.is_finite() or n < 0:
                raise ValueError()
            cost = float(n.quantize(decimal.Decimal('.01'), rounding=decimal.ROUND_HALF_UP))
        except (decimal.InvalidOperation, ValueError):
            cost_error = 'invalid_cost'
    else:
        cost_error = 'missing_cost'
    return {'track': str(entity.get('cdek_number') or ''), 'uuid': entity.get('uuid'),
            'status': status, 'status_error': status_error,
            'cost': cost, 'cost_error': cost_error,
            'breakdown': {'delivery_sum': (entity.get('delivery_detail') or {}).get('delivery_sum'),
                          'services': [{k: s.get(k) for k in ('code', 'total_sum', 'vat_sum')}
                                       for s in entity.get('services') or []],
                          'is_return': bool(entity.get('is_return')),
                          'has_reverse_order': bool(entity.get('has_reverse_order'))}}


class API:
    def __init__(self):
        self.ms_token = os.environ.get('MOYSKLAD_TOKEN', '')
        self.client_id = os.environ.get('CDEK_CLIENT_ID', '')
        self.client_secret = os.environ.get('CDEK_CLIENT_SECRET', '')
        if not all((self.ms_token, self.client_id, self.client_secret)):
            raise SyncError('configuration_missing', False)
        self.token, self.expires = '', 0
        self.token_gate = threading.Lock()
        self.gate = threading.Lock()
        self.next_request = 0

    def request(self, method, url, token='', body=None, form=False):
        with self.gate:
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + .25
        data = None
        headers = {'Accept-Encoding': 'gzip', 'Accept': 'application/json;charset=utf-8'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers,
                                                               method=method), timeout=30) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # Neither error bodies nor request URLs (which include tracks) enter logs.
            code = 'ms' if url.startswith(MS_BASE) else 'cdek'
            raise SyncError(f'{code}_http_{e.code}', e.code in (401, 408, 429) or e.code >= 500)
        except (OSError, ValueError):
            raise SyncError('network_or_json_error')

    def ms(self, method, path, body=None):
        return self.request(method, MS_BASE + path, self.ms_token, body)

    def order(self, oid):
        return self.ms('GET', '/entity/customerorder/' + str(uuid.UUID(oid)))

    def orders(self, filter_value=None):
        offset = 0
        while True:
            q = {'limit': 1000, 'offset': offset, 'order': 'id,asc'}
            if filter_value:
                q['filter'] = filter_value
            body = self.ms('GET', '/entity/customerorder?' + urllib.parse.urlencode(q))
            if 'rows' not in body or not isinstance(body['rows'], list):
                raise SyncError('incomplete_order_scan')
            yield from body['rows']
            count = len(body['rows'])
            offset += count
            if count < 1000:
                if offset < body.get('meta', {}).get('size', offset):
                    raise SyncError('incomplete_order_scan')
                return

    def peers(self, track):
        # Exact attribute filter catches duplicates even before their event is processed.
        field = MS_BASE + '/entity/customerorder/metadata/attributes/' + TRACK
        return [o for o in self.orders(field + '~' + track) if tracking(o) == track]

    def validate_metadata(self):
        body = self.ms('GET', '/entity/customerorder/metadata/attributes')
        fields = {a['id']: a for a in body.get('rows', [])}
        for fid, name, kind in [(LEGACY, 'Трек-номер LiveInform', 'string'),
                                (TRACK, 'Трек-номер', 'string'),
                                (COST, 'Факт. стоимость СДЭК', 'double'),
                                (STATUS, 'Статус СДЭК', 'string')]:
            if fields.get(fid, {}).get('name') != name or fields.get(fid, {}).get('type') != kind:
                raise SyncError('metadata_mismatch', False)

    def write(self, oid, changes):
        return self.ms('PUT', '/entity/customerorder/' + str(uuid.UUID(oid)), payload(changes))

    def carrier(self, track):
        for attempt in range(2):
            with self.token_gate:
                if time.time() >= self.expires:
                    b = self.request('POST', CDEK_BASE + '/oauth/token', body={
                        'grant_type': 'client_credentials', 'client_id': self.client_id,
                        'client_secret': self.client_secret}, form=True)
                    self.token = b.get('access_token', '')
                    if not self.token:
                        raise SyncError('cdek_auth_failed')
                    self.expires = time.time() + max(1, int(b.get('expires_in', 3600)) - 60)
            try:
                b = self.request('GET', CDEK_BASE + '/orders?' +
                                 urllib.parse.urlencode({'cdek_number': track}), self.token)
                result = carrier_result(b)
                if result['track'] != track:
                    raise SyncError('carrier_track_mismatch', False)
                return result
            except SyncError as e:
                if e.code == 'cdek_http_401' and attempt == 0:
                    self.expires = 0
                    continue
                raise


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('CDEK_STATE_DIR', '/var/lib/2my-cdek'))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'state.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS orders(
              id TEXT PRIMARY KEY, track TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              owner_track TEXT, own_status TEXT, own_cost REAL, last_success REAL,
              error TEXT, observed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, priority INTEGER NOT NULL, due REAL NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 1,
              created REAL NOT NULL, lease_until REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS journal(
              id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, at REAL,
              track TEXT, before_json TEXT, changes_json TEXT, state TEXT);
            CREATE TABLE IF NOT EXISTS baseline(id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS migrated(id TEXT PRIMARY KEY, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS cache(track TEXT PRIMARY KEY, at REAL, result TEXT);
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'lease_until' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN lease_until REAL NOT NULL DEFAULT 0')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))
                return value
            r = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return r['value'] if r else None

    def enqueue(self, oid, priority=0, force=True):
        with self.db() as db:
            db.execute('''INSERT INTO jobs(id,priority,due,created) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET priority=MIN(priority,excluded.priority),
                  due=MIN(due,excluded.due), generation=generation+?''',
                       (oid, priority, time.time(), time.time(), int(force)))

    def row(self, oid):
        with self.db() as db:
            r = db.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
            return dict(r) if r else None

    def observe(self, order, priority=10, force=False):
        oid, track = order['id'], tracking(order)
        prior = self.row(oid)
        if not prior and not track:
            return
        changed = not prior or track != prior['track']
        with self.db() as db:
            db.execute('''INSERT INTO orders(id,track,observed) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET track=excluded.track,
                revision=revision+(track!=excluded.track),observed=excluded.observed''',
                       (oid, track, time.time()))
        if changed or force:
            self.enqueue(oid, priority)
        if changed:
            # Re-evaluate both ends of a changed membership.
            with self.db() as db:
                ids = [r['id'] for r in db.execute('SELECT id FROM orders WHERE track IN (?,?)',
                                                   (track, prior['track'] if prior else track))]
            for other in ids:
                if other != oid:
                    self.enqueue(other, priority)

    def audit(self, order):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO baseline VALUES(?,?)',
                       (order['id'], json.dumps(order, ensure_ascii=False)))

    def summary(self):
        with self.db() as db:
            errors = [dict(r) for r in db.execute(
                'SELECT id,error,last_success FROM orders WHERE error IS NOT NULL ORDER BY id')]
            jobs = db.execute('SELECT COUNT(*),MIN(due) FROM jobs').fetchone()
            oldest = db.execute('SELECT MIN(created) FROM jobs').fetchone()[0]
            migration_conflicts = [r['id'] for r in db.execute(
                'SELECT id FROM migrated WHERE result="legacy_conflict"')]
            total = db.execute('SELECT COUNT(*) FROM orders WHERE track!=""').fetchone()[0]
        return {'tracked_orders': total, 'queued': jobs[0],
                'oldest_due': jobs[1], 'errors': errors,
                'oldest_job_age_seconds': round(time.time() - oldest, 1) if oldest else 0,
                'migration_conflicts': migration_conflicts,
                'last_scan': self.setting('last_scan'), 'scan_error': self.setting('scan_error'),
                'last_cycle_seconds': self.setting('last_cycle_seconds'),
                'last_idle': self.setting('last_idle'),
                'worker_error': self.setting('worker_error'),
                'migration_complete': self.setting('migration_complete') == '1',
                'cost_enabled': (os.environ.get('CDEK_COST_VERIFIED') == '1'
                                 and os.environ.get('CDEK_COST_CURRENCY') == 'RUB'
                                 and bool(os.environ.get('CDEK_FINANCIAL_VERIFICATION'))),
                'financial_verification': os.environ.get('CDEK_FINANCIAL_VERIFICATION', '')}


class Engine:
    def __init__(self, store, api, cost_enabled=False):
        self.store, self.api, self.cost_enabled = store, api, cost_enabled
        self.write_lock = threading.Lock()
        self.track_locks = {}
        self.track_locks_gate = threading.Lock()

    def write_verified(self, order, changes, expected_track, owner_track=None):
        oid = order['id']
        latest = self.api.order(oid)
        if tracking(latest) != expected_track:
            self.store.observe(latest, 0)
            raise SyncError('track_changed')
        current = values(latest)
        changes = {k: v for k, v in changes.items() if current.get(k) != v}
        if not changes:
            return latest
        self.store.audit(latest)
        with self.store.db() as db:
            cur = db.execute('INSERT INTO journal(order_id,at,track,before_json,changes_json,state) '
                             'VALUES(?,?,?,?,?,?)', (oid, time.time(), expected_track,
                             json.dumps({k: current.get(k) for k in changes}), json.dumps(changes), 'pending'))
            jid = cur.lastrowid
        try:
            self.api.write(oid, changes)
        except SyncError:
            # A timeout can mean that the write succeeded. Always read back.
            try:
                result = self.api.order(oid)
            except SyncError:
                raise SyncError('ambiguous_write')
            if not all(values(result).get(k) == v for k, v in changes.items()):
                raise
        else:
            result = self.api.order(oid)
        actual = values(result)
        if not all(actual.get(k) == v for k, v in changes.items()):
            raise SyncError('write_readback_mismatch')
        with self.store.db() as db:
            db.execute('UPDATE journal SET state=? WHERE id=?', ('verified', jid))
            if STATUS in changes:
                db.execute('UPDATE orders SET own_status=?,owner_track=? WHERE id=?',
                           (changes[STATUS], owner_track if owner_track is not None else expected_track, oid))
            if COST in changes:
                db.execute('UPDATE orders SET own_cost=?,owner_track=? WHERE id=?',
                           (changes[COST], owner_track if owner_track is not None else expected_track, oid))
        if tracking(result) != expected_track:
            self.store.observe(result, 0)
            raise SyncError('track_changed_after_write')
        return result

    def recover_pending(self, order):
        # Recover ownership after a process crash between remote write and local commit.
        with self.store.db() as db:
            rows = db.execute('SELECT * FROM journal WHERE order_id=? AND state="pending" ORDER BY id',
                              (order['id'],)).fetchall()
        for row in rows:
            changes = json.loads(row['changes_json'])
            matching = {k: v for k, v in changes.items() if values(order).get(k) == v}
            with self.store.db() as db:
                db.execute('UPDATE journal SET state=? WHERE id=?',
                           ('recovered' if len(matching) == len(changes) else 'unconfirmed', row['id']))
                for key, col in [(STATUS, 'own_status'), (COST, 'own_cost')]:
                    if key in matching:
                        db.execute(f'UPDATE orders SET {col}=?,owner_track=? WHERE id=?',
                                   (matching[key], row['track'], order['id']))

    def process(self, oid):
        order = self.api.order(oid)
        self.store.observe(order)
        if not self.store.row(oid):
            return
        self.recover_pending(order)
        row = self.store.row(oid)
        track = tracking(order)
        if track and values(order).get(TRACK) != track:
            order = self.write_verified(order, {TRACK: track}, track)
        own = values(order)
        if row['owner_track'] is not None and row['owner_track'] != track:
            clear = {}
            if row['own_status'] is not None and own.get(STATUS) == row['own_status']:
                clear[STATUS] = None
            if row['own_cost'] is not None and own.get(COST) == row['own_cost']:
                clear[COST] = None
            order = self.write_verified(order, clear, track)
            with self.store.db() as db:
                db.execute('UPDATE orders SET own_status=NULL,own_cost=NULL,owner_track=? WHERE id=?',
                           (track, oid))
        if not track:
            self.finish(oid, None)
            return
        if not valid_track(track):
            raise SyncError('invalid_track_format', False)
        # Complete live lookup is mandatory before any cost write/duplicate cleanup.
        peers = self.api.peers(track)
        if not any(p['id'] == oid for p in peers):
            raise SyncError('track_changed_during_peer_lookup')
        for peer in peers:
            self.store.observe(peer)
        duplicate = len(peers) > 1
        if duplicate:
            current = values(order)
            row = self.store.row(oid)
            if row['own_cost'] is not None and current.get(COST) == row['own_cost']:
                order = self.write_verified(order, {COST: None}, track)
        with self.track_locks_gate:
            track_lock = self.track_locks.setdefault(track, threading.Lock())
        with track_lock:
            with self.store.db() as db:
                cached = db.execute('SELECT * FROM cache WHERE track=?', (track,)).fetchone()
            cycle = float(self.store.setting('cycle_started') or 0)
            if cached and cached['at'] >= max(time.time() - INTERVAL, cycle):
                result = json.loads(cached['result'])
            else:
                result = self.api.carrier(track)
                with self.store.db() as db:
                    db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',
                               (track, time.time(), json.dumps(result)))
        changes = {}
        errors = []
        if result['status']:
            changes[STATUS] = result['status']['name']
        else:
            errors.append(result['status_error'])
        if duplicate:
            errors.append('duplicate_track_cost_blocked')
        elif self.cost_enabled:
            if result['cost_error']:
                errors.append(result['cost_error'])
            else:
                changes[COST] = result['cost']
        # Recheck peers just before financial write; a new peer event may be queued.
        if COST in changes and len(self.api.peers(track)) != 1:
            changes.pop(COST)
            errors.append('duplicate_track_cost_blocked')
        self.write_verified(order, changes, track)
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       ('status:' + oid, json.dumps(result['status'])))
        self.finish(oid, ','.join(filter(None, errors)) or None)

    def finish(self, oid, error):
        with self.store.db() as db:
            db.execute('UPDATE orders SET last_success=?,error=? WHERE id=?', (time.time(), error, oid))

    def one(self):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE due<=? AND lease_until<=? '
                             'ORDER BY priority,due LIMIT 1', (time.time(), time.time())).fetchone()
            if job:
                db.execute('UPDATE jobs SET lease_until=? WHERE id=?', (time.time() + 600, job['id']))
        if not job:
            return False
        try:
            self.process(job['id'])
        except SyncError as e:
            attempts = job['attempts'] + 1
            delay = min(INTERVAL, 5 * 2 ** min(attempts, 8)) if e.retry else INTERVAL
            with self.store.db() as db:
                db.execute('UPDATE orders SET error=? WHERE id=?', (e.code, job['id']))
                if e.retry:
                    db.execute('UPDATE jobs SET due=?,attempts=? WHERE id=? AND generation=?',
                               (time.time() + delay, attempts, job['id'], job['generation']))
                else:
                    db.execute('DELETE FROM jobs WHERE id=? AND generation=?',
                               (job['id'], job['generation']))
        else:
            with self.store.db() as db:
                db.execute('DELETE FROM jobs WHERE id=? AND generation=?',
                           (job['id'], job['generation']))
        with self.store.db() as db:
            db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
        return True

    def scan(self):
        started = time.monotonic()
        self.store.setting('cycle_started', time.time())
        for order in self.api.orders():
            self.store.observe(order, 10)
        # Queue existing rows once per completed sweep, not while partially scanning.
        with self.store.db() as db:
            rows = [r['id'] for r in db.execute('SELECT id FROM orders')]
        for oid in rows:
            self.store.enqueue(oid, 10, force=False)
        self.store.setting('last_scan', time.time())
        self.store.setting('scan_error', '')
        self.store.setting('last_scan_seconds', round(time.monotonic() - started, 2))

    def migrate(self, apply=False, limit=0):
        if self.store.setting('migration_complete') == '1':
            return {'already_complete': True}
        counts = {'candidates': 0, 'copied': 0, 'conflicts': 0, 'failures': 0}
        candidates = []
        snapshots = {}
        # No mutation before a full successful scan and durable baseline.
        for order in self.api.orders():
            if str(values(order).get(LEGACY) or '').strip():
                self.store.audit(order)
                candidates.append(order['id'])
                snapshots[order['id']] = order
        total_candidates = len(candidates)
        if limit:
            candidates = candidates[:limit]

        def migrate_one(oid):
            with self.store.db() as db:
                done = db.execute('SELECT result FROM migrated WHERE id=?', (oid,)).fetchone()
            if done:
                return 'already'
            order = self.api.order(oid) if apply else snapshots[oid]
            legacy = str(values(order).get(LEGACY) or '').strip()
            new = tracking(order)
            result = 'same_or_empty'
            if new and legacy and new != legacy:
                result = 'legacy_conflict'
            elif legacy and not new:
                result = 'copied'
                if apply:
                    latest = self.api.order(oid)
                    if tracking(latest):
                        raise SyncError('migration_concurrent_track_change', False)
                    if str(values(latest).get(LEGACY) or '').strip() != legacy:
                        raise SyncError('migration_concurrent_legacy_change', False)
                    order = latest
                    self.store.audit(order)
                    with self.store.db() as db:
                        journal = db.execute(
                            'INSERT INTO journal(order_id,at,track,before_json,changes_json,state) '
                            'VALUES(?,?,?,?,?,?)', (oid, time.time(), legacy,
                            json.dumps({TRACK: values(order).get(TRACK)}),
                            json.dumps({TRACK: legacy}), 'pending')).lastrowid
                    try:
                        self.api.write(oid, {TRACK: legacy})
                    except SyncError:
                        check = self.api.order(oid)
                        if tracking(check) != legacy:
                            raise
                    check = self.api.order(oid)
                    if tracking(check) != legacy:
                        raise SyncError('migration_readback_mismatch')
                    if digest(protected(order)) != digest(protected(check)):
                        raise SyncError('migration_concurrent_document_change', False)
                    with self.store.db() as db:
                        db.execute('UPDATE journal SET state="verified" WHERE id=?', (journal,))
                    order = check
            if apply:
                with self.store.db() as db:
                    db.execute('INSERT OR REPLACE INTO migrated VALUES(?,?)', (oid, result))
                self.store.observe(order)
            return result

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=4 if apply else 1) as pool:
            tasks = [pool.submit(migrate_one, oid) for oid in candidates]
            for task in as_completed(tasks):
                counts['candidates'] += 1
                try:
                    result = task.result()
                    counts['copied'] += result == 'copied'
                    counts['conflicts'] += result == 'legacy_conflict'
                except SyncError:
                    counts['failures'] += 1
                if apply and counts['candidates'] % 25 == 0:
                    print(json.dumps(counts), flush=True)
        if apply and not counts['failures'] and len(candidates) == total_candidates:
            self.store.setting('migration_complete', '1')
        return counts


def event_ids(body):
    result = set()
    if not isinstance(body, dict):
        raise ValueError('body')
    # Supports native MS webhooks and user scenarios sending {id: ...}.
    if body.get('id'):
        result.add(str(uuid.UUID(str(body['id']))))
    for event in body.get('events', []):
        href = event.get('meta', {}).get('href', '')
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme != 'https' or parsed.netloc != 'api.moysklad.ru':
            raise ValueError('event_host')
        prefix = '/api/remap/1.2/entity/customerorder/'
        if not parsed.path.startswith(prefix):
            raise ValueError('event_type')
        result.add(str(uuid.UUID(parsed.path[len(prefix):])))
    if not result or len(result) > 1000:
        raise ValueError('events')
    return sorted(result)


def route(handler, method):
    path = handler.path.split('?', 1)[0]
    if path not in (WEBHOOK_PATH, '/api/cdek/status'):
        return False
    if path == '/api/cdek/status':
        if method != 'GET' or not handler._authed():
            handler._json(401, {'error': 'Требуется вход'})
        else:
            handler._json(200, Store().summary())
        return True
    if method != 'POST':
        handler._json(405, {'error': 'method'})
        return True
    given = handler.headers.get('X-Cdek-Webhook-Token') or ''
    secret = os.environ.get('CDEK_WEBHOOK_TOKEN', '')
    if not secret:
        handler._json(403, {'error': 'forbidden'})
        return True
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 262144:
            raise ValueError('length')
        body = json.loads(handler.rfile.read(length))
        given = given or (body.get('token', '') if isinstance(body, dict) else '')
        if not isinstance(given, str) or not hmac.compare_digest(given, secret):
            handler._json(403, {'error': 'forbidden'})
            return True
        ids = event_ids(body)
        store = Store()
        for oid in ids:
            store.enqueue(oid, 0)
        handler._json(202, {'queued': len(ids)})
    except (ValueError, TypeError, KeyError, AttributeError):
        authenticated = isinstance(given, str) and hmac.compare_digest(given, secret)
        handler._json(400 if authenticated else 403, {'error': 'invalid_event'})
    except sqlite3.Error:
        handler._json(503, {'error': 'queue_unavailable'})
    return True


def run_worker(engine):
    with (engine.store.root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Worker already running')
        def scanner():
            while True:
                start = time.monotonic()
                try:
                    engine.scan()
                except SyncError as e:
                    engine.store.setting('scan_error', e.code)
                except Exception:
                    engine.store.setting('scan_error', 'unexpected_scan_error')
                time.sleep(max(1, INTERVAL - (time.monotonic() - start)))
        threading.Thread(target=scanner, daemon=True).start()
        cycle_start = None
        while True:
            if cycle_start is None:
                cycle_start = time.monotonic()
            try:
                worked = engine.one()
            except Exception:
                engine.store.setting('worker_error', 'unexpected_worker_error')
                time.sleep(5)
                continue
            if not worked:
                duration = round(time.monotonic() - cycle_start, 2)
                if duration > .1:
                    engine.store.setting('last_cycle_seconds', duration)
                engine.store.setting('last_idle', time.time())
                time.sleep(1)
                cycle_start = None


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['worker', 'scan', 'migrate', 'status', 'once'])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=0, help='Control subset of legacy migration')
    args = parser.parse_args()
    store = Store()
    if args.command == 'status':
        print(json.dumps(store.summary(), ensure_ascii=False))
        return
    costs = (os.environ.get('CDEK_COST_VERIFIED') == '1'
             and os.environ.get('CDEK_COST_CURRENCY') == 'RUB'
             and bool(os.environ.get('CDEK_FINANCIAL_VERIFICATION')))
    engine = Engine(store, API(), costs)
    engine.api.validate_metadata()
    if args.command == 'worker':
        run_worker(engine)
    elif args.command == 'migrate':
        with (store.root / 'worker.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(engine.migrate(args.apply, args.limit)))
    elif args.command == 'scan':
        engine.scan()
        print(json.dumps(store.summary(), ensure_ascii=False))
    elif args.command == 'once':
        with (store.root / 'worker.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps({'worked': engine.one()}))


if __name__ == '__main__':
    main()
