"""Повторная сверка снимка с МС и проход демо на временной БД, только GET."""
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from ms_read import MoySklad, READY, ReadError, fetch_label, decode_sticker
from run import load_vault_access
from workflow import Workflow


def verify():
    root = Path(__file__).parent
    ms = MoySklad()
    ms.load_states()
    with sqlite3.connect(root / 'data/demo.sqlite') as db:
        orders = [json.loads(r[0]) for r in db.execute('SELECT payload FROM snapshot LIMIT 20')]
    if len(orders) != 20:
        raise ReadError('Для проверки нужен снимок из 20 заданий')
    problems = []
    units = 0
    for o in orders:
        fresh = ms.get('/entity/customerorder/' + o['id'])
        positions = list(ms.rows('/entity/customerorder/' + o['id'] + '/positions', expand='assortment'))
        total = sum(p['quantity'] for p in positions)
        units += total
        if len(positions) != 1 or total != o['quantity'] or positions[0]['assortment']['id'] != o['product_id']:
            problems.append(o['number'] + ': состав изменился')
        codes = [str(v) for p in positions for b in p['assortment'].get('barcodes', []) for v in b.values() if v]
        if set(codes) != set(o['barcodes']):
            problems.append(o['number'] + ': штрихкоды изменились')
        if ms.states.get(fresh.get('state', {}).get('meta', {}).get('href')) != READY:
            problems.append(o['number'] + ': статус изменился')
    sample = orders[0]
    original = fetch_label(sample['label_url'])
    qr, mm = decode_sticker(original)
    checks = {'at': time.time(), 'orders': len(orders), 'source_units': units,
              'snapshot_units': sum(o['quantity'] for o in orders), 'mismatches': problems,
              'label_order': sample['number'], 'label_size_mm': mm,
              'qr_matches_ms_sticker': ms.verify_label(sample['id'], qr)}
    # Локальная симуляция не меняет состояние показанного пользователю демо.
    with tempfile.TemporaryDirectory() as td:
        flow = Workflow(Path(td) / 'proof.sqlite', ms)
        with flow.db() as db:
            for o in orders:
                db.execute('INSERT INTO snapshot VALUES(?,?)', (o['id'], json.dumps(o)))
        actor = {'employee': 'Живая проверка Ио', 'station': 'Проверка 1'}
        wave = flow.wave(actor, [sample['id']], 'DEMO-CART')
        flow.action(actor, sample['id'], 'pick', sample['barcodes'][0])
        flow.action(actor, sample['id'], 'print', sample['barcodes'][0])
        first = flow.label(actor, sample['id'])
        flow.action(actor, sample['id'], 'print', sample['barcodes'][0])
        repeat = flow.label(actor, sample['id'])
        checks['original_equals_demo_pdf'] = first == original
        checks['reprint_same_sha256'] = hashlib.sha256(first).digest() == hashlib.sha256(repeat).digest()
        flow.action(actor, sample['id'], 'sticker', qr)
        try:
            flow.action(actor, sample['id'], 'box', 'FOREIGN-BOX')
            checks['foreign_box_rejected'] = False
        except ReadError:
            checks['foreign_box_rejected'] = True
        flow.action(actor, sample['id'], 'box', wave['box'])
        checks['path_completed'] = flow.view()['orders'][0]['state'] == 'packed'
    checks['passed'] = not problems and units == checks['snapshot_units'] and all(checks[k] for k in (
        'qr_matches_ms_sticker', 'original_equals_demo_pdf', 'reprint_same_sha256', 'foreign_box_rejected', 'path_completed'))
    (root / 'data/verification.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return checks['passed']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--vault-access', action='store_true')
    args = parser.parse_args()
    if args.vault_access:
        load_vault_access()
    os.environ.setdefault('TOPSELLER_LABEL_HOSTS', 'print.topseller.ru')
    raise SystemExit(0 if verify() else 1)
