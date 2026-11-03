"""QR Ozon persists independently and resolves the correct order without changing marks."""
import json,os,tempfile,unittest
from unittest.mock import patch
os.environ['FF_DB']=os.path.join(tempfile.mkdtemp(),'scanit.sqlite')
import db,shipments_pull as pull,night_check,statuses
class Scanit(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  db.init_db();c=db.connect();c.execute("INSERT INTO clients(id,code,name) VALUES (1,'T','Test'),(2,'U','Other')");c.execute("INSERT INTO cabinets(id,client_id,marketplace,token,client_id_ext) VALUES (1,1,'ozon','test','1'),(2,2,'ozon','other','2')");c.commit();c.close()
 def setUp(self):
  c=db.connect();c.execute('delete from shipment_marks');c.execute('delete from shipments');c.commit();c.close()
 def row(self,**extra):
  values=dict(status_group='assembling',accepted_at='2026-11-03 12:00',track='TRACK',items_json=json.dumps([dict(product_id=1,article='ART',name='Name',qty=1)]));values.update(extra)
  return db.upsert_shipment(1,1,'ozon','fbs','POST-1',statuses.ru('awaiting_packaging'),'2026-11-03','ART','PRODUCT','Name',1,None,0,'now',extra=values)
 def test_search_both_sections(self):
  sid=self.row(scanit='ii123456');self.assertEqual([r['id'] for r in db.list_assembly(client_id=1,query='  II123456  ')],[sid]);self.assertEqual([r['id'] for r in db.list_shipments(client_id=1,query='ii123456')],[sid]);self.assertEqual(db.list_assembly(client_id=2,query='ii123456'),[])
 def test_missing_qr_preserves_existing(self):
  self.row(scanit='ii123');self.row();self.assertEqual(db.get_shipments_by_ext(1,'fbs',['POST-1'])[0]['scanit'],'ii123')
 def test_helper_changes_only_qr(self):
  sid=self.row();db.replace_shipment_marks(sid,[dict(code='mark-code',article='ART',gtin='1')]);c=db.connect();before=dict(c.execute('select * from shipments where id=?',(sid,)).fetchone());marks=[tuple(r) for r in c.execute('select * from shipment_marks')];c.close()
  self.assertEqual(db.set_shipment_scanit(sid,'ii123'),1)
  c=db.connect();after=dict(c.execute('select * from shipments where id=?',(sid,)).fetchone());self.assertEqual(marks,[tuple(r) for r in c.execute('select * from shipment_marks')]);c.close();after['scanit']=before['scanit'];self.assertEqual(before,after)
 def test_sync_stale_mark_count_does_not_hide_new_scan(self):
  sid=self.row(scanit='ii123');stale=db.get_shipments_by_ext(1,'fbs',['POST-1'])[0]
  db.replace_shipment_marks(sid,[dict(code='scanned-after-snapshot',article='ART',gtin='1')])
  pull.save_row(db.get_client_by_id(1),db.get_cabinet(1),'fbs','POST-1',stale['status'],stale['shipped_at'],'ART','PRODUCT','Name',1,None,known=stale,extra={'status_group':'assembling'})
  self.assertEqual(db.get_shipments_by_ext(1,'fbs',['POST-1'])[0]['marks_count'],1)
 def test_empty_qr_cannot_erase(self):
  sid=self.row(scanit='ii123');self.assertEqual(db.set_shipment_scanit(sid,''),0);self.assertEqual(db.get_shipments_by_ext(1,'fbs',['POST-1'])[0]['scanit'],'ii123')
 def test_bad_qr_rejected(self):
  sid=self.row()
  with self.assertRaises(ValueError):db.set_shipment_scanit(sid,'ii1\nBAD')
 def test_qr_delta_not_skipped(self):
  self.row();row=db.get_shipments_by_ext(1,'fbs',['POST-1'])[0];post=dict(posting_number='POST-1',status='awaiting_packaging',scanit='ii123',products=[dict(sku=1,offer_id='ART',name='Name',quantity=1)])
  self.assertFalse(pull.same_ozon(row,post));db.set_shipment_scanit(row['id'],'ii123');self.assertTrue(pull.same_ozon(db.get_shipments_by_ext(1,'fbs',['POST-1'])[0],post))
 def test_refresh_updates_qr_without_mark_loss(self):
  sid=self.row();db.replace_shipment_marks(sid,[dict(code='mark-code',article='ART',gtin='1')]);post=dict(posting_number='POST-1',status='delivering',scanit='ii456',tracking_number='TRACK',products=[dict(sku=1,offer_id='ART',name='Name',quantity=1)])
  cache={}
  with patch.object(pull,'pull_ozon_get',return_value=post):pull.handle_ozon(db.get_client_by_id(1),db.get_cabinet(1),'fbs',[post],{},status_cache=cache)
  self.assertEqual(cache['POST-1']['scanit'],'ii456');self.assertEqual(db.get_shipments_by_ext(1,'fbs',['POST-1'])[0]['barcode'],'')
  self.assertEqual(len(db.list_shipment_marks([sid])),1)
 def test_live_check_saves_qr_when_status_unchanged(self):
  sid=self.row();post=dict(posting_number='POST-1',status='awaiting_packaging',scanit='ii789')
  with patch.object(night_check,'list_open_shipments',return_value=db.get_shipments_by_ext(1,'fbs',['POST-1'])):
   checked,fixed=night_check.check_ozon_live(db.get_cabinet(1),status_cache={'POST-1':post})
  self.assertEqual((checked,fixed),(1,1));self.assertEqual(db.list_assembly(query='ii789')[0]['id'],sid)
unittest.main()
