"""Изолированные проверки опасных переходов; внешних записей и печати нет."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ms_read import READY, ReadError, MoySklad, fetch_label
from workflow import Workflow


class FakeMS:
    def __init__(self):
        self.statuses = {str(i): READY for i in range(1, 5)}

    def status(self, oid):
        return self.statuses[oid]

    def verify_label(self, oid, qr):
        return self.status(oid) == READY and qr == 'actual-qr'

    def collect(self):
        return {"orders": [{"id": str(i), "number": str(i), "store": "Склад ФБС", "product_id": "p",
                "product": "Тест", "quantity": 1, "article": "a", "barcodes": ["123"],
                "label_url": "https://print.topseller.ru/test", "cell_verified": False,
                "cell": "", "status": READY} for i in range(1, 5)], "scanned_orders": 4}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ms = FakeMS()
        self.f = Workflow(Path(self.temp.name) / 'test.db', self.ms)
        self.a = {"employee": "Сборщик", "station": "Станция 1"}
        self.b = {"employee": "Сборщик", "station": "Станция 2"}
        self.f.refresh(self.a)
        self.wave = self.f.wave(self.a, ['1', '2', '3'], 'CART')
        self.f.action(self.a, '1', 'pick', '123')
        self.p1 = patch('workflow.fetch_label', return_value=b'%PDF-original')
        self.p2 = patch('workflow.decode_sticker', return_value=('actual-qr', [58, 40]))
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.temp.cleanup()

    def test_same_label_reprint_and_box(self):
        self.f.action(self.a, '1', 'print', '123')
        first = self.f.label(self.a, '1')
        self.f.action(self.a, '1', 'print', '123')
        self.assertEqual(first, self.f.label(self.a, '1'))
        with self.assertRaises(ReadError): self.f.action(self.a, '1', 'sticker', '1')
        self.f.action(self.a, '1', 'sticker', 'actual-qr')
        with self.assertRaises(ReadError): self.f.action(self.a, '1', 'box', 'FOREIGN')
        self.f.action(self.a, '1', 'box', self.wave['box'])
        with self.assertRaises(ReadError): self.f.action(self.a, '1', 'print', '123')

    def test_two_stations_cannot_claim_one_job(self):
        def claim(a):
            try:
                self.f.action(a, '1', 'print', '123')
                return True
            except ReadError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            result = list(pool.map(claim, [self.a, self.b]))
        self.assertEqual(sum(result), 1)

    def test_station_cannot_take_next_while_pending(self):
        self.f.action(self.a, '1', 'print', '123')
        self.f.action(self.a, '2', 'pick', '123')
        with self.assertRaises(ReadError): self.f.action(self.a, '2', 'print', '123')
        self.f.action(self.a, '1', 'transfer', target='Станция 2')
        with self.assertRaises(ReadError): self.f.label(self.a, '1')
        self.assertTrue(self.f.label(self.b, '1'))

    def test_cancel_after_print_goes_to_return(self):
        self.f.action(self.a, '1', 'print', '123')
        self.ms.statuses['1'] = 'Отменен'
        result = self.f.action(self.a, '1', 'sticker', 'actual-qr')
        self.assertTrue(result['blocked'])
        with self.assertRaises(ReadError): self.f.action(self.a, '1', 'box', self.wave['box'])
        self.f.action(self.a, '1', 'return', '123', 'RETURN-CELL')
        self.assertEqual(next(o['state'] for o in self.f.view()['orders'] if o['id']=='1'), 'returned')

    def test_shortage_does_not_block_other_orders(self):
        self.f.action(self.a, '2', 'shortage', target='Старший склада')
        self.f.action(self.a, '3', 'pick', '123')
        with self.assertRaises(ReadError): self.f.action(self.a, '2', 'pick', '123')
        with self.assertRaises(ReadError): self.f.action(self.a, '3', 'pick', 'WRONG')

    def test_fetch_error_releases_station(self):
        with patch('workflow.fetch_label', side_effect=ReadError('Нет связи')):
            with self.assertRaises(ReadError): self.f.action(self.a, '1', 'print', '123')
        self.f.action(self.b, '1', 'print', '123')

    def test_other_picker_and_duplicate_wave_rejected(self):
        with self.assertRaises(ReadError): self.f.wave(self.a, ['1'], 'CART2')
        with self.assertRaises(ReadError): self.f.action({'employee':'Другой','station':'3'}, '2', 'pick', '123')

    def test_read_client_blocks_foreign_host(self):
        with self.assertRaises(ReadError): MoySklad().get('https://example.com/entity/customerorder')
        with self.assertRaises(ReadError): fetch_label('http://127.0.0.1/secret')

    def test_auto_station_scan_reuses_pending_job(self):
        result = self.f.station_scan(self.a, self.wave['wave'], '123')
        self.assertEqual(result['job'], '1')
        self.assertEqual(self.f.station_scan(self.a, self.wave['wave'], '123')['job'], '1')
        with self.assertRaises(ReadError): self.f.station_scan(self.a, self.wave['wave'], 'WRONG')

    def test_pdf_qr_must_match_current_ms_order(self):
        with patch.object(self.ms, 'verify_label', return_value=False):
            with self.assertRaises(ReadError): self.f.action(self.a, '1', 'print', '123')
        self.assertEqual(next(o['state'] for o in self.f.view()['orders'] if o['id']=='1'), 'picked')

    def test_pick_scan_without_choosing_job_and_replay(self):
        picker = {'employee': 'Сборщик', 'station': 'Отбор', 'role': 'picker'}
        first = self.f.pick_scan(picker, self.wave['wave'], '123', request_id='same')
        again = self.f.pick_scan(picker, self.wave['wave'], '123', request_id='same')
        self.assertEqual(first['job'], again['job'])
        picked = [o['id'] for o in self.f.view()['orders'] if o['state'] == 'picked']
        self.assertEqual(picked.count(first['job']), 1)
        second = self.f.pick_scan(picker, self.wave['wave'], '123', request_id='next')
        self.assertNotEqual(second['job'], first['job'])

    def test_shift_assigns_picker_and_can_transfer(self):
        shift = {'employee': 'Старший', 'station': 'Смена', 'role': 'shift'}
        picker = {'employee': 'Ира', 'station': 'Отбор', 'role': 'picker'}
        wave = self.f.wave(shift, ['4'], 'CART-4', picker='Ира')
        row = next(w for w in self.f.view()['waves'] if w['id'] == wave['wave'])
        self.assertEqual(row['employee'], 'Ира')
        self.assertEqual(row['created_by'], 'Старший')
        self.assertEqual(row['num'], 2)
        with self.assertRaises(ReadError):
            self.f.pick_scan(shift, wave['wave'], '123')
        self.f.pick_scan(picker, wave['wave'], '123', request_id='ira')
        station = {'employee': 'Упаковка', 'station': 'Станция 9', 'role': 'station'}
        self.f.accept_cart(station, 'CART-4')
        self.f.station_scan(station, wave['wave'], '123', request_id='st')
        self.f.action(shift, '4', 'transfer', target='Станция 2')
        self.assertEqual(next(o['station'] for o in self.f.view()['orders'] if o['id'] == '4'), 'Станция 2')
        with self.assertRaises(ReadError):
            self.f.action(picker, '4', 'box', wave['box'])

    def test_shortage_without_name_and_both_resolutions(self):
        picker = {'employee': 'Сборщик', 'station': 'Отбор', 'role': 'picker'}
        shift = {'employee': 'Старший', 'station': 'Смена', 'role': 'shift'}
        self.f.action(picker, '2', 'shortage')
        note = next(o['note'] for o in self.f.view()['orders'] if o['id'] == '2')
        self.assertIn('Смена', note)
        self.assertEqual(next(o['state'] for o in self.f.view()['orders'] if o['id'] == '3'), 'picking')
        self.f.resolve(shift, '2', 'found')
        self.assertEqual(next(o['state'] for o in self.f.view()['orders'] if o['id'] == '2'), 'picking')
        self.f.action(picker, '2', 'shortage')
        self.f.resolve(shift, '2', 'missing', 'Нет на полке')
        self.assertEqual(next(o['state'] for o in self.f.view()['orders'] if o['id'] == '2'), 'excluded')
        self.ms.statuses['3'] = 'Отменен'
        self.f.action(picker, '3', 'shortage')
        blocked = self.f.resolve(shift, '3', 'found')
        self.assertTrue(blocked['blocked'])

    def test_migration_keeps_old_numbers_and_links(self):
        path = Path(self.temp.name) / 'old.db'
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE waves (id TEXT PRIMARY KEY, employee TEXT, cart TEXT, store TEXT, box TEXT, created REAL)')
        con.execute("INSERT INTO waves VALUES ('old','Сборщик','CART','Склад ФБС','DEMO-BOX-old', 5)")
        con.commit()
        con.close()
        flow = Workflow(path, self.ms)
        self.assertEqual(flow.view()['waves'][0]['num'], 1)
        self.assertTrue(path.with_name('old.db.before-wave-num').exists())
        again = Workflow(path, self.ms)
        self.assertEqual(again.view()['waves'][0]['num'], 1)
        again.refresh(self.a)
        created = again.wave(self.a, ['1'], 'NEW')
        self.assertEqual(created['num'], 2)
        self.assertEqual(again.view()['waves'][1]['id'] if again.view()['waves'][1]['id'] == 'old' else again.view()['waves'][0]['id'], 'old')
        nums = sorted(w['num'] for w in again.view()['waves'])
        self.assertEqual(nums, [1, 2])

    def test_parallel_pick_cannot_take_same_unit_twice(self):
        picker = {'employee': 'Сборщик', 'station': 'Отбор', 'role': 'picker'}

        def once(i):
            try:
                self.f.pick_scan(picker, self.wave['wave'], '123', request_id=f'p{i}')
                return True
            except ReadError:
                return False

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            result = list(pool.map(once, [1, 2]))
        self.assertEqual(sum(result), 2)
        picked = [o for o in self.f.view()['orders'] if o['state'] == 'picked']
        self.assertEqual(len(picked), 3)

    def test_ekaterinburg_is_not_added_to_lobnya_wave(self):
        self.ms.collect = lambda: {"orders": [{"id": "9", "number": "9", "store": "Склад Екатеринбург", "product_id": "p",
            "product": "Там", "quantity": 1, "article": "e", "barcodes": ["999"], "label_url": "https://print.topseller.ru/test",
            "cell_verified": False, "cell": "", "status": READY}], "scanned_orders": 1, "ekb_skipped": 1}
        self.f.refresh(self.a)
        with self.assertRaises(ReadError):
            self.f.wave(self.a, ['9'], 'EKB')
        self.assertEqual(self.f.view()['ekb_skipped'], 1)

    def test_restart_restores_wave_without_new_one(self):
        again = Workflow(self.f.path, self.ms)
        waves = again.view()['waves']
        self.assertEqual([w['id'] for w in waves if w['id'] == self.wave['wave']], [self.wave['wave']])
        self.assertEqual(len(waves), 1)


if __name__ == '__main__':
    unittest.main()
