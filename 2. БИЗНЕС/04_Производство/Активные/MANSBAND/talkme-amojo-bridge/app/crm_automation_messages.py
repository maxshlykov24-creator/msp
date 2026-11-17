"""Verified chat-event intake and night-response planning; sending uses managed queue."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base
from app.crm_automation import MSK, PIPELINES, is_test, MigrationEntry
from app.pipeline_guard import migration_blocked

NIGHT_ID = 'crm-night-response'
NIGHT_TEXT = ('Здравствуйте!\nСпасибо за обращение в магазин мужских костюмов Mansband.\n\n'
              'Сейчас мы не на связи, но обязательно ответим вам в рабочее время.\n'
              'На сообщения отвечаем ежедневно с 10:00 до 23:00 по московскому времени.\n\n'
              'Будем рады помочь вам!')

class ChatEvent(Base):
    __tablename__ = 'crm_automation_chat_event'
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    contact_id: Mapped[int] = mapped_column(Integer)
    at: Mapped[int] = mapped_column(Integer, index=True)
    direction: Mapped[str] = mapped_column(String(10))
    message_id: Mapped[str] = mapped_column(String(160))
    talk_id: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(160), default='')
    state: Mapped[str] = mapped_column(String(40), default='received')

class ChatCursor(Base):
    __tablename__ = 'crm_automation_chat_cursor'
    direction: Mapped[str] = mapped_column(String(10), primary_key=True)
    through: Mapped[int] = mapped_column(Integer)
    activated_at: Mapped[int] = mapped_column(Integer)

TABLES = [ChatEvent.__table__, ChatCursor.__table__]


def seed_night(db):
    from app import message_templates as m
    if db.get(m.Document,NIGHT_ID) is not None: return
    rule=m.Rule(name='Ответ вне рабочего времени',enabled=False,pipelines=sorted(PIPELINES),
        content=NIGHT_TEXT,trigger=m.Trigger(type='webhook'),window_start=0,window_end=24,late_policy='same_day')
    db.add(m.Document(id=NIGHT_ID,kind='rule',version=1,published_version=0,draft=rule.model_dump()))
    db.commit()


def night_key(at):
    local=at.astimezone(MSK)
    if 10 <= local.hour < 23: return None
    day=local.date() if local.hour >= 23 else (local-timedelta(days=1)).date()
    return 'night:'+day.isoformat()


def collect_chats(db,c,activated_at,until,direction):
    if activated_at <= 0 or direction not in ('incoming','outgoing'): raise ValueError('invalid_chat_activation')
    cursor=db.get(ChatCursor,direction)
    if cursor and cursor.activated_at != activated_at: raise ValueError('chat_activation_changed')
    start=max(activated_at,(cursor.through if cursor else activated_at)-300)
    if until < start: raise ValueError('clock_before_chat_cursor')
    rows=[]
    for page in range(1,101):
        # amoCRM stores chat events on contacts, returning their linked lead.
        data=c.get('/api/v4/events',page=page,limit=100,**{'filter[entity]':'contact',
            'filter[type]':direction+'_chat_message','filter[created_at][from]':start,'filter[created_at][to]':until})
        batch=data.get('_embedded',{}).get('events',[]);rows.extend(batch)
        if len(batch)<100 and not data.get('_links',{}).get('next'): break
    else: raise ValueError('incomplete_chat_scan')
    parsed=[];talks={}
    for row in rows:
        if row.get('type')!=direction+'_chat_message': raise ValueError('unexpected_chat_event')
        if row.get('entity_type') in ('contact','customer'): continue
        if row.get('entity_type')!='lead': raise ValueError('unexpected_chat_entity')
        at=int(row['created_at']);lead_id=int(row['entity_id'])
        if not start<=at<=until or lead_id<=0 or not row.get('id'): raise ValueError('invalid_chat_event')
        meta=row['value_after'][0]['message']
        message=str(meta['id'])
        if not message or len(message)>160: raise ValueError('invalid_chat_identity')
        contact=int(row.get('linked_talk_contact_id') or 0)
        talk_id=int(meta.get('talk_id') or 0)
        if not contact and talk_id:
            if talk_id not in talks: talks[talk_id]=c.get(f'/api/v4/talks/{talk_id}')
            talk=talks[talk_id]
            if talk.get('talk_id')==talk_id and talk.get('entity_type')=='lead' and talk.get('entity_id')==lead_id:
                contact=int(talk.get('contact_id') or 0)
        parsed.append(dict(id=str(row['id']),lead_id=lead_id,contact_id=contact,
                           at=at,direction=direction,message_id=message,talk_id=talk_id,origin=str(meta.get('origin') or '')))
    for row in parsed:
        if db.get(ChatEvent,row['id']) is None: db.add(ChatEvent(**row));db.flush()
    if cursor is None: db.add(ChatCursor(direction=direction,through=until,activated_at=activated_at))
    else: cursor.through=until
    db.commit()
    return len(parsed)


def plan_night(db,c,now,*,preview=True,cutover_at=0):
    from app import message_templates as m
    from app.reservation_reminders import primary_contact
    cursors=[db.get(ChatCursor,d) for d in ('incoming','outgoing')]
    if any(cur is None for cur in cursors): return
    through=min(cur.through for cur in cursors)
    for event in list(db.scalars(select(ChatEvent).where(ChatEvent.direction=='incoming',
            ChatEvent.state.in_(('received','preview','migration','disabled','history_pending')),
            ChatEvent.at<=through).order_by(ChatEvent.at).limit(100))):
        if not preview and event.at < cutover_at:
            event.state='pre_cutover';db.commit();continue
        event_time=datetime.fromtimestamp(event.at,timezone.utc)
        key=night_key(event_time)
        if key is None or key!=night_key(now) or now-event_time>timedelta(minutes=5):
            event.state='expired';db.commit();continue
        lead=c.get(f'/api/v4/leads/{event.lead_id}',**{'with':'contacts'})
        migration=db.get(MigrationEntry,event.lead_id)
        if migration_blocked(lead) or (migration and migration.state not in ('preview','verified','rolled_back')):
            event.state='migration';db.commit();continue
        if lead.get('pipeline_id') not in PIPELINES or lead.get('status_id') in (142,143) or lead.get('is_deleted') or is_test(lead):
            event.state='excluded';db.commit();continue
        if not event.contact_id or primary_contact(lead)!=event.contact_id:
            event.state='contact_requires_review';db.commit();continue
        from app.stage_notifications import ORIGINS, DIRECT, SOCIAL
        transport=ORIGINS.get(event.origin,event.origin)
        if transport=='instagram_business': transport='instagram'
        if transport not in set(DIRECT)|SOCIAL:
            event.state='channel_requires_review';db.commit();continue
        # Avoid answering a message already answered by a human or the old bot.
        answered=db.scalar(select(ChatEvent.id).where(ChatEvent.lead_id==event.lead_id,
            ChatEvent.contact_id==event.contact_id,ChatEvent.direction=='outgoing',ChatEvent.at>=event.at).limit(1))
        if answered: event.state='answered';db.commit();continue
        if preview: event.state='preview';db.commit();continue
        doc=db.get(m.Document,NIGHT_ID)
        if not doc or not doc.active or not doc.active.get('enabled'):
            event.state='disabled';db.commit();continue
        prior=db.scalar(select(m.Job).where(m.Job.rule_id==NIGHT_ID,m.Job.lead_id==event.lead_id,m.Job.occurrence==key))
        if prior is None:
            saved=m.snapshot(db,doc)
            saved['trigger_contact_id']=event.contact_id
            saved['routing']={'order':[transport],'previous_first':False,'social_fallback':False}
            rule=m.Rule(**doc.active)
            plan,recipient_key=m.make_plan(db,c,lead,rule,saved['routing'])
            db.add(m.Job(rule_id=NIGHT_ID,lead_id=event.lead_id,occurrence=key,due_at=now,source_at=event_time,
                         snapshot=saved,rendered=m.render(lead,rule),plan=plan,recipient_key=recipient_key))
        event.state='queued';db.commit()


def night_send_allowed(db,job,lead,now,c):
    from app.reservation_reminders import primary_contact
    from app.message_templates import aware
    if not job.source_at or night_key(now)!=job.occurrence or now-aware(job.source_at)>timedelta(minutes=5): return False
    contact=(job.snapshot or {}).get('trigger_contact_id')
    if not contact or primary_contact(lead)!=contact: return False
    if db.scalar(select(ChatEvent.id).where(ChatEvent.lead_id==job.lead_id,
        ChatEvent.contact_id==contact,ChatEvent.direction=='outgoing',
        ChatEvent.at>=int(aware(job.source_at).timestamp())).limit(1)) is not None: return False
    # The worker cursor intentionally trails real time. Refresh outgoing events
    # before POST so an already-visible human reply cannot slip through that gap.
    # Fail closed on malformed/paginated responses; never retry a provider POST here.
    for page in range(1,101):
        data=c.get('/api/v4/events',page=page,limit=100,**{'filter[entity]':'contact',
            'filter[type]':'outgoing_chat_message',
            'filter[created_at][from]':int(aware(job.source_at).timestamp()),
            'filter[created_at][to]':int(now.timestamp())})
        rows=data.get('_embedded',{}).get('events',[])
        for row in rows:
            if row.get('type')!='outgoing_chat_message': raise ValueError('unexpected_chat_event')
            if row.get('entity_type')=='lead' and int(row['entity_id'])==job.lead_id:
                # Missing contact identity cannot establish that a reply is unrelated.
                if not row.get('linked_talk_contact_id') or int(row['linked_talk_contact_id'])==contact: return False
        if len(rows)<100 and not data.get('_links',{}).get('next'): return True
    raise ValueError('incomplete_outgoing_chat_scan')
