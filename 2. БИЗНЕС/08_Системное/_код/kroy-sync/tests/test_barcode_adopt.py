import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.catalog_cards import ozon_variants, remember
from app.db import Entity, Item, now, transaction
from app.product_identity import adopt_barcodes_from_linked_sizes, create_approved_geisha, planned_barcode_adoptions
from app.sales import assemble_ozon_window
from app.service import DomainError
from test_app import setup


def wb(item='s', codes=('001', '555')):
    return Entity(id='mapping:wb:' + item, kind='mapping', version=1, data=dict(
        item_id=item, platform='wb', state='unique', local_conflicts=[], approved=False,
        candidates=[dict(product_id='1', variant_id='10', barcodes=['001'], market_barcodes=list(codes))]))


def card(pid, offer, barcode, sku=900):
    return dict(id=pid, offer_id=offer, name=offer, barcodes=[barcode], sources=[{'sku': sku}], is_archived=False)


def remember_ozon(engine, products):
    with transaction(engine) as s:
        remember(s, 'ozon', now().isoformat(), ozon_variants(products))


def test_barcode_on_the_linked_size_is_stored_and_ozon_is_linked(setup):
    engine, _, _ = setup
    remember_ozon(engine, [card(90, 'ФТБ-СЕРД-ЧЕРН-S', '555')])
    with transaction(engine) as s:
        s.add(wb())
        assert [row['item_id'] for row in planned_barcode_adoptions(s)] == ['s']
        applied = adopt_barcodes_from_linked_sizes(s)
        assert applied[0]['offer_id'] == 'ФТБ-СЕРД-ЧЕРН-S' and applied[0]['needs_link']
    with Session(engine) as s:
        item = s.get(Item, 's')
        assert item.barcodes == ['001', '555']
        assert s.get(Entity, 'mapping:ozon:s').data['state'] == 'unique'
    with transaction(engine) as s:
        assert adopt_barcodes_from_linked_sizes(s) == []
    with Session(engine) as s:
        assert s.get(Item, 's').barcodes == ['001', '555']


def test_already_stored_barcode_is_not_linked_again(setup):
    engine, _, _ = setup
    remember_ozon(engine, [card(90, 'ФТБ-СКМС-ЧЕРН-S', '001')])
    with transaction(engine) as s:
        s.add(wb(codes=('001',)))
        assert planned_barcode_adoptions(s) == []


def test_second_barcode_is_stored_without_a_new_link(setup):
    engine, _, _ = setup
    remember_ozon(engine, [dict(id=90, offer_id='ФТБ-СКМС-ЧЕРН-S', name='x', barcodes=['001', '555'], sources=[{'sku': 900}], is_archived=False)])
    with transaction(engine) as s:
        s.add(wb(codes=('001', '555')))
        s.add(Entity(id='mapping:ozon:s', kind='mapping', version=3, data=dict(
            item_id='s', platform='ozon', state='unique', candidates=[dict(product_id='90', skus=['900'], barcodes=['001'])],
            manual_card=dict(product_id='90', skus=['900'], barcodes=['001']))))
        applied = adopt_barcodes_from_linked_sizes(s)
        assert applied[0]['barcodes'] == ['555'] and applied[0]['needs_link'] is False
    with Session(engine) as s:
        assert s.get(Item, 's').barcodes == ['001', '555']
        assert s.get(Entity, 'mapping:ozon:s').version == 3


def test_barcode_owned_by_another_garment_is_not_taken(setup):
    engine, _, _ = setup
    remember_ozon(engine, [card(90, 'ФТБ-СЕРД-ЧЕРН-S', '002')])
    with transaction(engine) as s:
        s.add(wb(codes=('001', '002')))
        assert planned_barcode_adoptions(s) == []
        assert adopt_barcodes_from_linked_sizes(s) == []
    with Session(engine) as s:
        assert s.get(Item, 's').barcodes == ['001']
        assert s.get(Entity, 'mapping:ozon:s') is None


def test_two_cards_on_one_size_are_left_alone(setup):
    engine, _, _ = setup
    remember_ozon(engine, [card(90, 'A', '555', 900), card(91, 'B', '556', 901)])
    with transaction(engine) as s:
        s.add(wb(codes=('001', '555', '556')))
        assert planned_barcode_adoptions(s) == []


def test_card_without_a_linked_size_is_not_created(setup):
    engine, _, _ = setup
    remember_ozon(engine, [card(90, 'ФТБ-ГША-ЧЕРН-S', '777')])
    with transaction(engine) as s:
        s.add(wb())
        assert planned_barcode_adoptions(s) == []


def test_sale_of_the_added_barcode_lands_on_the_same_garment(setup):
    engine, _, _ = setup
    with transaction(engine) as s:
        s.add(wb())
    catalog = dict(at=now().isoformat(), complete=True, catalog_schema=2, items=[card(90, 'ФТБ-СЕРД-ЧЕРН-S', '555')])
    raw = [dict(accrual_id='a1', date='2026-11-01', unit_number='U1', posting={'products': [dict(
        sku=900, quantity=2, commission=dict(seller_price=dict(amount='10'), sale_amount=dict(amount='10')), delivery={})]})]
    data, adopted = assemble_ozon_window(engine, catalog, raw, ['hash'], '2026-10-26', '2026-11-24', {'verified': True})
    assert len(adopted) == 1 and data['unmatched'] == 0
    assert sum(event['quantity'] for event in data['events']) == 2
    assert data['events'][0]['item_id'] == 's'
    data, adopted = assemble_ozon_window(engine, catalog, raw, ['hash'], '2026-10-26', '2026-11-24', {'verified': True})
    assert adopted == [] and data['unmatched'] == 0


def test_only_the_four_geisha_offers_are_created(setup):
    engine, user, _ = setup
    products = [
        card(1, 'ФТБ-ГША-ЧЕРН-S', '701', 1), card(2, 'ФТБ-ГША-ЧЕРН-M', '702', 2),
        card(3, 'ФТБ-ГША-Т.КОРИЧ-S', '703', 3), card(4, 'ФТБ-ГША-Т.КОРИЧ-M', '704', 4),
        card(5, 'ФТБ-СЕРД-ЧЕРН-S', '705', 5),
    ]
    remember_ozon(engine, products)
    created = create_approved_geisha(engine, user, products)
    assert len(created) == 4
    assert create_approved_geisha(engine, user, products) == []
    with Session(engine) as s:
        rows = list(s.scalars(select(Item).where(Item.model == 'Футболка Гейша')))
        assert {(row.color, row.size) for row in rows} == {('Чёрный', 'S'), ('Чёрный', 'M'), ('Тёмно-коричневый', 'S'), ('Тёмно-коричневый', 'M')}
        assert all(row.category == 'YUJI' and len(row.barcodes) == 1 for row in rows)
        assert all(s.get(Entity, 'mapping:ozon:' + row.id).data['state'] == 'unique' for row in rows)
    broken = [card(1, 'ФТБ-ГША-ЧЕРН-S', '801', 11), dict(id=9, offer_id='ФТБ-ГША-ЧЕРН-M', barcodes=['802', '803'], sources=[{'sku': 12}])]
    remember_ozon(engine, broken)
    with pytest.raises(DomainError, match='не однозначна'):
        create_approved_geisha(engine, user, broken)
    with Session(engine) as s:
        black_m = s.scalar(select(Item).where(Item.model == 'Футболка Гейша', Item.color == 'Чёрный', Item.size == 'M'))
        assert black_m.barcodes == ['702']
