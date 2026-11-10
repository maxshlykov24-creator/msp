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
from sqlalchemy import Integer, String, JSON, DateTime, Boolean, UniqueConstraint, select, text, func, case, or_
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
def campaign_windows(day):
    if day.weekday()<4:return ((9,14),(17,18),(19,20))
    if day.weekday()==4:return ((10,14),(16,18))
    return ((10,14),(17,19))

def in_campaign_window(stamp, *, starting=False):
    local=stamp.astimezone(MSK)
    for start,end in campaign_windows(local.date()):
        if start<=local.hour<end:
            return not (starting and local.hour==end-1 and local.minute>=59)
    return False

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
    for msg in body.get('messages') or []:
        mid=str(msg.get('messageId') or '')
        if msg.get('isEcho') is not True or not mid or len(mid)>100:continue
        try:stamp=datetime.fromisoformat(str(msg['dateTime']).replace('Z','+00:00'))
        except (KeyError,ValueError):continue
        if stamp.tzinfo is None:continue
        if not db.get(ChannelUsage,mid):
            db.add(ChannelUsage(id=mid,channel=str(msg.get('channelId','')),transport=str(msg.get('chatType','')),at=stamp));db.commit()
    for m in body.get('messages') or []:
        if m.get('isEcho') is True and m.get('messageId') and m.get('chatId'):
            p=db.scalar(select(Part).where(Part.message_id==str(m['messageId'])))
            if p:
                j=db.get(Recipient,p.recipient_id)
                snapshot=json.loads(json.dumps(j.snapshot))
                snapshot['routes'][p.position]['resolved_chat_id']=str(m['chatId'])
                j.snapshot=snapshot
                identity=identity_key(str(m.get('chatType'))+':chatId',str(m['chatId']))
                claimed=db.get(Identity,identity)
                if claimed and claimed.recipient_id!=j.id:
                    if not db.get(Control,'campaign-halt'):db.add(Control(id='campaign-halt',data={'reason':'resolved_chat_identity_conflict','at':now().isoformat()}))
                elif not claimed:db.add(Identity(id=identity,recipient_id=j.id))
                db.commit()
    # The raw text is classified but not retained. Source message ID is the audit pointer.
    for m in body.get('messages') or []:
        if m.get('isEcho') is not False or m.get('isDeleted') or m.get('isEdited'): continue
        mid=str(m.get('messageId') or '')
        if not mid or len(mid)>100 or db.get(ResponseEvent,mid): continue
        try: stamp=datetime.fromisoformat(str(m['dateTime']).replace('Z','+00:00'))
        except (KeyError,ValueError): continue
        if stamp.tzinfo is None or stamp>now()+timedelta(minutes=5): continue
        matches=[]
        identity_ids=[identity_key(str(m.get('chatType'))+':'+k,str(m.get('chatId'))) for k in ('chatId','phone','username')]
        candidate_ids=set(db.scalars(select(Identity.recipient_id).where(Identity.id.in_(identity_ids))))
        candidate_ids.update(db.scalars(select(Recipient.id).where(Recipient.test==True)))
        for j in db.scalars(select(Recipient).where(Recipient.id.in_(candidate_ids))):
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
            j.classification='unsubscribe'
            row=db.get(Control,'unsubscribe-'+j.person)
            if row is None: db.add(Control(id='unsubscribe-'+j.person,data={'at':stamp.isoformat()}))
        db.commit()

def reconcile(db):
    from app.reservation_reminders import ReminderEvent
    for j in db.scalars(select(Recipient).where(Recipient.state=='crm_inflight')):
        stamp=j.snapshot.get('crm_claim_at')
        if stamp and now()-datetime.fromisoformat(stamp)>timedelta(minutes=5):stop(db,j,'crm_creation_requires_reconciliation')
    for p in db.scalars(select(Part).where(Part.state.in_(['inflight','pending','unknown']))):
        j=db.get(Recipient,p.recipient_id)
        if not p.message_id:
            p.state='unknown';db.commit();stop(db,j,'interrupted_or_unknown_send');continue
        events=list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id==p.message_id)))
        if any(e.status in SUCCESS for e in events):
            p.state='sent'
            if p.kind=='text':
                j.sent_at=j.sent_at or p.at
                if not db.get(Control,'confirmed-'+p.crm_id):
                    db.add(Control(id='confirmed-'+p.crm_id,data={'at':now().isoformat()}))
                j.state='photo_ready'
            else: j.state='complete'
            db.commit()
        elif any(e.status=='error' for e in events):
            if any(e.error_code in {'MESSAGES_IS_SPAM','CHANNEL_BLOCKED','CHANNEL_LIMIT_EXCEEDED'} for e in events):
                if not db.get(Control,'campaign-halt'):db.add(Control(id='campaign-halt',data={'reason':'asynchronous_provider_restriction','at':now().isoformat()}))
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
        confirmation=db.get(Control,'confirmed-'+sent.crm_id) if sent else None
        if not confirmation or now()-datetime.fromisoformat(confirmation.data['at'])<timedelta(seconds=15):return 'halt'
    if not channel_room(db,j,position,kind):return 'budget'
    route=j.snapshot['routes'][position]
    prior=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.position==position,Part.kind==kind))
    if prior: return prior.state
    channels=c.wz.get('/channels');channels.raise_for_status()
    channel=next((x for x in channels.json() if x['channelId']==route['channelId']),{})
    if channel.get('state')!='active':
        if kind=='text' and channel.get('state') in {'init','phoneUnavailable','disabled','notEnoughMoney'}:
            db.add(Part(recipient_id=j.id,position=position,kind=kind,state='absent',crm_id=f'{CAMPAIGN}-{j.id}-{position}-{kind}',reason='preflight_'+channel['state']))
            j.state='prepared';db.commit()
            notice(db,'technical-channel-'+route['transport']+'-'+str(now().astimezone(MSK).date()),f'MANSBAND · Канал временно недоступен: {route["transport"]}\nЗапрос на отправку не выполнялся. Используется следующий канал по плану.\nПричина: {channel["state"]}')
            return 'absent'
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
    if j.test:
        if not ctl or j.person not in ctl.data.get('allowlist',[]):return
    elif not production_enabled(db):return
    local=now().astimezone(MSK)
    if local.date()>date(2026,12,31):stop(db,j,'offer_expired');return
    if not in_campaign_window(local):return
    if j.state=='prepared' and not in_campaign_window(local,starting=True):return
    if not j.test and j.state=='prepared':
        used,_=daily_usage(db,now())
        if used>=day_budget(db,j.segment,now()) and not db.scalar(select(Part.id).where(Part.recipient_id==j.id).limit(1)):return
    if db.get(Control,'unsubscribe-'+j.person) or j.replied_at:
        j.state='reply_handoff';db.commit();return
    if not j.lead_id:stop(db,j,'missing_campaign_deal');return
    pace=db.get(Control,'pace' if j.test else 'production-pace')
    if j.state=='prepared' and pace and now()<datetime.fromisoformat(pace.data['next']):return
    owner=duty(now())
    if not j.test:
        users=c.get('/api/v4/users').get('_embedded',{}).get('users',[])
        if not any(u['id']==owner and u.get('rights',{}).get('is_active') for u in users):stop(db,j,'inactive_shift_owner');return
    lead=c.get(f'/api/v4/leads/{j.lead_id}')
    if lead.get('pipeline_id')!=10730010 or lead.get('status_id')!=89984117:
        stop(db,j,'campaign_stage_changed');return
    if lead.get('responsible_user_id')!=owner:
        r=c.amo.patch(f'/api/v4/leads/{j.lead_id}',json={'responsible_user_id':owner});r.raise_for_status()
    sent=db.scalar(select(Part).where(Part.recipient_id==j.id,Part.kind=='text',Part.state=='sent'))
    if sent:
        if now()-aware(sent.at)<timedelta(seconds=15):return
        if now()-aware(sent.at)>timedelta(minutes=10):stop(db,j,'photo_delay_exceeded');return
        if aware(sent.at).astimezone(MSK).date()!=local.date():stop(db,j,'photo_next_day_forbidden');return
        send_part(db,j,c,sent.position,'photo',asset);return
    # Persisted variable gap, shared by all pilot recipients, no catch-up burst.
    pace=db.get(Control,'pace' if j.test else 'production-pace')
    if pace and now()<datetime.fromisoformat(pace.data['next']):return
    if not pace:pace=Control(id='pace' if j.test else 'production-pace',data={});db.add(pace)
    cap=day_budget(db,j.segment,now()) if not j.test else 50
    override=db.get(Control,'daily-plan-'+now().astimezone(MSK).date().isoformat()) if not j.test else None
    minimum=int(override.data['minimum_gap_seconds']) if override and override.data.get('minimum_gap_seconds') else max(300,14400//cap)
    pace.data={'next':(now()+timedelta(seconds=minimum+secrets.randbelow(int(minimum*.4)+1))).isoformat()};db.commit()
    if not j.test:
        if not db.get(Control,f'segment-start-{j.segment}'):
            db.add(Control(id=f'segment-start-{j.segment}',data={'day':local.date().isoformat()}));db.commit()
        snapshot=dict(j.snapshot)
        snapshot['text']=render(snapshot['template'],snapshot.get('name',''),j.segment,now(),neutral=j.id%3==0)
        j.snapshot=snapshot;db.commit()
    for pos,route in enumerate(j.snapshot['routes']):
        outcome=send_part(db,j,c,pos,'text',asset)
        if outcome!='absent':return
    j.state,j.reason='unreachable','all_recipients_absent';db.commit()

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
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==tests,Recipient.segment.between(1,6))))
    if day:
        jobs=[j for j in jobs if j.sent_at and aware(j.sent_at).astimezone(MSK).date()==day]
    ids={j.id for j in jobs}
    parts=list(db.scalars(select(Part).where(Part.recipient_id.in_(ids)))) if ids else []
    text_sent=[j for j in jobs if j.sent_at]
    jobs_by_id={j.id:j for j in jobs}
    photo_sent_ids={p.recipient_id for p in parts if p.kind=='photo' and p.state=='sent'}
    complete=sum(j.id in photo_sent_ids for j in text_sent)
    by_channel={}
    for p in parts:
        if p.kind=='text' and p.state=='sent':
            j=jobs_by_id[p.recipient_id]
            transport=j.snapshot['routes'][p.position]['transport']
            by_channel[transport]=by_channel.get(transport,0)+1
    purchases=list(db.scalars(select(Purchase).where(Purchase.recipient_id.in_(ids)))) if ids else []
    purchases=[p for p in purchases if p.evidence.get('active',True)]
    return {'recipients':len(jobs),'text_sent':len(text_sent),'complete':complete,
        'partial':len(text_sent)-complete,'replied':sum(bool(j.replied_at) for j in jobs),
        'reply_activity_today':len(set(db.scalars(select(ResponseEvent.recipient_id).where(ResponseEvent.recipient_id.in_(ids),ResponseEvent.at>=datetime.combine(now().astimezone(MSK).date(),datetime.min.time(),MSK))))),
        'responses':{v:sum(j.classification==v for j in jobs) for v in ('interest','refusal','unsubscribe','review')},
        'channels':by_channel,'segments':{str(n):sum(j.segment==n and bool(j.sent_at) for j in jobs) for n in range(1,7)},
        'chat_history':{v:sum(j.snapshot.get('chat_history','unverified')==v and bool(j.sent_at) for j in jobs) for v in ('existing','new','unverified')},
        'observed_purchases':len(purchases),'linked_purchases':sum(p.linked for p in purchases),
        'buyers':len({p.recipient_id for p in purchases}),'buyers_after_returns':len({p.recipient_id for p in purchases if p.amount_kopecks>p.refund_kopecks}),
        'purchase_refunds':sum(p.refund_kopecks>0 for p in purchases),
        'gross_kopecks':sum(p.amount_kopecks for p in purchases),'refund_kopecks':sum(p.refund_kopecks for p in purchases),
        'net_kopecks':sum(p.amount_kopecks-p.refund_kopecks for p in purchases),
        'purchase_sync_verified':purchase_sync_fresh(db),
        'purchase_coverage':'observed_kassa_documents',
        'attention':sum(j.state=='attention' for j in jobs),'unreachable':sum(j.state=='unreachable' for j in jobs),'excluded':sum(j.state in ('excluded','duplicate') for j in jobs),
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
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==tests,Recipient.segment.between(1,6))))
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
                    production_tick(db,c,asset)
                    for j in db.scalars(select(Recipient).where(Recipient.state.in_(['prepared','photo_ready'])).order_by(Recipient.due_at)):
                        dispatch(db,j,c,asset)
                    local=now().astimezone(MSK)
                    if (local.hour,local.minute)>=(20,15) and not db.get(Notification,'daily-'+str(local.date())):
                        total=detailed_report(db)
                        today=detailed_report(db,day=local.date())
                        tests=detailed_report(db,tests=True)
                        cat=total['responses']
                        purchase_line=(f"Покупки по документам кассы после рассылки: {total['observed_purchases']}; связанные: {total['linked_purchases']}; чистая сумма {total['net_kopecks']/100:.2f} ₽" if total['purchase_sync_verified'] else 'Покупки: автоматическая сверка не подключена, нулевую конверсию не утверждаем.')
                        notice(db,'daily-'+str(local.date()),f"MANSBAND · Рассылка от 10.11\n{local:%d.%m %H:%M} МСК\n"
                            f"Сегодня: текст {today['text_sent']}, полный комплект {today['complete']}, частично {today['partial']}; ответили сегодня {total['reply_activity_today']}.\n"
                            f"Всего: текст {total['text_sent']}, полный комплект {total['complete']}.\n"
                            f"Ответили {total['replied']}; интерес {cat['interest']}; отказ {cat['refusal']}; отписка {cat['unsubscribe']}; разобрать {cat['review']}.\n"
                            f"Каналы: {total['channels'] or 'нет отправок клиентам'}.\n"
                            f"Сегменты 1–6: {', '.join(str(total['segments'][str(n)]) for n in range(1,7))}.\n"
                            f"Диалоги: новых {total['chat_history']['new']}, существующих {total['chat_history']['existing']}, не проверено {total['chat_history']['unverified']}.\n"
                            f"{purchase_line}\nНет адресата: {total['unreachable']}; исключено: {total['excluded']}; проблемы: {total['attention']}. Тесты: {tests['complete']}/{tests['recipients']} комплектов.\n"
                            f'Режим: {mode(db)}. Следующий сегмент: {active_segment(db)}. Завтра максимум {day_budget(db,active_segment(db),now()+timedelta(days=1)) if active_segment(db) else 0} человек; фактический объём зависит от каналов и проверок.' )
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
    actor: str = 'internal'
    category: Literal['interest','refusal','unsubscribe','review']
    reason: str

@router.get('/certificate-files/campaign-health')
def campaign_health():
    with get_session_factory()() as db:
        heartbeat=db.get(Control,'heartbeat')
        stamp=datetime.fromisoformat(heartbeat.data['at']) if heartbeat else None
        fresh=bool(stamp and now()-stamp<timedelta(minutes=3))
        return {'ok':fresh,'mode':mode(db),'checked_at':stamp.isoformat() if stamp else None}

@router.get('/internal/templates/campaign/stats',dependencies=[Depends(admin)])
def campaign_stats(tests: bool=False):
    with get_session_factory()() as db:
        return {'campaign':CAMPAIGN,'mode':mode(db),'today':detailed_report(db,tests,now().astimezone(MSK).date()),'total':detailed_report(db,tests)}

@router.post('/internal/templates/campaign/recipients/{recipient_id}/classification',dependencies=[Depends(admin)])
def change_classification(recipient_id: int,body: ClassificationChange):
    if not body.reason.strip() or len(body.reason)>500:raise HTTPException(400,'Укажи причину, до 500 символов')
    with get_session_factory()() as db:
        operator_lock(db)
        j=db.get(Recipient,recipient_id)
        if not j:raise HTTPException(404)
        if not j.replied_at and body.category!='unsubscribe':raise HTTPException(409,'В журнале ещё нет ответа. Можно только исключить получателя или отметить отписку.')
        if j.classification=='unsubscribe' and body.category!='unsubscribe':raise HTTPException(409,'Отписка требует отдельной проверки согласия')
        row=db.get(Control,'classification-'+str(j.id))
        if not row:row=Control(id='classification-'+str(j.id),data={});db.add(row)
        row.data={'category':body.category,'reason':body.reason,'at':now().isoformat(),'previous':j.classification,'actor':body.actor}
        j.classification=body.category
        db.add(ResponseEvent(id='manual-'+secrets.token_hex(16),recipient_id=j.id,classification=body.category,at=now()))
        if body.category=='unsubscribe' and not db.get(Control,'unsubscribe-'+j.person):db.add(Control(id='unsubscribe-'+j.person,data={'at':now().isoformat()}))
        db.commit()
        return {'id':j.id,'classification':j.classification,'manual':True}


def run():
    import time
    init()
    if hashlib.sha256((Path(__file__).parent/'assets'/ASSET_NAME).read_bytes()).hexdigest()!=ASSET_SHA256:raise SystemExit('campaign_asset_changed')
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

# Production queue is imported without creating CRM deals or sending messages.
# Every activation is explicit and bound to the audited source snapshot.
CHANNEL_BUDGETS = {'telegram':40,'max':20,'whatsapp':40}
TERMINAL = {'complete','reply_handoff','excluded','duplicate','unreachable'}

class Identity(Base):
    __tablename__='campaign_identity'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    recipient_id: Mapped[int] = mapped_column(Integer,index=True)

TABLES.append(Identity.__table__)

def identity_key(kind,value):
    if kind.endswith(':username'):value=str(value).lstrip('@').lower()
    if kind.endswith(':phone'):value=phone(value)
    return hashlib.sha256(f'{CAMPAIGN}:{kind}:{value}'.encode()).hexdigest()

def claim_identities(db,j,contact,routes):
    values={identity_key('phone',j.person),identity_key('contact',contact['id'])}
    values.update(identity_key('contact',cid) for cid in j.snapshot.get('matched_contact_ids',[]))
    for f in contact.get('custom_fields_values') or []:
        if f.get('field_code')=='PHONE':
            values.update(identity_key('phone',key(v['value'])) for v in f.get('values') or [])
    for r in routes:
        for k,v in (r.get('recipient') or {}).items():
            values.add(identity_key(r['transport']+':'+k,str(v)))
    conflicts=[db.get(Identity,v) for v in values]
    if any(x and x.recipient_id!=j.id for x in conflicts):
        j.state,j.reason='duplicate','shared_crm_or_messenger_identity';db.commit();return False
    for v,x in zip(values,conflicts):
        if not x:db.add(Identity(id=v,recipient_id=j.id))
    db.commit();return True

def active_segment(db):
    return db.scalar(select(Recipient.segment).where(Recipient.test==False,~Recipient.state.in_(TERMINAL)).order_by(Recipient.segment).limit(1))

def day_budget(db,segment,stamp):
    override=db.get(Control,'daily-plan-'+stamp.astimezone(MSK).date().isoformat())
    if override:return int(override.data['recipients'])
    row=db.get(Control,f'segment-start-{segment}')
    if not row:return 5 if segment==1 else 15
    elapsed=max(0,(stamp.astimezone(MSK).date()-date.fromisoformat(row.data['day'])).days)
    ramp=[5,10,15,25,25,25] if segment==1 else [15,15,15,25,25,25]
    return ramp[elapsed] if elapsed<len(ramp) else 50

def production_enabled(db):
    row=db.get(Control,'production')
    return bool(row and row.data.get('enabled') and row.data.get('audit_complete') and row.data.get('source_sha256'))

def mode(db):return 'attention' if db.get(Control,'campaign-halt') else 'production' if production_enabled(db) else 'paused'

def daily_usage(db,stamp):
    start=datetime.combine(stamp.astimezone(MSK).date(),datetime.min.time(),MSK).astimezone(timezone.utc)
    parts=list(db.scalars(select(Part).join(Recipient,Recipient.id==Part.recipient_id).where(Part.at>=start,Recipient.test==False)))
    parts=[p for p in parts if not p.reason.startswith('preflight_')]
    jobs={j.id:j for j in db.scalars(select(Recipient).where(Recipient.id.in_({p.recipient_id for p in parts})))} if parts else {}
    channels={k:0 for k in CHANNEL_BUDGETS}
    for p in parts:
        r=jobs[p.recipient_id].snapshot['routes'][p.position]
        channels[r['transport']]+=1
    own_ids={p.message_id for p in parts if p.message_id}
    for e in db.scalars(select(ChannelUsage).where(ChannelUsage.at>=start)):
        if e.id not in own_ids and e.transport in channels:channels[e.transport]+=1
    # Reserve a photo slot for all accepted or uncertain texts, including previous days.
    for j in db.scalars(select(Recipient).where(Recipient.test==False,Recipient.state.in_(['pending','inflight','photo_ready']))):
        ps=list(db.scalars(select(Part).where(Part.recipient_id==j.id)))
        for p in ps:
            if p.kind=='text' and p.state in {'sent','pending','inflight','unknown'} and not any(q.kind=='photo' for q in ps):
                channels[j.snapshot['routes'][p.position]['transport']]+=1
    return len({p.recipient_id for p in parts if p.kind=='text'}),channels

def channel_room(db,j,pos,kind):
    if j.test:return True
    _,usage=daily_usage(db,now())
    channel=j.snapshot['routes'][pos]['transport']
    # The pending photo already owns its slot; text must reserve two slots.
    return usage[channel]+(2 if kind=='text' else 0)<=CHANNEL_BUDGETS[channel]

def queue_next(db,existing_only=False):
    segment=active_segment(db)
    if not segment:return None
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==False,Recipient.segment==segment,Recipient.state=='queued')))
    # Do not send until the entire active segment's history/identities are checked.
    if any(not j.snapshot.get('enriched') for j in jobs):return None
    if existing_only:jobs=[j for j in jobs if j.snapshot.get('chat_history')=='existing']
    return next(iter(sorted(jobs,key=lambda j:(-int(j.snapshot.get('last_incoming',0)),-int(j.snapshot.get('last_purchase',0)),j.id))),None)

def enrich_one(db,c):
    segment=active_segment(db)
    if not segment:return
    jobs=db.scalars(select(Recipient).where(Recipient.test==False,Recipient.segment==segment,Recipient.state=='queued').order_by(Recipient.id))
    j=next((x for x in jobs if not x.snapshot.get('enriched')),None)
    if not j:return
    if not j.contact_id:
        if j.snapshot.get('matched_contact_ids'):stop(db,j,'ambiguous_crm_contacts');return
        channels=c.wz.get('/channels');channels.raise_for_status()
        from app.reservation_reminders import ReminderSettings
        settings=ReminderSettings()
        routes=[]
        for transport in ('telegram','max','whatsapp'):
            candidates=[x for x in channels.json() if x.get('transport')==('tgapi' if transport=='telegram' else transport)]
            configured=getattr(settings,transport+'_channel')
            channel=configured or (candidates[0]['channelId'] if len(candidates)==1 else None)
            if channel:
                routes.append({'transport':transport,'channelId':channel,'recipient':{'chatId' if transport=='whatsapp' else 'phone':j.snapshot['phone']}})
        if not routes:stop(db,j,'no_configured_channels');return
        # This is a new CRM contact, not evidence of an existing dialogue.
        j.snapshot={**j.snapshot,'routes':routes,'last_incoming':0,'chat_history':'new','enriched':True,'checked_at':now().isoformat()};db.commit();return
    from app.stage_notifications import resolve_plan,StageSettings
    from app.reservation_reminders import ReminderSettings
    contact=c.get(f'/api/v4/contacts/{j.contact_id}')
    phones={phone(v['value']) for f in contact.get('custom_fields_values') or [] if f.get('field_code')=='PHONE' for v in f.get('values') or []}
    if j.person not in {key(p) for p in phones}:stop(db,j,'contact_phone_changed');return
    class Capture:
        def __init__(self):self.wz=c.wz;self.incoming=[];self.talks=[]
        def get(self,path,**kwargs):
            data=c.get(path,**kwargs)
            if path=='/api/v4/events':self.incoming=data.get('_embedded',{}).get('events',[])
            if path=='/api/v4/talks':self.talks=data.get('_embedded',{}).get('talks',[])
            return data
    capture=Capture()
    routes=resolve_plan(db,capture,{},contact,StageSettings(),ReminderSettings(),routing={'order':['telegram','max','whatsapp'],'previous_first':True,'social_fallback':False})
    routes=[r for r in routes if r.get('recipient') and r.get('channelId')]
    if not routes:j.state,j.reason='unreachable','no_verified_destination';db.commit();return
    if not claim_identities(db,j,contact,routes):return
    latest=max((int(e.get('created_at',0)) for e in capture.incoming),default=0)
    from app.stage_notifications import ORIGINS
    direct=any(ORIGINS.get((v.get('message') or {}).get('origin'),(v.get('message') or {}).get('origin')) in ('telegram','max','whatsapp') for e in capture.incoming for v in e.get('value_after') or [])
    j.snapshot={**j.snapshot,'routes':routes,'last_incoming':latest,'chat_history':'existing' if direct else 'new','direct_history_verified':True,'enriched':True,'checked_at':now().isoformat()};db.commit()

def create_campaign_deal(db,j,c):
    owner=duty(now())
    if j.contact_id:
        contact=c.get(f'/api/v4/contacts/{j.contact_id}')
        phones={key(v['value']) for f in contact.get('custom_fields_values') or [] if f.get('field_code')=='PHONE' for v in f.get('values') or []}
        if j.person not in phones:stop(db,j,'contact_phone_changed');return
        if not claim_identities(db,j,contact,j.snapshot['routes']):return
    if not j.contact_id and not create_campaign_contact(db,j,c):return
    users=c.get('/api/v4/users').get('_embedded',{}).get('users',[])
    if not any(u['id']==owner and u.get('rights',{}).get('is_active') for u in users):raise ValueError('inactive_shift_owner')
    # Claim before CRM mutation. Interrupted creation is never blindly retried.
    j.state='crm_inflight';j.snapshot={**j.snapshot,'crm_claim_at':now().isoformat()};db.commit()
    payload=[{'name':f'Рассылка от 10.11 · #{j.id}','pipeline_id':10730010,'status_id':89984117,'responsible_user_id':owner,
        '_embedded':{'contacts':[{'id':j.contact_id,'is_main':True}],'tags':[{'name':CAMPAIGN},{'name':f'Сегмент {j.segment}'}]}}]
    try:
        r=c.amo.post('/api/v4/leads',json=payload);r.raise_for_status()
        j.lead_id=int(r.json()['_embedded']['leads'][0]['id']);db.commit()
        lead=c.get(f'/api/v4/leads/{j.lead_id}',**{'with':'contacts'})
        if lead.get('pipeline_id')!=10730010 or lead.get('status_id')!=89984117 or lead.get('responsible_user_id')!=owner:raise ValueError('crm_readback_mismatch')
        j.state='prepared';db.commit()
    except Exception:
        stop(db,j,'crm_creation_requires_reconciliation');raise

def production_tick(db,c,asset):
    # Enrichment is read-only with respect to CRM; available while paused.
    refresh_history_day(db)
    resolve_ambiguous_one(db,c)
    import time
    started=time.monotonic()
    for _ in range(5):
        enrich_one(db,c)
        if time.monotonic()-started>15:break
    if not production_enabled(db) or db.get(Control,'campaign-halt'):return
    local=now().astimezone(MSK)
    if local.date()>date(2026,12,31):
        notice(db,'offer-expired','MANSBAND · Очередь остановлена\nСрок исходных шаблонов закончился. Нужны новые утверждённые тексты и изображение.');return
    if not in_campaign_window(local):return
    if not in_campaign_window(local,starting=True):return
    # No parallel bundle: finish or reconcile the current one first.
    pending=db.scalar(select(Recipient.id).where(Recipient.test==False,Recipient.segment==active_segment(db),Recipient.state.in_(['prepared','pending','inflight','photo_ready','crm_inflight','attention']),Recipient.reason.notin_(['ambiguous_crm_contacts'])).limit(1))
    if pending:return
    first=db.get(Control,'segment-start-1')
    pilot_day=active_segment(db)==1 and (not first or first.data['day']==local.date().isoformat())
    j=queue_next(db,existing_only=pilot_day)
    if not j:return
    ctl=db.get(Control,'production')
    if local.date()<date.fromisoformat(ctl.data['start_day']):return
    used,_=daily_usage(db,now())
    cap=day_budget(db,j.segment,now())
    if used>=cap:return
    pace=db.get(Control,'production-pace')
    if pace and now()<datetime.fromisoformat(pace.data['next']):return
    # First customer pilot is existing conversations only.
    if j.segment==1 and (not db.get(Control,'segment-start-1') or db.get(Control,'segment-start-1').data['day']==local.date().isoformat()) and j.snapshot['chat_history']!='existing':
        notice(db,'no-warm-pilot','MANSBAND · Клиентский пилот на паузе\nВ первом сегменте не найден подтверждённый входящий диалог. Нужна проверка.');return
    if not any(channel_room(db,j,pos,'text') for pos in range(len(j.snapshot['routes']))):return
    create_campaign_deal(db,j,c)
    dispatch(db,j,c,asset)

class PurchaseBatch(BaseModel):
    checked_at: datetime
    records: list[dict]
    source: Literal['kassa'] = 'kassa'

@router.post('/internal/templates/campaign/purchases',dependencies=[Depends(admin)])
def purchase_feed(body: PurchaseBatch):
    if len(body.records)>10000 or body.checked_at.tzinfo is None:raise HTTPException(400)
    with get_session_factory()() as db:
        valid={str(r.get('document_id')) for r in body.records if r.get('paid') and r.get('posted') and not r.get('test') and not r.get('cancelled')}
        for purchase in db.scalars(select(Purchase)):
            if purchase.id.startswith('kassa:') and purchase.id[6:] not in valid:
                purchase.evidence={**purchase.evidence,'active':False,'invalidated_at':now().isoformat()}
        db.commit()
        result=ingest_purchases(db,body.records)
        row=db.get(Control,'purchase-sync-verified')
        if not row:row=Control(id='purchase-sync-verified',data={});db.add(row)
        row.data={'at':body.checked_at.isoformat(),'source':'kassa','coverage':'observed_kassa_documents','result':result};db.commit()
        return result

def ingest_purchases(db,records):
    jobs=list(db.scalars(select(Recipient).where(Recipient.test==False,Recipient.sent_at.is_not(None))))
    result={'linked':0,'observed':0,'ignored':0,'ambiguous':0}
    for record in records:
        if record.get('test') or not record.get('document_id') or not record.get('paid') or not record.get('posted') or record.get('cancelled'):
            result['ignored']+=1;continue
        try:
            stamp=datetime.fromisoformat(record['at']);created=datetime.fromisoformat(record['order_created_at'])
            amount=int(record['amount_kopecks']);refund=int(record.get('refund_kopecks',0))
            if not stamp.tzinfo or not created.tzinfo or amount<=0 or refund<0 or refund>amount:raise ValueError()
        except (KeyError,ValueError,TypeError):result['ignored']+=1;continue
        person=record.get('person')
        candidates=[j for j in jobs if (j.person==person or j.lead_id==record.get('amo_lead_id')) and aware(j.sent_at)<=stamp and aware(j.sent_at)<=created]
        if len(candidates)!=1:
            result['ambiguous' if candidates else 'ignored']+=1;continue
        j=candidates[0];identity='kassa:'+str(record['document_id'])
        linked=j.lead_id==record.get('amo_lead_id')
        p=db.get(Purchase,identity)
        if p and p.recipient_id!=j.id:result['ambiguous']+=1;continue
        if not p:p=Purchase(id=identity,recipient_id=j.id,at=stamp);db.add(p)
        p.amount_kopecks,p.refund_kopecks,p.linked=amount,refund,linked
        p.evidence={k:record[k] for k in ('document_id','order_id','amo_lead_id','at','order_created_at','payment_ids') if k in record}|{'active':True}
        result['linked' if linked else 'observed']+=1
    db.commit();return result


def purchase_sync_fresh(db):
    row=db.get(Control,'purchase-sync-verified')
    return bool(row and now()-datetime.fromisoformat(row.data['at'])<timedelta(minutes=30))


class ChannelUsage(Base):
    __tablename__='campaign_channel_usage'
    id: Mapped[str] = mapped_column(String(100),primary_key=True)
    channel: Mapped[str] = mapped_column(String(100))
    transport: Mapped[str] = mapped_column(String(24))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),index=True)

TABLES.append(ChannelUsage.__table__)

def create_campaign_contact(db,j,c):
    from app.reservation_reminders import ReminderSettings
    # Fresh exact phone verification immediately before creation, no substring match.
    candidates=c.get('/api/v4/contacts',query=j.snapshot['phone'],limit=250).get('_embedded',{}).get('contacts',[])
    exact=[x for x in candidates if j.person in {key(v['value']) for f in x.get('custom_fields_values') or [] if f.get('field_code')=='PHONE' for v in f.get('values') or []}]
    if len(candidates)>=250 or len(exact)>1:stop(db,j,'ambiguous_crm_contacts');return False
    if exact:
        j.contact_id=exact[0]['id'];j.snapshot={**j.snapshot,'enriched':False};j.state='queued';db.commit();return False
    j.state='crm_inflight';j.snapshot={**j.snapshot,'crm_claim_at':now().isoformat()};db.commit()
    try:
        r=c.amo.post('/api/v4/contacts',json=[{'name':j.snapshot.get('name') or 'Клиент MANSBAND','custom_fields_values':[{'field_code':'PHONE','values':[{'value':'+'+j.snapshot['phone'],'enum_code':'WORK'}]}]}]);r.raise_for_status()
        j.contact_id=int(r.json()['_embedded']['contacts'][0]['id']);db.commit()
        contact=c.get(f'/api/v4/contacts/{j.contact_id}')
        if not claim_identities(db,j,contact,j.snapshot['routes']):return False
        return True
    except Exception:
        stop(db,j,'crm_contact_creation_requires_reconciliation');raise

class QueueCommand(BaseModel):
    actor: str = 'internal'
    action: Literal['pause','resume','exclude']
    reason: str
    recipient_id: int|None = None

@router.get('/internal/templates/campaign/queue',dependencies=[Depends(admin)])
def queue_status(state: str='', page: int=1, segment: int=0, search: str=''):
    if page<1 or page>1000 or segment not in range(7) or len(search)>100:raise HTTPException(400)
    with get_session_factory()() as db:
        base=select(Recipient).where(Recipient.test==False,Recipient.segment.between(1,6))
        query=base
        if state=='buyers':
            live_purchases=select(Purchase.recipient_id).where(or_(Purchase.evidence['active'].as_boolean()==True,Purchase.evidence['active'].as_boolean().is_(None)))
            query=query.where(Recipient.id.in_(live_purchases))
        elif state=='replies':query=query.where(Recipient.replied_at.is_not(None))
        elif state=='sent':query=query.where(Recipient.sent_at.is_not(None))
        elif state=='delivered':query=query.where(Recipient.sent_at.is_not(None),Recipient.id.in_(select(Part.recipient_id).where(Part.kind=='photo',Part.state=='sent')))
        elif state=='excluded':query=query.where(Recipient.state.in_(['excluded','duplicate']))
        elif state in ('interest','refusal','unsubscribe','review'):query=query.where(Recipient.classification==state)
        elif state:query=query.where(Recipient.state==state)
        if segment:query=query.where(Recipient.segment==segment)
        if search.strip():
            term=search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            tests=[Recipient.snapshot['name'].as_string().ilike('%'+term+'%',escape='\\'),Recipient.snapshot['phone'].as_string().ilike('%'+term+'%',escape='\\')]
            if search.strip().lstrip('#').isdigit():tests.append(Recipient.id==int(search.strip().lstrip('#')))
            query=query.where(or_(*tests))
        total=db.scalar(select(func.count()).select_from(query.subquery()))
        jobs=list(db.scalars(query.order_by(Recipient.segment,Recipient.id).offset((page-1)*50).limit(50)))
        ids=[j.id for j in jobs]
        attempted=set(db.scalars(select(Part.recipient_id).where(Part.recipient_id.in_(ids)))) if ids else set()
        purchases=list(db.scalars(select(Purchase).where(Purchase.recipient_id.in_(ids)))) if ids else []
        by_recipient={}
        for p in purchases:
            if p.evidence.get('active',True):by_recipient.setdefault(p.recipient_id,[]).append({'id':p.id,'linked':p.linked,'net_kopecks':p.amount_kopecks-p.refund_kopecks,'document_id':p.evidence.get('document_id'),'at':p.at.isoformat()})
        current=active_segment(db)
        used,channels=daily_usage(db,now())
        segments=[]
        grouped=db.execute(select(Recipient.segment,func.count(),
            func.sum(case((Recipient.sent_at.is_not(None),1),else_=0)),
            func.sum(case((Recipient.state.in_(TERMINAL),1),else_=0)),
            func.sum(case((Recipient.state=='attention',1),else_=0)),
            func.sum(case((Recipient.snapshot['chat_history'].as_string()=='existing',1),else_=0)),
            func.sum(case((Recipient.snapshot['enriched'].as_boolean()==True,1),else_=0)))
            .where(Recipient.test==False,Recipient.segment.between(1,6)).group_by(Recipient.segment).order_by(Recipient.segment))
        for n,count,sent,done,attention,existing,checked in grouped:
            segments.append({'segment':n,'total':count,'sent':sent,'processed':done,'attention':attention,'existing':existing,'checked':checked})
        ctl=db.get(Control,'production');halt=db.get(Control,'campaign-halt')
        start=(ctl.data.get('start_day') if ctl else None)
        local=now().astimezone(MSK)
        day=max(local.date(),date.fromisoformat(start)) if start else local.date()
        next_window=None
        for offset in range(2):
            for hour,end in campaign_windows(day+timedelta(days=offset)):
                candidate=datetime.combine(day+timedelta(days=offset),datetime.min.time(),MSK)+timedelta(hours=hour)
                if candidate.date()>date(2026,12,31):continue
                if candidate+timedelta(hours=end-hour)>local:
                    next_window=candidate.isoformat();break
            if next_window:break
        return {'mode':mode(db),'segment':current,'day_budget':day_budget(db,current,now()) if current else 0,'today_started':used,
            'server_time':now().isoformat(),'start_day':start,'next_window':next_window,'offer_expired':local.date()>date(2026,12,31),
            'halt_reason':halt.data.get('reason') if halt else None,'segments':segments,'total':total,'page_size':50,
            'channel_usage':channels,'channel_budgets':CHANNEL_BUDGETS,'purchase_sync_fresh':purchase_sync_fresh(db),
            'purchase_coverage':'Касса: оплаченные проведённые документы МойСклад. Продажи вне кассы требуют сверки.',
            'items':[{'id':j.id,'name':j.snapshot.get('name') or '',
                'phone_tail':str(j.snapshot.get('phone') or '')[-4:],
                'segment':j.segment,'state':j.state,'reason':j.reason,'lead_id':j.lead_id,'contact_id':j.contact_id,'matched_contact_ids':j.snapshot.get('matched_contact_ids',[]),
                'can_exclude':j.id not in attempted,'can_classify':bool(j.replied_at),
                'classification':j.classification,'chat_history':j.snapshot.get('chat_history','unverified'),'purchases':by_recipient.get(j.id,[]),
                'sent_at':j.sent_at.isoformat() if j.sent_at else None,'replied_at':j.replied_at.isoformat() if j.replied_at else None} for j in jobs]}

@router.post('/internal/templates/campaign/control',dependencies=[Depends(admin)])
def queue_command(body: QueueCommand):
    if not body.reason.strip() or len(body.reason)>500:raise HTTPException(400,'Нужна причина, до 500 символов')
    with get_session_factory()() as db:
        operator_lock(db)
        row=db.get(Control,'production')
        if body.action=='exclude':
            j=db.get(Recipient,body.recipient_id)
            if not j or j.test:raise HTTPException(404)
            if db.scalar(select(Part.id).where(Part.recipient_id==j.id).limit(1)):raise HTTPException(409,'Есть попытка отправки. Сначала нужна сверка результата.')
            j.state='excluded';j.reason='operator_excluded';j.snapshot={**j.snapshot,'exclusion_reason':body.reason,'excluded_at':now().isoformat()}
        elif body.action=='pause':
            if not row:row=Control(id='production',data={});db.add(row)
            row.data={**row.data,'enabled':False,'reason':body.reason,'changed_at':now().isoformat()}
        else:
            if not row or not row.data.get('audit_complete') or not row.data.get('source_sha256'):raise HTTPException(409,'Техническая приёмка ещё не закрыта')
            if db.get(Control,'campaign-halt'):raise HTTPException(409,'Есть ограничение канала. Нужна техническая сверка, кнопка не снимает блокировку.')
            if now().astimezone(MSK).date()>date(2026,12,31):raise HTTPException(409,'Срок шаблонов закончился')
            row.data={**row.data,'enabled':True,'reason':body.reason,'changed_at':now().isoformat()}
        db.add(Control(id='operator-'+secrets.token_hex(16),data=body.model_dump()|{'at':now().isoformat()}));db.commit()
        return {'mode':mode(db),'action':body.action}

def resolve_ambiguous_one(db,c):
    candidates=db.scalars(select(Recipient).where(Recipient.test==False,Recipient.segment==active_segment(db),Recipient.reason=='ambiguous_crm_contacts',Recipient.state=='attention').order_by(Recipient.id))
    j=next((x for x in candidates if not x.snapshot.get('ambiguity_reviewed')),None)
    if not j:return
    history=[]
    for cid in j.snapshot.get('matched_contact_ids',[]):
        contact=c.get(f'/api/v4/contacts/{cid}')
        phones={key(v['value']) for f in contact.get('custom_fields_values') or [] if f.get('field_code')=='PHONE' for v in f.get('values') or []}
        if j.person not in phones:continue
        events=c.get('/api/v4/events',**{'filter[entity]':'contact','filter[entity_id]':cid,'filter[type]':'incoming_chat_message','limit':100}).get('_embedded',{}).get('events',[])
        history.append((max((int(e.get('created_at',0)) for e in events),default=0),cid))
    history.sort(reverse=True)
    j.snapshot={**j.snapshot,'ambiguity_reviewed':True,'contact_history':[{'contact_id':cid,'last_incoming':stamp} for stamp,cid in history]}
    if history and history[0][0]>0 and (len(history)==1 or history[0][0]>history[1][0]):
        j.contact_id=history[0][1];j.state,j.reason='queued',''
        j.snapshot={**j.snapshot,'identity_resolution':'unique_latest_incoming_same_exact_phone'}
    db.commit()


def operator_lock(db):
    if db.get_bind().dialect.name=='postgresql' and not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK}):
        raise HTTPException(409,'Обработчик выполняет текущую проверку. Повтори действие через несколько секунд.')


def refresh_history_day(db):
    segment=active_segment(db)
    if not segment:return
    identity=f'history-day-{segment}'
    ctl=db.get(Control,identity);today=now().astimezone(MSK).date().isoformat()
    if ctl and ctl.data.get('day')==today:return
    for j in db.scalars(select(Recipient).where(Recipient.test==False,Recipient.segment==segment,Recipient.state=='queued',Recipient.contact_id>0)):
        stamp=j.snapshot.get('checked_at')
        checked=datetime.fromisoformat(stamp).astimezone(MSK).date().isoformat() if stamp else None
        if checked!=today:j.snapshot={**j.snapshot,'enriched':False}
    if not ctl:ctl=Control(id=identity,data={});db.add(ctl)
    ctl.data={'day':today};db.commit()
