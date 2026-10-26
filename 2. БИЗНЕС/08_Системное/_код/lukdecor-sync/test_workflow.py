"""Изолированные проверки опасных переходов; внешних записей и печати нет."""
import concurrent.futures
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


if __name__ == '__main__':
    unittest.main()
