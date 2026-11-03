"""Async Ozon label API: no live API calls, isolated filesystem cache."""
import concurrent.futures
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import labels
from pypdf import PdfReader, PdfWriter

class Reply:
    def __init__(self, code=200, data=None, blob=b'', headers=None):
        self.status_code, self.data, self.content = code, data, blob
        self.text = json.dumps(data or {})
        self.headers = headers or {}
    def json(self): return self.data

def pdf(n=1):
    w=PdfWriter()
    for _ in range(n): w.add_blank_page(width=164, height=113)
    b=io.BytesIO();w.write(b);return b.getvalue()

def created(tasks=None):
    return Reply(data={'tasks': tasks or [{'task_id': 123, 'task_type':'small_label'}]})

def ready(url='https://ir.ozone.ru/label.pdf', n=1, printed=None):
    return Reply(data={'status':{'code':'completed','postings_count':n,'printed_postings_count':n if printed is None else printed,'unprinted_postings':[]},'file_url':url,'error':None})

class AsyncLabels(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'FF_DB':self.tmp.name+'/test.db'});self.env.start()
        self.cab={'client_id_ext':'seller1','token':'secret-one'}
        self.sleep=patch.object(labels.time,'sleep');self.sleep.start()
    def tearDown(self):
        self.sleep.stop();self.env.stop();self.tmp.cleanup()
    def run_pdf(self, postings=None):
        r=labels._ozon_label_response(self.cab,postings or ['A'])
        self.assertEqual(r.status_code,200)
        return r.content
    def test_generation_poll_download_and_reprint(self):
        pending=Reply(data={'status':{'code':'pending'}})
        with patch.object(labels,'req',side_effect=[created(),pending,ready(),Reply(blob=pdf())]) as call:
            self.assertEqual(len(PdfReader(io.BytesIO(self.run_pdf())).pages),1)
            self.run_pdf()
            self.assertEqual(call.call_count,4)
        calls=call.call_args_list
        self.assertEqual(calls[0].kwargs['json'],{'posting_numbers':['A']})
        self.assertTrue(calls[0].args[1].endswith('/v3/posting/fbs/package-label/create'))
        self.assertTrue(calls[1].args[1].endswith('/v2/posting/fbs/package-label/get'))
        self.assertNotIn('headers',calls[-1].kwargs)
        self.assertFalse(calls[-1].kwargs['allow_redirects'])
        for p in Path(self.tmp.name).rglob('*.pdf'):self.assertEqual(p.stat().st_mode & 0o777,0o600)
    def test_pending_task_resumed_without_create(self):
        with patch.object(labels,'req',side_effect=[created(),Reply(data={'status':{'code':'pending'}})]),patch.object(labels.time,'monotonic',side_effect=[0,100]):
            with self.assertRaisesRegex(labels.LabelError,'ещё формирует'):self.run_pdf()
        with patch.object(labels,'req',side_effect=[ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,2)
            self.assertTrue(call.call_args_list[0].args[1].endswith('/get'))
    def test_429_bounded_backoff(self):
        with patch.object(labels,'req',side_effect=[Reply(429,headers={'Retry-After':'0'}),created(),ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,4)
    def test_long_retry_after_not_hammered(self):
        with patch.object(labels,'req',return_value=Reply(429,headers={'Retry-After':'60'})) as call:
            r=labels._ozon_label_response(self.cab,['A']);self.assertEqual(r.status_code,429);self.assertEqual(call.call_count,1)
    def test_incomplete_task_is_not_downloaded(self):
        with patch.object(labels,'req',side_effect=[created(),ready(n=2,printed=1)]) as call:
            with self.assertRaisesRegex(labels.LabelError,'неполный'):self.run_pdf()
            self.assertEqual(call.call_count,2)
    def test_corrupt_pdf_not_cached(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=b'%PDF-broken')]):
            with self.assertRaises(labels.LabelError):self.run_pdf()
        self.assertFalse(list(Path(self.tmp.name).rglob('*.pdf')))
    def test_wrong_page_count_rejected(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf(3))]):
            with self.assertRaisesRegex(labels.LabelError,'3 страниц'):self.run_pdf()
    def test_failed_task_can_be_recreated(self):
        with patch.object(labels,'req',side_effect=[created(),Reply(data={'status':{'code':'failed'},'error':'failure'})]):
            with self.assertRaises(labels.LabelError):self.run_pdf()
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertTrue(call.call_args_list[0].args[1].endswith('/create'))
    def test_download_failure_resumes_existing_task(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(503)]):
            with self.assertRaisesRegex(labels.LabelError,'скачать PDF'):self.run_pdf()
        with patch.object(labels,'req',side_effect=[ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,2)
    def test_credentials_isolate_caches(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]*2) as call:
            self.run_pdf();self.cab['token']='new-token';self.run_pdf();self.assertEqual(call.call_count,6)
    def test_multiple_small_tasks_are_complete(self):
        tasks=[{'task_id':1,'task_type':'small_label'},{'task_id':2,'task_type':'small_label'}]
        with patch.object(labels,'req',side_effect=[created(tasks),ready(),Reply(blob=pdf()),ready(url='https://ir.ozone.ru/second.pdf'),Reply(blob=pdf())]):
            self.assertEqual(len(PdfReader(io.BytesIO(self.run_pdf(['A','B']))).pages),2)

    def test_old_format_big_label_is_supported(self):
        with patch.object(labels,'req',side_effect=[created([{'task_id':1,'task_type':'big_label'}]),ready(),Reply(blob=pdf())]):
            self.assertEqual(len(PdfReader(io.BytesIO(self.run_pdf())).pages),1)

    def test_mixed_new_and_old_formats_are_not_dropped(self):
        tasks=[{'task_id':1,'task_type':'small_label'},{'task_id':2,'task_type':'big_label'}]
        with patch.object(labels,'req',side_effect=[created(tasks),ready(),Reply(blob=pdf()),ready(),Reply(blob=pdf())]):
            self.assertEqual(len(PdfReader(io.BytesIO(self.run_pdf(['A','B']))).pages),2)

    def test_missing_task_can_be_recreated(self):
        with patch.object(labels,'req',side_effect=[created(),Reply(404)]):
            self.assertEqual(labels._ozon_label_response(self.cab,['A']).status_code,404)
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertTrue(call.call_args_list[0].args[1].endswith('/create'))
    def test_corrupt_cache_is_refetched(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]):self.run_pdf()
        next(Path(self.tmp.name).rglob('*.pdf')).write_bytes(b'corrupt')
        with patch.object(labels,'req',side_effect=[ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,2)
    def test_concurrent_reprint_uses_one_generation(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]) as call:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:self.run_pdf(),range(2)))
            self.assertEqual(results[0],results[1]);self.assertEqual(call.call_count,3)
    def test_unexpected_format_rejected(self):
        with patch.object(labels,'req',return_value=created([{'task_id':1,'task_type':'unknown'}])):
            with self.assertRaisesRegex(labels.LabelError,'формата'):self.run_pdf()
    def test_foreign_download_host_rejected(self):
        with patch.object(labels,'req',side_effect=[created(),ready(url='https://example.com/label.pdf')]) as call:
            with self.assertRaisesRegex(labels.LabelError,'ссылку'):self.run_pdf()
            self.assertEqual(call.call_count,2)
    def test_posting_sets_do_not_share_cache(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]*2) as call:
            self.run_pdf(['A']);self.run_pdf(['B']);self.assertEqual(call.call_count,6)
    def test_expired_cache_starts_new_task(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]):self.run_pdf()
        for p in Path(self.tmp.name).rglob('*.pdf'):os.utime(p,(0,0))
        for p in Path(self.tmp.name).rglob('*.json'):
            x=json.loads(p.read_text());x['created_at']=0;p.write_text(json.dumps(x))
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,3)

    def test_expired_pdf_keeps_task_for_reprint_after_handoff(self):
        with patch.object(labels,'req',side_effect=[created(),ready(),Reply(blob=pdf())]):self.run_pdf()
        for p in Path(self.tmp.name).rglob('*.pdf'):os.utime(p,(0,0))
        with patch.object(labels,'req',side_effect=[ready(),Reply(blob=pdf())]) as call:
            self.run_pdf();self.assertEqual(call.call_count,2)
            self.assertTrue(call.call_args_list[0].args[1].endswith('/get'))

    def test_product_and_posting_stay_paired_with_quantities(self):
        from reportlab.pdfgen import canvas
        rows=[dict(cabinet_id=1,cabinet=dict(self.cab,marketplace='ozon'),marketplace='ozon',
                   ext_id=ext,article='SKU-'+ext,name='Robot',barcode='OZN123456',qty=qty)
              for ext,qty in [('A',2),('B',1)]]
        def platform(cab, postings):
            b=io.BytesIO();c=canvas.Canvas(b,pagesize=(164,113))
            c.drawString(5,50,'PLATFORM_'+postings[0]);c.showPage();c.save()
            return Reply(blob=b.getvalue())
        with patch.object(labels,'_ozon_label_response',side_effect=platform):
            blob,notes,n=labels.build(rows,'both')
        pages=PdfReader(io.BytesIO(blob)).pages
        self.assertEqual(n,5);self.assertFalse(notes)
        self.assertIn('SKU-A',pages[0].extract_text());self.assertIn('SKU-A',pages[1].extract_text())
        self.assertIn('PLATFORM_A',pages[2].extract_text())
        self.assertIn('SKU-B',pages[3].extract_text());self.assertIn('PLATFORM_B',pages[4].extract_text())

    def test_product_only_never_requests_marketplace(self):
        row=dict(cabinet_id=1,cabinet=dict(self.cab,marketplace='ozon'),marketplace='ozon',
                 ext_id='A',article='SKU-A',name='Robot',barcode='OZN123456',qty=2)
        with patch.object(labels,'req',side_effect=AssertionError('Unexpected API request')):
            blob,notes,n=labels.build([row],'product')
        self.assertEqual(n,2);self.assertEqual(len(PdfReader(io.BytesIO(blob)).pages),2)

    def test_partial_set_never_downloaded_as_success(self):
        rows=[dict(cabinet_id=1,cabinet=dict(self.cab,marketplace='ozon'),marketplace='ozon',
                   ext_id=ext,article='SKU-'+ext,name='Robot',barcode='OZN123456',qty=1)
              for ext in ['A','B']]
        with patch.object(labels,'_ozon_label_response',side_effect=[Reply(blob=pdf()),Reply(429)]):
            with self.assertRaises(labels.LabelError):labels.build(rows,'both')

if __name__=='__main__':unittest.main()
