#!/usr/bin/env python3
"""Подготовка справочников и импорт исходных норм. Складские документы не создаёт."""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

from production import Engine, REASONS, STATES, Review, ident


def setup_ms(engine):
    ms = engine.ms
    config = engine.config()
    config.setdefault('states', {})
    for kind, labels in STATES.items():
        existing = {x['name']: x for x in ms.get('/entity/' + kind + '/metadata').get('states', [])}
        config['states'].setdefault(kind, {})
        for label in labels:
            if label not in existing:
                existing[label] = ms.request('POST', '/entity/' + kind + '/metadata/states',
                                            {'name': label, 'color': 10066329, 'stateType': 'Regular'})
            config['states'][kind][label] = existing[label]['id']
    dictionaries = ms.get('/entity/companysettings/metadata').get('customEntities', [])
    target = [d for d in dictionaries if d.get('name') == 'Причины списания 2MY']
    if len(target) > 1:
        raise Review('Несколько справочников причин списания')
    dictionary = target[0] if target else ms.request('POST', '/entity/customentity', {'name': 'Причины списания 2MY'})
    did = dictionary.get('id') or ident(dictionary)
    values = {x['name']: x for x in ms.rows('/entity/customentity/' + did)}
    for label in REASONS:
        if label not in values:
            values[label] = ms.request('POST', '/entity/customentity/' + did, {'name': label})
    attrs = ms.rows('/entity/loss/metadata/attributes')
    attrs = [a for a in attrs if a['name'] == 'Причина списания']
    if len(attrs) > 1:
        raise Review('Несколько полей причины списания')
    if attrs:
        attr = attrs[0]
        if attr.get('type') != 'customentity' or attr.get('customEntityMeta', {}).get('href', '').split('/')[-2:-1] != [did]:
            raise Review('Существующее поле причины использует другой тип/справочник')
    else:
        attr = ms.request('POST', '/entity/loss/metadata/attributes', {
            'name': 'Причина списания', 'type': 'customentity',
            'customEntityMeta': {'href': f'{ms_base()}/entity/customentity/{did}/metadata',
                                 'type': 'customentitymetadata', 'mediaType': 'application/json'}})
    config.update(reason_dictionary=did, reason_attribute=attr['id'],
                  reason_production=values[REASONS[0]]['id'])
    # Не включать автоматически: нормы и сценарии ещё требуют проверки владельцем.
    config['enabled'] = False
    engine.set_config(config)
    return {'states': sum(len(x) for x in config['states'].values()), 'reasons': len(values), 'enabled': False}


def ms_base():
    from production import BASE
    return BASE


def import_xlsx(engine, source):
    import openpyxl
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    config = engine.config()
    factories = {x['name']: x for x in config.get('factories', [])}
    recipes = {r['id'] for r in engine.recipes()}
    count = 0
    rows = list(book['Нормы'].values)
    for n, row in enumerate(rows[2:], 3):
        if not row[0]:
            continue
        model, color, unit, work = row[:4]
        name = str(row[10]).strip()
        if name not in factories:
            factories[name] = {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, '2my-factory:' + name)),
                               'name': name, 'agent': '', 'store': ''}
        for column in range(4, 10):
            norm = row[column]
            if norm is None:
                continue
            size = rows[1][column]
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'2my-norm:{model}:{color}:{size}'))
            if key in recipes:
                continue
            engine.save_recipe({'id': key, 'name': f'{str(model).strip()} · {str(color).strip()} · {size}',
                'model': str(model).strip(), 'color': str(color).strip(), 'size': size,
                'approved': False, 'zip_checked': False,
                'source_note': f'Лист Нормы, строка {n}; исходная норма {norm} {unit}',
                'source_sha256': source_hash,
                'stages': [{'factory': factories[name]['id'], 'work_cost': work,
                            'materials': [{'quantity': norm, 'source_unit': unit, 'unit': unit,
                                           'name': 'Основной материал — выбрать карточку'}]}]})
            count += 1
    config['factories'] = list(factories.values())
    # Незавершённые сопоставления разрешены только в выключенной конфигурации.
    config['enabled'] = False
    engine.set_config(config)
    book.close()
    return {'imported': count, 'factories': len(factories), 'source_sha256': source_hash}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data')
    parser.add_argument('--setup-ms', action='store_true')
    parser.add_argument('--import-xlsx')
    args = parser.parse_args()
    service = Engine(args.data)
    if args.setup_ms:
        print(json.dumps(setup_ms(service), ensure_ascii=False))
    if args.import_xlsx:
        print(json.dumps(import_xlsx(service, args.import_xlsx), ensure_ascii=False))
