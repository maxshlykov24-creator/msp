#!/usr/bin/env python3
"""Подготовка справочников и импорт исходных норм. Складские документы не создаёт."""
import argparse
import csv
import re
from decimal import Decimal
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
    dictionaries = ms.get('/context/companysettings/metadata').get('customEntities', [])
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
        href = attr.get('customEntityMeta', {}).get('href', '').rstrip('/')
        if href.endswith('/metadata'):
            href = href[:-len('/metadata')]
        if attr.get('type') != 'customentity' or href.rsplit('/', 1)[-1] != did:
            raise Review('Существующее поле причины использует другой тип/справочник')
    else:
        attr = ms.request('POST', '/entity/loss/metadata/attributes', {
            'name': 'Причина списания', 'type': 'customentity',
            'customEntityMeta': {'href': f'{ms_base()}/entity/customentity/{did}/metadata',
                                 'type': 'customentitymetadata', 'mediaType': 'application/json'}})
    config.update(reason_dictionary=did, reason_attribute=attr['id'],
                  reason_production=values[REASONS[0]]['id'])
    operation_labels = ['Материалы на производство', 'Расход материалов',
                        'Изделия на доработку', 'Возврат с доработки']
    target = [d for d in dictionaries if d.get('name') == 'Операции производства 2MY']
    operation_dict = target[0] if target else ms.request('POST', '/entity/customentity', {'name': 'Операции производства 2MY'})
    oid = operation_dict.get('id') or ident(operation_dict)
    operations = {x['name']: x for x in ms.rows('/entity/customentity/' + oid)}
    for label in operation_labels:
        if label not in operations:
            operations[label] = ms.request('POST', '/entity/customentity/' + oid, {'name': label})
    config.update(operation_dictionary=oid, operation_values={k: v['id'] for k, v in operations.items()})
    config['operation_attributes'] = {}
    for kind in ('move', 'loss'):
        attrs = [a for a in ms.rows('/entity/' + kind + '/metadata/attributes') if a['name'] == 'Операция производства']
        if len(attrs) > 1:
            raise Review('Несколько полей операции производства')
        attr = attrs[0] if attrs else ms.request('POST', '/entity/' + kind + '/metadata/attributes', {
            'name': 'Операция производства', 'type': 'customentity',
            'customEntityMeta': {'href': f'{ms_base()}/entity/customentity/{oid}/metadata',
                                 'type': 'customentitymetadata', 'mediaType': 'application/json'}})
        config['operation_attributes'][kind] = attr['id']
    # Не включать автоматически: нормы и сценарии ещё требуют проверки владельцем.
    config['enabled'] = False
    engine.set_config(config)
    return {'states': sum(len(x) for x in config['states'].values()), 'reasons': len(values), 'enabled': False}



MATERIAL_GROUP = 'Материалы производства'
MATERIAL_CATEGORIES = {'ткань': 'Ткани', 'пряжа': 'Пряжа', 'пуговица': 'Пуговицы',
    'пуговицы': 'Пуговицы', 'плечевые накладки': 'Плечевые накладки',
    'клеевые материалы': 'Клеевые материалы', 'тесьма': 'Тесьма', 'молнии': 'Молнии',
    'фурнитура': 'Фурнитура', 'резинка': 'Резинки', 'кружево': 'Кружево', 'кнопки': 'Кнопки'}
MATERIAL_UNITS = {'метр': 'м', 'грамм': 'г', 'штука': 'шт', 'шт': 'шт', 'пара': 'пара', 'рулон': 'рулон'}


def material_text(value):
    return re.sub(r'\s+', ' ', str(value or '').strip())


def material_key(value):
    return material_text(value).casefold().replace('ё', 'е')


def read_material_csv(source):
    """Keep empty quantities/prices unknown and join locations of one material."""
    out = {}
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'артикул производителя', 'цвет', 'состав', 'единица измерения',
                    'Контрагент', 'категория', 'склад', 'цена,руб', 'остаток на 31.10.2026'}
        if not required.issubset(reader.fieldnames or []):
            raise Review('В CSV отсутствуют обязательные столбцы материалов')
        for line, row in enumerate(reader, 2):
            name, color = material_text(row['артикул производителя']), material_text(row['цвет'])
            if not name or len(name) > 255:
                raise Review('Проверь название материала в строке ' + str(line))
            unit = MATERIAL_UNITS.get(material_key(row['единица измерения']))
            category = MATERIAL_CATEGORIES.get(material_key(row['категория']))
            if not unit or not category:
                raise Review('Неизвестная единица/категория в строке ' + str(line))
            def number(value):
                value = material_text(value).replace(' ', '').replace(',', '.')
                if not value:
                    return None
                result = Decimal(value)
                if not result.is_finite() or result < 0:
                    raise Review('Некорректное число в строке ' + str(line))
                return str(result)
            key = '|'.join(map(material_key, (name, color, unit)))
            entry = out.setdefault(key, {'source_name': name, 'color': color[:1].upper() + color[1:].lower(),
                'composition': material_text(row['состав']), 'unit': unit, 'category': category,
                'supplier': material_text(row['Контрагент']), 'locations': [], 'rows': [],
                'externalCode': '2my-material-' + hashlib.sha256(key.encode()).hexdigest()[:32]})
            for field, current in [('composition', material_text(row['состав'])),
                                   ('supplier', material_text(row['Контрагент']))]:
                if entry[field] != current:
                    raise Review('Разные сведения одного материала: строки ' + str(entry['rows'] + [line]))
            entry['rows'].append(line)
            entry['locations'].append({'source_row': line, 'store': material_text(row['склад']),
                'quantity': number(row['остаток на 31.10.2026']), 'price': number(row['цена,руб'])})
    return list(out.values())


def material_candidates(item, existing):
    """Exact name or manufacturer SKU plus matching color; no fuzzy auto-merges."""
    name, color = material_key(item['source_name']), material_key(item['color'])
    codes = set(re.findall(r'\b[a-z]{1,5}\d{3,}\b', name))
    matches = []
    for product in existing:
        current = material_key(product['name'])
        same = current == name or (codes and codes.intersection(re.findall(r'\b[a-z]{1,5}\d{3,}\b', current))
                                  and color and color in current)
        if same and product.get('meta', {}).get('type') == 'product':
            matches.append(product)
    return matches


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
