#!/usr/bin/env python3
"""Производство 2MY: нормативы, очередь, воспроизводимые документы. Секреты вне БД."""
from __future__ import annotations

import copy
import gzip
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = 'https://api.moysklad.ru/api/remap/1.2'
TZ = ZoneInfo('Europe/Moscow')
TYPES = ('supply', 'move', 'loss', 'purchaseorder')
REASONS = ['Расход на производство', 'Производственный брак', 'Уработка материалов',
           'Порча', 'Утрата', 'Недостача при инвентаризации', 'Прочее']
STATES = {
    'move': ['Ожидает отправки', 'Передано на доработку', 'Доработка принята'],
    'supply': ['Ожидает обработки', 'На доработке', 'Завершена', 'Требует проверки'],
    'purchaseorder': ['Черновик', 'В работе', 'Завершён', 'Отменён', 'Завершён с недопоставкой'],
}
ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
RESERVED = re.compile(r'^(?:ВШ|МТ|СП-[ПВ])[-/]')
RETURN = re.compile(r'^(ВШ-\d+(?:-Э\d+)?)/(\d{2,})$')


class Review(Exception):
    """Остановить цепочку без угадывания и без потери уже выполненных шагов."""


class StockShortage(Review):
    """Недостаток остатков; только явная команда сотрудника разрешает списать в минус."""


class Transient(Review):
    """Сеть/лимит API: повторить из журнала с задержкой."""


def now():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')


def ident(value):
    href = (value or {}).get('meta', {}).get('href', '')
    return urllib.parse.urlparse(href).path.rstrip('/').rsplit('/', 1)[-1]


def ref(kind, value):
    if not ID.fullmatch(str(value)):
        raise Review('Некорректный идентификатор ' + kind)
    typ = 'state' if '/metadata/states' in kind else ('customentity' if kind.startswith('customentity/') else kind)
    return {'meta': {'href': f'{BASE}/entity/{kind}/{value}', 'type': typ, 'mediaType': 'application/json'}}


def dec(value):
    try:
        result = Decimal(str(value))
    except Exception:
        raise Review('Некорректное количество')
    if not result.is_finite():
        raise Review('Некорректное количество')
    return result


def quantities(lines):
    out = defaultdict(Decimal)
    for row in lines:
        key = ident(row.get('assortment'))
        if not ID.fullmatch(key):
            raise Review('Позиция без карточки товара')
        q = dec(row['quantity'])
        if q <= 0:
            raise Review('Количество должно быть положительным')
        out[key] += q
    return dict(out)


def remaining(sent, returned):
    result = dict(sent)
    for key, q in returned.items():
        if key not in result or q > result[key]:
            raise Review('Возвращено больше отправленного или товар из другой партии')
        result[key] -= q
    return {k: v for k, v in result.items() if v > 0}


def fingerprint(doc, positions, ignore_price=False):
    # Статус и номер меняются штатно; новые цепочки разрешают ручную цену после расчёта.
    body = {k: doc.get(k) for k in ('applicable', 'moment', 'organization', 'store',
                                    'sourceStore', 'targetStore', 'agent', 'purchaseOrder', 'overhead', 'rate')}
    body['positions'] = sorted((ident(p['assortment']), str(dec(p['quantity'])),
                                str(0 if ignore_price else p.get('price', 0))) for p in positions)
    if ignore_price:
        body['position_structure'] = sorted((p.get('id', ''), ident(p['assortment']),
            str(dec(p['quantity'])), str(p.get('discount', 0)), str(p.get('vat', 0)),
            bool(p.get('vatEnabled', False))) for p in positions)
        body['vatEnabled'] = doc.get('vatEnabled')
        body['vatIncluded'] = doc.get('vatIncluded')
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class MSClient:
    """Один запрос на запись: после timeout восстановление через syncId, не слепой POST."""
    def __init__(self):
        import lib
        self.token = os.environ.get('MOYSKLAD_TOKEN') or lib.load_env().get('MOYSKLAD_TOKEN')
        if not self.token:
            raise Review('Нет доступа к МоемСкладу')
        self.lock = threading.Lock()
        self.last = 0.

    def request(self, method, path, body=None, params=None):
        if not path.startswith('/') or path.startswith('//'):
            raise Review('Недопустимый API-путь')
        url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        with self.lock:
            time.sleep(max(0, self.last + .25 - time.monotonic()))
            req = urllib.request.Request(url, data=data, method=method, headers={
                'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                'Accept-Encoding': 'gzip', 'Accept': 'application/json;charset=utf-8'})
            try:
                with urllib.request.urlopen(req, timeout=40) as response:
                    raw = response.read()
                    if response.headers.get('Content-Encoding') == 'gzip':
                        raw = gzip.decompress(raw)
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                # Не выводить тело ответа с данными контрагентов.
                if exc.code in (429, 500, 502, 503, 504):
                    raise Transient(f'МойСклад: HTTP {exc.code}; ожидается повтор') from None
                raise Review(f'МойСклад: HTTP {exc.code}; действие не подтверждено') from None
            except (OSError, TimeoutError):
                raise Transient('Нет подтверждения ответа МоегоСклада; повтор через журнал') from None
            finally:
                self.last = time.monotonic()

    def get(self, path, **params):
        return self.request('GET', path, params=params or None)

    def rows(self, path, **params):
        out, offset = [], 0
        while True:
            page = self.get(path, limit=100, offset=offset, **params)
            chunk = page.get('rows', [])
            out.extend(chunk)
            offset += len(chunk)
            if offset >= page.get('meta', {}).get('size', offset) or not chunk:
                return out

    def positions(self, kind, value):
        return self.rows(f'/entity/{kind}/{value}/positions')


def recipe_changes(before, after):
    """Читаемые изменения только по данным, сохранённым в версии нормы."""
    changes = []
    def text(value):
        return str(value if value not in (None, '') else 'не указано')
    def change(label, old, new):
        if old != new:
            changes.append(f'{label}: {text(old)} → {text(new)}')
    if not before:
        changes.append('Создана норма')
        if after.get('assortment_id'):
            changes.append('Выбрана карточка МоегоСклада: ' + text(after.get('assortment_name')))
    elif before.get('assortment_id') != after.get('assortment_id'):
        changes.append('Карточка МоегоСклада: ' + text(before.get('assortment_name')) + ' → ' + text(after.get('assortment_name')))
    if before:
        change('Модель', before.get('model'), after.get('model'))
        change('Цвет', before.get('color'), after.get('color'))
        change('Размер', before.get('size'), after.get('size'))
    old_stages, new_stages = before.get('stages', []), after.get('stages', [])
    for i in range(max(len(old_stages), len(new_stages))):
        old = old_stages[i] if i < len(old_stages) else None
        new = new_stages[i] if i < len(new_stages) else None
        label = (new or old or {}).get('name') or f'Этап {i+1}'
        if old is None:
            changes.append(f'Добавлен этап: {label}')
            old = {}
        elif new is None:
            changes.append(f'Удалён этап: {label}')
            continue
        change(f'Этап {i+1}', old.get('name'), new.get('name'))
        change(f'{label} · фабрика', old.get('factory'), new.get('factory'))
        change(f'{label} · работа, ₽', old.get('work_cost', 0), new.get('work_cost', 0))
        def materials(stage):
            return {m.get('id') or m.get('name') or str(j): m for j, m in enumerate(stage.get('materials', []))}
        om, nm = materials(old), materials(new)
        def amount(m):
            return text(m.get('input_quantity', m.get('quantity'))) + ' ' + text(m.get('input_unit') or m.get('unit'))
        for key in om.keys() - nm.keys():
            changes.append(f'{label} · убран материал: {text(om[key].get("name"))} ({amount(om[key])})')
        for key in nm.keys() - om.keys():
            changes.append(f'{label} · добавлен материал: {text(nm[key].get("name"))} ({amount(nm[key])})')
        for key in om.keys() & nm.keys():
            change(f'{label} · {text(nm[key].get("name"))}', amount(om[key]), amount(nm[key]))
    if before and before.get('approved') != after.get('approved'):
        changes.append('Норма применена' if after.get('approved') else 'Норма снята с применения')
    return changes


class Engine:
    def __init__(self, folder=None, client=None):
        self.folder = Path(folder or os.environ.get('PRODUCTION_DATA', '/var/lib/2my-production'))
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.folder / 'production.sqlite3'
        self.client = client
        self.work_lock = threading.Lock()
        self.catalogue_lock = threading.Lock()
        self.catalogue_retry_at = 0
        self.catalogue_error = ""
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalogues(kind TEXT PRIMARY KEY,updated REAL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,version INTEGER,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipe_history(id TEXT,version INTEGER,at TEXT,body TEXT,PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS recipe_events(id TEXT,version INTEGER,at TEXT,actor TEXT,changes TEXT,PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS jobs(kind TEXT,id TEXT,due REAL,status TEXT,error TEXT,
              attempts INTEGER DEFAULT 0,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY,kind TEXT,doc_id TEXT,
              body TEXT NOT NULL,status TEXT NOT NULL,fingerprint TEXT);
            CREATE TABLE IF NOT EXISTS chains(id TEXT PRIMARY KEY,number INTEGER UNIQUE,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS number_counters(kind TEXT PRIMARY KEY,number INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS names(kind TEXT,id TEXT,number INTEGER,original TEXT,
              PRIMARY KEY(kind,id),UNIQUE(kind,number));
            CREATE TABLE IF NOT EXISTS receipt_numbers(
              id TEXT PRIMARY KEY,order_id TEXT NOT NULL,sequence INTEGER NOT NULL,label TEXT NOT NULL UNIQUE,
              UNIQUE(order_id,sequence));
            CREATE TABLE IF NOT EXISTS material_fifo_cache(id TEXT PRIMARY KEY, updated REAL, body TEXT NOT NULL,generation INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS audit(at REAL,kind TEXT,id TEXT,message TEXT);
            CREATE TABLE IF NOT EXISTS supplier_jobs(id TEXT PRIMARY KEY,status TEXT,due REAL,revision INTEGER DEFAULT 1,error TEXT DEFAULT '',attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS supplier_history(at REAL,id TEXT,before TEXT,after TEXT);
            ''')
            if 'generation' not in {r['name'] for r in db.execute('PRAGMA table_info(material_fifo_cache)')}:
                db.execute('ALTER TABLE material_fifo_cache ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
                self.invalidate_fifo(db)
            db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
            db.execute("UPDATE supplier_jobs SET status='pending' WHERE status='running'")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @property
    def ms(self):
        if self.client is None:
            self.client = MSClient()
        return self.client

    def config(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM kv WHERE key='config'").fetchone()
            snapshots = {r['kind']: json.loads(r['body']) for r in db.execute(
                "SELECT kind,body FROM catalogues WHERE kind IN ('counterparty','store')")}
        cfg = json.loads(row[0]) if row else {'enabled': False, 'delay': 1, 'factories': [], 'states': {}}
        agents = snapshots.get('counterparty')
        if agents is None or any('tags' not in agent for agent in agents):
            return cfg
        stores = {s['id']: s for s in snapshots.get('store', []) if not s.get('archived')}
        previous = cfg.get('factories', [])
        factories = []
        for agent in agents:
            if agent.get('archived') or not any(str(t).strip().casefold() == 'производство' for t in agent.get('tags', [])):
                continue
            matches = [f for f in previous if f.get('agent') == agent['id']]
            if not matches:
                matches = [f for f in previous if not f.get('agent') and f.get('name','').strip().casefold() == agent.get('name','').strip().casefold()]
            legacy = matches[0] if len(matches) == 1 else {}
            store = agent.get('production_store') or ''
            factories.append({'id': legacy.get('id', agent['id']), 'name': agent['name'], 'agent': agent['id'],
                              'agent_name': agent['name'], 'store': store, 'store_name': stores.get(store, {}).get('name', ''),
                              'store_ok': bool(store and store in stores)})
        counts = defaultdict(int)
        for factory in factories:
            if factory['store']:
                counts[factory['store']] += 1
        for factory in factories:
            if factory['store'] and counts[factory['store']] > 1:
                factory['store_ok'] = False
                factory['store_issue'] = 'Склад указан у нескольких фабрик'
        cfg['factories'] = sorted(factories, key=lambda f: f['name'].casefold())
        return cfg

    def set_config(self, config):
        # Справочник фабрик редактируется в МоемСкладе, а локальные ключи норм сохраняются.
        with self.db() as db:
            previous = db.execute("SELECT value FROM kv WHERE key='config'").fetchone()
        config['factories'] = json.loads(previous[0]).get('factories', []) if previous else config.get('factories', [])
        # Включение возможно только после настройки складов, статусов и даты старта.
        if config.get('enabled'):
            if os.environ.get('PRODUCTION_ALLOW_WRITES') != '1':
                raise Review('Запуск заблокирован до проверки рабочего процесса и контрольной партии')
            if not config.get('start_at') or not config.get('company_store') or not config.get('states'):
                raise Review('Перед включением нужны дата старта, склад компании и статусы')
            if not config.get('reason_attribute') or not config.get('reason_production'):
                raise Review('Не настроен справочник причин списания')
        config['delay'] = max(1, min(60, int(config.get('delay', 1))))
        for item in self.config().get('factories', []) if config.get('enabled') else []:
            for field in ('agent', 'store'):
                if not ID.fullmatch(str(item.get(field, ''))):
                    raise Review('Выбери контрагента и склад каждой фабрики')
            if item.get('store_ok') is False:
                raise Review('Проверь склад фабрики в МоемСкладе')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('config', json.dumps(config, ensure_ascii=False)))

    def recipes(self):
        with self.db() as db:
            result = sorted([json.loads(r[0]) for r in db.execute('SELECT body FROM recipes')], key=lambda r: r.get('name', ''))
            for recipe in result:
                events = [dict(h) for h in db.execute('SELECT version,at,actor,changes FROM recipe_events WHERE id=?', (recipe['id'],))]
                for event in events:
                    event['changes'] = json.loads(event['changes'])
                recorded = {e['version'] for e in events}
                snapshots = list(db.execute('SELECT version,at,body FROM recipe_history WHERE id=? ORDER BY version', (recipe['id'],)))
                for index, snapshot in enumerate(snapshots):
                    next_version = snapshots[index + 1]['version'] if index + 1 < len(snapshots) else recipe['version']
                    if next_version in recorded:
                        continue
                    following = json.loads(snapshots[index + 1]['body']) if index + 1 < len(snapshots) else recipe
                    changes = recipe_changes(json.loads(snapshot['body']), following)
                    if changes:
                        events.append({'version': next_version, 'at': snapshot['at'], 'actor': 'Автор не сохранён', 'changes': changes})
                recipe['history'] = sorted(events, key=lambda event: (event['at'], event['version']), reverse=True)
            return result

    @staticmethod
    def normalized_size(value):
        value = re.sub(r'\s*[-–—]\s*', '-', str(value or '').strip())
        value = re.sub(r'\s+', ' ', value)
        if not value or value.casefold() == 'без размера':
            return 'Без размера'
        return 'one size' if value.casefold() == 'one size' else value.upper()

    @classmethod
    def card_size(cls, item):
        for char in item.get('characteristics', []):
            if str(char.get('name', '')).strip().casefold() == 'размер':
                return cls.normalized_size(char.get('value'))
        return 'one size' if re.search(r'one size', item.get('name', ''), re.I) else 'Без размера'

    def reconcile_recipe_sizes(self, db, products, complete=False):
        """Publish catalogue changes and invalidations in the same transaction."""
        for row in db.execute('SELECT id,version,body FROM recipes').fetchall():
            before = json.loads(row['body'])
            key = before.get('assortment_id')
            if not key or not complete and key not in products:
                continue
            item = products.get(key)
            recipe = copy.deepcopy(before)
            unavailable = not item or item.get('archived')
            size = self.card_size(item) if item else before.get('size_watch', {}).get('size', self.normalized_size(before.get('size')))
            watch = before.get('size_watch')
            previous = watch.get('size') if watch else self.normalized_size(before.get('size'))
            unknown = not watch and not str(before.get('size') or '').strip()
            changed = size != previous
            existing = before.get('norm_review') or {}
            reason = 'card_unavailable' if unavailable else 'size_changed' if changed else 'baseline_unknown' if unknown else ''
            if reason and not (reason == 'card_unavailable' and existing.get('reason') == reason):
                recipe['approved'] = False
                mats = [m for st in recipe.get('stages', []) for m in st.get('materials', []) if m.get('role') != 'packaging']
                if changed:
                    for mat in mats:
                        if mat.get('size_dependent') is True:
                            mat['quantity'] = None
                            mat['input_quantity'] = None
                recipe['norm_review'] = {'reason': reason, 'old_size': previous, 'new_size': size,
                    'at': now(), 'needs_primary': any(not isinstance(m.get('size_dependent'), bool) for m in mats)}
            # A restored card still needs explicit confirmation; do not auto-approve.
            if not unavailable and existing.get('reason') == 'card_unavailable' and not reason:
                recipe['norm_review'] = dict(existing, reason='card_restored', new_size=size)
            revision = (watch or {}).get('revision', 0) + (1 if reason and recipe.get('norm_review') != existing else 0)
            recipe['size_watch'] = {'size': size, 'revision': revision}
            recipe['size'] = size
            if item:
                recipe['assortment_name'] = item.get('name', recipe.get('assortment_name', ''))
            if recipe == before:
                continue
            recipe['version'] = row['version'] + 1
            db.execute('INSERT OR IGNORE INTO recipe_history VALUES(?,?,?,?)', (row['id'], row['version'], now(), row['body']))
            db.execute('UPDATE recipes SET version=?,body=? WHERE id=?', (recipe['version'], json.dumps(recipe, ensure_ascii=False), row['id']))
            changes = [f'Размер: {previous} → {size}. Норма требует проверки.'] if reason else ['Обновлена привязка размера к карточке МоегоСклада']
            db.execute('INSERT INTO recipe_events VALUES(?,?,?,?,?)', (row['id'], recipe['version'], now(), 'Синхронизация МойСклада', json.dumps(changes, ensure_ascii=False)))

    def verify_live_sizes(self, keys):
        """Read current cards before approving or using a norm; no MS writes."""
        keys = set(filter(None, keys))
        if not keys:
            return
        with self.catalogue_lock:
            with self.db() as db:
                cached = db.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
                products = {x['id']: x for x in json.loads(cached['body'])} if cached else {}
            fresh = {}
            for key in sorted(keys):
                item = products.get(key)
                if not item or item.get('meta', {}).get('type') not in ('product', 'variant'):
                    raise Review('Обнови справочник: карточка изделия не найдена')
                try:
                    live = self.ms.get('/entity/' + item['meta']['type'] + '/' + key)
                except Exception as exc:
                    raise Review('Не удалось проверить актуальный размер в МоемСкладе. Повтори после обновления справочника.') from exc
                if live.get('id') != key:
                    raise Review('МойСклад вернул другую карточку изделия')
                fresh[key] = dict(item, name=live.get('name', item['name']), characteristics=live.get('characteristics', []), archived=live.get('archived', False))
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self.reconcile_recipe_sizes(db, fresh)
                products.update(fresh)
                db.execute("UPDATE catalogues SET body=? WHERE kind='assortment'", (json.dumps(list(products.values()), ensure_ascii=False),))

    def guard_chain_sizes(self, chain):
        if chain.get('complete'):
            return
        self.verify_live_sizes(ident(line['assortment']) for line in chain.get('lines', []))
        current = {r.get('assortment_id'): r for r in self.recipes()}
        for line in chain.get('lines', []):
            key = ident(line['assortment'])
            recipe = current.get(key, {})
            saved = line['recipe']
            old_watch = saved.get('size_watch')
            new_watch = recipe.get('size_watch')
            if recipe.get('norm_review') or not recipe.get('approved') or (old_watch != new_watch if old_watch else (new_watch or {}).get('revision', 0) > 0 or self.normalized_size(saved.get('size')) != (new_watch or {}).get('size')):
                raise Review('Размер или норма изменились после начала обработки. Цепочка приостановлена; нужна сверка уже выполненных списаний.')

    def save_recipe(self, recipe, db=None, propagate=True, actor=None):
        if db is None:
            self.verify_live_sizes([recipe.get('assortment_id')])
            with self.db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.save_recipe(recipe, connection, propagate, actor)
        key = str(recipe.get('id') or uuid.uuid4())
        recipe.pop('history', None)
        old_row = db.execute('SELECT version,body FROM recipes WHERE id=?', (key,)).fetchone()
        previous = json.loads(old_row['body']) if old_row else {}
        if old_row and recipe.get('version') != old_row['version']:
            raise Review('Норма уже изменена или изменился размер. Обнови страницу перед сохранением.')
        cached = db.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
        products = {x['id']: x for x in json.loads(cached['body'])} if cached else {}
        item = products.get(recipe.get('assortment_id'))
        rebound = previous and previous.get('assortment_id') != recipe.get('assortment_id')
        review = previous.get('norm_review')
        if rebound:
            review = {'reason': 'card_changed', 'old_size': previous.get('size'), 'new_size': self.card_size(item) if item else ''}
        if item:
            size = self.card_size(item)
            if self.normalized_size(recipe.get('size')) != size:
                raise Review('Размер в МоемСкладе изменился. Обнови страницу перед сохранением.')
            recipe['size_watch'] = previous.get('size_watch', {'size': size, 'revision': 0}) if not rebound else {'size': size, 'revision': previous.get('size_watch', {}).get('revision', 0) + 1}
            recipe['size'] = size
            recipe['assortment_name'] = item.get('name', recipe.get('assortment_name', ''))
        if recipe.get('approved') and review:
            if not recipe.pop('confirm_size_review', False):
                raise Review('Подтверди проверку нормы для актуального размера')
            mats = [m for st in recipe.get('stages', []) for m in st.get('materials', []) if m.get('role') != 'packaging']
            if any(not isinstance(m.get('size_dependent'), bool) for m in mats):
                raise Review('Укажи назначение каждого материала: основная пряжа или фурнитура')
            recipe.pop('norm_review', None)
        elif review:
            recipe['norm_review'] = review
        else:
            recipe.pop('norm_review', None)
        recipe.pop('confirm_size_review', None)

        # Display units never become stock units without dimensional conversion.
        units = {'г': ('mass', 1), 'грамм': ('mass', 1), 'гр': ('mass', 1),
                 'кг': ('mass', 1000), 'м': ('length', 1), 'метр': ('length', 1),
                 'см': ('length', .01), 'шт': ('piece', 1), 'шт.': ('piece', 1)}
        for stage_index, stage in enumerate(recipe.get('stages', []), 1):
            for material in stage.get('materials', []):
                if recipe.get('approved') and not ID.fullmatch(str(material.get('id') or '')):
                    raise Review(f'Этап {stage_index}: выбери материал или убери пустую строку')
                if 'input_quantity' not in material:
                    continue
                if material['input_quantity'] in (None, ''):
                    if recipe.get('approved'):
                        raise Review(f'Этап {stage_index}: укажи расход материала {material.get("name") or "без названия"} больше нуля')
                    material['quantity'] = None
                    continue
                source = str(material.get('input_unit', '')).lower().strip()
                target = str(material.get('unit', '')).lower().strip()
                if source == target and source:
                    factor = 1
                elif source in units and target in units and units[source][0] == units[target][0]:
                    factor = units[source][1] / units[target][1]
                elif recipe.get('approved'):
                    raise Review(f'Этап {stage_index}: единица материала {material.get("name") or "без названия"} не соответствует единице его карточки')
                else:
                    continue
                material['quantity'] = float(dec(material['input_quantity']) * dec(factor))
        if recipe.get('approved'):
            if not ID.fullmatch(recipe.get('assortment_id', '')) or not recipe.get('stages'):
                raise Review('Для утверждения выбери изделие и этапы')
            for stage_index, stage in enumerate(recipe['stages'], 1):
                if not stage.get('factory'):
                    raise Review(f'Этап {stage_index}: выбери фабрику')
                for m in stage.get('materials', []):
                    if not ID.fullmatch(str(m.get('id') or '')):
                        raise Review(f'Этап {stage_index}: выбери материал или убери пустую строку')
                    if dec(m.get('quantity', 0)) <= 0:
                        raise Review(f'Этап {stage_index}: укажи расход материала {m.get("name") or "без названия"} больше нуля')
                    if m.get('type', 'product') not in ('product', 'variant'):
                        raise Review(f'Этап {stage_index}: выбери товар или модификацию материала')
                if dec(stage.get('work_cost', 0)) < 0:
                    raise Review(f'Этап {stage_index}: стоимость работы не может быть отрицательной')
            if recipe.get('editor_schema') == 2:
                packs = [m for stage in recipe['stages'] for m in stage['materials'] if m.get('role') == 'packaging']
                if len(packs) > 1:
                    raise Review('Зип-пакет можно добавить только на одном этапе')
        if recipe.get('approved') and (not item or item.get('archived')):
            raise Review('Карточка изделия недоступна. Обнови справочник и проверь привязку.')
        if recipe.get('assortment_id'):
            for candidate in db.execute('SELECT id,body FROM recipes WHERE id<>?', (key,)):
                if json.loads(candidate['body']).get('assortment_id') == recipe['assortment_id']:
                    raise Review('Для этой карточки изделия уже есть норма. Открой существующую.')
        old = db.execute('SELECT version,body FROM recipes WHERE id=?', (key,)).fetchone()
        if old and recipe.get('version') is not None and recipe['version'] != old['version']:
            raise Review('Норма уже изменена. Обнови страницу перед сохранением.')
        if old:
            db.execute('INSERT OR IGNORE INTO recipe_history VALUES(?,?,?,?)',
                       (key, old['version'], now(), old['body']))
        recipe.update(id=key, version=(old[0] if old else 0) + 1)
        db.execute('INSERT OR REPLACE INTO recipes VALUES(?,?,?)',
                   (key, recipe['version'], json.dumps(recipe, ensure_ascii=False)))
        self.record_recipe_event(db, key, json.loads(old['body']) if old else {}, recipe, actor)
        # Стоимость работы общая для модели и цвета; нормы остаются размерными.
        if propagate and recipe.get('model') and recipe.get('color'):
            for row in db.execute('SELECT id,version,body FROM recipes WHERE id<>?', (key,)).fetchall():
                other = json.loads(row['body'])
                before_other = copy.deepcopy(other)
                if (other.get('model'), other.get('color')) != (recipe['model'], recipe['color']):
                    continue
                changed = False
                for i, stage in enumerate(recipe.get('stages', [])):
                    if i < len(other.get('stages', [])) and other['stages'][i].get('factory') == stage.get('factory'):
                        if other['stages'][i].get('work_cost') != stage.get('work_cost'):
                            other['stages'][i]['work_cost'] = stage.get('work_cost', 0)
                            changed = True
                if changed:
                    db.execute('INSERT OR IGNORE INTO recipe_history VALUES(?,?,?,?)',
                               (other['id'], row['version'], now(), row['body']))
                    other['version'] = row['version'] + 1
                    db.execute('UPDATE recipes SET version=?,body=? WHERE id=?',
                               (other['version'], json.dumps(other, ensure_ascii=False), row['id']))
                    self.record_recipe_event(db, other['id'], before_other, other, actor)
        return recipe

    def record_recipe_event(self, db, key, before, after, actor=None):
        changes = recipe_changes(before, after)
        if changes:
            config = db.execute("SELECT value FROM kv WHERE key='config'").fetchone()
            factories = json.loads(config['value']).get('factories', []) if config else []
            for factory in factories:
                if factory.get('id'):
                    changes = [entry.replace(factory['id'], factory.get('name') or 'Фабрика') for entry in changes]
            db.execute('INSERT INTO recipe_events VALUES(?,?,?,?,?)',
                       (key, after['version'], now(), actor or 'Автор не сохранён', json.dumps(changes, ensure_ascii=False)))

    def save_recipe_group(self, recipes, actor=None):
        if not isinstance(recipes, list) or not 1 <= len(recipes) <= 100:
            raise Review('Выбери размеры, в которых внесены изменения')
        self.verify_live_sizes(r.get('assortment_id') for r in recipes)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            seen = set()
            cached = db.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
            products = {r['id']: r for r in json.loads(cached['body'])} if cached else {}
            def work_group(recipe):
                product = products.get(recipe.get('assortment_id'), {})
                model = product.get('model') or recipe.get('catalogue_model') or recipe.get('display_name') or recipe.get('model') or ''
                return (str(model).strip().casefold(), str(recipe.get('color') or '').strip().casefold())
            work_changes = {}
            for recipe in recipes:
                key = recipe.get('id')
                if key and key in seen:
                    raise Review('Размер добавлен повторно')
                if key:
                    seen.add(key)
                    old = db.execute('SELECT version,body FROM recipes WHERE id=?', (key,)).fetchone()
                    if old and recipe.get('version') != old['version']:
                        raise Review('Норма уже изменена. Обнови страницу перед сохранением.')
                else:
                    old = None
                previous = json.loads(old['body']) if old else {}
                for i, stage in enumerate(recipe.get('stages', [])):
                    old_stages = previous.get('stages', [])
                    old_work = old_stages[i].get('work_cost', 0) if i < len(old_stages) else 0
                    if stage.get('work_cost', 0) != old_work:
                        work_changes[(*work_group(recipe), i, stage.get('factory'))] = stage.get('work_cost', 0)
            saved = []
            for recipe in recipes:
                try:
                    recipe['approved'] = True
                    recipe['editor_schema'] = 2
                    saved.append(self.save_recipe(recipe, db, propagate=False, actor=actor))
                except Review as exc:
                    raise Review('Размер ' + str(recipe.get('size') or 'без размера') + ': ' + str(exc)) from None
            # Shared work updates affect matching sizes without approving unrelated drafts.
            for row in db.execute('SELECT id,version,body FROM recipes').fetchall():
                other = json.loads(row['body'])
                before_other = copy.deepcopy(other)
                changed = False
                for i, stage in enumerate(other.get('stages', [])):
                    key = (*work_group(other), i, stage.get('factory'))
                    if key in work_changes and stage.get('work_cost', 0) != work_changes[key]:
                        stage['work_cost'] = work_changes[key]
                        changed = True
                if changed:
                    db.execute('INSERT OR IGNORE INTO recipe_history VALUES(?,?,?,?)',
                               (other['id'], row['version'], now(), row['body']))
                    other['version'] = row['version'] + 1
                    db.execute('UPDATE recipes SET version=?,body=? WHERE id=?',
                               (other['version'], json.dumps(other, ensure_ascii=False), row['id']))
                    self.record_recipe_event(db, other['id'], before_other, other, actor)
            for recipe in saved:
                item = products.get(recipe.get('assortment_id'), {})
                if item:
                    kind = item.get('meta', {}).get('type')
                    parent = item.get('parent_id') if kind == 'variant' else item.get('id') if kind == 'product' else ''
                    if not ID.fullmatch(str(parent or '')):
                        raise Review('Не найдена карточка товара для записи поставщика')
                    self.enqueue_supplier(db, parent)
            return saved

    @staticmethod
    def enqueue_supplier(db, product_id):
        db.execute('''INSERT INTO supplier_jobs(id,status,due,revision,error,attempts)
                   VALUES(?,'pending',?,1,'',0)
                   ON CONFLICT(id) DO UPDATE SET status='pending',due=excluded.due,
                     revision=supplier_jobs.revision+1,error='',attempts=0''', (product_id, time.time()))

    def queue_existing_suppliers(self):
        with self.db() as db:
            row = db.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
            if not row:
                raise Review('Каталог изделий ещё не загружен')
            items = {x['id']: x for x in json.loads(row['body'])}
            queued = set()
            for rec in db.execute('SELECT body FROM recipes'):
                recipe = json.loads(rec['body'])
                if not recipe.get('approved'):
                    continue
                item = items.get(recipe.get('assortment_id'), {})
                kind = item.get('meta', {}).get('type')
                parent = item.get('parent_id') if kind == 'variant' else item.get('id') if kind == 'product' else ''
                if not ID.fullmatch(str(parent or '')):
                    raise Review('Не найдена карточка товара для записи поставщика')
                queued.add(parent)
            for parent in queued:
                self.enqueue_supplier(db, parent)
            return len(queued)

    def run_supplier_one(self):
        with self.db() as db:
            row = db.execute("SELECT * FROM supplier_jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            db.execute("UPDATE supplier_jobs SET status='running' WHERE id=? AND revision=?", (row['id'],row['revision']))
        product_id, revision = row['id'], row['revision']
        try:
            cfg = self.config()
            factories = {f['id']: f.get('agent') for f in cfg.get('factories', [])}
            with self.db() as db:
                cached = db.execute("SELECT body FROM catalogues WHERE kind='assortment'").fetchone()
                if not cached:
                    raise Review('Каталог изделий ещё не загружен')
                items = {x['id']: x for x in json.loads(cached['body'])}
                recipes = [json.loads(r['body']) for r in db.execute('SELECT body FROM recipes')]
            agents = set()
            for recipe in recipes:
                if not recipe.get('approved'):
                    continue
                item = items.get(recipe.get('assortment_id'), {})
                kind = item.get('meta', {}).get('type')
                parent = item.get('parent_id') if kind == 'variant' else item.get('id') if kind == 'product' else ''
                if parent != product_id:
                    continue
                stages = recipe.get('stages') or []
                agent = factories.get(stages[0].get('factory')) if stages else None
                if not ID.fullmatch(str(agent or '')):
                    raise Review('Не найден контрагент фабрики первого этапа')
                agents.add(agent)
            if not agents:
                raise Review('Не найдена применённая норма товара')
            if len(agents) != 1:
                raise Review('У размеров одного товара указаны разные фабрики первого этапа')
            target = next(iter(agents))
            current = self.ms.get('/entity/product/' + product_id)
            old = ident(current.get('supplier'))
            if old != target:
                with self.db() as db:
                    fresh = db.execute('SELECT revision FROM supplier_jobs WHERE id=?', (product_id,)).fetchone()
                    if not fresh or fresh['revision'] != revision:
                        return True
                    db.execute('INSERT INTO supplier_history VALUES(?,?,?,?)', (time.time(),product_id,old,target))
                self.ms.request('PUT', '/entity/product/' + product_id, {'supplier': ref('counterparty', target)})
            actual = self.ms.get('/entity/product/' + product_id)
            if ident(actual.get('supplier')) != target:
                raise Review('МойСклад не подтвердил поставщика в карточке товара')
            with self.db() as db:
                db.execute("UPDATE supplier_jobs SET status='done',error='' WHERE id=? AND revision=?", (product_id,revision))
        except Exception as exc:
            message = str(exc) if isinstance(exc, Review) else 'Не удалось обновить поставщика; нужна проверка'
            retry = isinstance(exc, Transient) and row['attempts'] < 8
            with self.db() as db:
                db.execute("UPDATE supplier_jobs SET status=?,error=?,due=?,attempts=attempts+1 WHERE id=? AND revision=?",
                           ('pending' if retry else 'review',message,time.time()+min(600,15*2**row['attempts']),product_id,revision))
        return True

    @staticmethod
    def invalidate_fifo(db):
        db.execute("INSERT INTO kv(key,value) VALUES('fifo_generation','1') ON CONFLICT(key) DO UPDATE SET value=CAST(kv.value AS INTEGER)+1")

    def enqueue(self, kind, value):
        if kind not in TYPES or not ID.fullmatch(str(value)):
            raise Review('Нужны тип документа и UUID')
        with self.db() as db:
            db.execute('''INSERT INTO jobs(kind,id,due,status,error) VALUES(?,?,?,'pending','')
              ON CONFLICT(kind,id) DO UPDATE SET due=CASE WHEN jobs.status='pending'
              THEN MIN(jobs.due,excluded.due) ELSE excluded.due END,status='pending',error='' ''',
                       (kind, value, time.time() + self.config().get('delay', 1)))
            if kind in ('supply', 'move', 'loss'):
                self.invalidate_fifo(db)

    def log(self, kind, value, message):
        with self.db() as db:
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), kind, value, message[:500]))

    def status(self):
        with self.db() as db:
            jobs = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY due DESC LIMIT 100')]
            audit = [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY at DESC LIMIT 100')]
            supplier_issues = [dict(r) for r in db.execute("SELECT id,error FROM supplier_jobs WHERE status='review'")]
            chains = [dict(json.loads(r[0]), id=r[1], number=r[2]) for r in db.execute(
                'SELECT body,id,number FROM chains ORDER BY number DESC LIMIT 100')]
        return {'config': self.config(), 'recipes': self.recipes(), 'jobs': jobs, 'audit': audit, 'chains': chains, 'supplier_issues': supplier_issues}

    CATALOGUE_KINDS = ('assortment', 'counterparty', 'store', 'organization')
    CATALOGUE_TTL = 20 * 60

    def catalogue_snapshot(self, include_rows=True):
        with self.db() as db:
            columns = 'kind,updated,body' if include_rows else 'kind,updated'
            saved = db.execute('SELECT ' + columns + ' FROM catalogues').fetchall()
        ready = set(r['kind'] for r in saved) == set(self.CATALOGUE_KINDS)
        updated = min((r['updated'] for r in saved), default=0) if ready else 0
        result = {'ready': ready, 'updated': updated, 'next_update': updated + self.CATALOGUE_TTL if updated else 0,
                  'refreshing': self.catalogue_lock.locked(), 'error': self.catalogue_error}
        if include_rows:
            result['catalogues'] = {r['kind']: json.loads(r['body']) for r in saved}
        return result

    def refresh_catalogues_now(self):
        # Collect privately; only publish a complete generation in one transaction.
        snapshots = {}
        for kind in self.CATALOGUE_KINDS:
            rows, offset = [], 0
            limit = 100 if kind == 'assortment' else 1000
            while True:
                params = {'limit': limit, 'offset': offset}
                if kind == 'assortment':
                    params['expand'] = 'uom,product,product.uom'
                page = self.ms.get('/entity/' + kind, **params)
                chunk = page.get('rows', [])
                rows.extend(chunk)
                offset += len(chunk)
                expected = page.get('meta', {}).get('size', offset)
                if not chunk and offset < expected:
                    raise Review('Каталог получен не полностью')
                if offset >= expected:
                    break
            # Only the named composition field is exposed; other custom fields stay private.
            def composition(item):
                for source in (item, item.get('product', {})):
                    for attr in source.get('attributes', []):
                        if str(attr.get('name', '')).strip().casefold() == 'состав':
                            value = attr.get('value', '')
                            return str(value.get('name', '')) if isinstance(value, dict) else str(value or '')
                return ''
            # Keep only fields required by selectors, without contacts/payment details.
            snapshots[kind] = [{'id': r['id'], 'name': r['name'], 'meta': r['meta'],
                'uom': r.get('uom') or r.get('product', {}).get('uom'), 'code': r.get('code', ''),
                'article': r.get('article') or r.get('product', {}).get('article', ''),
                'pathName': r.get('pathName') or r.get('product', {}).get('pathName', ''),
                'product_folder': ident(r.get('productFolder') or r.get('product', {}).get('productFolder')),
                'model': r.get('product', {}).get('name', r['name']),
                'companyType': r.get('companyType', ''),
                'tags': r.get('tags', []), 'archived': r.get('archived', False),
                'production_store': next((ident(a.get('value')) for a in r.get('attributes', []) if str(a.get('name','')).strip().casefold() == 'склад' and a.get('type') == 'store'), ''),
                'characteristics': r.get('characteristics', []),
                'parent_id': r.get('product', {}).get('id', ''),
                'composition': composition(r)} for r in rows]
        updated = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.reconcile_recipe_sizes(db, {x['id']: x for x in snapshots['assortment']}, complete=True)
            for kind, rows in snapshots.items():
                db.execute('INSERT OR REPLACE INTO catalogues VALUES(?,?,?)',
                           (kind, updated, json.dumps(rows, ensure_ascii=False)))
            saved = db.execute("SELECT value FROM kv WHERE key='config'").fetchone()
            if saved:
                cfg = json.loads(saved['value'])
                for factory in cfg.get('factories', []):
                    if factory.get('agent'):
                        continue
                    matches = [a for a in snapshots['counterparty'] if not a.get('archived')
                               and a.get('name','').strip().casefold() == factory.get('name','').strip().casefold()
                               and any(str(t).strip().casefold() == 'производство' for t in a.get('tags', []))]
                    if len(matches) == 1:
                        factory['agent'] = matches[0]['id']
                db.execute("UPDATE kv SET value=? WHERE key='config'", (json.dumps(cfg, ensure_ascii=False),))
        self.catalogue_error = ''
        self.catalogue_retry_at = 0

    def start_catalogue_refresh(self, force=False):
        snapshot = self.catalogue_snapshot(False)
        if not force and (time.time() < self.catalogue_retry_at or
                          snapshot['updated'] and time.time() - snapshot['updated'] < self.CATALOGUE_TTL):
            return False
        if not self.catalogue_lock.acquire(blocking=False):
            return False
        def refresh():
            try:
                self.refresh_catalogues_now()
            except Exception:
                self.catalogue_error = 'Не удалось обновить каталог. Доступна последняя сохранённая версия.'
                self.catalogue_retry_at = time.time() + 60
            finally:
                self.catalogue_lock.release()
        threading.Thread(target=refresh, daemon=True).start()
        return True

    def catalogue(self, kind, query=''):
        if kind not in self.CATALOGUE_KINDS:
            raise Review('Неизвестный справочник')
        if len(query) > 100:
            raise Review('Слишком длинный запрос')
        with self.db() as db:
            saved = db.execute('SELECT body FROM catalogues WHERE kind=?', (kind,)).fetchone()
        if not saved:
            raise Review('Каталог подготавливается. Обновление идёт в фоне.')
        rows = json.loads(saved['body'])
        needle = query.casefold().strip()
        return [r for r in rows if not needle or needle in ' '.join(str(r.get(k, '')) for k in
                ('name', 'code', 'article')).casefold()]

    def stock_shortages(self, store, positions):
        rows = self.ms.rows('/report/stock/all', filter='store=' + BASE + '/entity/store/' + store,
                            stockMode='all')
        stock = {r.get('id') or ident({'meta': r.get('meta', {})}): dec(r.get('stock', 0)) for r in rows}
        catalogues = self.catalogue_snapshot().get('catalogues', {})
        items = {x['id']: x for x in catalogues.get('assortment', [])}
        stores = {x['id']: x for x in catalogues.get('store', [])}
        label = re.sub(r'^Производство\s*\|\s*', '', stores.get(store, {}).get('name') or store, flags=re.I)
        fmt = lambda q: format(q.normalize(), 'f').replace('.', ',')
        problems = []
        for key, qty in quantities(positions).items():
            available = stock.get(key, Decimal(0))
            if available < qty:
                item = items.get(key, {})
                unit = item.get('uom', {}).get('name', '') if item.get('uom') else ''
                problems.append(f'{item.get("name") or key} — {fmt(qty - available)} {unit} ({label})'.strip())
        return problems

    def require_stock(self, store, positions):
        problems = self.stock_shortages(store, positions)
        if problems:
            raise StockShortage('Не хватает: ' + '; '.join(problems))

    @staticmethod
    def fifo_cost(layers, quantity, opening_balances=()):
        need, total, used = dec(quantity), Decimal(0), []
        if need <= 0:
            raise Review('Не указана положительная норма материала')
        if any(dec(row.get('stock', 0)) < 0 for row in layers):
            raise StockShortage('Отрицательный остаток материала: FIFO не подтверждён')
        ordered = sorted(layers, key=lambda r: (r.get('moment', ''),
                         r.get('operation', {}).get('meta', {}).get('href', ''),
                         r.get('store', {}).get('meta', {}).get('href', '')))
        for row in ordered:
            stock = dec(row.get('stock', 0))
            if stock <= 0:
                continue
            if not row.get('moment') or row.get('costPerUnit') is None or dec(row['costPerUnit']) <= 0:
                raise Review('Стоимость партии материала не подтверждена')
            origin = row.get('operation', {}).get('meta', {}).get('type')
            # costPerUnit is MoySklad's stock valuation, not the price in a move.
            # Transferred stock is reported under the incoming move, which keeps
            # its calculated acquisition cost even when its position price is zero.
            if origin not in ('supply', 'move') and not (origin == 'enter' and ident(row.get('operation')) in opening_balances):
                raise Review('Нет подтверждённой стоимости из приёмки материала')
            amount = min(stock, need)
            total += amount * dec(row['costPerUnit']) / 100
            used.append({'quantity': float(amount), 'unit_cost': float(dec(row['costPerUnit']) / 100),
                         'moment': row['moment'], 'operation': ident(row.get('operation')),
                         'operation_type': origin, 'store': ident(row.get('store'))})
            need -= amount
            if need == 0:
                return total, used
        raise StockShortage('Недостаточно остатков материала для расчёта FIFO')

    def verified_opening_balances(self, layers, checked):
        # Only an explicitly registered and unchanged opening inventory can act
        # as the first FIFO layer. Arbitrary inventory receipts stay unsupported.
        registered = self.config().get('opening_balance_documents', {})
        allowed = set()
        for row in layers:
            operation = row.get('operation', {})
            key = ident(operation)
            if operation.get('meta', {}).get('type') != 'enter' or key not in registered:
                continue
            if key not in checked:
                try:
                    doc = self.ms.get('/entity/enter/' + key)
                    if not doc.get('applicable') or fingerprint(doc, self.ms.positions('enter', key)) != registered[key]:
                        raise Review('Начальные остатки материалов изменены; требуется сверка')
                    checked[key] = True
                except Review as error:
                    checked[key] = error
            if isinstance(checked[key], Review):
                raise checked[key]
            allowed.add(key)
        return allowed

    def material_fifo_layers(self, key, kind='product', force=False):
        ref(kind, key)
        with self.db() as db:
            generation = db.execute("SELECT value FROM kv WHERE key='fifo_generation'").fetchone()
            generation = int(generation[0]) if generation else 0
            cached = db.execute('SELECT updated,body,generation FROM material_fifo_cache WHERE id=?', (key,)).fetchone()
        if cached and cached['generation'] == generation and not force and time.time() - cached['updated'] < self.CATALOGUE_TTL:
            return json.loads(cached['body'])
        rows, offset = [], 0
        while True:
            page = self.ms.get('/report/byoperations/stock', limit=1000, offset=offset,
                               filter='assortment=' + BASE + '/entity/' + kind + '/' + key)
            chunk = page.get('rows', [])
            if not chunk and offset < page.get('meta', {}).get('size', offset):
                raise Review('Данные партий материала получены не полностью')
            rows.extend(chunk)
            offset += len(chunk)
            if offset >= page.get('meta', {}).get('size', offset):
                break
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO material_fifo_cache(id,updated,body,generation) VALUES(?,?,?,?)',
                       (key, time.time(), json.dumps(rows, ensure_ascii=False), generation))
        return rows

    def production_costs(self, force=False):
        result, layers, opening_checked = {}, {}, {}
        for recipe in self.recipes():
            key = recipe.get('assortment_id')
            if not key:
                continue
            try:
                if recipe.get('norm_review'):
                    raise Review('Себестоимость требует уточнения: проверь норму после изменения размера')
                if not recipe.get('approved'):
                    raise Review('Норма изделия не заполнена и не применена')
                stages = recipe.get('stages', [])
                packs = [m for st in stages for m in st.get('materials', []) if m.get('role') == 'packaging']
                if not stages:
                    raise Review('Не заполнены этапы производства')
                if len(packs) > 1:
                    raise Review('Зип-пакет можно добавить только на одном этапе')
                requirements, kinds = defaultdict(Decimal), {}
                cfg = self.config()
                factories = {f['id']: f for f in cfg.get('factories', [])}
                total, breakdown = Decimal(0), []
                for stage in stages:
                    if 'work_cost' not in stage or dec(stage['work_cost']) < 0:
                        raise Review('Не заполнена стоимость работы этапа')
                    total += dec(stage['work_cost'])
                    for mat in stage.get('materials', []):
                        mid, kind = mat.get('id', ''), mat.get('type', 'product')
                        ref(kind, mid)
                        if dec(mat.get('quantity', 0)) <= 0:
                            raise Review('Не заполнен расход материала')
                        store = factories.get(stage.get('factory'), {}).get('store', '') if cfg.get('workflow') == 'finished_receipt' else ''
                        if cfg.get('workflow') == 'finished_receipt' and not store:
                            raise Review('Не указан склад фабрики этапа')
                        requirements[(mid, store)] += dec(mat['quantity'])
                        kinds[mid] = kind
                for (mid, store), qty in requirements.items():
                    if mid not in layers:
                        try:
                            layers[mid] = self.material_fifo_layers(mid, kinds[mid], force)
                        except Review as error:
                            layers[mid] = error
                    if isinstance(layers[mid], Review):
                        raise layers[mid]
                    available = [r for r in layers[mid] if not store or ident(r.get('store')) == store]
                    value, sources = self.fifo_cost(available, qty,
                        self.verified_opening_balances(available, opening_checked))
                    total += value
                    breakdown.append({'material': mid, 'quantity': float(qty),
                                      'cost': float(value), 'layers': sources})
                result[key] = {'cost': float(total.quantize(Decimal('.01'))), 'materials': breakdown,
                               'recipe_version': recipe.get('version'), 'basis': 'next_unit_fifo'}
            except Review as error:
                result[key] = {'cost': None, 'issue': str(error), 'basis': 'next_unit_fifo'}
        return result

    def analytics(self, store='', force=False):
        if store:
            ref('store', store)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS analytics_cache(scope TEXT PRIMARY KEY, updated REAL, body TEXT NOT NULL)')
            cached = db.execute('SELECT updated,body FROM analytics_cache WHERE scope=?', (store,)).fetchone()
        if cached and not force and time.time() - cached['updated'] < self.CATALOGUE_TTL:
            return {'updated': cached['updated'], 'rows': [{k: v for k, v in r.items() if k != 'cost'} for r in json.loads(cached['body'])],
                    'production_costs': self.production_costs(force)}
        params = {'stockMode': 'all'}
        if store:
            params['filter'] = 'store=' + BASE + '/entity/store/' + store
        rows, offset = [], 0
        while True:
            page = self.ms.get('/report/stock/all', limit=1000, offset=offset, **params)
            chunk = page.get('rows', [])
            if not chunk and offset < page.get('meta', {}).get('size', offset):
                raise Review('Отчёт остатков получен не полностью. Повтори обновление.')
            rows.extend(chunk)
            offset += len(chunk)
            if offset >= page.get('meta', {}).get('size', offset):
                break
        safe = [{'id': r.get('id') or urllib.parse.urlparse(r.get('meta', {}).get('href', '')).path.rsplit('/', 1)[-1],
                 'name': r.get('name', ''), 'stock': r.get('stock', 0), 'reserve': r.get('reserve', 0),
                 'sale': r.get('salePrice')} for r in rows]
        updated = time.time()
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO analytics_cache VALUES(?,?,?)',
                       (store, updated, json.dumps(safe, ensure_ascii=False)))
        return {'updated': updated, 'rows': safe, 'production_costs': self.production_costs(force)}

    def stock(self, store):
        ref('store', store)
        rows = self.ms.rows('/report/stock/all', filter='store=' + BASE + '/entity/store/' + store)
        return [{'id': ident({'meta': r['meta']}), 'name': r.get('name', ''),
                 'stock': r.get('stock', 0), 'reserve': r.get('reserve', 0),
                 'price_kopecks': r.get('price'), 'unit': r.get('uom', {}).get('name', '')} for r in rows]

    def execute(self, key, kind, payload, basis=None, allow_negative=False):
        """syncId + сохранённый payload обеспечивают восстановление после неопределённого ответа."""
        with self.db() as db:
            row = db.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()
            if row:
                payload = json.loads(row['body'])
            else:
                payload = copy.deepcopy(payload)
                cfg = self.config()
                receipt_id = key.split('/')[0]
                if (ID.fullmatch(cfg.get('production_project', '')) and ID.fullmatch(receipt_id)
                        and self.chain(receipt_id)[0] and self.project_receipt_basis(receipt_id)):
                    payload['project'] = ref('project', cfg['production_project'])
                if cfg.get('operation_attributes', {}).get(kind):
                    label = ('Изделия на доработку' if '/out/' in key else
                             'Возврат с доработки' if '/return/' in key else
                             'Материалы на производство' if kind == 'move' and key.endswith('/materials') else
                             'Расход материалов')
                    payload.setdefault('attributes', []).append({
                        'meta': {'href': BASE + '/entity/' + kind + '/metadata/attributes/' + cfg['operation_attributes'][kind],
                                 'type': 'attributemetadata', 'mediaType': 'application/json'},
                        'value': ref('customentity/' + cfg['operation_dictionary'], cfg['operation_values'][label])})
                payload['syncId'] = str(uuid.uuid5(uuid.NAMESPACE_URL, '2my-production:' + key))
                payload['externalCode'] = '2my-prod-' + hashlib.sha256(key.encode()).hexdigest()[:32]
                db.execute('INSERT INTO operations(key,kind,body,status) VALUES(?,?,?,?)',
                           (key, kind, json.dumps(payload), 'prepared'))
        found = self.ms.rows('/entity/' + kind, filter='syncId=' + payload['syncId'])
        if len(found) > 1:
            raise Review('Неоднозначная операция; требуется сверка')
        if found:
            doc = found[0]
        else:
            if allow_negative and (kind != 'loss' or '/direct/' not in key):
                raise Review('Списание в минус разрешено только для материалов готовой приёмки')
            if not allow_negative:
                self.require_stock(ident(payload.get('sourceStore') or payload.get('store')), payload['positions'])
            if basis and kind == 'move':
                template = self.ms.request('PUT', '/entity/move/new', {'supply': ref('supply', basis)})
                if ident(template.get('supply')) != basis:
                    raise Review('API не подтвердил основание приёмки для перемещения')
                payload = dict(template, **payload)
            doc = self.ms.request('POST', '/entity/' + kind, payload)
        # Сохраняем ID даже при последующем отказе проверки: документ уже существует.
        with self.db() as db:
            db.execute("UPDATE operations SET doc_id=? WHERE key=?", (doc['id'], key))
        doc = self.stamp_production_project(kind, doc)
        def verify():
            current = self.ms.get('/entity/' + kind + '/' + doc['id'])
            for field in ('sourceStore', 'targetStore', 'store', 'organization'):
                if field in payload and ident(payload[field]) != ident(current.get(field)):
                    raise Review('Документ ' + str(doc.get('name', doc['id'])) + ': изменён склад или организация')
            positions = self.ms.positions(kind, doc['id'])
            if not current.get('applicable') or quantities(positions) != quantities(payload['positions']):
                raise Review('Документ ' + str(current.get('name', doc['id'])) + ': изменён состав или снято проведение')
            if basis and kind == 'move' and ident(current.get('supply')) != basis:
                raise Review('Автоматический документ не подтвердил связь с приёмкой')
            return current, positions
        if not row or row['status'] != 'done':
            # Verify after asynchronous scenarios have run, before dependent actions.
            time.sleep(2)
        check, positions = verify()
        with self.db() as db:
            db.execute("UPDATE operations SET status='done',fingerprint=? WHERE key=?",
                       (fingerprint(check, positions), key))
            if kind in ('supply', 'move', 'loss') and (not row or row['status'] != 'done'):
                self.invalidate_fifo(db)
        return check

    def recipe_index(self):
        out = {}
        for r in self.recipes():
            if r.get('approved') and not r.get('norm_review'):
                key = r['assortment_id']
                if key in out:
                    raise Review('Две утверждённые нормы одного изделия')
                out[key] = r
        return out

    def check_production_order(self, value, supply=None):
        doc = self.ms.get('/entity/purchaseorder/' + value)
        cancelled = self.config().get('states', {}).get('purchaseorder', {}).get('Отменён')
        if not doc.get('applicable') or cancelled and ident(doc.get('state')) == cancelled:
            raise Review('Заказ поставщику отменён или не проведён; проверь производственную цепочку')
        if supply:
            for field in ('agent', 'organization'):
                if ident(doc.get(field)) != ident(supply.get(field)):
                    raise Review('Приёмка и заказ поставщику имеют разные организацию/фабрику')
        return doc

    def plan_supply(self, value):
        doc = self.ms.get('/entity/supply/' + value)
        positions = self.ms.positions('supply', value)
        cfg = self.config()
        if not doc.get('applicable'):
            raise Review('Приёмка не проведена')
        if not cfg.get('company_store') or ident(doc.get('store')) != cfg['company_store']:
            raise Review('Приёмка должна быть на склад компании: Склад Москва')
        if not ident(doc.get('purchaseOrder')):
            raise Review('Производственная приёмка должна иметь заказ поставщику')
        self.check_production_order(ident(doc['purchaseOrder']), doc)
        factories = {f['id']: f for f in cfg.get('factories', [])}
        self.verify_live_sizes(ident(row.get('assortment')) for row in positions)
        index = self.recipe_index()
        lines = []
        for row in positions:
            key = ident(row.get('assortment'))
            recipe = index.get(key)
            if not recipe:
                raise Review('Нет применённой нормы: ' + next((x['name'] for x in self.catalogue_snapshot().get('catalogues', {}).get('assortment', []) if x['id'] == key), key))
            first = factories.get(recipe['stages'][0]['factory'])
            if not first or first['agent'] != ident(doc.get('agent')):
                raise Review('Изготовитель приёмки не совпал с маршрутом изделия')
            for stage in recipe['stages']:
                factory = factories.get(stage['factory'])
                if not factory or (stage.get('materials') and (not ID.fullmatch(str(factory.get('store', ''))) or factory.get('store_ok') is False)):
                    raise Review('Проверь фабрику и склад этапа производства в МоемСкладе')
            lines.append({'assortment': row['assortment'], 'quantity': row['quantity'],
                          'recipe': copy.deepcopy(recipe), 'position_id': row.get('id'),
                          'original_price': row.get('price', 0)})
        return {'source': doc, 'fingerprint': fingerprint(doc, positions), 'lines': lines,
                'factories': factories, 'company_store': cfg.get('company_store')}

    def chain(self, value):
        with self.db() as db:
            row = db.execute('SELECT * FROM chains WHERE id=?', (value,)).fetchone()
        return (json.loads(row['body']), row['number']) if row else (None, None)

    def save_chain(self, value, body, number=None):
        external_max = 0
        if number is None:
            for doc in self.ms.rows('/entity/loss', filter='name~=СП-'):
                match = re.fullmatch(r'СП-(\d+)-\d+', doc.get('name', ''))
                if match:
                    external_max = max(external_max, int(match[1]))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if number is None:
                number = max(external_max, db.execute('SELECT COALESCE(MAX(number),0) FROM chains').fetchone()[0]) + 1
            db.execute('INSERT OR REPLACE INTO chains VALUES(?,?,?)', (value, number, json.dumps(body)))
        return number

    def set_state(self, kind, value, label):
        sid = self.config().get('states', {}).get(kind, {}).get(label)
        if not sid:
            raise Review('Не настроен статус: ' + label)
        doc = self.ms.get('/entity/' + kind + '/' + value)
        if ident(doc.get('state')) != sid:
            self.ms.request('PUT', '/entity/' + kind + '/' + value, {'state': ref(kind + '/metadata/states', sid)})

    def material_lines(self, lines, stage):
        totals, refs = defaultdict(Decimal), {}
        for line in lines:
            for mat in line['recipe']['stages'][stage]['materials']:
                totals[mat['id']] += dec(mat['quantity']) * dec(line['quantity'])
                refs[mat['id']] = ref(mat.get('type', 'product'), mat['id'])
        return [{'assortment': refs[k], 'quantity': float(q), 'price': 0} for k, q in totals.items()]

    def consume(self, chain_id, number, chain, lines, stage, suffix=''):
        factory_id = lines[0]['recipe']['stages'][stage]['factory']
        factory = chain['factories'][factory_id]
        materials = self.material_lines(lines, stage)
        source = chain['source']
        tag = 'П' if stage == 0 else ('В' if stage == 1 else 'Э' + str(stage + 1))
        base = {'organization': source['organization'], 'applicable': True,
                'moment': max(now(), source['moment']), 'description': f'Производство; приёмка {source["name"]}; этап {stage+1}'}
        key = f'{chain_id}/stage/{stage}/{factory_id}/{suffix}'
        company = chain['company_store']
        self.execute(key + '/materials', 'move', dict(base, name=f'МТ-{tag}-{number}{suffix}',
                     sourceStore=ref('store', factory['store']), targetStore=ref('store', company), positions=materials), basis=chain_id)
        cfg = self.config()
        attrs = [{'meta': {'href': BASE + '/entity/loss/metadata/attributes/' + cfg['reason_attribute'],
                           'type': 'attributemetadata', 'mediaType': 'application/json'},
                  'value': ref('customentity/' + cfg['reason_dictionary'], cfg['reason_production'])}]
        self.execute(key + '/loss', 'loss', dict(base, name=f'СП-{tag}-{number}{suffix}', store=ref('store', company),
                     positions=[dict(x, reason='Расход на производство') for x in materials], attributes=attrs))

    def outbound(self, chain_id, number, chain, lines, stage):
        fid = lines[0]['recipe']['stages'][stage]['factory']
        # Простая цепочка сохраняет согласованный номер ВШ-125.
        stage_factories = sorted({x['recipe']['stages'][stage]['factory'] for x in chain['lines']
                                  if len(x['recipe']['stages']) > stage})
        label = f'ВШ-{number}' + (f'-Э{stage+1}' if stage > 1 else '')
        if len(stage_factories) > 1:
            label += '-Г' + str(stage_factories.index(fid) + 1)
        doc = self.execute(f'{chain_id}/out/{stage}/{fid}', 'move', {
            'name': label, 'organization': chain['source']['organization'], 'applicable': True,
            'moment': max(now(), chain['source']['moment']),
            'sourceStore': ref('store', chain['company_store']),
            'targetStore': ref('store', chain['factories'][fid]['store']),
            'positions': [{'assortment': x['assortment'], 'quantity': x['quantity'], 'price': 0} for x in lines],
            'state': ref('move/metadata/states', self.config()['states']['move']['Ожидает отправки']),
            'description': 'Изделия на доработку; исходная приёмка ' + chain['source']['name']}, basis=chain_id)
        # A duplicate receipt event recovers the same outgoing document. Keep its
        # processed returns and later stages instead of reopening a completed batch.
        chain.setdefault('outbound', {}).setdefault(doc['id'],
            {'stage': stage, 'lines': lines, 'name': label, 'returns': {}})
        self.save_chain(chain_id, chain, number)

    def receipt_link_fields(self, count=2):
        fields = []
        existing = self.ms.rows('/entity/supply/metadata/attributes')
        for index in range(count):
            name = 'Списание материалов ' + str(index + 1)
            matches = [a for a in existing if a.get('name') == name]
            if len(matches) > 1 or matches and matches[0].get('type') != 'link':
                raise Review('Проверь поле приёмки: ' + name)
            field = matches[0] if matches else self.ms.request('POST', '/entity/supply/metadata/attributes',
                                                               {'name': name, 'type': 'link', 'required': False})
            fields.append(field)
        return fields

    def direct_material_groups(self, chain):
        groups = {}
        for line in chain['lines']:
            for index, stage in enumerate(line['recipe']['stages']):
                if not stage.get('materials'):
                    continue
                factory = chain['factories'][stage['factory']]
                store = factory['store']
                group = groups.setdefault(store, {'store': store, 'factories': [], 'stages': [], 'materials': {}})
                if stage['factory'] not in group['factories']:
                    group['factories'].append(stage['factory'])
                if index + 1 not in group['stages']:
                    group['stages'].append(index + 1)
                for mat in stage['materials']:
                    key = mat['id']
                    entry = group['materials'].setdefault(key, {'assortment': ref(mat.get('type', 'product'), key),
                                                               'quantity': 0, 'price': 0})
                    entry['quantity'] = float(dec(entry['quantity']) + dec(mat['quantity']) * dec(line['quantity']))
        return [dict(group, positions=list(group['materials'].values()))
                for group in sorted(groups.values(), key=lambda g: (min(g['stages']), g['store']))]

    def snapshot_direct_cost(self, chain, groups, allow_negative=False):
        total, traces, checked, cached = Decimal(0), [], {}, {}
        issues = []
        problems = [problem for group in groups for problem in self.stock_shortages(group['store'], group['positions'])]
        if problems and not allow_negative:
            raise StockShortage('Не хватает: ' + '; '.join(problems))
        for line in chain['lines']:
            for stage in line['recipe']['stages']:
                total += dec(stage.get('work_cost', 0)) * dec(line['quantity'])
        for group in groups:
            for material in group['positions']:
                mid = ident(material['assortment'])
                if mid not in cached:
                    cached[mid] = self.material_fifo_layers(mid, material['assortment']['meta']['type'], True)
                layers = [r for r in cached[mid] if ident(r.get('store')) == group['store']]
                allowed = self.verified_opening_balances(layers, checked)
                try:
                    cost, trace = self.fifo_cost(layers, material['quantity'], allowed)
                except Review as exc:
                    catalogues = self.catalogue_snapshot().get('catalogues', {})
                    name = next((x['name'] for x in catalogues.get('assortment', []) if x['id'] == mid), mid)
                    label = next((x['name'] for x in catalogues.get('store', []) if x['id'] == group['store']), group['store'])
                    message = f'{name} ({label}): {exc}'
                    if not allow_negative or not isinstance(exc, StockShortage):
                        raise type(exc)(message) from exc
                    issues.append(message)
                    continue
                total += cost
                traces.append({'material': mid, 'store': group['store'], 'cost': float(cost), 'layers': trace})
        prices = []
        if not issues:
            pools = {(t['store'], t['material']): copy.deepcopy(t['layers']) for t in traces}
            for line in chain['lines']:
                quantity = dec(line['quantity'])
                if quantity <= 0:
                    raise Review('Количество изделия должно быть больше нуля')
                amount = Decimal(0)
                for stage in line['recipe']['stages']:
                    amount += dec(stage.get('work_cost', 0)) * quantity
                    for material in stage.get('materials', []):
                        key = (chain['factories'][stage['factory']]['store'], material['id'])
                        need = dec(material['quantity']) * quantity
                        for layer in pools[key]:
                            take = min(need, dec(layer['quantity']))
                            amount += take * dec(layer['unit_cost'])
                            layer['quantity'] = str(dec(layer['quantity']) - take)
                            need -= take
                            if not need:
                                break
                        if need:
                            raise Review('Не удалось распределить стоимость материала по изделиям')
                prices.append({'id': line.get('position_id'), 'before': line.get('original_price', 0),
                               'price': float((amount / quantity * 100).quantize(Decimal('0.01'))),
                               'cost': float(amount), 'status': 'pending'})
        return {'prices': prices, 'total': None if issues else float(total), 'materials': traces, 'at': now(),
                'issues': issues, 'stock_problems': problems}

    @staticmethod
    def completion_note(chain):
        snapshot = chain.get('cost_snapshot', {})
        note = ('Материалы не указаны.' if not chain.get('material_groups') else
                'Материалы списаны в минус.' if snapshot.get('stock_problems') else 'Материалы списаны.')
        if snapshot.get('total') is None:
            note += ' Себестоимость пока не рассчитана.'
        return note

    def receipt_note(self, value, message=''):
        # Replace only the exact text previously written by this integration.
        key = 'receipt_note:' + value
        with self.db() as db:
            old = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        old = json.loads(old[0]) if old else ''
        doc = self.ms.get('/entity/supply/' + value)
        original = doc.get('description') or ''
        text = original
        if old and text == old:
            text = ''
        elif old and text.endswith('\n\n' + old):
            text = text[:-len(old)-2]
        elif old and old in text.split('\n'):
            text = '\n'.join(line for line in text.split('\n') if line != old)
        note = ('Производство: ' + str(message).replace('\n', ' ').strip()) if message else ''
        if note:
            room = 4096 - len(text) - (2 if text else 0)
            if room < 40:
                raise Review('В комментарии приёмки нет места для сообщения производства')
            note = note[:min(room, 1800)]
            text += ('\n\n' if text else '') + note
        if text != original:
            self.ms.request('PUT', '/entity/supply/' + value, {'description': text})
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, json.dumps(note, ensure_ascii=False)))

    def loss_receipt_link(self, value, receipt_id, current=None):
        """Attach a native clickable field without touching employee comments or stock."""
        field_id = self.config().get('loss_receipt_attribute', '')
        if not ID.fullmatch(field_id):
            raise Review('Не настроено поле приёмки в списании')
        url = 'https://online.moysklad.ru/app/#supply/edit?id=' + receipt_id
        current = current or self.ms.get('/entity/loss/' + value)
        attrs = {ident(a): a.get('value') for a in current.get('attributes', [])}
        if attrs.get(field_id) not in (None, '', url):
            raise Review('В списании ' + current.get('name', value) + ' указана другая приёмка')
        description = current.get('description') or ''
        # Strip only our old exact first line; retain any independent employee text.
        generated = re.compile(r'^Готовая продукция; приёмка [^\n]*?; этапы [0-9, ]+; '
                               + re.escape(url) + r'(?:\r?\n|$)')
        cleaned = generated.sub('', description, count=1)
        payload = {}
        if attrs.get(field_id) != url:
            payload['attributes'] = [{'meta': {'href': BASE + '/entity/loss/metadata/attributes/' + field_id,
                                                'type': 'attributemetadata', 'mediaType': 'application/json'},
                                      'value': url}]
        if cleaned != description:
            payload['description'] = cleaned
        if not payload:
            return current
        fresh = self.ms.get('/entity/loss/' + value)
        fresh_attrs = {ident(a): a.get('value') for a in fresh.get('attributes', [])}
        if (fresh.get('description') or '') != description or fresh_attrs != attrs:
            raise Review('Списание ' + current.get('name', value) + ' изменилось во время обработки')
        self.ms.request('PUT', '/entity/loss/' + value, payload)
        updated = self.ms.get('/entity/loss/' + value)
        result = {ident(a): a.get('value') for a in updated.get('attributes', [])}
        if result.get(field_id) != url or updated.get('description', '') != cleaned:
            raise Review('Не удалось сохранить приёмку в списании ' + current.get('name', value))
        if any(result.get(key) != val for key, val in attrs.items() if key != field_id):
            raise Review('Изменились другие поля списания ' + current.get('name', value))
        return updated

    def rename_document(self, kind, doc, name):
        if doc.get('name') == name:
            return doc
        if doc.get('deleted'):
            raise Review('Документ ' + str(doc.get('name', doc['id'])) + ' находится в корзине')
        self.ms.request('PUT', '/entity/' + kind + '/' + doc['id'], {'name': name})
        saved = self.ms.get('/entity/' + kind + '/' + doc['id'])
        if saved.get('name') != name:
            raise Review('Не удалось сохранить номер ' + name)
        if fingerprint(saved, []) != fingerprint(doc, []):
            raise Review('Документ изменился во время присвоения номера ' + name)
        return saved

    def number_receipt(self, doc):
        """Reserve once per order, including receipts whose webhook has not arrived yet."""
        order_id = ident(doc.get('purchaseOrder'))
        if not order_id:
            raise Review('Для номера приёмки укажи заказ поставщику')
        with self.db() as db:
            row = db.execute('SELECT * FROM receipt_numbers WHERE id=?', (doc['id'],)).fetchone()
        if row:
            if row['order_id'] != order_id:
                raise Review('Заказ приёмки изменён после присвоения номера; требуется проверка')
            return self.rename_document('supply', doc, row['label']), row['label'][3:]
        order = self.check_production_order(order_id, doc)
        order_name = str(order.get('name') or '').strip()
        if not order_name:
            raise Review('У заказа поставщику не указан номер')
        links = order.get('supplies', [])
        if not isinstance(links, list):
            raise Review('Не удалось прочитать приёмки заказа для нумерации')
        ids = {ident(link) for link in links} | {doc['id']}
        if any(not ID.fullmatch(key) for key in ids):
            raise Review('Некорректная связь приёмки в заказе')
        with self.db() as db:
            known = {r['id'] for r in db.execute('SELECT id FROM receipt_numbers WHERE order_id=?', (order_id,))}
        candidates = [doc if key == doc['id'] else self.ms.get('/entity/supply/' + key) for key in ids - known]
        if any(ident(d.get('purchaseOrder')) != order_id for d in candidates):
            raise Review('Приёмка относится к другому заказу; номер не присвоен')
        candidates.sort(key=lambda d: (d.get('created') or d.get('moment') or '', d['id']))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0) FROM receipt_numbers WHERE order_id=?', (order_id,)).fetchone()[0]
            for candidate in candidates:
                if db.execute('SELECT 1 FROM receipt_numbers WHERE id=?', (candidate['id'],)).fetchone():
                    continue
                sequence += 1
                label = f'ПР-{order_name}-{sequence:02d}'
                if db.execute('SELECT 1 FROM receipt_numbers WHERE label=?', (label,)).fetchone():
                    raise Review('Номер заказа поставщику повторяется; проверь номер ' + order_name)
                db.execute('INSERT INTO receipt_numbers VALUES(?,?,?,?)', (candidate['id'], order_id, sequence, label))
            label = db.execute('SELECT label FROM receipt_numbers WHERE id=?', (doc['id'],)).fetchone()[0]
        return self.rename_document('supply', doc, label), label[3:]

    def number_direct_losses(self, value, receipt_number, chain):
        """Number our existing/prepared documents even when stock processing is blocked."""
        for index, group in enumerate(chain.get('material_groups', [])):
            name = 'СП-' + receipt_number + (f'-{index + 1:02d}' if len(chain['material_groups']) > 1 else '')
            key = f'{value}/direct/{group["store"]}/loss'
            with self.db() as db:
                operation = db.execute('SELECT body,doc_id FROM operations WHERE key=?', (key,)).fetchone()
            if not operation:
                continue
            body = json.loads(operation['body'])
            loss_id = operation['doc_id'] or chain.get('losses', {}).get(group['store'])
            if loss_id and body.get('name') != name:
                doc = self.ms.get('/entity/loss/' + loss_id)
                if not body.get('externalCode') or doc.get('externalCode') != body['externalCode']:
                    raise Review('Не удалось подтвердить принадлежность списания приёмке')
                self.rename_document('loss', doc, name)
            if body.get('name') != name:
                body['name'] = name
                with self.db() as db:
                    db.execute('UPDATE operations SET body=? WHERE key=?', (json.dumps(body), key))

    @staticmethod
    def check_receipt_price_format(doc, positions):
        if any(dec(p.get('discount', 0)) != 0 for p in positions):
            raise Review('Убери скидку в приёмке: цена рассчитывается как полная себестоимость')
        if doc.get('vatEnabled') and not doc.get('vatIncluded') and any(p.get('vat', 0) for p in positions):
            raise Review('Для расчёта себестоимости НДС должен быть включён в цену приёмки')
        if dec((doc.get('overhead') or {}).get('sum', 0)) != 0:
            raise Review('Убери накладные расходы приёмки: они не входят в расчёт нормы')
        if dec((doc.get('rate') or {}).get('value', 1)) != 1:
            raise Review('Для расчёта себестоимости приёмка должна быть в рублях')

    def write_receipt_cost_once(self, value, chain, number):
        # Historical chains are intentionally not repriced. Unknown FIFO is not zero.
        if not chain.get('price_write_once') or chain['cost_snapshot']['total'] is None:
            return
        for item in chain['cost_snapshot']['prices']:
            if item['status'] in ('done', 'manual'):
                continue
            doc = self.ms.get('/entity/supply/' + value)
            positions = self.ms.positions('supply', value)
            if fingerprint(doc, positions, True) != chain['fingerprint']:
                raise Review('Приёмка изменилась во время расчёта себестоимости')
            self.check_receipt_price_format(doc, positions)
            row = next((p for p in positions if p.get('id') == item['id']), None)
            if not item['id'] or row is None:
                raise Review('Не найдена позиция приёмки для записи себестоимости')
            actual = dec(row.get('price', 0))
            if actual == dec(item['price']):
                item['status'] = 'done'
            elif actual != dec(item['before']):
                # A user changed the price while processing/recovering: keep their value.
                item['status'] = 'manual'
            else:
                if item['status'] == 'writing':
                    raise Review('Запись цены не подтверждена. Проверь цену приёмки: повторная запись остановлена')
                item['status'] = 'writing'
                self.save_chain(value, chain, number)
                self.ms.request('PUT', '/entity/supply/' + value + '/positions/' + item['id'],
                                {'price': item['price']})
                saved = next((p for p in self.ms.positions('supply', value) if p.get('id') == item['id']), None)
                if not saved or dec(saved.get('price', 0)) != dec(item['price']):
                    raise Review('Не удалось проверить запись себестоимости в приёмку')
                item['status'] = 'done'
            item['at'] = now()
            self.save_chain(value, chain, number)
        current = self.ms.get('/entity/supply/' + value)
        if fingerprint(current, self.ms.positions('supply', value), True) != chain['fingerprint']:
            raise Review('Приёмка изменилась во время записи себестоимости')

    def process_direct_supply(self, value):
        chain, number = self.chain(value)
        if chain:
            self.guard_chain_sizes(chain)
        current = self.ms.get('/entity/supply/' + value)
        if not chain:
            self.verify_live_sizes(ident(row.get('assortment')) for row in self.ms.positions('supply', value))
        current, receipt_number = self.number_receipt(current)
        if chain and chain.get('workflow') == 'finished_receipt':
            self.number_direct_losses(value, receipt_number, chain)
        positions = self.ms.positions('supply', value)
        force = ident(current.get('state')) == self.config()['states']['supply']['Завершена']
        if chain:
            if chain.get('workflow') != 'finished_receipt':
                raise Review('Приёмка уже обрабатывалась по прежней схеме; нужна отдельная сверка')
            if fingerprint(current, positions, bool(chain.get('price_write_once'))) != chain['fingerprint']:
                raise Review('Приёмка изменена или распроведена после списания материалов')
            self.check_production_order(ident(current['purchaseOrder']), current)
            if force and not chain.get('complete'):
                chain['allow_negative'] = True
                self.save_chain(value, chain, number)
        else:
            expected = self.config()['states']['supply']['Ожидает обработки']
            if not current.get('applicable'):
                return
            if not (force or ident(current.get('state')) == expected):
                if ident(current.get('state')) == self.config()['states']['supply']['Требует проверки']:
                    plan = self.plan_supply(value)
                    self.snapshot_direct_cost(plan, self.direct_material_groups(plan))
                    self.receipt_note(value, 'Для обработки выбери «Ожидает обработки».')
                return
            chain = self.plan_supply(value)
            self.check_receipt_price_format(current, positions)
            chain['price_write_once'] = True
            chain['fingerprint'] = fingerprint(current, positions, True)
            groups = self.direct_material_groups(chain)
            fields = self.receipt_link_fields(max(2, len(groups))) if groups else []
            attrs = {ident(a): a.get('value') for a in current.get('attributes', [])}
            if any(attrs.get(f['id']) for f in fields):
                raise Review('В приёмке уже указаны списания; проверь перед автоматической обработкой')
            # All stores and FIFO prices must be confirmed before the first write.
            cost = self.snapshot_direct_cost(chain, groups, allow_negative=force)
            chain.update(allow_negative=force, workflow='finished_receipt', material_groups=groups,
                         link_fields=fields[:len(groups)], cost_snapshot=cost, losses={}, complete=False)
            number = self.save_chain(value, chain)
        self.guard_chain_sizes(chain)
        cfg = self.config()
        if chain['material_groups'] and not ID.fullmatch(cfg.get('loss_receipt_attribute', '')):
            raise Review('Не настроено поле приёмки в списании')
        attrs = {ident(a): a.get('value') for a in current.get('attributes', [])}
        expected_links = []
        for index, group in enumerate(chain['material_groups']):
            field = chain['link_fields'][index]
            recorded = chain['losses'].get(group['store'])
            url = 'https://online.moysklad.ru/app/#loss/edit?id=' + recorded if recorded else None
            if attrs.get(field['id']) and attrs[field['id']] != url:
                raise Review('Ссылка на списание в приёмке изменена вручную; требуется сверка')
            loss = self.execute(f'{value}/direct/{group["store"]}/loss', 'loss', {
                'name': 'СП-' + receipt_number + (f'-{index + 1:02d}' if len(chain['material_groups']) > 1 else ''), 'organization': chain['source']['organization'],
                'applicable': True, 'moment': max(now(), chain['source']['moment']),
                'store': ref('store', group['store']), 'positions': group['positions'],
                'attributes': [{'meta': {'href': BASE + '/entity/loss/metadata/attributes/' + cfg['reason_attribute'],
                                         'type': 'attributemetadata', 'mediaType': 'application/json'},
                                'value': ref('customentity/' + cfg['reason_dictionary'], cfg['reason_production'])},
                               {'meta': {'href': BASE + '/entity/loss/metadata/attributes/' + cfg['loss_receipt_attribute'],
                                         'type': 'attributemetadata', 'mediaType': 'application/json'},
                                'value': 'https://online.moysklad.ru/app/#supply/edit?id=' + value}]},
                allow_negative=chain.get('allow_negative', False))
            loss_name = 'СП-' + receipt_number + (f'-{index + 1:02d}' if len(chain['material_groups']) > 1 else '')
            loss = self.rename_document('loss', loss, loss_name)
            # Old/prepared operations also recover with the new name, without another POST.
            with self.db() as db:
                key = f'{value}/direct/{group["store"]}/loss'
                operation = db.execute('SELECT body FROM operations WHERE key=?', (key,)).fetchone()
                body = json.loads(operation['body'])
                if body.get('name') != loss_name:
                    body['name'] = loss_name
                    db.execute('UPDATE operations SET body=? WHERE key=?', (json.dumps(body), key))
            chain['losses'][group['store']] = loss['id']
            self.save_chain(value, chain, number)
            self.loss_receipt_link(loss['id'], value, current=loss)
            expected_links.append({'meta': field['meta'],
                'value': 'https://online.moysklad.ru/app/#loss/edit?id=' + loss['id']})
            # Publish every successful write-off immediately, even if the next one fails.
            linked = self.ms.get('/entity/supply/' + value)
            if fingerprint(linked, self.ms.positions('supply', value), bool(chain.get('price_write_once'))) != chain['fingerprint']:
                raise Review('Приёмка изменилась во время списания материалов')
            linked_attrs = {ident(a): a.get('value') for a in linked.get('attributes', [])}
            link = expected_links[-1]
            if linked_attrs.get(field['id']) not in (None, '', link['value']):
                raise Review('Ссылка на списание изменена во время обработки')
            if linked_attrs.get(field['id']) != link['value']:
                self.ms.request('PUT', '/entity/supply/' + value, {'attributes': [link]})
            linked = self.ms.get('/entity/supply/' + value)
            if next((a.get('value') for a in linked.get('attributes', []) if ident(a) == field['id']), None) != link['value']:
                raise Review('Не удалось сохранить ссылку на списание в приёмке')
        # Never complete a receipt if it changed while documents were being created.
        current = self.ms.get('/entity/supply/' + value)
        if fingerprint(current, self.ms.positions('supply', value), bool(chain.get('price_write_once'))) != chain['fingerprint']:
            raise Review('Приёмка изменилась во время списания материалов')
        current_attrs = {ident(a): a.get('value') for a in current.get('attributes', [])}
        for link in expected_links:
            if current_attrs.get(ident(link)) not in (None, '', link['value']):
                raise Review('Ссылка на списание изменена во время обработки')
        if any(current_attrs.get(ident(a)) != a['value'] for a in expected_links):
            self.ms.request('PUT', '/entity/supply/' + value, {'attributes': expected_links})
        saved = self.ms.get('/entity/supply/' + value)
        saved_attrs = {ident(a): a.get('value') for a in saved.get('attributes', [])}
        if any(saved_attrs.get(ident(a)) != a['value'] for a in expected_links):
            raise Review('Не удалось сохранить ссылки на списания в приёмке')
        for loss_id in chain['losses'].values():
            self.audit_generated('loss', loss_id, self.ms.get('/entity/loss/' + loss_id))
        self.write_receipt_cost_once(value, chain, number)
        if chain.get('allow_negative'):
            note = self.completion_note(chain)
            self.receipt_note(value, note)
        else:
            self.receipt_note(value)
        self.set_state('supply', value, 'Завершена')
        chain['complete'] = True
        self.save_chain(value, chain, number)
        self.enqueue('purchaseorder', ident(chain['source']['purchaseOrder']))

    def process_supply(self, value):
        if self.config().get('workflow') == 'finished_receipt':
            return self.process_direct_supply(value)
        chain, number = self.chain(value)
        if chain:
            current = self.ms.get('/entity/supply/' + value)
            if fingerprint(current, self.ms.positions('supply', value)) != chain['fingerprint']:
                raise Review('Приёмка изменена после начала обработки; нужна корректировка цепочки')
            self.check_production_order(ident(current['purchaseOrder']), current)
        else:
            chain = self.plan_supply(value)
            number = self.save_chain(value, chain)
        self.guard_chain_sizes(chain)
        by_factory = defaultdict(list)
        for line in chain['lines']:
            by_factory[line['recipe']['stages'][0]['factory']].append(line)
        for lines in by_factory.values():
            self.consume(value, number, chain, lines, 0)
        groups = defaultdict(list)
        for line in chain['lines']:
            if len(line['recipe']['stages']) > 1:
                groups[(1, line['recipe']['stages'][1]['factory'])].append(line)
        for (stage, _), lines in groups.items():
            self.outbound(value, number, chain, lines, stage)
        self.finish(value, chain, number)

    def all_chains(self):
        with self.db() as db:
            return [(r['id'], r['number'], json.loads(r['body'])) for r in db.execute('SELECT * FROM chains')]

    def process_move(self, value):
        doc = self.ms.get('/entity/move/' + value)
        for cid, num, chain in self.all_chains():
            for oid, group in list(chain.get('outbound', {}).items()):
                if oid == value:
                    self.guard_chain_sizes(chain)
                    accepted = ident(doc.get('state')) == self.config()['states']['move']['Доработка принята']
                    return self.returns(cid, num, chain, oid, group, accepted)
                prefix = group['name'] + '/'
                if doc.get('name', '').startswith(prefix):
                    self.guard_chain_sizes(chain)
                    if not re.fullmatch(re.escape(prefix) + r'\d{2,}', doc['name']):
                        raise Review('Некорректный суффикс возврата')
                    return self.returns(cid, num, chain, oid, group, False)
        if doc.get('externalCode', '').startswith('2my-prod-'):
            return self.audit_generated('move', value, doc)
        if RESERVED.match(doc.get('name', '')):
            raise Review('Производственный номер без найденной цепочки')
        self.number_other('move', doc)

    def redirected_manual_return(self, doc, outgoing):
        """Recognize only the documented legacy scenario, never guess a destination."""
        cfg = self.config()
        operation_attr = cfg.get('operation_attributes', {}).get('move')
        if not operation_attr or any(ident(a) == operation_attr and a.get('value')
                                     for a in doc.get('attributes', [])):
            return False
        expected = ident(outgoing['sourceStore'])
        recipient = [a for a in doc.get('attributes', []) if a.get('name') == 'Склад получатель']
        if len(recipient) != 1 or ident(recipient[0].get('value')) != expected:
            return False
        history = self.ms.rows('/entity/move/' + doc['id'] + '/audit')
        # Inspect the latest routing change. A later employee edit must never be undone.
        for event in sorted(history, key=lambda x: x.get('moment', ''), reverse=True):
            diff = event.get('diff', {})
            if not any(k in diff for k in ('sourceStore', 'targetStore', 'organization')):
                continue
            change = diff.get('targetStore', {})
            target = change.get('newValue', {})
            return (event.get('source') == 'scriptor' and
                    not any(k in diff for k in ('sourceStore', 'organization')) and
                    ident(change.get('oldValue')) == expected and
                    ident(target) == ident(doc.get('targetStore')) and target.get('name') == 'В пути' and
                    ident(diff.get('Склад получатель', {}).get('newValue')) == expected)
        return False

    def restore_manual_return(self, doc, positions, outgoing):
        current = self.ms.get('/entity/move/' + doc['id'])
        if fingerprint(current, self.ms.positions('move', doc['id'])) != fingerprint(doc, positions):
            raise Review('Возврат изменился во время проверки')
        if not self.redirected_manual_return(current, outgoing):
            raise Review('Не подтверждён сценарий изменения склада возврата')
        cfg = self.config()
        attr = {'meta': {'href': BASE + '/entity/move/metadata/attributes/' + cfg['operation_attributes']['move'],
                         'type': 'attributemetadata', 'mediaType': 'application/json'},
                'value': ref('customentity/' + cfg['operation_dictionary'], cfg['operation_values']['Возврат с доработки'])}
        self.ms.request('PUT', '/entity/move/' + doc['id'],
                        {'targetStore': outgoing['sourceStore'], 'attributes': [attr]})
        time.sleep(2)
        result = self.ms.get('/entity/move/' + doc['id'])
        expected = dict(doc, targetStore=outgoing['sourceStore'])
        if fingerprint(result, self.ms.positions('move', doc['id'])) != fingerprint(expected, positions):
            raise Review('Возврат не прошёл проверку после восстановления склада')
        self.log('move', doc['id'], 'Возврат с доработки: восстановлен склад после штатного сценария В пути')
        return result

    def returns(self, cid, num, chain, oid, group, accepted):
        outgoing = self.ms.get('/entity/move/' + oid)
        self.audit_generated('move', oid, outgoing)
        if not outgoing.get('applicable') or quantities(self.ms.positions('move', oid)) != quantities(group['lines']):
            raise Review('Исходящее перемещение изменено или распроведено')
        candidates = self.ms.rows('/entity/move', filter='name~=' + group['name'] + '/')
        returned, documents, redirects = defaultdict(Decimal), [], set()
        names = set()
        for doc in candidates:
            if not re.fullmatch(re.escape(group['name']) + r'/\d{2,}', doc['name']):
                continue
            if doc['name'] in names:
                raise Review('Повтор номера возврата')
            names.add(doc['name'])
            if not doc.get('applicable'):
                if doc['id'] in group['returns']:
                    raise Review('Обработанный возврат распроведён')
                continue
            if ident(doc.get('sourceStore')) != ident(outgoing['targetStore']):
                raise Review('Неверное направление возврата')
            if ident(doc.get('targetStore')) != ident(outgoing['sourceStore']):
                if doc['id'] in group['returns'] or not self.redirected_manual_return(doc, outgoing):
                    raise Review('Неверное направление возврата')
                redirects.add(doc['id'])
            if ident(doc.get('organization')) != ident(outgoing.get('organization')):
                raise Review('Другая организация в возврате с доработки')
            positions = self.ms.positions('move', doc['id'])
            for k, q in quantities(positions).items():
                returned[k] += q
            documents.append((doc, positions))
        left = remaining(quantities(group['lines']), returned)
        # Validate the whole batch before restoring any legacy-redirection document.
        documents = [(self.restore_manual_return(d, ps, outgoing) if d['id'] in redirects else d, ps)
                     for d, ps in documents]
        if accepted and left:
            suffix = '/' + str(max([int(x.rsplit('/', 1)[1]) for x in names] or [0]) + 1).zfill(2)
            payload = {'name': group['name'] + suffix, 'organization': outgoing['organization'],
                       'sourceStore': outgoing['targetStore'], 'targetStore': outgoing['sourceStore'],
                       'applicable': True, 'moment': max(now(), outgoing['moment']),
                       'positions': [{'assortment': x['assortment'], 'quantity': float(left[ident(x['assortment'])]), 'price': 0}
                                     for x in self.unique_lines(group['lines']) if ident(x['assortment']) in left]}
            doc = self.execute(f'{cid}/return/{oid}/' + suffix, 'move', payload)
            # Store the API representation (prices/quantities may become floats).
            documents.append((doc, self.ms.positions('move', doc['id'])))
        for doc, positions in documents:
            fp = fingerprint(doc, positions)
            old = group['returns'].get(doc['id'])
            if old and old != fp:
                raise Review('Обработанный возврат изменён')
            qty = quantities(positions)
            matched = []
            for line in self.unique_lines(group['lines']):
                key = ident(line['assortment'])
                if key in qty:
                    matched.append(dict(line, quantity=float(qty[key])))
            suffix = group['name'][len('ВШ-' + str(num)):] + doc['name'][len(group['name']):]
            self.consume(cid, num, chain, matched, group['stage'], suffix)
            group['returns'][doc['id']] = fp
            next_groups = defaultdict(list)
            for line in matched:
                stage = group['stage'] + 1
                if len(line['recipe']['stages']) > stage:
                    next_groups[(stage, line['recipe']['stages'][stage]['factory'])].append(line)
            self.save_chain(cid, chain, num)
        total = defaultdict(Decimal)
        for rid in group['returns']:
            for key, q in quantities(self.ms.positions('move', rid)).items():
                total[key] += q
        if not remaining(quantities(group['lines']), total):
            next_groups = defaultdict(list)
            for line in group['lines']:
                stage = group['stage'] + 1
                if len(line['recipe']['stages']) > stage:
                    next_groups[(stage, line['recipe']['stages'][stage]['factory'])].append(line)
            for (stage, _), lines in next_groups.items():
                self.outbound(cid, num, chain, lines, stage)
        self.finish(cid, chain, num)

    @staticmethod
    def unique_lines(lines):
        out = {}
        for line in lines:
            key = ident(line['assortment'])
            if key in out:
                out[key]['quantity'] = float(dec(out[key]['quantity']) + dec(line['quantity']))
            else:
                out[key] = copy.deepcopy(line)
        return list(out.values())

    def finish(self, cid, chain, num):
        done = True
        for oid, group in chain.get('outbound', {}).items():
            self.audit_generated('move', oid, self.ms.get('/entity/move/' + oid))
            qty = defaultdict(Decimal)
            for rid, recorded in group['returns'].items():
                returned = self.ms.get('/entity/move/' + rid)
                positions = self.ms.positions('move', rid)
                if not returned.get('applicable') or fingerprint(returned, positions) != recorded:
                    raise Review('Обработанный возврат изменён или распроведён')
                for key, val in quantities(positions).items():
                    qty[key] += val
            if remaining(quantities(group['lines']), qty):
                done = False
        chain['complete'] = done
        self.save_chain(cid, chain, num)
        self.set_state('supply', cid, 'Завершена' if done else 'На доработке')
        self.enqueue('purchaseorder', ident(chain['source']['purchaseOrder']))

    def audit_generated(self, kind, value, doc):
        with self.db() as db:
            row = db.execute('SELECT * FROM operations WHERE kind=? AND doc_id=?', (kind, value)).fetchone()
        if not row:
            raise Review('Неизвестный автоматический документ')
        body = json.loads(row['body'])
        for field in ('sourceStore', 'targetStore', 'store', 'organization'):
            if field in body and ident(body[field]) != ident(doc.get(field)):
                raise Review('Документ ' + str(doc.get('name', doc['id'])) + ': изменён склад или организация')
        if not doc.get('applicable'):
            raise Review('Документ ' + str(doc.get('name', value)) + ' распроведён')
        if quantities(self.ms.positions(kind, value)) != quantities(body['positions']):
            raise Review('Документ ' + str(doc.get('name', value)) + ': изменён состав или количество')

    def production_number_scope(self, kind):
        with self.db() as db:
            ids = {r['doc_id'] for r in db.execute('SELECT doc_id FROM operations WHERE kind=? AND doc_id IS NOT NULL', (kind,))}
            agents = set()
            if kind == 'supply':
                ids.update(r['id'] for r in db.execute('SELECT id FROM receipt_numbers'))
                for row in db.execute('SELECT id,body FROM chains'):
                    ids.add(row['id'])
                    agents.add(ident(json.loads(row['body']).get('source', {}).get('agent')))
        if kind == 'supply':
            agents.update(f.get('agent') for f in self.config().get('factories', []))
        return ids, agents - {None, ''}

    def is_production_document(self, kind, doc, scope=None):
        ids, agents = scope if scope is not None else self.production_number_scope(kind)
        tracked = doc['id'] in ids or doc.get('externalCode', '').startswith('2my-prod-')
        if kind == 'supply':
            # A copied production number or a factory alone is not an order link.
            # Tracked receipts stay protected if somebody removes their order.
            return tracked or bool(ident(doc.get('purchaseOrder')) and (
                ident(doc.get('agent')) in agents or re.match(r'^ПР-', doc.get('name', ''))))
        # A manually copied number is not proof of a production operation.
        return tracked or self.needs_production_project(kind, doc)

    def project_receipt_basis(self, receipt_id):
        """Resolve an actual receipt/order relation; names and stock locations are not links."""
        if not ID.fullmatch(receipt_id):
            return False
        receipt = self.ms.get('/entity/supply/' + receipt_id)
        if receipt.get('deleted'):
            return False
        return self.needs_production_project('supply', receipt)

    def needs_production_project(self, kind, doc):
        if doc.get('deleted'):
            return False
        if kind == 'purchaseorder':
            return ident(doc.get('agent')) in {f.get('agent') for f in self.config().get('factories', [])}
        order_id = ident(doc.get('purchaseOrder'))
        if order_id:
            order = self.ms.get('/entity/purchaseorder/' + order_id)
            return not order.get('deleted') and self.needs_production_project('purchaseorder', order)
        if kind == 'supply':
            return False
        if kind not in ('move', 'loss'):
            return False
        receipt_id = ident(doc.get('supply'))
        if receipt_id:
            return self.project_receipt_basis(receipt_id)
        field = self.config().get('loss_receipt_attribute') if kind == 'loss' else None
        if field:
            value = next((a.get('value') for a in doc.get('attributes', []) if ident(a) == field), '')
            match = re.fullmatch(r'https://online\.moysklad\.ru/app/#supply/edit\?id=([0-9a-f-]{36})', str(value or ''))
            if match:
                return self.project_receipt_basis(match[1])
        with self.db() as db:
            operations = db.execute('SELECT key FROM operations WHERE kind=? AND doc_id=?',
                                    (kind, doc['id'])).fetchall()
        for operation in operations:
            receipt_id = operation['key'].split('/')[0]
            if ID.fullmatch(receipt_id) and self.chain(receipt_id)[0] and self.project_receipt_basis(receipt_id):
                return True
        # Legacy manual returns are recorded against a particular outgoing batch.
        if kind == 'move':
            for cid, _, chain in self.all_chains():
                if any(doc['id'] == mid or doc['id'] in group.get('returns', {})
                       for mid, group in chain.get('outbound', {}).items()):
                    return self.project_receipt_basis(cid)
        return False

    def stamp_production_project(self, kind, doc):
        project = self.config().get('production_project', '')
        if not ID.fullmatch(project) or doc.get('deleted'):
            return doc
        eligible = self.needs_production_project(kind, doc)
        current = ident(doc.get('project'))
        if eligible and current == project or not eligible and current != project:
            return doc
        # Remove only our production project; preserve unrelated projects.
        desired = ref('project', project) if eligible else None
        updated = self.ms.request('PUT', '/entity/' + kind + '/' + doc['id'], {'project': desired})
        if ident(updated.get('project')) != (project if eligible else ''):
            raise Review('Не удалось обновить проект документа ' + doc.get('name', doc['id']))
        return updated

    def number_other(self, kind, doc):
        if kind not in ('supply', 'move', 'loss') or doc.get('deleted'):
            return
        scope = self.production_number_scope(kind)
        if doc['id'] in scope[0] or doc.get('externalCode', '').startswith('2my-prod-'):
            if kind == 'supply':
                raise Review('Производственная приёмка не относится к обычной нумерации')
            return self.audit_generated(kind, doc['id'], doc)
        if self.is_production_document(kind, doc, scope):
            raise Review('Производственный документ не относится к обычной нумерации')
        with self.db() as db:
            row = db.execute('SELECT number,original FROM names WHERE kind=? AND id=?', (kind, doc['id'])).fetchone()
            counter = db.execute('SELECT number FROM number_counters WHERE kind=?', (kind,)).fetchone()
        if not row:
            existing = [] if counter else self.ms.rows('/entity/' + kind)
            ordinary = [x for x in existing if x['id'] != doc['id'] and not x.get('deleted')
                        and not self.is_production_document(kind, x, scope)
                        and re.fullmatch(r'\d+', x.get('name', ''))
                        and (not doc.get('created') or not x.get('created')
                             or (x['created'], x['id']) < (doc['created'], doc['id']))]
            maximum = max([int(x['name']) for x in ordinary] or [0])
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT number,original FROM names WHERE kind=? AND id=?', (kind, doc['id'])).fetchone()
                if not row:
                    counter = db.execute('SELECT number FROM number_counters WHERE kind=?', (kind,)).fetchone()
                    maximum = max(counter['number'] if counter else maximum,
                                  db.execute('SELECT COALESCE(MAX(number),0) FROM names WHERE kind=?', (kind,)).fetchone()[0])
                    n = maximum + 1
                    db.execute('INSERT OR REPLACE INTO number_counters VALUES(?,?)', (kind, n))
                    db.execute('INSERT INTO names VALUES(?,?,?,?)', (kind, doc['id'], n, doc.get('name', '')))
                    row = {'number': n, 'original': doc.get('name', '')}
        # Legacy manual returns keep their user-entered move number.
        if kind == 'move' and doc.get('name') not in (str(row['number']), row['original']):
            return
        self.rename_document(kind, doc, str(row['number']))

    def process_order(self, value):
        doc = self.check_production_order(value)
        ordered = quantities(self.ms.positions('purchaseorder', value))
        if not ordered:
            raise Review('Заказ поставщику не содержит изделий')
        # purchaseOrder is not a supported list filter (HTTP 412). Read native order links.
        links = doc.get('supplies', [])
        if not isinstance(links, list):
            raise Review('Не удалось прочитать связанные приёмки заказа')
        ids = {ident(link) for link in links}
        if any(not ID.fullmatch(key) for key in ids):
            raise Review('Некорректная связь приёмки в заказе')
        supplied = [self.ms.get('/entity/supply/' + key) for key in ids]
        if any(ident(d.get('purchaseOrder')) != value for d in supplied):
            raise Review('Связанная приёмка относится к другому заказу')
        accepted, complete = defaultdict(Decimal), True
        for supply in supplied:
            if not supply.get('applicable'):
                continue
            chain, _ = self.chain(supply['id'])
            if chain and fingerprint(supply, self.ms.positions('supply', supply['id']), bool(chain.get('price_write_once'))) != chain['fingerprint']:
                raise Review('Приёмка изменена после обработки; заказ не может быть завершён')
            if chain and chain.get('workflow') == 'finished_receipt':
                for loss_id in chain.get('losses', {}).values():
                    self.audit_generated('loss', loss_id, self.ms.get('/entity/loss/' + loss_id))
            if not chain or not chain.get('complete'):
                complete = False
            for key, qty in quantities(self.ms.positions('supply', supply['id'])).items():
                accepted[key] += qty
        left = remaining(ordered, accepted)
        short_sid = self.config()['states']['purchaseorder']['Завершён с недопоставкой']
        if ident(doc.get('state')) == short_sid:
            if not complete or not left:
                raise Review('Закрытие с недопоставкой не совпадает с принятыми изделиями/этапами')
            self.log('purchaseorder', value, 'Недопоставка: ' + json.dumps({k: str(v) for k, v in left.items()}))
        elif not left and complete:
            self.set_state('purchaseorder', value, 'Завершён')

    def mark_chain_review(self, kind, value, message=None):
        affected = {value} if kind == 'supply' and self.config().get('workflow') == 'finished_receipt' else set()
        for cid, _, chain in self.all_chains():
            if kind == 'loss' and value in chain.get('losses', {}).values():
                affected.add(cid)
            if kind == 'supply' and value == cid:
                affected.add(cid)
            if kind == 'move' and any(value == oid or value in group.get('returns', {})
                                      for oid, group in chain.get('outbound', {}).items()):
                affected.add(cid)
        for cid in affected:
            try:
                chain, number = self.chain(cid)
                if chain and chain.get('workflow') == 'finished_receipt':
                    chain['complete'] = False
                    self.save_chain(cid, chain, number)
                    order_id = ident(chain['source'].get('purchaseOrder'))
                    if order_id:
                        order = self.ms.get('/entity/purchaseorder/' + order_id)
                        states = self.config()['states']['purchaseorder']
                        if order.get('applicable') and ident(order.get('state')) == states['Завершён']:
                            self.set_state('purchaseorder', order_id, 'В работе')
                self.set_state('supply', cid, 'Требует проверки')
                if message:
                    self.receipt_note(cid, message)
            except Exception:
                self.log('supply', cid, 'Не удалось установить статус Требует проверки; нужна сверка')

    def run_one(self):
        if not self.config().get('enabled') or not self.work_lock.acquire(blocking=False):
            return False
        try:
            with self.db() as db:
                row = db.execute("SELECT * FROM jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT 1", (time.time(),)).fetchone()
                if not row:
                    return False
                db.execute("UPDATE jobs SET status='running' WHERE kind=? AND id=?", (row['kind'], row['id']))
            kind, value = row['kind'], row['id']
            production = kind != 'supply'
            try:
                doc = self.ms.get('/entity/' + kind + '/' + value)
                cfg = self.config()
                if kind == 'supply':
                    production = self.is_production_document(kind, doc)
                with self.db() as db:
                    tracked = db.execute('SELECT 1 FROM operations WHERE kind=? AND doc_id=?', (kind, value)).fetchone()
                if doc.get('deleted') or (doc.get('created', '') < cfg.get('start_at', '9999') and value not in cfg.get('test_document_ids', []) and not tracked):
                    result = 'ignored'
                else:
                    factories = self.config().get('factories', [])
                    if kind == 'purchaseorder' and ident(doc.get('agent')) not in {x['agent'] for x in factories}:
                        result = 'ignored'
                    else:
                        doc = self.stamp_production_project(kind, doc)
                        if kind == 'supply':
                            if production:
                                self.process_supply(value)
                            else:
                                self.number_other(kind, doc)
                        elif kind == 'move':
                            if self.config().get('workflow') == 'finished_receipt':
                                self.number_other('move', doc)
                            else:
                                self.process_move(value)
                        elif kind == 'loss': self.number_other(kind, doc)
                        else: self.process_order(value)
                        result = 'done'
                with self.db() as db:
                    db.execute("UPDATE jobs SET status=?,error=? WHERE kind=? AND id=? AND status='running'", (result, '', kind, value))
                self.log(kind, value, result)
            except Exception as exc:
                message = str(exc) if isinstance(exc, Review) else 'Внутренняя ошибка; требуется проверка сервера'
                retry = isinstance(exc, Transient) and row['attempts'] < 8
                with self.db() as db:
                    changed = db.execute("UPDATE jobs SET status=?,error=?,due=?,attempts=attempts+1 WHERE kind=? AND id=? AND status='running'",
                               ('pending' if retry else 'review', message,
                                time.time() + min(600, 15 * 2 ** row['attempts']), kind, value)).rowcount
                self.log(kind, value, message)
                if not retry and changed and production:
                    self.mark_chain_review(kind, value, message)
            return True
        finally:
            self.work_lock.release()

    def reconcile(self):
        cfg = self.config()
        if not cfg.get('enabled'):
            return
        # Окно перекрытия; не терять события во время простоя сервера.
        with self.db() as db:
            row = db.execute("SELECT value FROM kv WHERE key='cursor'").fetchone()
        cursor = json.loads(row[0]) if row else cfg['start_at']
        until = now()
        for kind in TYPES:
            for doc in self.ms.rows('/entity/' + kind, filter='updated>=' + cursor):
                self.enqueue(kind, doc['id'])
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('cursor', json.dumps(until)))


def worker(engine):
    last = 0
    while True:
        try:
            engine.start_catalogue_refresh()
            # A received webhook has priority over the background reconciliation scan.
            if engine.run_one():
                continue
            if engine.run_supplier_one():
                continue
            with engine.db() as db:
                imminent = db.execute("SELECT 1 FROM jobs WHERE status='pending' AND due<=? LIMIT 1",
                                      (time.time() + 1.5,)).fetchone()
            if not imminent and time.time() - last > 120:
                engine.reconcile()
                last = time.time()
            time.sleep(.25)
        except Exception:
            time.sleep(5)


_engine = None
_guard = threading.Lock()


def engine():
    global _engine
    with _guard:
        if _engine is None:
            _engine = Engine()
            threading.Thread(target=worker, args=(_engine,), daemon=True).start()
    return _engine


def route(handler, method):
    """Возвращает True, если HTTP-запрос обработан. Применяется существующая сессия."""
    parsed = urllib.parse.urlparse(handler.path)
    path = parsed.path
    hook = path.startswith('/hooks/production/')
    if not hook and path != '/production' and not path.startswith('/api/production'):
        return False
    if not hook and not handler._authed():
        if path == '/production':
            handler._redirect('/login')
        else:
            handler._json(401, {'error': 'Требуется вход'})
        return True
    try:
        if hook:
            secret = os.environ.get('PRODUCTION_WEBHOOK_TOKEN', '')
            given = handler.headers.get('X-Production-Token') or parsed.path.rsplit('/', 1)[-1]
            if not secret or not hmac.compare_digest(given, secret):
                handler._json(403, {'error': 'Недопустимый запрос'})
                return True
        if method == 'GET' and path == '/production':
            handler._send(200, (Path(__file__).with_name('production.html')).read_bytes(), 'text/html; charset=utf-8')
            return True
        service = engine()
        if method == 'GET':
            if path == '/api/production/supplier/status':
                with service.db() as db:
                    result = {'issues': [dict(r) for r in db.execute("SELECT id,error FROM supplier_jobs WHERE status='review'")]}
            elif path == '/api/production/stock':
                qs = urllib.parse.parse_qs(parsed.query)
                result = {'rows': service.stock(qs.get('store', [''])[0])}
            elif path == '/api/production/analytics':
                qs = urllib.parse.parse_qs(parsed.query)
                result = service.analytics(qs.get('store', [''])[0], qs.get('refresh', [''])[0] == '1')
            elif path == '/api/production/catalogues':
                qs = urllib.parse.parse_qs(parsed.query)
                result = service.catalogue_snapshot(not qs.get('status'))
            elif path == '/api/production/catalogue':
                qs = urllib.parse.parse_qs(parsed.query)
                result = {'rows': service.catalogue(qs.get('kind', ['assortment'])[0], qs.get('q', [''])[0])}
            elif path == '/api/production/setup':
                result = {'reasons': REASONS, 'states': STATES, 'hook_paths': [
                    '/hooks/production/' + kind for kind in TYPES], 'auth': 'X-Production-Token',
                    'ready': bool(os.environ.get('PRODUCTION_WEBHOOK_TOKEN'))}
            else:
                result = service.status()
            handler._json(200, result)
            return True
        if method != 'POST' or (not hook and handler.headers.get('X-Production-Request') != '1'):
            raise Review('Недопустимый запрос')
        size = int(handler.headers.get('Content-Length') or 0)
        if not 0 < size <= 262144:
            raise Review('Недопустимый размер запроса')
        raw = handler.rfile.read(size)
        body = json.loads(raw)
        if hook:
            kind = path.split('/')[3]
            ids = []
            if body.get('id'):
                ids.append(body['id'])
            for event in body.get('events', []):
                url = event.get('meta', {}).get('href', '')
                match = re.fullmatch(re.escape(BASE) + '/entity/' + re.escape(kind) + '/(' + ID.pattern[1:-1] + ')', url)
                if match: ids.append(match.group(1))
            if not ids:
                raise Review('Нет идентификатора документа')
            for value in ids: service.enqueue(kind, value)
            result = {'queued': len(ids)}
        elif path == '/api/production/catalogues/refresh':
            started = service.start_catalogue_refresh(force=True)
            result = dict(service.catalogue_snapshot(False), started=started)
        elif path == '/api/production/recipes/apply':
            result = {'recipes': service.save_recipe_group(body.get('recipes'), actor='Общий аккаунт')}
        elif path == '/api/production/supplier/retry':
            value = str(body.get('id') or '')
            if not ID.fullmatch(value):
                raise Review('Не найдена карточка товара')
            with service.db() as db:
                if not db.execute('SELECT 1 FROM supplier_jobs WHERE id=?', (value,)).fetchone():
                    raise Review('Нет задания для этого товара')
                service.enqueue_supplier(db, value)
            result = {'queued': True}
        elif path == '/api/production/recipe':
            result = service.save_recipe(body, actor='Общий аккаунт')
        elif path == '/api/production/config':
            service.set_config(body)
            result = {'saved': True}
        elif path == '/api/production/preview':
            result = service.plan_supply(body['id'])
        elif path == '/api/production/retry':
            service.enqueue(body['kind'], body['id'])
            result = {'queued': True}
        else:
            raise Review('Неизвестное действие')
        handler._json(200, result)
    except (Review, ValueError, KeyError, TypeError) as exc:
        handler._json(422, {'error': str(exc)[:250]})
    except Exception:
        handler._json(503, {'error': 'Ошибка сервера производства; подробности в журнале'})
    return True
