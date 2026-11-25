import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import create_app
from app.cutting import calculate
from app.db import Entity, User, now, transaction
from app.fbo_stock import store_snapshot
from app.order_feed import set_sync
from app.pulse import build_pulse
from app.sales import MSK, annotate_saved_gaps, attach_window_fields, begin_sales_run, finish_sales_run, store_window
from test_app import setup


def _has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _moment(hour):
    return datetime.now(MSK).replace(hour=hour, minute=0, second=0, microsecond=0)


def _period(moment):
    today = moment.date()
    return str(today - timedelta(days=30)), str(today - timedelta(days=1)), str(today - timedelta(days=2))


def _window(engine, platform, start, end, items, **extra):
    events = extra.pop('events', [])
    store_window(engine, dict(platform=platform, start=start, end=end, at=now().isoformat(), complete=True, verified=True, covered_items=list(items), events=events, **extra))


def _fbo(engine):
    store_snapshot(engine, 'ozon', {'s': 1, 'm': 0}, source='test', data_at=now().isoformat())


def _source(pulse, source_id):
    return next(row for row in pulse['sources'] if row['id'] == source_id)


def test_morning_waits_and_afternoon_is_a_hole(setup):
    engine, _, _ = setup
    moment = _moment(2)
    start, _, floor = _period(moment)
    _fbo(engine)
    proof = dict(all_products_matched=True, catalog_products=2, matched_products=2)
    _window(engine, 'ozon', start, floor, ['s', 'm'], catalog_proof=proof)
    _window(engine, 'wb', start, floor, ['s', 'm'])
    morning = build_pulse(engine, moment)
    assert _source(morning, 'ozon-sales')['tone'] == 'waiting'
    assert _source(morning, 'wb-sales')['tone'] == 'waiting'
    assert morning['badge'] == 0
    afternoon = build_pulse(engine, moment.replace(hour=12))
    assert _source(afternoon, 'ozon-sales')['tone'] == 'failure'
    assert _source(afternoon, 'wb-sales')['tone'] == 'failure'
    assert afternoon['badge'] == 2
    assert any(row['id'] == 'wb-fbo' for row in afternoon['limits'])
    assert all(row['id'] != 'wb-fbo' for row in afternoon['sources'])


def test_stuck_timer_is_visible_and_manual_has_longer_grace(setup):
    engine, _, _ = setup
    moment = _moment(12)
    with transaction(engine) as s:
        s.add(Entity(id='sales-run', kind='sales_run', version=1, data=dict(state='running', source='timer', started_at=(moment - timedelta(minutes=10)).astimezone(timezone.utc).isoformat(), finished_at=None, error=None)))
    assert _source(build_pulse(engine, moment), 'sales-run')['tone'] == 'failure'
    assert 'не завершилась' in _source(build_pulse(engine, moment), 'sales-run')['summary']
    with transaction(engine) as s:
        row = s.get(Entity, 'sales-run')
        row.data = {**row.data, 'source': 'manual'}
    assert _source(build_pulse(engine, moment), 'sales-run')['tone'] == 'waiting'
    with transaction(engine) as s:
        row = s.get(Entity, 'sales-run')
        row.data = {**row.data, 'started_at': (moment - timedelta(minutes=16)).astimezone(timezone.utc).isoformat()}
    assert _source(build_pulse(engine, moment), 'sales-run')['tone'] == 'failure'


def test_run_marker_closes_after_the_work(setup):
    engine, _, _ = setup
    begin_sales_run(engine, 'timer')
    assert _source(build_pulse(engine), 'sales-run')['tone'] == 'waiting'
    finish_sales_run(engine)
    assert _source(build_pulse(engine), 'sales-run')['tone'] == 'ok'


def test_order_pause_is_not_a_failure_and_secrets_stay_out(setup):
    engine, _, _ = setup
    moment = _moment(12)
    start, yesterday, _ = _period(moment)
    _fbo(engine)
    proof = dict(all_products_matched=True, catalog_products=2, matched_products=2)
    _window(engine, 'ozon', start, yesterday, ['s', 'm'], catalog_proof=proof)
    _window(engine, 'wb', start, yesterday, ['s', 'm'])
    set_sync(engine, 'ozon-fbs', state='waiting', account='secret-account', token_fingerprint='secret-token')
    set_sync(engine, 'limit:ozon', value='hidden-limit')
    waiting = build_pulse(engine, moment)
    assert _source(waiting, 'orders-ozon')['tone'] == 'waiting'
    raw = json.dumps(waiting, ensure_ascii=False)
    assert 'secret-account' not in raw and 'secret-token' not in raw and 'hidden-limit' not in raw
    assert not _has_key(waiting, 'events')
    assert not any(row['id'] == 'wb-fbo' for row in waiting['sources'])
    assert waiting['badge'] == sum(1 for row in waiting['sources'] if row['tone'] == 'failure') + len(waiting['topics'])
    set_sync(engine, 'ozon-fbs', state='error', error='Площадка не ответила', account='secret-account')
    failed = build_pulse(engine, moment)
    assert _source(failed, 'orders-ozon')['tone'] == 'failure'
    assert 'secret-account' not in json.dumps(failed, ensure_ascii=False)


def test_foreign_card_is_one_topic_and_known_item_stays_out(setup):
    engine, _, _ = setup
    moment = _moment(12)
    start, yesterday, _ = _period(moment)
    _fbo(engine)
    _window(engine, 'ozon', start, yesterday, ['s'], events=[dict(id='sale-s', item_id='s', date=yesterday, kind='sale', quantity=4)], catalog_proof=dict(all_products_matched=False, catalog_products=2, matched_products=1), unmatched=6, gaps=[dict(offer_id='ФТБ-ТЕСТ', name='Футболка тест', units=6)])
    _window(engine, 'wb', start, yesterday, ['s', 'm'], unmatched=63, unmatched_by_day={yesterday: 3, start: 60})
    pulse = build_pulse(engine, moment)
    cards = next(row for row in pulse['topics'] if row['id'] == 'ozon-cards')
    assert cards['count'] == 1 and cards['units'] == 6 and cards['rows'][0]['offer_id'] == 'ФТБ-ТЕСТ'
    local = next(row for row in pulse['topics'] if row['id'] == 'ozon-local')
    assert [row['item_id'] for row in local['rows']] == ['m']
    assert all(row.get('item_id') != 's' and 'Лонг скимс S' not in (row.get('name') or '') for topic in pulse['topics'] for row in topic['rows'])
    lines = next(row for row in pulse['topics'] if row['id'] == 'wb-lines')
    assert lines['count'] == 63 and '63 строк' in lines['title'] and start in lines['title'] and yesterday in lines['title']


def test_gap_note_does_not_change_cutting_fingerprint(setup):
    engine, _, _ = setup
    moment = datetime.now(MSK)
    start, end = str(moment.date() - timedelta(days=30)), str(moment.date() - timedelta(days=1))
    _window(engine, 'ozon', start, end, ['s'], events=[dict(id='a', item_id='s', date=end, kind='sale', quantity=3)])
    _window(engine, 'wb', start, end, ['s'], events=[dict(id='b', item_id='s', date=end, kind='sale', quantity=2)], unmatched_by_day={end: 4}, unmatched=9)
    with Session(engine) as s:
        first = calculate(s, dict(location='own', item_ids=['s'], start=start, end=end, include_fbo=False))
        saved = s.get(Entity, 'sales:ozon:' + start + ':' + end)
        version, digest = saved.version, saved.data['digest']
    assert attach_window_fields(engine, 'sales:ozon:' + start + ':' + end, dict(gaps=[dict(offer_id='X', name='Карточка', units=1)]))
    with Session(engine) as s:
        saved = s.get(Entity, 'sales:ozon:' + start + ':' + end)
        assert saved.version == version and saved.data['digest'] == digest and saved.data['gaps'][0]['offer_id'] == 'X'
        second = calculate(s, dict(location='own', item_ids=['s'], start=start, end=end, include_fbo=False))
    assert first['fingerprint'] == second['fingerprint']
    assert any('4 строк' in text and start in text and end in text for text in second['warnings'])
    assert all('9 строк' not in text for text in second['warnings'])


def test_annotate_uses_local_cache_without_touching_version(setup, tmp_path):
    engine, _, _ = setup
    day = str(datetime.now(MSK).date() - timedelta(days=1))
    _window(engine, 'ozon', day, day, ['s'])
    _window(engine, 'wb', day, day, ['s'], unmatched=1)
    cache = tmp_path / 'cache'
    cache.mkdir()
    catalog = dict(at=now().isoformat(), complete=True, items=[dict(id='99', offer_id='ФТБ-ТЕСТ', name='Футболка тест', barcodes=['zzz'], sources=[{'sku': '9'}], is_archived=False)])
    (cache / 'ozon-catalog.json').write_text(json.dumps(catalog))
    accrual = dict(accruals=[dict(accrual_id='1', date=day, unit_number='u', posting={'products': [{'sku': '9', 'quantity': 2, 'commission': {'seller_price': {'amount': '10'}, 'sale_amount': {'amount': '10'}}}]})])
    (cache / ('ozon-' + day + '.json')).write_text(json.dumps(accrual))
    with transaction(engine) as s:
        s.add(Entity(id='raw-1', kind='market_sale_raw', version=1, data=dict(account='a', row=dict(date=day + 'T12:00:00+03:00', barcode='missing', saleID='S1'))))
    assert annotate_saved_gaps(engine, cache) == {'ozon': 1, 'wb': 1}
    with Session(engine) as s:
        ozon = s.get(Entity, 'sales:ozon:' + day + ':' + day)
        wb = s.get(Entity, 'sales:wb:' + day + ':' + day)
        assert ozon.version == 1 and ozon.data['gaps'][0]['offer_id'] == 'ФТБ-ТЕСТ' and ozon.data['gaps'][0]['units'] == 2
        assert wb.version == 1 and wb.data['unmatched_by_day'] == {day: 1}
    assert annotate_saved_gaps(engine, cache) == {'ozon': 0, 'wb': 0}


def test_cutter_can_read_pulse_without_sale_bodies(setup):
    engine, _, _ = setup
    with transaction(engine) as s:
        s.add(User(id='cutter', name='Закройщик', role='cutter', active=True, token_hash=sha256(b'cutter-token').hexdigest()))
    client = TestClient(create_app(engine))
    owner = client.get('/api/attention/pulse', headers={'Authorization': 'Bearer test-only'})
    cutter = client.get('/api/attention/pulse', headers={'Authorization': 'Bearer cutter-token'})
    assert owner.status_code == 200 and cutter.status_code == 200
    body = owner.json()
    assert isinstance(body['badge'], int) and body['period']['start'] < body['period']['end']
    assert not _has_key(body, 'events')
