"""MANSBAND campaign ledger. Default disabled, pilot restricted to explicit IDs.

At-most-once provider claims; ambiguous outcomes require operator reconciliation.
Campaign identity survives template changes and CRM pipeline movement.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import Integer, String, JSON, DateTime, Boolean, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base
from app.database import get_engine, get_session_factory

MSK = ZoneInfo('Europe/Moscow')
CAMPAIGN = 'collection-2026-11-10'
LOCK = 674001944
SHEET = 'https://docs.google.com/spreadsheets/d/1l9x4zC_AsiKZXcSRyraqfd36HHvepKZwHyyD8doSXLQ/export?format=csv&gid=0'
OWNERS = {'Нина':14292162, 'Ангелина':14077886}
ABSENT = {'CHANNEL_TGAPI_PHONE_NOT_OCCUPIED', 'CHANNEL_MAX_PHONE_NOT_OCCUPIED'}
SUCCESS = {'sent','delivered','read'}

def now(): return datetime.now(timezone.utc)
def aware(x): return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x
def phone(x):
    x = re.sub(r'\D','',str(x))
    return '7'+x[1:] if len(x)==11 and x.startswith('8') else x

def key(x): return hashlib.sha256(phone(x).encode()).hexdigest()

class Recipient(Base):
    __tablename__ = 'campaign_recipient'
    __table_args__ = (UniqueConstraint('campaign','person'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign: Mapped[str] = mapped_column(String(80), default=CAMPAIGN)
    person: Mapped[str] = mapped_column(String(64), index=True)
    contact_id: Mapped[int] = mapped_column(Integer, default=0)
    lead_id: Mapped[int] = mapped_column(Integer, default=0)
    segment: Mapped[int] = mapped_column(Integer)
    test: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(32), default='prepared')
    reason: Mapped[str] = mapped_column(String(100), default='')
    snapshot: Mapped[dict] = mapped_column(JSON)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    classification: Mapped[str] = mapped_column(String(30), default='')
    moved: Mapped[bool] = mapped_column(Boolean, default=False)

class Part(Base):
    __tablename__ = 'campaign_part'
    __table_args__ = (UniqueConstraint('recipient_id','position','kind'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(24))
    message_id: Mapped[str] = mapped_column(String(100), default='', index=True)
    crm_id: Mapped[str] = mapped_column(String(100), unique=True)
    reason: Mapped[str] = mapped_column(String(100), default='')
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ResponseEvent(Base):
    __tablename__ = 'campaign_response'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    recipient_id: Mapped[int] = mapped_column(Integer, index=True)
    classification: Mapped[str] = mapped_column(String(30))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class Control(Base):
    __tablename__ = 'campaign_control'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

class Notification(Base):
    __tablename__ = 'campaign_notification'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    body: Mapped[str] = mapped_column(String(4000))
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Purchase(Base):
    __tablename__ = 'campaign_purchase'
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    recipient_id: Mapped[int] = mapped_column(Integer, index=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    linked: Mapped[bool] = mapped_column(Boolean, default=False)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    refund_kopecks: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

ASSET_NAME = 'campaign-bc57fcda6de49e50e314.jpg'
ASSET_SHA256 = 'bc57fcda6de49e50e3146c50c6c06c18b9b83e667f78b5afbf34f101f280e692'
from fastapi import APIRouter
from fastapi.responses import FileResponse
router = APIRouter()
@router.get('/certificate-files/'+ASSET_NAME)
def campaign_image():
    return FileResponse(Path(__file__).parent/'assets'/ASSET_NAME, media_type='image/jpeg')

TABLES = [x.__table__ for x in (Recipient,Part,ResponseEvent,Control,Notification,Purchase)]

def init(): Base.metadata.create_all(get_engine(), tables=TABLES)

def classify(value):
    t = re.sub(r'\s+',' ',str(value or '').lower().replace('ё','е')).strip()
    if any(x in t for x in ('не пишите','не присылайте','удалите мой номер','отпишите','отписаться')) or re.fullmatch(r'стоп[.! ]*',t): return 'unsubscribe'
    refusal = any(x in t for x in ('не нужно','не актуально','неактуально','не интересно','неинтересно','не планирую','пока не надо','спасибо не надо'))
    interest = bool(re.search(r'\b(размер\w*|цен[ауые]|стоимость|доставк\w*|наличи\w*|примерк\w*|записаться|подберите|адрес|купить|хочу)\b',t))
    if refusal and interest: return 'review'
    if refusal: return 'refusal'
    if interest: return 'interest'
    return 'review'

def parse_templates(source):
    parts = re.split(r'### \*\*Вариант (\d+)\*\*\s*',source)
    result = {int(parts[i]):re.sub(r'\\([!_&:.])',r'\1',parts[i+1]).strip() for i in range(1,len(parts),2)}
    if set(result)!=set(range(1,16)): raise ValueError('expected_15_source_templates')
    return result

def render(template, name, segment, stamp, neutral=False):
    if not 1 <= segment <= 6: raise ValueError('invalid_segment')
    result=template.replace('{ссылка}',f'https://mansband.ru/selection/collection-202609?utm_source=sms&utm_medium=phon&utm_campaign=auditor_{segment}')
    if not name.strip():
        result='Здравствуйте!\n'+result.split('\n',1)[1]
    else: result=result.replace('{Имя}',name.strip())
    greeting='Здравствуйте' if neutral else ('Добрый вечер' if stamp.astimezone(MSK).hour>=18 else 'Добрый день')
    result=result.replace('Добрый день',greeting).replace('добрый день',greeting.lower())
    # Owner-approved formatting: blank line after the campaign link.
    result=re.sub(r'(https://mansband\.ru/selection/collection-202609\?utm_source=sms&utm_medium=phon&utm_campaign=auditor_[1-6])[^\S\n]*\n+',r'\1\n\n',result)
    if '{' in result or '}' in result: raise ValueError('unfilled_placeholder')
    return '\n'.join(line.rstrip() for line in result.splitlines())

def duty(stamp):
    r=httpx.get(SHEET,follow_redirects=True,timeout=20);r.raise_for_status()
    rows=list(csv.reader(io.StringIO(r.text)))
    if len(rows)<3 or rows[1][:4]!=['Дата','День','Нина','Ангелина']: raise ValueError('shift_headers_changed')
    matches=[x for x in rows[2:] if x and x[0]==stamp.astimezone(MSK).strftime('%d.%m.%Y')]
    if len(matches)!=1: raise ValueError('shift_date_missing_or_duplicate')
    chosen=[rows[1][i] for i in (2,3) if len(matches[0])>i and matches[0][i].upper()=='TRUE']
    if len(chosen)!=1: raise ValueError('shift_not_unique')
    return OWNERS[chosen[0]]

def notice(db, identity, body):
    if db.get(Notification,identity) is None: db.add(Notification(id=identity,body=body))
    db.commit()

def stop(db,j,reason):
    j.state,j.reason='attention',reason[:100];db.commit()
    notice(db,f'contact-{j.id}-{reason}'[:100],f'MANSBAND · Рассылка от 10.11\nТребует проверки: {reason}\nОтправка контакту остановлена.\nДействие: проверить результат, не повторять вслепую.\nhttps://mansband.amocrm.ru/leads/detail/{j.lead_id}\n{now().astimezone(MSK):%d.%m %H:%M} МСК')

def record_messages(db,body):
    for m in body.get('messages') or []:
        if m.get('isEcho') is True and m.get('messageId') and m.get('chatId'):
            p=db.scalar(select(Part).where(Part.message_id==str(m['messageId'])))
            if p:
                j=db.get(Recipient,p.recipient_id)
                snapshot=json.loads(json.dumps(j.snapshot))
                snapshot['routes'][p.position]['resolved_chat_id']=str(m['chatId'])
                j.snapshot=snapshot;db.commit()
    # The raw text is classified but not retained. Source message ID is the audit pointer.
    for m in body.get('messages') or []:
        if m.get('isEcho') is not False or m.get('isDeleted') or m.get('isEdited'): continue
        mid=str(m.get('messageId') or '')
        if not mid or len(mid)>100 or db.get(ResponseEvent,mid): continue
        try: stamp=datetime.fromisoformat(str(m['dateTime']).replace('Z','+00:00'))
        except (KeyError,ValueError): continue
        if stamp.tzinfo is None or stamp>now()+timedelta(minutes=5): continue
        matches=[]
        for j in db.scalars(select(Recipient)):
            started=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.kind=='text',Part.state.in_(['pending','sent','unknown'])).order_by(Part.at))
            if not started or stamp<aware(started.at): continue
            for route in j.snapshot['routes']:
                ids={str(v) for v in route['recipient'].values()} | {route.get('resolved_chat_id','')}
                if route['channelId']==m.get('channelId') and route['transport']==m.get('chatType') and str(m.get('chatId')) in ids:
                    matches.append(j);break
        if len(matches)!=1: continue
        j=matches[0]; category=classify(m.get('text'))
        db.add(ResponseEvent(id=mid,recipient_id=j.id,classification=category,at=stamp))
        if not j.replied_at: j.replied_at=stamp
        override=db.get(Control,'classification-'+str(j.id))
        if j.classification!='unsubscribe' and not override: j.classification=category
        if category=='unsubscribe':
            row=db.get(Control,'unsubscribe-'+j.person)
            if row is None: db.add(Control(id='unsubscribe-'+j.person,data={'at':stamp.isoformat()}))
        db.commit()

def reconcile(db):
    from app.reservation_reminders import ReminderEvent
    for p in db.scalars(select(Part).where(Part.state.in_(['inflight','pending','unknown']))):
        j=db.get(Recipient,p.recipient_id)
        if not p.message_id:
            p.state='unknown';db.commit();stop(db,j,'interrupted_or_unknown_send');continue
        events=list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id==p.message_id)))
        if any(e.status in SUCCESS for e in events):
            p.state='sent'
            if p.kind=='text':
                j.sent_at=j.sent_at or p.at
                j.state='photo_ready'
            else: j.state='complete'
            db.commit()
        elif any(e.status=='error' for e in events):
            p.state='error';db.commit();stop(db,j,'provider_delivery_error')
        elif now()-aware(p.at)>timedelta(minutes=5): stop(db,j,'provider_confirmation_missing')

def send_part(db,j,c,position,kind,asset):
    db.refresh(j)
    if j.replied_at or db.get(Control,'unsubscribe-'+j.person):
        j.state='reply_handoff';db.commit();return 'halt'
    other=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.kind=='text',Part.position!=position,Part.state!='absent'))
    if other:return 'halt'
    if kind=='photo':
        sent=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.position==position,Part.kind=='text',Part.state=='sent'))
        if not sent or now()-aware(sent.at)<timedelta(seconds=15):return 'halt'
    route=j.snapshot['routes'][position]
    prior=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.position==position,Part.kind==kind))
    if prior: return prior.state
    channels=c.wz.get('/channels');channels.raise_for_status()
    channel=next((x for x in channels.json() if x['channelId']==route['channelId']),{})
    if channel.get('state')!='active':
        if not db.get(Control,'campaign-halt'):db.add(Control(id='campaign-halt',data={'reason':'channel_not_active','at':now().isoformat()}))
        stop(db,j,'channel_not_active');return 'halt'
    if channel.get('transport')!=('tgapi' if route['transport']=='telegram' else route['transport']):
        stop(db,j,'channel_transport_mismatch');return 'halt'
    p=Part(recipient_id=j.id,position=position,kind=kind,state='inflight',crm_id=f'{CAMPAIGN}-{j.id}-{position}-{kind}')
    db.add(p);j.state='inflight';db.commit()
    payload={'channelId':route['channelId'],'chatType':route['transport'],**route['recipient'],'clearUnanswered':False,'crmMessageId':p.crm_id}
    payload.update({'text':j.snapshot['text']} if kind=='text' else {'contentUri':asset})
    try:
        r=c.wz.post('/message',json=payload)
        try: data=r.json()
        except ValueError: data={}
        if not isinstance(data,dict):data={}
        code=str(data.get('error',''))
        if r.status_code==201 and data.get('messageId'):
            p.state='pending';p.message_id=str(data['messageId']);j.state='pending';db.commit();return 'pending'
        if 400<=r.status_code<500 and code in ABSENT and kind=='text':
            p.state='absent';p.reason=code;j.state='prepared';db.commit();return 'absent'
        if code in {'MESSAGES_IS_SPAM','CHANNEL_BLOCKED','CHANNEL_LIMIT_EXCEEDED'} or r.status_code in (401,403,429):
            if not db.get(Control,'campaign-halt'):db.add(Control(id='campaign-halt',data={'reason':code or str(r.status_code),'at':now().isoformat()}))
        p.state='unknown';p.reason=f'http_{r.status_code}';db.commit()
        stop(db,j,code if re.fullmatch('[A-Z_]{1,90}',code) else p.reason)
    except httpx.HTTPError:
        p.state='unknown';db.commit();stop(db,j,'network_result_unknown')
    return 'halt'

def dispatch(db,j,c,asset):
    if j.state not in {'prepared','photo_ready'} or aware(j.due_at)>now():return
    # Until production eligibility/automation audit is complete, fail closed.
    if db.get(Control,'campaign-halt'):return
    ctl=db.get(Control,'pilot')
    if not j.test or not ctl or j.person not in ctl.data.get('allowlist',[]):return
    local=now().astimezone(MSK)
    if local.date()>date(2026,12,31):stop(db,j,'offer_expired');return
    if not (10<=local.hour<11 or 17<=local.hour<20):return
    if db.get(Control,'unsubscribe-'+j.person) or j.replied_at:
        j.state='reply_handoff';db.commit();return
    if not j.lead_id:stop(db,j,'missing_campaign_deal');return
    pace=db.get(Control,'pace')
    if j.state=='prepared' and pace and now()<datetime.fromisoformat(pace.data['next']):return
    owner=duty(now())
    lead=c.get(f'/api/v4/leads/{j.lead_id}')
    if lead.get('pipeline_id')!=10730010 or lead.get('status_id')!=89984117:
        stop(db,j,'campaign_stage_changed');return
    if lead.get('responsible_user_id')!=owner:
        r=c.amo.patch(f'/api/v4/leads/{j.lead_id}',json={'responsible_user_id':owner});r.raise_for_status()
    sent=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.kind=='text',Part.state=='sent'))
    if sent:
        if now()-aware(sent.at)<timedelta(seconds=15):return
        if aware(sent.at).astimezone(MSK).date()!=local.date():stop(db,j,'photo_next_day_forbidden');return
        send_part(db,j,c,sent.position,'photo',asset);return
    # Persisted variable gap, shared by all pilot recipients, no catch-up burst.
    pace=db.get(Control,'pace')
    if pace and now()<datetime.fromisoformat(pace.data['next']):return
    if not pace:pace=Control(id='pace',data={});db.add(pace)
    pace.data={'next':(now()+timedelta(seconds=300+secrets.randbelow(121))).isoformat()};db.commit()
    for pos,route in enumerate(j.snapshot['routes']):
        if send_part(db,j,c,pos,'text',asset)!='absent':return
    stop(db,j,'all_recipients_absent')

def handle_replies(db,c):
    for j in db.scalars(select(Recipient).where(Recipient.replied_at.is_not(None),Recipient.moved==False)):
        lead=c.get(f'/api/v4/leads/{j.lead_id}')
        if lead.get('pipeline_id')==9601214:
            j.moved=True;db.commit();continue
        if lead.get('pipeline_id')!=10730010 or lead.get('status_id')!=89984117:
            notice(db,f'move-{j.id}',f'MANSBAND · Ответ на рассылку\nЭтап уже изменён. Проверь сделку:\nhttps://mansband.amocrm.ru/leads/detail/{j.lead_id}');continue
        owner=duty(now())
        r=c.amo.patch(f'/api/v4/leads/{j.lead_id}',json={'pipeline_id':9601214,'status_id':76654882,'responsible_user_id':owner});r.raise_for_status()
        j.moved=True;db.commit()

def detailed_report(db, tests=False, day=None):
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==tests)))
    if day:
        jobs=[j for j in jobs if j.sent_at and aware(j.sent_at).astimezone(MSK).date()==day]
    ids={j.id for j in jobs}
    parts=list(db.scalars(select(Part).where(Part.recipient_id.in_(ids)))) if ids else []
    text_sent=[j for j in jobs if j.sent_at]
    complete=sum(any(p.recipient_id==j.id and p.kind=='photo' and p.state=='sent' for p in parts) for j in text_sent)
    by_channel={}
    for p in parts:
        if p.kind=='text' and p.state=='sent':
            j=next(j for j in jobs if j.id==p.recipient_id)
            transport=j.snapshot['routes'][p.position]['transport']
            by_channel[transport]=by_channel.get(transport,0)+1
    purchases=list(db.scalars(select(Purchase).where(Purchase.recipient_id.in_(ids)))) if ids else []
    return {'recipients':len(jobs),'text_sent':len(text_sent),'complete':complete,
        'partial':len(text_sent)-complete,'replied':sum(bool(j.replied_at) for j in jobs),
        'responses':{v:sum(j.classification==v for j in jobs) for v in ('interest','refusal','unsubscribe','review')},
        'channels':by_channel,'segments':{str(n):sum(j.segment==n and bool(j.sent_at) for j in jobs) for n in range(1,7)},
        'chat_history':{v:sum(j.snapshot.get('chat_history','unverified')==v and bool(j.sent_at) for j in jobs) for v in ('existing','new','unverified')},
        'observed_purchases':len(purchases),'linked_purchases':sum(p.linked for p in purchases),
        'gross_kopecks':sum(p.amount_kopecks for p in purchases),'refund_kopecks':sum(p.refund_kopecks for p in purchases),
        'net_kopecks':sum(p.amount_kopecks-p.refund_kopecks for p in purchases),
        'purchase_sync_verified':bool(db.get(Control,'purchase-sync-verified')),
        'attention':sum(j.state=='attention' for j in jobs),
        'reply_rate':round(100*sum(bool(j.replied_at) for j in text_sent)/len(text_sent),1) if text_sent else None}


def sync_reply_notes(db,c):
    for e in db.scalars(select(ResponseEvent)):
        identity='reply-note-'+hashlib.sha256(e.id.encode()).hexdigest()
        if db.get(Control,identity):continue
        j=db.get(Recipient,e.recipient_id)
        if not j or not j.lead_id:continue
        labels={'interest':'Интерес','refusal':'Отказ','unsubscribe':'Отписка','review':'Требует разбора'}
        db.add(Control(id=identity,data={'state':'claimed','at':now().isoformat()}));db.commit()
        try:
            r=c.amo.post(f'/api/v4/leads/{j.lead_id}/notes',json=[{'note_type':'common','params':{'text':f'Рассылка от 10.11 · {labels[e.classification]}\nКампания: {CAMPAIGN}\nПолучатель кампании: {j.id}\nВремя ответа: {aware(e.at).astimezone(MSK):%d.%m.%Y %H:%M} МСК\nID сообщения: {e.id}'+('\nТЕСТ: исключён из статистики клиентов.' if j.test else '')}}]);r.raise_for_status()
            row=db.get(Control,identity);row.data={'state':'sent','at':now().isoformat()};db.commit()
        except httpx.HTTPError:
            notice(db,'note-error-'+str(j.id),f'MANSBAND · Ответ учтён на сервере\nНе подтверждена запись заметки в CRM. Не повторяем автоматически.\nhttps://mansband.amocrm.ru/leads/detail/{j.lead_id}')


def report(db,tests=False):
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==tests)))
    counts={s:sum(j.state==s for j in jobs) for s in {'complete','attention','pending','prepared','reply_handoff'}}
    counts.update(recipients=len(jobs),text_sent=sum(bool(j.sent_at) for j in jobs),replied=sum(bool(j.replied_at) for j in jobs))
    counts['responses']={s:sum(j.classification==s for j in jobs) for s in ('interest','refusal','unsubscribe','review')}
    return counts

def tick(asset):
    from app.reservation_reminders import Clients,ReminderSettings
    from app.calltrack.telegram import send as telegram
    with get_engine().connect() as conn:
        if not conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK}):return
        try:
            with get_session_factory()() as db:
                c=Clients(db,ReminderSettings())
                try:
                    reconcile(db);handle_replies(db,c);sync_reply_notes(db,c)
                    for j in db.scalars(select(Recipient).where(Recipient.state.in_(['prepared','photo_ready'])).order_by(Recipient.due_at)):
                        dispatch(db,j,c,asset)
                    local=now().astimezone(MSK)
                    if (local.hour,local.minute)>=(20,15):
                        total=detailed_report(db)
                        today=detailed_report(db,day=local.date())
                        tests=detailed_report(db,tests=True)
                        cat=total['responses']
                        purchase_line=(f"Покупки после рассылки: {total['observed_purchases']}; связанные: {total['linked_purchases']}; чистая сумма {total['net_kopecks']/100:.2f} ₽" if total['purchase_sync_verified'] else 'Покупки: автоматическая сверка не подключена, нулевую конверсию не утверждаем.')
                        notice(db,'daily-'+str(local.date()),f"MANSBAND · Рассылка от 10.11\n{local:%d.%m %H:%M} МСК\n"
                            f"Сегодня: текст {today['text_sent']}, полный комплект {today['complete']}, частично {today['partial']}.\n"
                            f"Всего: текст {total['text_sent']}, полный комплект {total['complete']}.\n"
                            f"Ответили {total['replied']}; интерес {cat['interest']}; отказ {cat['refusal']}; отписка {cat['unsubscribe']}; разобрать {cat['review']}.\n"
                            f"Каналы: {total['channels'] or 'нет отправок клиентам'}.\n"
                            f"Сегменты 1–6: {', '.join(str(total['segments'][str(n)]) for n in range(1,7))}.\n"
                            f"Диалоги: новых {total['chat_history']['new']}, существующих {total['chat_history']['existing']}, не проверено {total['chat_history']['unverified']}.\n"
                            f"{purchase_line}\nПроблемы: {total['attention']}. Тесты: {tests['complete']}/{tests['recipients']} комплектов.\n"
                            'Далее: клиентский пилот после технической приёмки. Массовая очередь выключена.')
                    heartbeat=db.get(Control,'heartbeat')
                    if not heartbeat:heartbeat=Control(id='heartbeat',data={});db.add(heartbeat)
                    heartbeat.data={'at':now().isoformat()};db.commit()
                    for n in db.scalars(select(Notification).where(Notification.sent==False).order_by(Notification.at).limit(5)):
                        if telegram(n.body):n.sent=True;db.commit()
                finally:c.close()
        finally:conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':LOCK})

from fastapi import Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Literal
import hmac

def admin(x_reminder_key: str = Header(default='')):
    from app.reservation_reminders import ReminderSettings
    secret=ReminderSettings().admin_key.get_secret_value()
    if not secret or not hmac.compare_digest(secret,x_reminder_key):raise HTTPException(403)

class ClassificationChange(BaseModel):
    category: Literal['interest','refusal','unsubscribe','review']
    reason: str

@router.get('/certificate-files/campaign-health')
def campaign_health():
    with get_session_factory()() as db:
        heartbeat=db.get(Control,'heartbeat')
        stamp=datetime.fromisoformat(heartbeat.data['at']) if heartbeat else None
        fresh=bool(stamp and now()-stamp<timedelta(minutes=3))
        return {'ok':fresh,'mode':'pilot_only','checked_at':stamp.isoformat() if stamp else None}

@router.get('/internal/templates/campaign/stats',dependencies=[Depends(admin)])
def campaign_stats(tests: bool=False):
    with get_session_factory()() as db:
        return {'campaign':CAMPAIGN,'mode':'pilot_only','today':detailed_report(db,tests,now().astimezone(MSK).date()),'total':detailed_report(db,tests)}

@router.post('/internal/templates/campaign/recipients/{recipient_id}/classification',dependencies=[Depends(admin)])
def change_classification(recipient_id: int,body: ClassificationChange):
    if not body.reason.strip() or len(body.reason)>500:raise HTTPException(400,'Укажи причину, до 500 символов')
    with get_session_factory()() as db:
        j=db.get(Recipient,recipient_id)
        if not j:raise HTTPException(404)
        if j.classification=='unsubscribe' and body.category!='unsubscribe':raise HTTPException(409,'Отписка требует отдельной проверки согласия')
        row=db.get(Control,'classification-'+str(j.id))
        if not row:row=Control(id='classification-'+str(j.id),data={});db.add(row)
        row.data={'category':body.category,'reason':body.reason,'at':now().isoformat(),'previous':j.classification}
        j.classification=body.category
        db.add(ResponseEvent(id='manual-'+secrets.token_hex(16),recipient_id=j.id,classification=body.category,at=now()))
        if body.category=='unsubscribe' and not db.get(Control,'unsubscribe-'+j.person):db.add(Control(id='unsubscribe-'+j.person,data={'at':now().isoformat()}))
        db.commit()
        return {'id':j.id,'classification':j.classification,'manual':True}


def run():
    import time
    init()
    asset=os.environ.get('CAMPAIGN_ASSET_URL','')
    if not asset:raise SystemExit('CAMPAIGN_ASSET_URL required')
    while True:
        try:tick(asset)
        except Exception as exc:
            # Never print provider payloads or credentials.
            with get_session_factory()() as db:
                notice(db,'worker-'+type(exc).__name__,f'MANSBAND · Рассылка остановлена на проверку\nОшибка обработчика: {type(exc).__name__}\nНужна техническая проверка. Клиентские сообщения автоматически не повторяются.')
                from app.calltrack.telegram import send as telegram
                for n in db.scalars(select(Notification).where(Notification.sent==False).limit(3)):
                    if telegram(n.body):n.sent=True;db.commit()
        time.sleep(10)
