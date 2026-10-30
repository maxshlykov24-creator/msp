"""Two durable, ordered parts. Once either part is accepted the route is pinned."""
from __future__ import annotations
import asyncio,base64,hashlib,hmac,json,logging,uuid
from datetime import datetime,timedelta,timezone
from typing import Optional,Literal
import httpx
from fastapi import APIRouter,HTTPException,Depends
from fastapi.responses import Response
from pydantic import SecretStr,Field
from pydantic_settings import BaseSettings,SettingsConfigDict
from sqlalchemy import Integer,String,DateTime,JSON,LargeBinary,select,text
from sqlalchemy.orm import Mapped,mapped_column
from app.models import Base
from app.database import get_engine,get_session_factory
from app.certificate_mail import CertificateMailJob,MailSettings,field_value,anniversary
from zoneinfo import ZoneInfo
MSK=ZoneInfo("Europe/Moscow")
from app import message_templates as m
from app.certificate_pdf import render_pdf,DEFAULT_INTRO,DEFAULT_INSTRUCTIONS,CONTACTS

log=logging.getLogger(__name__)
RULE_ID="gift-certificate"
BODY="""Вас приветствует магазин мужских костюмов Mansband👋🏻

Отправляем вам подарочный сертификат от {{buyer.name}}! Вся необходимая информация ждет внутри🎁

Поздравляем и с нетерпением будем ждать вас в магазинах нашей сети☺️"""
def demo_data():
 start=m.now().astimezone(MSK).date();until=anniversary(start)
 return {"guest":"Александр","buyer":"покупателя","number":"ТЕСТ, НЕ ДЛЯ СПИСАНИЯ","nominal":15000,"start":start.strftime("%d.%m.%Y"),"until":until.strftime("%d.%m.%Y"),"start_iso":start.isoformat(),"greeting":"С днём рождения! Пусть этот сертификат добавит ещё один повод для улыбки в твою копилку счастливых моментов.","phone":""}
class GiftTest(m.TestSend):
 lead_id:int=Field(ge=0)
class Settings(BaseSettings):
 model_config=SettingsConfigDict(env_prefix="CERTGIFT_",extra="ignore")
 mode:Literal["preview","live"]="preview"
 kassa_url:str="https://mansband-kassa.ru"
 service_key:SecretStr=SecretStr("")
 file_key:SecretStr=SecretStr("")
 public_url:str="https://ai-msp.ru"
 activated_at:int=0
 def ready(self):return self.mode=="live" and self.activated_at>0 and len(self.service_key.get_secret_value())>=32 and len(self.file_key.get_secret_value())>=32
class GiftJob(Base):
 __tablename__="certificate_gift_job"
 lead_id:Mapped[int]=mapped_column(Integer,primary_key=True)
 state:Mapped[str]=mapped_column(String(32),default="queued",index=True)
 reason:Mapped[str]=mapped_column(String(160),default="")
 check_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=m.now,index=True)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=m.now)
 due_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True)
 snapshot:Mapped[Optional[dict]]=mapped_column(JSON,nullable=True)
 attempts:Mapped[list]=mapped_column(JSON,default=list)
 pdf:Mapped[Optional[bytes]]=mapped_column(LargeBinary,nullable=True)
 number:Mapped[str]=mapped_column(String(100),default="")
TABLES=[GiftJob.__table__]
router=APIRouter()

def seed(db):
 if db.get(m.Document,RULE_ID):return
 rule=m.Rule(name="Выдача электронного сертификата",enabled=False,action="certificate",pipelines=[9601214],statuses=[76675038],trigger=m.Trigger(type="date",field=3829442,offset=0,time="10:00",repeat="once"),recipient="field",phone_field=3830052,content=BODY,certificate_pdf={"intro":DEFAULT_INTRO,"instructions":DEFAULT_INSTRUCTIONS,"contacts":CONTACTS})
 data=rule.model_dump();db.add(m.Document(id=RULE_ID,kind="rule",draft=data,active=data,published_version=1));db.commit()

def data_for(c,lead):
 from app.stage_notifications import certificate_phone,CalendarSettings
 if lead.get("is_deleted") or lead.get("pipeline_id")!=9601214 or lead.get("status_id") not in (76675038,142):raise ValueError("Сделка больше не является оплаченным сертификатом")
 if field_value(lead,3828225).lower() not in ("true","1"):raise ValueError("Не электронный сертификат")
 phone=certificate_phone(lead,CalendarSettings())
 contacts=lead.get("_embedded",{}).get("contacts",[]);main=[x for x in contacts if x.get("is_main")]
 buyer=main[0] if len(main)==1 else contacts[0] if len(contacts)==1 else None
 if not buyer:raise ValueError("Не определён единственный покупатель")
 name=str(c.get(f"/api/v4/contacts/{buyer['id']}").get("name","")).strip()
 if not name:raise ValueError("Не заполнено имя покупателя")
 try:start=datetime.fromtimestamp(int(field_value(lead,3829442)),MSK).date()
 except (ValueError,OverflowError,OSError):raise ValueError("Не заполнена дата начала")
 until=anniversary(start)
 if until<m.now().astimezone(MSK).date():raise ValueError("Сертификат просрочен")
 amount=lead.get("price")
 if not isinstance(amount,(int,float)) or isinstance(amount,bool) or amount<=0:raise ValueError("Нет номинала")
 return {"phone":phone,"buyer":name,"guest":field_value(lead,3185675),"greeting":field_value(lead,3830305),"number":field_value(lead,3135463),"nominal":amount,"start":start.strftime("%d.%m.%Y"),"until":until.strftime("%d.%m.%Y"),"start_iso":start.isoformat()}

def rendered(rule,data):
 value=rule.content.replace("{{buyer.name}}",data["buyer"])
 if "{{" in value:raise ValueError("Неизвестная переменная сообщения")
 if len(value)>4096:raise ValueError("Сообщение превышает лимит мессенджера")
 return value

def preview(db,c,doc,lead_id):
 if not lead_id:return {"total":0,"samples":[],"overlaps":[],"text":"Укажи ID сделки для проверки сообщения и PDF. Выпуск номера и отправка не выполняются."}
 lead=c.get(f"/api/v4/leads/{lead_id}",**{"with":"contacts"});data=data_for(c,lead);rule=m.Rule(**doc.draft)
 return {"samples":[{"lead_id":lead_id,"text":rendered(rule,data),"reason":"PDF: "+(data["number"] or "номер появится после выпуска")+". Проверка без отправки."}],"overlaps":[]}

def public_token(job,stamp=None):
 stamp=stamp or m.now();payload=f"{job.lead_id}:{int(stamp.timestamp())+8*3600}:{hashlib.sha256(job.pdf).hexdigest()}"
 encoded=base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
 signature=hmac.new(Settings().file_key.get_secret_value().encode(),encoded.encode(),hashlib.sha256).hexdigest()
 return encoded+"."+signature

def allowed_parts(job):return any(a["state"] in ("pending","sent","delivered","unknown","inflight") for a in job.attempts)

def save_attempt(db,job,index,**patch):
 rows=[dict(x) for x in job.attempts];rows[index].update(patch);job.attempts=rows;db.commit()

def reconcile(db,job):
 from app.reservation_reminders import ReminderEvent,SUCCESS
 rows=[dict(x) for x in job.attempts]
 for a in rows:
  if a.get("archived"):continue
  if a["state"]=="inflight":a["state"]="unknown";job.state="unknown";job.reason="Результат запроса до перезапуска неизвестен"
  if not a.get("message_id"):continue
  events=list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id==a["message_id"])))
  if any(e.status in ("delivered","read") for e in events):a["state"]="delivered"
  elif any(e.status in SUCCESS for e in events):a["state"]="sent"
  elif any(e.status=="error" for e in events) and a["state"] not in ("sent","delivered"):
   a["state"]="error";job.state="partial";job.reason="Провайдер сообщил об ошибке; требуется сверка, резерв не запускается"
 job.attempts=rows
 accepted=[a for a in rows if a["state"] in ("pending","sent","delivered")]
 if any(a["part"]=="pdf" for a in accepted) and any(a["part"]=="text" for a in accepted):
  job.state="delivered" if all(any(a["part"]==p and a["state"]=="delivered" for a in accepted) for p in ("text","pdf")) else "sent" if all(any(a["part"]==p and a["state"] in ("sent","delivered") for a in accepted) for p in ("text","pdf")) else "pending"
 db.commit()

def issue(lead_id):
 s=Settings()
 with httpx.Client(base_url=s.kassa_url,timeout=45,headers={"X-Certificate-Key":s.service_key.get_secret_value()}) as h:
  r=h.post("/api/internal/certificates/issue",json={"amoLeadId":lead_id})
  if r.status_code!=200:
   try:reason=str(r.json().get("message","Выпуск требует сверки"))
   except ValueError:reason="Выпуск требует сверки"
   raise ValueError("Касса: "+reason[:140])
  return r.json()

def process(db,c,job,stamp,issuer=issue):
 from app.reservation_reminders import ReminderSettings,REJECTED
 doc=db.get(m.Document,RULE_ID)
 if not doc or not doc.active or not doc.active["enabled"]:return
 rule=m.Rule(**doc.active)
 if job.state in ("unknown","partial","failed","sent","delivered","cancelled","pending"):return
 lead=c.get(f"/api/v4/leads/{job.lead_id}",**{"with":"contacts"})
 try:
  if lead.get("is_deleted") or lead.get("pipeline_id")!=9601214 or lead.get("status_id") not in (76675038,142) or field_value(lead,3828225).lower() not in ("true","1"):raise ValueError("Нет подтверждённого оплаченного электронного сертификата")
  cert=issuer(job.lead_id)
  data=data_for(c,lead)
  if job.snapshot and data["phone"]!=job.snapshot["data"]["phone"]:raise ValueError("Получатель изменился после начала отправки")
  if cert["status"]!="active" or cert["balance"]<=0:raise ValueError("Сертификат недействителен или использован")
  data.update(number=cert["number"],nominal=cert["nominal"])
  job.number=cert["number"];job.check_at=stamp+timedelta(seconds=60)
  hour,minute=map(int,rule.trigger.time.split(":"));start=datetime.strptime(data["start_iso"],"%Y-%m-%d").date()
  due=datetime.combine(start,datetime.min.time(),MSK).replace(hour=hour,minute=minute)
  job.due_at=due;db.commit()
  if stamp<due:return
  if not rule.window_start<=stamp.astimezone(MSK).hour<rule.window_end:return
  if not job.snapshot:
   from app.stage_notifications import certificate_plan,StageSettings,CalendarSettings
   routing=db.get(m.Document,"routing").active
   plan=certificate_plan(db,c,lead,StageSettings(),ReminderSettings(),CalendarSettings(),routing=routing)
   plan=[p for p in plan if p["transport"] in ("telegram","whatsapp","max")]
   job.pdf=render_pdf(data,rule.certificate_pdf);job.snapshot={"data":data,"text":rendered(rule,data),"plan":plan,"version":doc.published_version,"routing":routing};db.commit()
  channels={v["channelId"]:v for v in c.wz.get("/channels").json()}
  snap=job.snapshot
  for route in snap["plan"]:
   transport=route["transport"]
   attempted=[a for a in job.attempts if a["transport"]==transport]
   if any(a["state"]=="rejected" for a in attempted):continue
   if channels.get(route["channelId"],{}).get("state")!="active":continue
   if allowed_parts(job) and not attempted:continue
   for part in ("text","pdf"):
    prior=[a for a in attempted if a["part"]==part and not a.get("archived")]
    if prior:
     if prior[-1]["state"] in ("pending","sent","delivered"):continue
     return
    # Recheck disabling, source and recipient before every outbound request.
    db.refresh(doc)
    if not doc.active["enabled"]:return
    fresh=data_for(c,c.get(f"/api/v4/leads/{job.lead_id}",**{"with":"contacts"}))
    if fresh["phone"]!=snap["data"]["phone"]:raise ValueError("Получатель изменился перед отправкой")
    if part=="text" and fresh!=snap["data"]:
     # Number/amount come from authoritative registry, compare the mutable source fields.
     if any(fresh[k]!=snap["data"][k] for k in ("buyer","guest","greeting","start","until")):raise ValueError("Данные изменились перед отправкой; нужна повторная проверка")
    attempt={"part":part,"transport":transport,"state":"inflight","crm_id":f"gift-{job.lead_id}-{part}-{uuid.uuid4().hex}","message_id":""}
    job.attempts=[*job.attempts,attempt];job.state="sending";db.commit();index=len(job.attempts)-1
    payload={"channelId":route["channelId"],"chatType":transport,**route["recipient"],"crmMessageId":attempt["crm_id"],"clearUnanswered":False}
    if part=="text":payload["text"]=snap["text"]
    else:payload["contentUri"]=Settings().public_url.rstrip("/")+"/certificate-files/"+public_token(job)
    try:
     response=c.wz.post("/message",json=payload)
     try:result=response.json()
     except ValueError:result={}
     if response.status_code==201 and result.get("messageId"):
      save_attempt(db,job,index,state="pending",message_id=str(result["messageId"]));attempted.append({**attempt,"state":"pending"});continue
     code=str(result.get("error","")).upper()
     if 400<=response.status_code<500 and code in REJECTED:
      if part=="text" and not any(a["state"] in ("pending","sent","delivered") for a in job.attempts):
       save_attempt(db,job,index,state="rejected");job.state="queued";db.commit();break
      save_attempt(db,job,index,state="error");job.state="partial";job.reason="PDF не принят. Досылка только в выбранный мессенджер";db.commit();return
     save_attempt(db,job,index,state="unknown");job.state="unknown";job.reason="Ответ провайдера неоднозначен; повтор запрещён";db.commit();return
    except httpx.HTTPError:
     save_attempt(db,job,index,state="unknown");job.state="unknown";job.reason="Результат отправки неизвестен";db.commit();return
   else:
    job.state="pending";job.reason="";db.commit();return
  job.state="failed";job.reason="Нет доступного канала";db.commit()
 except ValueError as exc:
  job.state="partial" if allowed_parts(job) else "blocked";job.reason=str(exc)[:160];job.check_at=stamp+timedelta(seconds=60);db.commit()

def tick():
 from app.reservation_reminders import Clients,ReminderSettings
 if not Settings().ready():return
 with get_engine().connect() as lock,get_session_factory()() as db:
  if not lock.execute(text("SELECT pg_try_advisory_lock(674002025)")).scalar():return
  c=Clients(db,ReminderSettings())
  try:
   for old in db.scalars(select(CertificateMailJob).where(CertificateMailJob.state.in_(["queued","blocked"]),CertificateMailJob.attempted_at.is_(None))):
    if m.aware(old.event_at).timestamp()<Settings().activated_at:
     old_lead=c.get(f"/api/v4/leads/{old.lead_id}")
     try:old_start=datetime.fromtimestamp(int(field_value(old_lead,3829442)),MSK).date()
     except (ValueError,OverflowError,OSError):continue
     if old_start<datetime.fromtimestamp(Settings().activated_at,MSK).date():continue
    if not db.get(GiftJob,old.lead_id):db.add(GiftJob(lead_id=old.lead_id))
   # Kassa-created sales may enter Success directly; poll only issuances after activation.
   s=Settings()
   with httpx.Client(base_url=s.kassa_url,headers={"X-Certificate-Key":s.service_key.get_secret_value()},timeout=30) as h:
    response=h.get("/api/internal/certificates/pending",params={"since":s.activated_at});response.raise_for_status()
    for row in response.json()["items"]:
     if datetime.fromisoformat(row["created_at"].replace("Z","+00:00")).timestamp()<s.activated_at:continue
     lid=int(row["source_key"].rsplit(":",1)[1]);old=db.get(CertificateMailJob,lid)
     if old and old.attempted_at:continue
     if not db.get(GiftJob,lid):db.add(GiftJob(lead_id=lid))
   db.commit()
   for job in db.scalars(select(GiftJob).where(GiftJob.state.notin_(["delivered","cancelled","failed"])).order_by(GiftJob.check_at).limit(200)):
    try:
     due=m.aware(job.check_at)<=m.now()
     reconcile(db,job)
     if job.state not in ("queued","blocked","sending"):
      job.check_at=m.now()+timedelta(seconds=30);db.commit()
     if job.lead_id>0 and job.state in ("queued","blocked","sending") and due:process(db,c,job,m.now())
    except Exception as exc:db.rollback();log.error("Certificate gift %s: %s",job.lead_id,type(exc).__name__)
  finally:c.close();lock.execute(text("SELECT pg_advisory_unlock(674002025)"))
async def worker():
 while True:
  try:await asyncio.to_thread(tick)
  except Exception as exc:log.error("Certificate gifts: %s",type(exc).__name__)
  await asyncio.sleep(15)

@router.get("/certificate-files/{token}")
def download(token:str):
 try:
  if len(Settings().file_key.get_secret_value())<32:raise ValueError()
  encoded,signature=token.split(".")
  expected=hmac.new(Settings().file_key.get_secret_value().encode(),encoded.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(signature,expected):raise ValueError()
  payload=base64.urlsafe_b64decode(encoded+"="*(-len(encoded)%4)).decode();lid,expiry,digest=payload.split(":")
  if int(expiry)<int(m.now().timestamp()):raise ValueError()
  with get_session_factory()() as db:
   job=db.get(GiftJob,int(lid))
   if not job or not job.pdf or not hmac.compare_digest(digest,hashlib.sha256(job.pdf).hexdigest()):raise ValueError()
   return Response(job.pdf,media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="Mansband-certificate.pdf"',"Cache-Control":"private, no-store"})
 except (ValueError,UnicodeError):raise HTTPException(404)

@router.get("/internal/templates/gift/preview/{lead_id}",dependencies=[Depends(m.authorized)])
def preview_pdf(lead_id:int,db=Depends(m.session),c=Depends(m.client)):
 d=db.get(m.Document,RULE_ID);data=data_for(c,c.get(f"/api/v4/leads/{lead_id}",**{"with":"contacts"})) if lead_id else demo_data()
 data["number"]=data["number"] or "ПРЕДПРОСМОТР"
 return Response(render_pdf(data,d.draft.get("certificate_pdf",{})),media_type="application/pdf",headers={"Cache-Control":"no-store"})

@router.post("/internal/templates/gift/retry-pdf/{lead_id}",dependencies=[Depends(m.authorized),Depends(m.mutation_lock)])
def retry_pdf(lead_id:int,body:dict,db=Depends(m.session)):
 job=db.get(GiftJob,lead_id)
 if not body.get("confirmed") or not job or job.state!="partial":raise HTTPException(409,"Нужна подтверждённая ошибка PDF")
 failed=[a for a in job.attempts if a["part"]=="pdf" and not a.get("archived")]
 if not failed or any(a["state"]!="error" for a in failed):raise HTTPException(409,"Неизвестный результат не разрешает повтор")
 job.attempts=[{**a,"archived":True} if a in failed else a for a in job.attempts];job.state="queued";job.reason="Досылка PDF согласована сотрудником";job.check_at=m.now();db.commit()
 m.audit(db,db.get(m.Document,RULE_ID),str(body.get("actor","admin")),"retry_pdf",{"lead_id":lead_id});db.commit()
 return {"state":job.state}

@router.post("/internal/templates/gift/test",dependencies=[Depends(m.authorized),Depends(m.mutation_lock)])
def test_package(body:GiftTest,db=Depends(m.session),c=Depends(m.client)):
 from app.reservation_reminders import ReminderSettings
 import re
 doc=db.get(m.Document,RULE_ID)
 if not body.confirmed or body.version!=doc.version or doc.checked!=m.digest([doc.version,doc.draft]):raise HTTPException(409,"Проверь текущую версию и подтверди тест")
 opid="gift-test-"+body.operation_id
 if db.get(m.Operation,opid):raise HTTPException(409,"Этот тест уже запущен, повтор запрещён")
 destination=body.destination.strip()
 if body.transport=="telegram" and re.fullmatch(r"@?[a-zA-Z][a-zA-Z0-9_]{4,31}",destination):recipient={"username":destination.lstrip("@")}
 else:
  from app.stage_notifications import normalize_recipient_phone,phone_destination
  try:recipient=phone_destination(normalize_recipient_phone(destination),body.transport)
  except ValueError:raise HTTPException(422,"Некорректный тестовый получатель")
 data=data_for(c,c.get(f"/api/v4/leads/{body.lead_id}",**{"with":"contacts"})) if body.lead_id else demo_data();data["number"]="ТЕСТ, НЕ ДЛЯ СПИСАНИЯ"
 rule=m.Rule(**doc.draft);rendered_text="ТЕСТ MANSBAND.\n\n"+rendered(rule,data)
 job=GiftJob(lead_id=-int(uuid.uuid4().hex[:7],16),state="queued",number="TEST",pdf=render_pdf(data,rule.certificate_pdf),snapshot={"data":data,"text":rendered_text,"source_lead":body.lead_id})
 db.add(job);db.add(m.Operation(id=opid,digest=m.digest(body.model_dump()),state="inflight",result={"job_id":job.lead_id}));db.commit()
 settings=ReminderSettings();channel=getattr(settings,body.transport+"_channel")
 for part in ("text","pdf"):
  attempt={"part":part,"transport":body.transport,"state":"inflight","crm_id":opid+"-"+part,"message_id":""};job.attempts=[*job.attempts,attempt];db.commit();index=len(job.attempts)-1
  payload={"channelId":channel,"chatType":body.transport,**recipient,"crmMessageId":attempt["crm_id"],"clearUnanswered":False}
  if part=="text":payload["text"]=rendered_text
  else:payload["contentUri"]=Settings().public_url.rstrip("/")+"/certificate-files/"+public_token(job)
  try:
   response=c.wz.post("/message",json=payload);result=response.json()
   if response.status_code==201 and result.get("messageId"):save_attempt(db,job,index,state="pending",message_id=str(result["messageId"]));continue
   save_attempt(db,job,index,state="unknown");job.state="unknown";break
  except (httpx.HTTPError,ValueError):save_attempt(db,job,index,state="unknown");job.state="unknown";break
 else:job.state="pending"
 op=db.get(m.Operation,opid);op.state="done";db.commit();return {"job_id":job.lead_id,"state":job.state}

def journal(db):
 items=[]
 labels={"queued":"Запланировано","blocked":"Заблокировано","sending":"Начата отправка","unknown":"Результат неизвестен","partial":"Требуется дослать PDF","pending":"Принято провайдером","sent":"Отправлено","delivered":"Доставлено","failed":"Ошибка","cancelled":"Отменено"}
 for j in db.scalars(select(GiftJob).order_by(GiftJob.created_at.desc()).limit(100)):
  items.append({"id":-abs(j.lead_id)-1000000000,"rule_id":RULE_ID,"lead_id":j.snapshot.get("source_lead",j.lead_id) if j.snapshot else j.lead_id,"due_at":m.aware(j.due_at or j.created_at).isoformat(),"state":j.state,"label":labels.get(j.state,j.state),"reason":j.reason,"gift_lead_id":j.lead_id,"attempts":[{"channel":a["transport"],"state":a["part"]+": "+a["state"],"reason":"предыдущая попытка" if a.get("archived") else ""} for a in j.attempts]})
 return items
