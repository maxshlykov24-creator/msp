"""Read-only API synchronization, partial history recovery and seller isolation."""
import os,tempfile,json,unittest
from unittest.mock import patch
os.environ['FF_DB']=os.path.join(tempfile.mkdtemp(),'ops.sqlite')
import db,net,orders_pull as orders,shipments_pull as ships,supply_flow,wb_supply,sync_rota
class Response:
 def __init__(self,code=200,data=None,headers=None):self.status_code=code;self.data=data;self.headers=headers or {};self.text='fixture'
 def json(self):return self.data
class Sync(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  db.init_db();c=db.connect();c.execute("insert into clients(id,code,name) values (1,'T','Test')");c.execute("insert into cabinets(id,client_id,marketplace,active,token) values (1,1,'wb',1,'test')");c.commit();c.close()
 def setUp(self):
  c=db.connect()
  for t in ['shipment_marks','shipments','wb_boxes','wb_supplies','settings']:c.execute('delete from '+t)
  c.commit();c.close();self.client=db.get_client_by_id(1);self.cab=db.get_cabinet(1)
 def order(self,id=123):return dict(id=id,article='ART',skus=['PRODUCT'],createdAt='2026-11-03T10:00:00Z')
 def test_new_orders_survive_truncated_history(self):
  pages=[Response(data={'orders':[self.order()]}),Response(429)]
  with patch.object(orders,'wb_read',side_effect=pages):
   with self.assertRaises(RuntimeError):orders.pull_wb_fbs('test',orders.since_days(14),strict=True,on_new=lambda rows:ships.stage_wb_new(self.client,self.cab,rows))
  rows=db.get_shipments_by_ext(1,'fbs',['123']);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status_group'],'new')
 def test_existing_work_and_marks_preserved(self):
  ships.stage_wb_new(self.client,self.cab,[self.order()]);r=db.get_shipments_by_ext(1,'fbs',['123'])[0];db.set_work_state([r['id']],'ready');db.replace_shipment_marks(r['id'],[{'code':'existing-mark','article':'ART','gtin':'1'}])
  self.assertEqual(ships.stage_wb_new(self.client,self.cab,[self.order()]),0);self.assertEqual(db.get_shipments_by_ext(1,'fbs',['123'])[0]['work_state'],'ready');self.assertEqual(len(db.list_shipment_marks([r['id']])),1)
 def test_empty_remote_meta_cannot_erase_saved_marks(self):
  ships.stage_wb_new(self.client,self.cab,[self.order()]);r=db.get_shipments_by_ext(1,'fbs',['123'])[0];db.replace_shipment_marks(r['id'],[{'code':'existing-mark','article':'ART','gtin':'1'}])
  with patch.object(ships,'wb_statuses',return_value={'123':{'supplier':'new','wb':'waiting'}}),patch.object(ships,'wb_meta',return_value={'123':[]}):ships.handle_wb_fbs(self.client,self.cab,[self.order()])
  self.assertEqual(len(db.list_shipment_marks([r['id']])),1)
 def test_new_response_requires_order_list(self):
  with patch.object(orders,'wb_read',return_value=Response(data={})):
   with self.assertRaises(RuntimeError):orders.pull_wb_new('test',strict=True)
 def test_duplicate_new_id_rejected(self):
  with self.assertRaises(RuntimeError):ships.stage_wb_new(self.client,self.cab,[self.order(),self.order()])
  self.assertFalse(db.map_shipments(1,'fbs'))
 def test_repeated_history_cursor_rejected(self):
  pages=[Response(data={'orders':[]}),Response(data={'orders':[self.order()],'next':42}),Response(data={'orders':[self.order(124)],'next':42})]
  with patch.object(orders,'wb_read',side_effect=pages),patch.object(orders.time,'sleep'):
   with self.assertRaises(RuntimeError):orders.pull_wb_fbs('test',orders.since_days(14),strict=True)
 def test_history_deduplicates_new_page(self):
  pages=[Response(data={'orders':[self.order()]}),Response(data={'orders':[self.order(),self.order(124)],'next':0})]
  with patch.object(orders,'wb_read',side_effect=pages):self.assertEqual(len(orders.pull_wb_fbs('test',orders.since_days(14),strict=True)),2)
 def test_wb_read_retry_honors_server(self):
  with patch.object(net,'req',side_effect=[Response(429,headers={'X-Ratelimit-Retry':'7'}),Response(data={})]) as call,patch.object(net.time,'sleep') as sleep:
   self.assertEqual(net.wb_read('GET',net.WB_BASE+'/api/v3/orders').status_code,200);sleep.assert_called_once_with(7);self.assertEqual(call.call_count,2)
 def test_wb_read_does_not_retry_mutation(self):
  with patch.object(net,'req') as call:
   with self.assertRaises(ValueError):net.wb_read('POST',net.WB_BASE+'/api/v3/supplies',json={})
   call.assert_not_called()
 def test_wb_long_limit_stays_failed(self):
  with patch.object(net,'req',return_value=Response(429,headers={'Retry-After':'120'})) as call,patch.object(net.time,'sleep') as sleep:
   self.assertEqual(net.wb_read('GET',net.WB_BASE+'/api/v3/orders').status_code,429);self.assertEqual(call.call_count,1);sleep.assert_not_called()
 def test_ozon_pause_isolated_by_seller(self):
  a={'Client-Id':'1','Api-Key':'a'};b={'Client-Id':'2','Api-Key':'b'};net.pause_fbs_list(headers=a);self.assertTrue(net.fbs_list_paused(a));self.assertFalse(net.fbs_list_paused(b));self.assertTrue(net.fbs_list_paused(dict(a,**{'Api-Key':'rotated'})))
  net.pause_fbs_list(headers=b);net.clear_fbs_list_pause(a);self.assertFalse(net.fbs_list_paused(a));self.assertTrue(net.fbs_list_paused(b))
 def test_warehouse_sync_precedes_ms_orders(self):
  events=[]
  with patch.object(ships,'run',side_effect=lambda **kw:events.append('warehouse') or {}),patch.object(orders,'run',side_effect=lambda **kw:events.append('ms') or {}):sync_rota.sync_client(1)
  self.assertEqual(events,['warehouse','ms'])
 def test_missing_supply_not_retried_each_cycle(self):
  sid=db.insert_wb_supply(1,1,'MISSING','Missing','2026-11-03T10:00:00','test');ships.stage_wb_new(self.client,self.cab,[self.order()]);r=db.get_shipments_by_ext(1,'fbs',['123'])[0];db.set_shipment_supply([r['id']],'MISSING')
  with patch.object(wb_supply,'list_supplies',return_value=[]),patch.object(wb_supply,'info',side_effect=wb_supply.SupplyNotFound('missing')) as fetch,patch.object(supply_flow.time,'sleep'):
   _,first=supply_flow._sync_supply_cabinet(self.cab,'test');_,second=supply_flow._sync_supply_cabinet(self.cab,'test')
  self.assertEqual(fetch.call_count,1);self.assertTrue(first and second);self.assertEqual(db.get_wb_supply(sid)['state'],'open')
 def test_missing_historical_supply_not_source_error(self):
  db.insert_wb_supply(1,1,'OLD','Old','2026-01-01T10:00:00','test')
  with patch.object(wb_supply,'list_supplies',return_value=[]),patch.object(wb_supply,'info',side_effect=wb_supply.SupplyNotFound('missing')),patch.object(supply_flow.time,'sleep'):
   _,notes=supply_flow._sync_supply_cabinet(self.cab,'test')
  self.assertFalse(notes)
unittest.main()
