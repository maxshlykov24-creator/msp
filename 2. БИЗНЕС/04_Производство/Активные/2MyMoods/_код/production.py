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


def fingerprint(doc, positions):
    # Статус и номер могут изменяться штатно. Цена приёмки сохраняется как введена.
    body = {k: doc.get(k) for k in ('applicable', 'moment', 'organization', 'store',
                                    'sourceStore', 'targetStore', 'agent', 'purchaseOrder')}
    body['positions'] = sorted((ident(p['assortment']), str(dec(p['quantity'])),
                                str(p.get('price', 0))) for p in positions)
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
            CREATE TABLE IF NOT EXISTS jobs(kind TEXT,id TEXT,due REAL,status TEXT,error TEXT,
              attempts INTEGER DEFAULT 0,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY,kind TEXT,doc_id TEXT,
              body TEXT NOT NULL,status TEXT NOT NULL,fingerprint TEXT);
            CREATE TABLE IF NOT EXISTS chains(id TEXT PRIMARY KEY,number INTEGER UNIQUE,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS names(kind TEXT,id TEXT,number INTEGER,original TEXT,
              PRIMARY KEY(kind,id),UNIQUE(kind,number));
            CREATE TABLE IF NOT EXISTS audit(at REAL,kind TEXT,id TEXT,message TEXT);
            ''')
            db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
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
        return json.loads(row[0]) if row else {'enabled': False, 'delay': 5, 'factories': [], 'states': {}}

    def set_config(self, config):
        # Включение возможно только после настройки складов, статусов и даты старта.
        if config.get('enabled'):
            if os.environ.get('PRODUCTION_ALLOW_WRITES') != '1':
                raise Review('Запуск заблокирован до проверки штатных связей документов и контрольной партии')
            if not config.get('start_at') or not config.get('company_store') or not config.get('states'):
                raise Review('Перед включением нужны дата старта, склад компании и статусы')
            if not config.get('reason_attribute') or not config.get('reason_production'):
                raise Review('Не настроен справочник причин списания')
        config['delay'] = max(3, min(60, int(config.get('delay', 5))))
        for item in config.get('factories', []) if config.get('enabled') else []:
            for field in ('agent', 'store'):
                if not ID.fullmatch(str(item.get(field, ''))):
                    raise Review('Выбери контрагента и склад каждой фабрики')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('config', json.dumps(config, ensure_ascii=False)))

    def recipes(self):
        with self.db() as db:
            result = sorted([json.loads(r[0]) for r in db.execute('SELECT body FROM recipes')], key=lambda r: r.get('name', ''))
            for recipe in result:
                recipe['history'] = [dict(h) for h in db.execute(
                    'SELECT version,at FROM recipe_history WHERE id=? ORDER BY version DESC', (recipe['id'],))]
            return result

    def save_recipe(self, recipe):
        key = str(recipe.get('id') or uuid.uuid4())
        recipe.pop('history', None)
        # Display units never become stock units without dimensional conversion.
        units = {'г': ('mass', 1), 'грамм': ('mass', 1), 'гр': ('mass', 1),
                 'кг': ('mass', 1000), 'м': ('length', 1), 'метр': ('length', 1),
                 'см': ('length', .01), 'шт': ('piece', 1), 'шт.': ('piece', 1)}
        for stage in recipe.get('stages', []):
            for material in stage.get('materials', []):
                if 'input_quantity' not in material:
                    continue
                source = str(material.get('input_unit', '')).lower().strip()
                target = str(material.get('unit', '')).lower().strip()
                if source == target and source:
                    factor = 1
                elif source in units and target in units and units[source][0] == units[target][0]:
                    factor = units[source][1] / units[target][1]
                elif recipe.get('approved'):
                    raise Review('Единица нормы не соответствует единице карточки материала')
                else:
                    continue
                material['quantity'] = float(dec(material['input_quantity']) * dec(factor))
        if recipe.get('approved'):
            if not ID.fullmatch(recipe.get('assortment_id', '')) or not recipe.get('stages'):
                raise Review('Для утверждения выбери изделие и этапы')
            for stage in recipe['stages']:
                if not stage.get('factory') or not stage.get('materials'):
                    raise Review('У каждого этапа нужны фабрика и материалы')
                for m in stage['materials']:
                    if not ID.fullmatch(m.get('id', '')) or dec(m.get('quantity', 0)) <= 0:
                        raise Review('Выбери материал и положительную норму в единицах его карточки')
                    if m.get('type', 'product') not in ('product', 'variant'):
                        raise Review('Материалом должен быть товар или модификация')
                if dec(stage.get('work_cost', 0)) < 0:
                    raise Review('Стоимость работы не может быть отрицательной')
            if recipe.get('editor_schema') == 2:
                packs = [m for stage in recipe['stages'] for m in stage['materials'] if m.get('role') == 'packaging']
                if len(packs) != 1 or not packs[0].get('id') or dec(packs[0].get('quantity', 0)) <= 0:
                    raise Review('Выбери один зип-пакет и укажи его расход')
            if not recipe.get('zip_checked'):
                raise Review('Добавь зип-пакет и его расход в блоке Производственная упаковка')
        with self.db() as db:
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
            # Стоимость работы общая для модели и цвета; нормы остаются размерными.
            if recipe.get('model') and recipe.get('color'):
                for row in db.execute('SELECT id,version,body FROM recipes WHERE id<>?', (key,)).fetchall():
                    other = json.loads(row['body'])
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
        return recipe

    def enqueue(self, kind, value):
        if kind not in TYPES or not ID.fullmatch(str(value)):
            raise Review('Нужны тип документа и UUID')
        with self.db() as db:
            db.execute('''INSERT INTO jobs(kind,id,due,status,error) VALUES(?,?,?,'pending','')
              ON CONFLICT(kind,id) DO UPDATE SET due=excluded.due,status='pending',error='' ''',
                       (kind, value, time.time() + self.config().get('delay', 5)))

    def log(self, kind, value, message):
        with self.db() as db:
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), kind, value, message[:500]))

    def status(self):
        with self.db() as db:
            jobs = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY due DESC LIMIT 100')]
            audit = [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY at DESC LIMIT 100')]
            chains = [dict(json.loads(r[0]), id=r[1], number=r[2]) for r in db.execute(
                'SELECT body,id,number FROM chains ORDER BY number DESC LIMIT 100')]
        return {'config': self.config(), 'recipes': self.recipes(), 'jobs': jobs, 'audit': audit, 'chains': chains}

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
            # Keep only fields required by selectors, without contacts/payment details.
            snapshots[kind] = [{'id': r['id'], 'name': r['name'], 'meta': r['meta'],
                'uom': r.get('uom') or r.get('product', {}).get('uom'), 'code': r.get('code', ''),
                'article': r.get('article') or r.get('product', {}).get('article', ''),
                'pathName': r.get('pathName') or r.get('product', {}).get('pathName', ''),
                'model': r.get('product', {}).get('name', r['name']),
                'characteristics': r.get('characteristics', [])} for r in rows]
        updated = time.time()
        with self.db() as db:
            for kind, rows in snapshots.items():
                db.execute('INSERT OR REPLACE INTO catalogues VALUES(?,?,?)',
                           (kind, updated, json.dumps(rows, ensure_ascii=False)))
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

    def require_stock(self, store, positions):
        rows = self.ms.rows('/report/stock/all', filter='store=' + BASE + '/entity/store/' + store,
                            stockMode='all')
        stock = {r.get('id') or ident({'meta': r.get('meta', {})}): dec(r.get('stock', 0)) for r in rows}
        for key, qty in quantities(positions).items():
            if stock.get(key, Decimal(0)) < qty:
                raise Review('Недостаточно материала/изделий на складе: ' + key)

    def stock(self, store):
        ref('store', store)
        rows = self.ms.rows('/report/stock/all', filter='store=' + BASE + '/entity/store/' + store)
        return [{'id': ident({'meta': r['meta']}), 'name': r.get('name', ''),
                 'stock': r.get('stock', 0), 'reserve': r.get('reserve', 0),
                 'price_kopecks': r.get('price'), 'unit': r.get('uom', {}).get('name', '')} for r in rows]

    def execute(self, key, kind, payload, basis=None):
        """syncId + сохранённый payload обеспечивают восстановление после неопределённого ответа."""
        with self.db() as db:
            row = db.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()
            if row:
                payload = json.loads(row['body'])
            else:
                payload = copy.deepcopy(payload)
                cfg = self.config()
                if cfg.get('operation_attributes', {}).get(kind):
                    label = ('Изделия на доработку' if '/out/' in key else
                             'Возврат с доработки' if '/return/' in key else 'Расход материалов')
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
            doc = self.ms.get('/entity/' + kind + '/' + found[0]['id'])
            positions = self.ms.positions(kind, doc['id'])
            for field in ('sourceStore', 'targetStore', 'store', 'organization'):
                if field in payload and ident(payload[field]) != ident(doc.get(field)):
                    raise Review('Изменён склад/организация автоматического документа')
            if not doc.get('applicable') or quantities(positions) != quantities(payload['positions']):
                raise Review('Автоматический документ изменён или распроведён')
        else:
            self.require_stock(ident(payload.get('sourceStore') or payload.get('store')), payload['positions'])
            if basis and kind == 'move':
                template = self.ms.request('PUT', '/entity/move/new', {'supply': ref('supply', basis)})
                if ident(template.get('supply')) != basis:
                    raise Review('API не подтвердил основание приёмки для перемещения')
                payload = dict(template, **payload)
            doc = self.ms.request('POST', '/entity/' + kind, payload)
            check = self.ms.get('/entity/' + kind + '/' + doc['id'])
            if quantities(self.ms.positions(kind, doc['id'])) != quantities(payload['positions']) or not check.get('applicable'):
                raise Review('Созданный документ не прошёл проверку состава/проведения')
            if basis and kind == 'move' and ident(check.get('supply')) != basis:
                raise Review('Созданный документ не подтвердил связь с приёмкой')
        with self.db() as db:
            db.execute("UPDATE operations SET doc_id=?,status='done' WHERE key=?", (doc['id'], key))
        return doc

    def recipe_index(self):
        out = {}
        for r in self.recipes():
            if r.get('approved'):
                key = r['assortment_id']
                if key in out:
                    raise Review('Две утверждённые нормы одного изделия')
                out[key] = r
        return out

    def plan_supply(self, value):
        doc = self.ms.get('/entity/supply/' + value)
        positions = self.ms.positions('supply', value)
        cfg = self.config()
        if not doc.get('applicable'):
            raise Review('Приёмка не проведена')
        if not ident(doc.get('purchaseOrder')):
            raise Review('Производственная приёмка должна иметь заказ поставщику')
        factories = {f['id']: f for f in cfg.get('factories', [])}
        index = self.recipe_index()
        lines = []
        for row in positions:
            key = ident(row.get('assortment'))
            recipe = index.get(key)
            if not recipe:
                raise Review('Нет утверждённой нормы для изделия ' + key)
            first = factories.get(recipe['stages'][0]['factory'])
            if not first or first['agent'] != ident(doc.get('agent')):
                raise Review('Изготовитель приёмки не совпал с маршрутом изделия')
            for stage in recipe['stages']:
                if stage['factory'] not in factories:
                    raise Review('Не настроен склад этапа производства')
            lines.append({'assortment': row['assortment'], 'quantity': row['quantity'],
                          'recipe': copy.deepcopy(recipe)})
        return {'source': doc, 'fingerprint': fingerprint(doc, positions), 'lines': lines,
                'factories': factories, 'company_store': cfg.get('company_store')}

    def chain(self, value):
        with self.db() as db:
            row = db.execute('SELECT * FROM chains WHERE id=?', (value,)).fetchone()
        return (json.loads(row['body']), row['number']) if row else (None, None)

    def save_chain(self, value, body, number=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if number is None:
                number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM chains').fetchone()[0]
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
        chain.setdefault('outbound', {})[doc['id']] = {'stage': stage, 'lines': lines, 'name': label, 'returns': {}}
        self.save_chain(chain_id, chain, number)

    def process_supply(self, value):
        chain, number = self.chain(value)
        if chain:
            current = self.ms.get('/entity/supply/' + value)
            if fingerprint(current, self.ms.positions('supply', value)) != chain['fingerprint']:
                raise Review('Приёмка изменена после начала обработки; нужна корректировка цепочки')
        else:
            chain = self.plan_supply(value)
            number = self.save_chain(value, chain)
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
                    accepted = ident(doc.get('state')) == self.config()['states']['move']['Доработка принята']
                    return self.returns(cid, num, chain, oid, group, accepted)
                prefix = group['name'] + '/'
                if doc.get('name', '').startswith(prefix):
                    if not re.fullmatch(re.escape(prefix) + r'\d{2,}', doc['name']):
                        raise Review('Некорректный суффикс возврата')
                    return self.returns(cid, num, chain, oid, group, False)
        if doc.get('externalCode', '').startswith('2my-prod-'):
            return self.audit_generated('move', value, doc)
        if RESERVED.match(doc.get('name', '')):
            raise Review('Производственный номер без найденной цепочки')
        self.number_other('move', doc)

    def returns(self, cid, num, chain, oid, group, accepted):
        outgoing = self.ms.get('/entity/move/' + oid)
        if not outgoing.get('applicable') or quantities(self.ms.positions('move', oid)) != quantities(group['lines']):
            raise Review('Исходящее перемещение изменено или распроведено')
        candidates = self.ms.rows('/entity/move', filter='name~=' + group['name'] + '/')
        returned, documents = defaultdict(Decimal), []
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
            if (ident(doc.get('sourceStore')) != ident(outgoing['targetStore']) or
                    ident(doc.get('targetStore')) != ident(outgoing['sourceStore'])):
                raise Review('Неверное направление возврата')
            positions = self.ms.positions('move', doc['id'])
            for k, q in quantities(positions).items():
                returned[k] += q
            documents.append((doc, positions))
        left = remaining(quantities(group['lines']), returned)
        if accepted and left:
            suffix = '/' + str(max([int(x.rsplit('/', 1)[1]) for x in names] or [0]) + 1).zfill(2)
            payload = {'name': group['name'] + suffix, 'organization': outgoing['organization'],
                       'sourceStore': outgoing['targetStore'], 'targetStore': outgoing['sourceStore'],
                       'applicable': True, 'moment': max(now(), outgoing['moment']),
                       'positions': [{'assortment': x['assortment'], 'quantity': float(left[ident(x['assortment'])]), 'price': 0}
                                     for x in self.unique_lines(group['lines']) if ident(x['assortment']) in left]}
            doc = self.execute(f'{cid}/return/{oid}/' + suffix, 'move', payload)
            documents.append((doc, payload['positions']))
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
            qty = defaultdict(Decimal)
            for rid in group['returns']:
                for key, val in quantities(self.ms.positions('move', rid)).items():
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
        if not doc.get('applicable') or quantities(self.ms.positions(kind, value)) != quantities(body['positions']):
            raise Review('Автоматический документ изменён/распроведён')

    def number_other(self, kind, doc):
        if kind not in ('move', 'loss'):
            return
        if doc.get('externalCode', '').startswith('2my-prod-'):
            return self.audit_generated(kind, doc['id'], doc)
        if RESERVED.match(doc.get('name', '')):
            raise Review('Производственный номер без найденной цепочки')
        with self.db() as db:
            row = db.execute('SELECT number FROM names WHERE kind=? AND id=?', (kind, doc['id'])).fetchone()
        if row:
            if doc.get('name') == str(row['number']):
                return
            with self.db() as db:
                original = db.execute('SELECT original FROM names WHERE kind=? AND id=?', (kind, doc['id'])).fetchone()[0]
            if doc.get('name') != original:
                return  # Сотрудник мог вписать номер возврата после CREATE.
            self.ms.request('PUT', '/entity/' + kind + '/' + doc['id'], {'name': str(row['number'])})
            return
        existing = self.ms.rows('/entity/' + kind)
        maximum = max([int(x['name']) for x in existing if x['id'] != doc['id'] and re.fullmatch(r'\d+', x.get('name', ''))] or [0])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            maximum = max(maximum, db.execute('SELECT COALESCE(MAX(number),0) FROM names WHERE kind=?', (kind,)).fetchone()[0])
            n = maximum + 1
            db.execute('INSERT INTO names VALUES(?,?,?,?)', (kind, doc['id'], n, doc.get('name', '')))
        self.ms.request('PUT', '/entity/' + kind + '/' + doc['id'], {'name': str(n)})

    def process_order(self, value):
        doc = self.ms.get('/entity/purchaseorder/' + value)
        supplied = self.ms.rows('/entity/supply', filter='purchaseOrder=' + BASE + '/entity/purchaseorder/' + value)
        accepted, complete = defaultdict(Decimal), True
        for supply in supplied:
            if not supply.get('applicable'):
                continue
            chain, _ = self.chain(supply['id'])
            if not chain or not chain.get('complete'):
                complete = False
            for key, qty in quantities(self.ms.positions('supply', supply['id'])).items():
                accepted[key] += qty
        left = remaining(quantities(self.ms.positions('purchaseorder', value)), accepted)
        short_sid = self.config()['states']['purchaseorder']['Завершён с недопоставкой']
        if ident(doc.get('state')) == short_sid:
            if not complete or not left:
                raise Review('Закрытие с недопоставкой не совпадает с принятыми изделиями/этапами')
            self.log('purchaseorder', value, 'Недопоставка: ' + json.dumps({k: str(v) for k, v in left.items()}))
        elif not left and complete:
            self.set_state('purchaseorder', value, 'Завершён')

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
            try:
                doc = self.ms.get('/entity/' + kind + '/' + value)
                if doc.get('created', '') < self.config().get('start_at', '9999'):
                    result = 'ignored'
                else:
                    factories = self.config().get('factories', [])
                    if kind in ('supply', 'purchaseorder') and ident(doc.get('agent')) not in {x['agent'] for x in factories}:
                        result = 'ignored'
                    else:
                        if kind == 'supply': self.process_supply(value)
                        elif kind == 'move': self.process_move(value)
                        elif kind == 'loss': self.number_other(kind, doc)
                        else: self.process_order(value)
                        result = 'done'
                with self.db() as db:
                    db.execute('UPDATE jobs SET status=?,error=? WHERE kind=? AND id=?', (result, '', kind, value))
                self.log(kind, value, result)
            except Exception as exc:
                message = str(exc) if isinstance(exc, Review) else 'Внутренняя ошибка; требуется проверка сервера'
                retry = isinstance(exc, Transient) and row['attempts'] < 8
                with self.db() as db:
                    db.execute('UPDATE jobs SET status=?,error=?,due=?,attempts=attempts+1 WHERE kind=? AND id=?',
                               ('pending' if retry else 'review', message,
                                time.time() + min(600, 15 * 2 ** row['attempts']), kind, value))
                self.log(kind, value, message)
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
            if time.time() - last > 120:
                engine.reconcile()
                last = time.time()
            if not engine.run_one():
                time.sleep(1)
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
            if path == '/api/production/stock':
                qs = urllib.parse.parse_qs(parsed.query)
                result = {'rows': service.stock(qs.get('store', [''])[0])}
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
        elif path == '/api/production/recipe':
            result = service.save_recipe(body)
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
