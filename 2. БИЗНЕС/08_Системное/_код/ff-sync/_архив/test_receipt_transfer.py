import copy,unittest,importlib.util
from datetime import date
import account
import accept as a
def doc(id,party,day,qty,kind='supply'):
 return dict(id=id,name=id,kind=kind,party=party,store='s',posted=True,moment=day+'T00:00:00',positions=[dict(id=id+'p',product='p',qty=qty)])
class Transfer(unittest.TestCase):
 def setUp(self):
  old=doc('receipt','wb','2026-09-09',10);current=doc('receipt','wb','2026-09-09',7);dest=doc('transfer','ozon','2026-10-28',3)
  self.s=dict(products={'p':dict(article='p',name='p',liters=1,pick_rate=10)},documents=[current,dest,doc('shipped','wb','2026-10-15',4,'demand')],stock={'p':6},receipt_transfers=[dict(source_before=old,source_after=current,destination=dest)])
 def test_balance_storage_no_duplicate_pick(self):
  p=a.build_plan(self.s,[]);self.assertEqual(p['qty'],6);self.assertEqual(sum(m['qty'] for m in p['moves'].values() if m['kind']=='pick'),4)
  sums={};perday={}
  for key,l in p['lots'].items():
   days={}
   for m in p['moves'].values():
    if m['lot_key']==key:days[m['created_at'][:10]]=days.get(m['created_at'][:10],0)+m['qty']
   v=account.storage_of(dict(l,id=0,billed_until='2026-10-25' if l['party']=='wb' else None),{},date_to=date(2026,11,1),ship_days=days)
   sums[l['party']]=sums.get(l['party'],0)+v['storage']
   for d in v['by_day']:perday[d['day']]=perday.get(d['day'],0)+d['qty']
  self.assertAlmostEqual(sums['wb'],4.5);self.assertAlmostEqual(sums['ozon'],1.8)
  self.assertEqual(set(perday.values()),{6})
 def test_raw_source_unchanged(self):
  before=copy.deepcopy(self.s);a.build_plan(self.s,[]);self.assertEqual(self.s,before)
 def test_config_repeated_stable(self):self.assertEqual(a.build_plan(self.s,[]),a.build_plan(self.s,[]))
 def test_future_source_edit_blocked(self):
  self.s['documents'][0]['positions'][0]['qty']=6
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_missing_destination_blocked(self):
  self.s['documents'].pop(1)
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_wrong_transfer_quantity_blocked(self):
  self.s['receipt_transfers'][0]['source_before']['positions'][0]['qty']=11
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_shared_group_blocked(self):
  with self.assertRaises(ValueError):a.build_plan(self.s,[['wb','ozon']])
 def test_insufficient_at_transfer_blocked(self):
  self.s['documents'][2]['positions'][0]['qty']=8;self.s['stock']['p']=2
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_ozon_cannot_consume_wb_after_transfer(self):
  self.s['documents'].append(doc('ozon-demand','ozon','2026-10-29',4,'demand'));self.s['stock']['p']=2
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_global_stock_mismatch_blocked(self):
  self.s['stock']['p']=7
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
class ReadRetry(unittest.TestCase):
 def test_rate_limit_retry(self):
  from unittest.mock import patch,Mock
  limited=Mock(status_code=429,headers={'Retry-After':'4'})
  done=Mock(status_code=200)
  with patch.object(a,'req',side_effect=[limited,done]) as req,patch.object(a,'ms_headers',return_value={}),patch('time.sleep') as sleep:
   self.assertIs(a.ledger_get('/x'),done);self.assertEqual(req.call_count,2);sleep.assert_called_once_with(4)
 def test_long_cooldown_no_early_retry(self):
  from unittest.mock import patch,Mock
  limited=Mock(status_code=429,headers={'Retry-After':'90'})
  with patch.object(a,'req',return_value=limited) as req,patch.object(a,'ms_headers',return_value={}),patch('time.sleep') as sleep:
   self.assertIs(a.ledger_get('/x'),limited);self.assertEqual(req.call_count,1);sleep.assert_not_called()
 def test_bounded_attempts(self):
  from unittest.mock import patch,Mock
  limited=Mock(status_code=429,headers={})
  with patch.object(a,'req',return_value=limited) as req,patch.object(a,'ms_headers',return_value={}),patch('time.sleep'):
   self.assertIs(a.ledger_get('/x'),limited);self.assertEqual(req.call_count,5)
class LateReceipt(unittest.TestCase):
 def setUp(self):
  d=doc('late','andreeva','2026-09-23',10)
  self.rule=dict(source_key='ms:supply:late:latep',party='andreeva',ms_store_id='s',accepted_at=d['moment'],ms_product_id='p',qty_in=10,storage_from='2026-10-15',closed_through='2026-10-25')
  self.s=dict(products={'p':dict(article='p',name='p',liters=1,pick_rate=10)},documents=[d],stock={'p':10},late_receipts=[self.rule])
 def test_separate_physical_date_and_charged_date(self):
  lot=dict(next(iter(a.build_plan(self.s,[])['lots'].values())),id=1,billed_until=None)
  self.assertEqual(lot['accepted_at'][:10],'2026-09-23')
  v=account.storage_of(lot,{},date_to=date(2026,10,25),ship_days={})
  self.assertEqual(v['from'],'2026-10-15');self.assertEqual(v['days'],11);self.assertEqual(v['storage'],16.5)
  self.assertEqual(account.storage_of(dict(lot,billed_until='2026-11-01'),{},date_to=date(2026,11,1),ship_days={})['storage'],0)
 def test_changed_quantity_rejected(self):
  self.rule['qty_in']=11
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_date_before_actual_receipt_rejected(self):
  self.rule['storage_from']='2026-09-20'
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
 def test_duplicate_rule_rejected(self):
  self.s['late_receipts'].append(self.rule)
  with self.assertRaises(ValueError):a.build_plan(self.s,[])
class IndependentScopes(unittest.TestCase):
 def test_registered_client(self):
  self.assertEqual(a.independent_scopes({'parties':{'gay':{}},'independent_scopes':[['gay']]}),{('gay',)})
 def test_unknown_client_blocked(self):
  with self.assertRaises(ValueError):a.independent_scopes({'parties':{},'independent_scopes':[['missing']]})
 def test_overlap_blocked(self):
  s={'parties':{'wb':{},'ozon':{}},'receipt_transfers':[{'source_before':{'party':'wb'},'destination':{'party':'ozon'}}],'independent_scopes':[['wb']]}
  with self.assertRaises(ValueError):a.independent_scopes(s)
 def test_default_no_added_scope(self):
  self.assertEqual(a.independent_scopes({'parties':{}}),set())
unittest.main()
