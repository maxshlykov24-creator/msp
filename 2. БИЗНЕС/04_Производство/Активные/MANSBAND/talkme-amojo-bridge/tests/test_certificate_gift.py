from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from io import BytesIO
import httpx,pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from pypdf import PdfReader
from app import certificate_gift as g,message_templates as m
from app.reservation_reminders import ReminderEvent,MSK
from app.certificate_mail import TABLES as MAIL_TABLES
from app.certificate_pdf import render_pdf

STAMP=datetime(2026,11,1,10,tzinfo=MSK).astimezone(timezone.utc)
@pytest.fixture
def db(monkeypatch):
 monkeypatch.setattr(m,"now",lambda:STAMP)
 monkeypatch.setenv("CERTGIFT_FILE_KEY","test-only-key-"+"x"*32)
 engine=create_engine("sqlite://")
 for table in [*m.TABLES,*g.TABLES,*MAIL_TABLES,ReminderEvent.__table__]:table.create(engine,checkfirst=True)
 with Session(engine) as db:
  routing=m.Routing().model_dump();db.add(m.Document(id="routing",kind="routing",draft=routing,active=routing));db.commit();g.seed(db)
  d=db.get(m.Document,g.RULE_ID);d.active={**d.active,"enabled":True};db.commit()
  yield db

def lead():
 return {"id":1,"pipeline_id":9601214,"status_id":76675038,"price":15000,"_embedded":{"contacts":[{"id":10,"is_main":True}]},"custom_fields_values":[{"field_id":key,"values":[{"value":value}]} for key,value in [(3830333,"15000"),(3828225,True),(3829442,int(STAMP.timestamp())),(3830052,"79000000001"),(3135463,"MB-4837-9261"),(3185675,"Получатель"),(3830305,"С праздником!")]]}
class CRM:
 def __init__(self,replies):
  self.lead=lead();self.replies=list(replies);self.posts=[];self.wz=self
 def get(self,path,**kw):
  if path=="/channels":return httpx.Response(200,json=[{"channelId":t,"transport":"tgapi" if t=="telegram" else t,"state":"active"} for t in ("telegram","whatsapp","max")])
  if path.startswith("/api/v4/contacts/"):return {"id":10,"name":"Покупатель"}
  return self.lead
 def post(self,path,json):
  self.posts.append(json);reply=self.replies.pop(0)
  if isinstance(reply,Exception):raise reply
  return httpx.Response(reply[0],json=reply[1])

def prepare(monkeypatch,db,c):
 from app import stage_notifications as st
 monkeypatch.setattr(st,"certificate_plan",lambda *a,**kw:[{"transport":t,"channelId":t,"recipient":{"phone":"79000000001"}} for t in ("telegram","whatsapp","max")])
 j=g.GiftJob(lead_id=1);db.add(j);db.commit();return j

def issuer(_):return {"number":"MB-4837-9261","nominal":15000,"balance":15000,"status":"active"}

def test_both_parts_one_channel(monkeypatch,db):
 c=CRM([(201,{"messageId":"text"}),(201,{"messageId":"pdf"})]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 assert [x["chatType"] for x in c.posts]==["telegram","telegram"]
 assert "text" in c.posts[0] and "contentUri" not in c.posts[0]
 assert "contentUri" in c.posts[1] and "text" not in c.posts[1]
 g.process(db,c,j,STAMP,issuer);assert len(c.posts)==2

def test_text_unknown_stops_all_fallbacks(monkeypatch,db):
 c=CRM([httpx.ReadTimeout("lost")]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 assert j.state=="unknown" and len(c.posts)==1
 g.process(db,c,j,STAMP,issuer);assert len(c.posts)==1

def test_text_definite_rejection_allows_next(monkeypatch,db):
 from app.reservation_reminders import REJECTED
 code=next(iter(REJECTED));c=CRM([(400,{"error":code}),(201,{"messageId":"text"}),(201,{"messageId":"pdf"})]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 assert [x["chatType"] for x in c.posts]==["telegram","whatsapp","whatsapp"]

def test_pdf_rejection_pins_channel(monkeypatch,db):
 from app.reservation_reminders import REJECTED
 code=next(iter(REJECTED));c=CRM([(201,{"messageId":"text"}),(400,{"error":code})]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 assert j.state=="partial" and len(c.posts)==2
 g.process(db,c,j,STAMP,issuer);assert len(c.posts)==2

def test_restart_inflight_never_resends(db):
 j=g.GiftJob(lead_id=1,attempts=[{"part":"text","transport":"telegram","state":"inflight","message_id":""}]);db.add(j);db.commit();g.reconcile(db,j)
 assert j.state=="unknown"

def test_recipient_missing_is_blocked(monkeypatch,db):
 c=CRM([]);c.lead["custom_fields_values"]=[f for f in c.lead["custom_fields_values"] if f["field_id"]!=3830052];j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 assert j.state=="blocked" and not c.posts

def test_future_date_issues_without_sending(monkeypatch,db):
 c=CRM([])
 for f in c.lead["custom_fields_values"]:
  if f["field_id"]==3829442:f["values"][0]["value"]=int((STAMP+timedelta(days=1)).timestamp())
 j=prepare(monkeypatch,db,c);calls=[];g.process(db,c,j,STAMP,lambda lid:(calls.append(lid) or issuer(lid)))
 assert calls==[1] and j.number and not c.posts

def test_delivery_requires_both_callbacks(db):
 j=g.GiftJob(lead_id=1,state="pending",attempts=[{"part":p,"transport":"telegram","state":"pending","message_id":p} for p in ("text","pdf")]);db.add(j)
 db.add(ReminderEvent(key="t",message_id="text",status="delivered",error_code=""));db.commit();g.reconcile(db,j);assert j.state!="delivered"
 db.add(ReminderEvent(key="p",message_id="pdf",status="delivered",error_code=""));db.commit();g.reconcile(db,j);assert j.state=="delivered"

def test_preview_does_not_issue_or_send(db):
 c=CRM([]);doc=db.get(m.Document,g.RULE_ID);result=g.preview(db,c,doc,1)
 assert "Покупатель" in result["samples"][0]["text"] and not c.posts

def test_nominal_comes_from_field_not_deal_budget():
 c=CRM([]);c.lead["price"]=42000
 assert g.data_for(c,c.lead)["nominal"]==15000

@pytest.mark.parametrize("value", ["", "0", "-1", "abc", "nan", "inf"])
def test_invalid_nominal_blocks_sending_even_with_budget(value):
 c=CRM([])
 for field in c.lead["custom_fields_values"]:
  if field["field_id"]==3830333:field["values"][0]["value"]=value
 with pytest.raises(ValueError,match="Номинал"):g.data_for(c,c.lead)


def test_pdf_has_cyrillic_and_links():
 data={"guest":"Александр","number":"MB-4837-9261","nominal":15000,"start":"01.11.2026","until":"01.11.2027","greeting":"Поздравляем!"}
 reader=PdfReader(BytesIO(render_pdf(data)));assert len(reader.pages)==1
 text="".join(p.extract_text() for p in reader.pages);assert "MB-4837-9261" in text and "Александр" in text
 urls=[a.get_object().get("/A",{}).get("/URI") for p in reader.pages for a in p.get("/Annots",[])]
 assert "https://t.me/Mansband_manager" in urls and "https://wa.me/79310090251" in urls
 assert "₽" in text
 assert tuple(map(float,reader.pages[0].mediabox[2:]))==(403.5,831.0)
 fonts=[str(f.get_object()["/BaseFont"]) for f in reader.pages[0]["/Resources"]["/Font"].values()]
 assert any("Montserrat-SemiBold" in f for f in fonts) and any("Montserrat-Regular" in f for f in fonts)
 assert not any("DejaVu" in f for f in fonts)


@pytest.mark.parametrize("greeting", [None, "", "   ", "\n\t\u00a0"])
def test_empty_greeting_omits_entire_section_and_moves_content_up(greeting):
 data={"guest":"Александр","number":"TEST","nominal":15000,"until":"01.11.2027","greeting":greeting}
 empty=PdfReader(BytesIO(render_pdf(data))).pages[0]
 filled=PdfReader(BytesIO(render_pdf({**data,"greeting":"Поздравляем!"}))).pages[0]
 assert "Поздравление отправителя:" not in empty.extract_text()
 assert "Поздравление отправителя:" in filled.extract_text()
 label="Как воспользоваться подарочным сертификатом?"
 positions=[]
 for page in (empty,filled):
  found=[]
  page.extract_text(visitor_text=lambda text,cm,tm,font,size: found.append(cm[5]+tm[5]) if label in text else None)
  positions.append(found[0])
 assert positions[0]>positions[1]
 assert len(empty["/Annots"])==len(filled["/Annots"])==11


def test_long_greeting_not_truncated():
 greeting="Поздравление "*3000
 reader=PdfReader(BytesIO(render_pdf({"guest":"","number":"TEST","nominal":1,"start":"01.11.2026","until":"01.11.2027","greeting":greeting})))
 assert len(reader.pages)>1
 assert "".join(p.extract_text() for p in reader.pages).count("Поздравление")>=3000

def test_bad_links_are_rejected():
 with pytest.raises(ValueError):render_pdf({},{"contacts":{"telegram":"javascript:alert(1)"}})


def test_disabling_after_text_stops_pdf(monkeypatch,db):
 c=CRM([(201,{"messageId":"text"})]);j=prepare(monkeypatch,db,c);original=c.post
 def post(path,json):
  response=original(path,json);doc=db.get(m.Document,g.RULE_ID);doc.active={**doc.active,"enabled":False};db.commit();return response
 c.post=post;g.process(db,c,j,STAMP,issuer);assert len(c.posts)==1


def test_nominal_change_after_text_stops_pdf(monkeypatch,db):
 c=CRM([(201,{"messageId":"text"})]);j=prepare(monkeypatch,db,c);original=c.post
 def post(path,json):
  response=original(path,json)
  for field in c.lead["custom_fields_values"]:
   if field["field_id"]==3830333:field["values"][0]["value"]="20000"
  return response
 c.post=post;g.process(db,c,j,STAMP,issuer);assert len(c.posts)==1 and j.state=="partial"


def test_recipient_change_after_text_stops_pdf(monkeypatch,db):
 c=CRM([(201,{"messageId":"text"})]);j=prepare(monkeypatch,db,c);original=c.post
 def post(path,json):
  response=original(path,json)
  for field in c.lead["custom_fields_values"]:
   if field["field_id"]==3830052:field["values"][0]["value"]="79000000002"
  return response
 c.post=post;g.process(db,c,j,STAMP,issuer);assert len(c.posts)==1 and j.state=="partial"


def test_pdf_retry_never_repeats_text(monkeypatch,db):
 from app.reservation_reminders import REJECTED
 code=next(iter(REJECTED));c=CRM([(201,{"messageId":"text"}),(400,{"error":code}),(201,{"messageId":"pdf"})]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 g.retry_pdf(1,{"confirmed":True,"actor":"test"},db);g.process(db,c,j,STAMP,issuer)
 assert len(c.posts)==3 and ["text" in x for x in c.posts]==[True,False,False]
 assert {x["chatType"] for x in c.posts}=={"telegram"}


def test_unknown_pdf_cannot_be_retried(monkeypatch,db):
 from fastapi import HTTPException
 c=CRM([(201,{"messageId":"text"}),httpx.ReadTimeout("lost")]);j=prepare(monkeypatch,db,c);g.process(db,c,j,STAMP,issuer)
 with pytest.raises(HTTPException):g.retry_pdf(1,{"confirmed":True},db)
 assert len(c.posts)==2


def test_legacy_migration_does_not_backfill_past_gifts():
 old=SimpleNamespace(event_at=STAMP-timedelta(days=3));data=lead()
 assert g.eligible_legacy(old,data,STAMP.timestamp())
 for f in data["custom_fields_values"]:
  if f["field_id"]==3829442:f["values"][0]["value"]=int((STAMP-timedelta(days=1)).timestamp())
 assert not g.eligible_legacy(old,data,STAMP.timestamp())
 assert g.eligible_legacy(SimpleNamespace(event_at=STAMP+timedelta(seconds=1)),data,STAMP.timestamp())
 for f in data["custom_fields_values"]:
  if f["field_id"]==3828225:f["values"][0]["value"]=False
 assert not g.eligible_legacy(old,data,STAMP.timestamp())


def test_smtp_cannot_resume_after_cutover(monkeypatch,db):
 from app import certificate_mail as mail
 monkeypatch.setenv("CERTGIFT_MODE","preview")
 assert not mail.smtp_replaced(db)
 g.mark_cutover(db);g.mark_cutover(db)
 assert mail.smtp_replaced(db)
 calls=[]
 mail.process_job(db,1,mail.MailSettings(),STAMP,fetch=lambda *a:calls.append(a))
 assert not calls
 assert mail.summary(db)["replaced_by_gift"]
 assert not mail.summary(db)["ready"]


def test_publication_replans_only_unattempted_gifts(db):
 pending=g.GiftJob(lead_id=1,state="failed",snapshot={"old":True},pdf=b"old")
 started=g.GiftJob(lead_id=2,state="partial",snapshot={"frozen":True},pdf=b"frozen",attempts=[{"part":"text","transport":"telegram","state":"sent","message_id":"accepted"}])
 db.add_all([pending,started]);db.commit()
 routing=db.get(m.Document,"routing");routing.checked=m.digest([routing.version,routing.draft]);db.commit()
 m.publish("routing",m.Publish(version=routing.version,actor="test"),db=db,c=CRM([]))
 assert pending.state=="queued" and pending.snapshot is None and pending.pdf is None
 assert started.state=="partial" and started.snapshot=={"frozen":True} and started.pdf==b"frozen"
 gift=db.get(m.Document,g.RULE_ID);gift.draft={**gift.draft,"enabled":False};gift.checked=m.digest([gift.version,gift.draft]);db.commit()
 m.publish(g.RULE_ID,m.Publish(version=gift.version,actor="test"),db=db,c=CRM([]))
 assert pending.state=="cancelled" and started.snapshot=={"frozen":True}


def test_gift_can_be_published_with_buyer_variable(db,monkeypatch):
 cat={"fields":[{"id":3829442,"type":"date"},{"id":3830052,"type":"text"}],"pipelines":[{"id":9601214,"_embedded":{"statuses":[{"id":76675038}]}}]}
 monkeypatch.setattr(m,"catalog",lambda c:cat)
 d=db.get(m.Document,g.RULE_ID);d.draft={**d.draft,"enabled":True};d.checked=m.digest([d.version,d.draft]);db.commit()
 result=m.publish(g.RULE_ID,m.Publish(version=d.version,actor="test"),db=db,c=CRM([]))
 assert result["active"]["enabled"] and "{{buyer.name}}" in result["active"]["content"]
 bad=m.Rule(**{**d.draft,"content":"{{lead.id}}"})
 with pytest.raises(ValueError,match="переменная сертификата"):m.validate_catalog(bad,cat)
 ordinary=m.Rule(**{**d.draft,"action":"text"})
 with pytest.raises(ValueError,match="Неизвестная переменная"):m.validate_catalog(ordinary,cat)
