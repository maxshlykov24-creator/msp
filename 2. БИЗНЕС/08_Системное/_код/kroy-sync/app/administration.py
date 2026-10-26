"""Catalog, staff and versioned settings. Called inside the shared transaction."""
import copy
import re
from datetime import date
from sqlalchemy import select
from .db import Item, User, Entity
from .service import require, quantity, entity, save, get_item, PERMISSIONS


def administer(s, doc, command, p, actor_id):
    if command == 'item.update':
        item = get_item(s, p['id'])
        version = item.source.get('version', 1)
        require(p['version'] == version, 'Карточка изменилась. Обнови экран')
        require(p.get('name') and p.get('model'), 'Нужны название и модель', 422)
        codes = p.get('barcodes', [])
        require(isinstance(codes, list) and all(isinstance(c, str) and c.strip() for c in codes), 'Штрихкоды должны быть строками', 422)
        require(len(codes) == len(set(codes)), 'Штрихкод повторяется')
        for other in s.scalars(select(Item).where(Item.id != item.id)):
            require(not set(codes).intersection(other.barcodes), 'Штрихкод уже используется')
        photo = p.get('photo', item.photo)
        require(not photo or photo.startswith('https://'), 'Фото: HTTPS-ссылка', 422)
        previous = {k: getattr(item, k) for k in ('name', 'model', 'color', 'size', 'category', 'barcodes', 'photo')}
        s.add(Entity(id='item-revision:' + doc, kind='item_revision', version=version, data={'item_id': item.id, 'previous': previous}))
        for key in previous:
            if key in p:
                setattr(item, key, p[key])
        item.source = {**item.source, 'version': version + 1, 'last_document': doc}
        return {'id': item.id, 'version': version + 1}
    if command == 'user.create':
        require(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', p['id']) is not None, 'Логин: латиница, цифры, дефис', 422)
        require(s.get(User, p['id']) is None, 'Логин уже занят')
        require(p['role'] in PERMISSIONS and p.get('name'), 'Нужны имя и допустимая роль', 422)
        require(re.fullmatch(r'[0-9a-f]{64}', p['token_hash']) is not None, 'Некорректный хеш ключа', 422)
        require(s.scalar(select(User).where(User.token_hash == p['token_hash'])) is None, 'Ключ уже используется')
        s.add(User(id=p['id'], name=p['name'], role=p['role'], token_hash=p['token_hash'], active=True))
        s.add(Entity(id='user-version:' + p['id'], kind='user_version', version=1, data={}))
        return {'id': p['id'], 'version': 1}
    if command == 'user.update':
        user = s.get(User, p['id'])
        require(user is not None, 'Сотрудник не найден', 404)
        require(p['role'] in PERMISSIONS and type(p['active']) is bool, 'Некорректная роль или доступ', 422)
        e = s.get(Entity, 'user-version:' + user.id)
        if e is None:
            e = Entity(id='user-version:' + user.id, kind='user_version', version=1, data={})
            s.add(e)
        require(p['version'] == e.version, 'Права уже изменились. Обнови экран')
        require(not (user.id == actor_id and (not p['active'] or p['role'] != 'owner')), 'Нельзя отозвать собственный доступ владельца')
        if user.role == 'owner' and user.active and (p['role'] != 'owner' or not p['active']):
            owners = list(s.scalars(select(User).where(User.role == 'owner', User.active.is_(True))))
            require(len(owners) > 1, 'Нельзя убрать последнего владельца')
        user.role, user.active = p['role'], p['active']
        user.name = p.get('name', user.name)
        save(e, {'last_document': doc})
        return {'id': user.id, 'version': e.version}
    if command == 'norm.set':
        item = get_item(s, p['item_id'])
        require(item.kind == 'garment', 'Норма задаётся для изделия')
        date.fromisoformat(p['effective_date'])
        q = quantity(p['yield'])
        require(q == q.to_integral_value(), 'Выход должен быть целым')
        require(type(p['confirmed']) is bool, 'Укажи подтверждение применимости', 422)
        key = 'norm:' + doc
        s.add(Entity(id=key, kind='norm', version=1, data={**p, 'yield': str(q), 'model': item.model, 'document_id': doc}))
        return {'id': key, 'version': 1}
    if command == 'telegram.link':
        require(type(p['active']) is bool, 'Нужен явный статус доступа', 422)
        require(s.get(User, p['user_id']) is not None, 'Сотрудник не найден')
        require(isinstance(p['telegram_id'], str) and p['telegram_id'].isdigit(), 'Нужен числовой Telegram ID', 422)
        key = 'telegram:' + p['telegram_id']
        e = s.get(Entity, key)
        if e:
            require(e.version == p['version'], 'Привязка уже изменилась')
            save(e, {'user_id': p['user_id'], 'active': bool(p['active'])})
        else:
            require(p.get('version', 0) == 0, 'Привязка не найдена')
            e = Entity(id=key, kind='telegram_link', version=1, data={'user_id': p['user_id'], 'active': bool(p['active'])})
            s.add(e)
        return {'id': key, 'version': e.version}
    raise ValueError('Unknown administration command')
