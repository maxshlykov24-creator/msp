import asyncio,json,tempfile,unittest
from pathlib import Path
from .engine import render,respond,validate_plan
from .storage import Storage

COMPANY={'hours':'ежедневно с 10:00 до 20:00','address':'Автозаводская 18','seller':'Автомобиль выкуплен нами','credit':'Кредит не оформляем','tradein':'Обмен на автомобиль рассматриваем'}
CAR={'listing':{'title':'FAW NAT','price':'1 700 000 ₽'},'card':{'price_cash':'1 700 000 ₽','price_vat':'2 000 000 ₽','mileage':'6 798 км','owners':'2','damage':'ДТП не было, кузов не чинили','report':'https://autoteka.ru/report/test'}}
class EngineTests(unittest.TestCase):
 def test_paint_absent_is_unknown(self):
  a=render(['paint'],CAR,{},COMPANY,'Окрасы?');self.assertIn('окрасы кузова',a.unknown);self.assertNotIn('окрасов нет',a.text.lower())
 def test_long_cannot_be_inferred(self):
  a=render(['long'],CAR,{},COMPANY,'Не Long?');self.assertTrue(a.unknown);self.assertNotIn('не Long',a.text)
 def test_mileage_is_not_report_verified(self):
  a=render(['mileage','mileage_verified'],CAR,{},COMPANY,'Пробег проверяли?');self.assertIn('проверку пробега',a.unknown);self.assertNotIn('подтверждён',a.text)
 def test_multi_question(self):
  a=render(['vat','report'],CAR,{},COMPANY,'НДС и отчёт?');self.assertIn('2 000 000',a.text);self.assertIn('https://autoteka',a.text)
 def test_contact_no_reask(self):
  a=render(['battery'],CAR,{'has_contact':True},COMPANY,'SOH?');self.assertEqual(a.action,'handoff');self.assertNotIn('номер',a.text)
 def test_dispute_without_phone(self):
  self.assertEqual(render(['dispute'],CAR,{},COMPANY,'Вы обещали').action,'handoff')
 def test_no_repeated_contact(self):
  s={};render(['service'],CAR,s,COMPANY,'ГРМ?');a=render(['long'],CAR,s,COMPANY,'Long?');self.assertEqual(a.action,'handoff');self.assertNotIn('номер',a.text)
 def test_unknown_card_does_not_confirm_availability(self):
  a=render(['availability'],{'listing':CAR['listing'],'card':None},{},COMPANY,'Есть?');self.assertTrue(a.unknown)
 def test_invalid_schema(self):
  for x in ('{"intents":["price"],"text":"выдумка"}','{"intents":["invent"]}','{"intents":[]}'):
   with self.assertRaises(ValueError):validate_plan(x)
 def test_model_text_never_reaches_reply(self):
  async def bad(h,t):return '{"intents":["long"],"answer":"Не Long, капот красили"}',0
  a=asyncio.run(respond('какая база?',{},CAR,COMPANY,bad));self.assertEqual(a.action,'error');self.assertEqual(a.text,'')
 def test_phone_redacted_and_no_call_promise(self):
  async def unused(h,t):raise AssertionError('LLM should not run')
  s={};a=asyncio.run(respond('+7 900 000 00 00 Ватсап',s,CAR,COMPANY,unused));self.assertEqual(a.action,'handoff');self.assertNotIn('наберу',a.text);self.assertNotIn('900',json.dumps(s))
 def test_new_question_after_thanks(self):
  async def planner(h,t):return '{"intents":["price"]}',0
  a=asyncio.run(respond('Цена?',{'messages':[{'role':'user','content':'Спасибо'}]},CAR,COMPANY,planner));self.assertIn('1 700 000',a.text)
 def test_persistent_budget_and_update_dedupe(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'s.db';s=Storage(p);i=s.reserve(.025);s.settle(i,.01)
   with self.assertRaises(RuntimeError):s.reserve(.025)
   self.assertTrue(s.claim(1));self.assertFalse(s.claim(1));s.db.close()
   r=Storage(p);self.assertFalse(r.claim(1));self.assertAlmostEqual(r.spent(),.01);r.db.close()


class TransportSafetyTests(unittest.TestCase):
 def test_production_bot_id_rejected_before_network(self):
  from .main import Lab
  with self.assertRaisesRegex(RuntimeError,'Production bot'):
   Lab({'LAB_TELEGRAM_TOKEN':'8840235455:fake-test-only','LAB_ALLOWED_USERS':'1','LAB_FORBIDDEN_BOT_IDS':'8840235455'})
 def test_outsider_and_group_do_not_reach_handler(self):
  from .main import Lab
  from unittest.mock import AsyncMock
  app=Lab.__new__(Lab);app.allowed={1};app.send=AsyncMock();app.command=AsyncMock()
  for chat,sender,typ in [(2,2,'private'),(-1,1,'group'),(1,2,'private')]:
   asyncio.run(app.handle({'update_id':1,'message':{'chat':{'id':chat,'type':typ},'from':{'id':sender},'text':'/start'}}))
  app.send.assert_not_called();app.command.assert_not_called()



class FollowupTests(unittest.TestCase):
 def test_media_mention_is_not_request(self):
  async def plan(h,t):return '{"intents":["long","options","photo"]}',0
  a=asyncio.run(respond('На фотографиях колонок не увидел. Это не Long?',{},CAR,COMPANY,plan))
  self.assertNotIn('photo',a.intents)
 def test_video_contact_first_ask_not_premature_handoff(self):
  a=render(['video','contact'],CAR,{},COMPANY,'Видео возможно в Ватсап?')
  self.assertEqual(a.action,'reply');self.assertEqual(a.text.count('номер телефона'),1)
if __name__=='__main__':unittest.main()
