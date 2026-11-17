"""Daily read-only CRM overdue summary; durable one-send claim per group/day."""
from collections import Counter
from datetime import datetime, timezone
from typing import Literal
import asyncio
import logging
import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import String, Integer, Text, select, update, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base
from app.crm_automation import PIPELINES, MSK, is_test, field_date, MigrationEntry
from app.crm_automation_rules import STAGE_DATE_FIELDS
from app.pipeline_guard import migration_blocked

log=logging.getLogger(__name__)
LOCK=674001942

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix='CRM_DIGEST_',extra='ignore')
    mode: Literal['off','preview','live']='off'
    chat_id: int=0

class Digest(Base):
    __tablename__='crm_automation_digest'
    id: Mapped[str]=mapped_column(String(80),primary_key=True)
    state: Mapped[str]=mapped_column(String(24),default='planned')
    body: Mapped[str]=mapped_column(Text)
    message_id: Mapped[int]=mapped_column(Integer,default=0)
    reason: Mapped[str]=mapped_column(String(80),default='')

TABLES=[Digest.__table__]


def eligible(lead,held):
    return lead.get('pipeline_id') in PIPELINES and not lead.get('is_deleted') and not is_test(lead) and not migration_blocked(lead) and lead['id'] not in held


def collect(c,now,held=frozenset()):
    from app.message_templates import all_pages
    from app.crm_automation import Catalog
    pipelines=all_pages(c,'/api/v4/leads/pipelines','pipelines')
    cat=Catalog(pipelines)
    if PIPELINES-set(cat.pipelines): raise ValueError('missing_digest_pipeline')
    tasks=all_pages(c,'/api/v4/tasks','tasks',**{'filter[is_completed]':0})
    overdue=[];leads={}
    for task in tasks:
        if task.get('is_completed') or int(task['complete_till'])>=int(now.timestamp()): continue
        if task.get('entity_type')!='leads': continue
        lid=int(task['entity_id'])
        if lid not in leads:leads[lid]=c.get(f'/api/v4/leads/{lid}')
        lead=leads[lid]
        if lead.get('id')!=lid: raise ValueError('unexpected_digest_lead')
        if eligible(lead,held):overdue.append({'id':task['id'],'lead_id':lid,'pipeline_id':lead['pipeline_id'],'due':int(task['complete_till'])})
    stages=[];missing=[]
    for pid in sorted(PIPELINES):
        statuses=[s for s in cat.pipelines[pid]['_embedded']['statuses'] if s.get('type')!=1 and s['id'] not in (142,143)]
        if not statuses:raise ValueError('missing_digest_open_stages')
        params={}
        for i,status in enumerate(statuses):
            params[f'filter[statuses][{i}][pipeline_id]']=pid
            params[f'filter[statuses][{i}][status_id]']=status['id']
        for lead in all_pages(c,'/api/v4/leads','leads',**params):
            if lead.get('pipeline_id')!=pid or lead.get('status_id') in (142,143):raise ValueError('unexpected_digest_pipeline')
            if not eligible(lead,held):continue
            stage=cat.stage(lead)
            field=STAGE_DATE_FIELDS.get(stage,3279145)
            value=field_date(lead,field)
            if value is None or value.year<2020:
                if field!=3279145:missing.append(lead['id'])
                continue
            # These CRM fields are calendar dates; today's date is not overdue.
            if value.astimezone(MSK).date()<now.astimezone(MSK).date():
                stages.append({'lead_id':lead['id'],'pipeline_id':pid,'stage':stage,'due':int(value.timestamp())})
    return {'tasks':sorted(overdue,key=lambda x:(x['due'],x['id'])),
            'stages':sorted(stages,key=lambda x:(x['due'],x['lead_id'])),
            'missing_dates':sorted(set(missing)),
            'pipeline_names':{p:cat.pipelines[p]['name'] for p in PIPELINES}}


def render(result,now):
    tasks,stages=result['tasks'],result['stages']
    lines=[f"Просрочки MANSBAND · {now.astimezone(MSK):%d.%m.%Y}",
           f"Задачи: {len(tasks)}. Сделки с просроченным сроком этапа: {len(stages)}."]
    counts=Counter(x['pipeline_id'] for x in tasks);stage_counts=Counter(x['pipeline_id'] for x in stages)
    for pid in sorted(set(counts)|set(stage_counts)):
        lines.append(f"{result['pipeline_names'][pid]}: задач {counts[pid]}, этапов {stage_counts[pid]}.")
    if result['missing_dates']:lines.append(f"Нужно указать обязательную дату: {len(result['missing_dates'])} сделок.")
    for title,rows in [('Самые давние задачи',tasks),('Просроченные этапы',stages)]:
        if not rows:continue
        lines.extend(['',title+':'])
        for row in rows[:8]:
            due=datetime.fromtimestamp(row['due'],MSK).strftime('%d.%m.%Y')
            label=f"Задача {row['id']}" if 'id' in row else row['stage']
            lines.append(f"{label}, срок {due}: https://mansband.amocrm.ru/leads/detail/{row['lead_id']}")
        if len(rows)>8:lines.append(f"Ещё {len(rows)-8}.")
    body='\n'.join(lines)
    if len(body)>4000:raise ValueError('digest_too_long')
    return body


class Telegram:
    def __init__(self,token):
        if not token:raise ValueError('missing_digest_token')
        self.http=httpx.Client(base_url='https://api.telegram.org/bot'+token+'/',timeout=20)
    def close(self):self.http.close()
    def check(self,chat_id):
        me=self.http.get('getMe').json()
        if not me.get('ok'):raise ValueError('digest_bot_unavailable')
        chat=self.http.get('getChat',params={'chat_id':chat_id}).json()
        if not chat.get('ok') or chat['result']['id']!=chat_id:raise ValueError('digest_chat_unavailable')
        member=self.http.get('getChatMember',params={'chat_id':chat_id,'user_id':me['result']['id']}).json()
        m=member.get('result',{})
        allowed=m.get('status') in ('administrator','creator') or (m.get('status')=='member' and chat['result'].get('permissions',{}).get('can_send_messages') is True)
        if not member.get('ok') or not allowed:raise ValueError('digest_send_permission_missing')
    def send(self,chat_id,body):
        # No automatic POST retry: a timeout can follow successful delivery.
        r=self.http.post('sendMessage',json={'chat_id':chat_id,'text':body,'disable_web_page_preview':True})
        result=r.json()
        if not result.get('ok'):raise ValueError('digest_send_not_confirmed')
        if result['result']['chat']['id']!=chat_id:raise ValueError('digest_wrong_destination')
        return int(result['result']['message_id'])


def deliver(db,telegram,chat_id,now,body,*,preview=True):
    key=f'{chat_id}:{now.astimezone(MSK).date()}'
    job=db.get(Digest,key)
    if job is None:
        job=Digest(id=key,body=body);db.add(job);db.commit()
    if job.state!='planned':return job.state
    if preview:
        job.body=body;db.commit();return 'preview'
    telegram.check(chat_id)
    job.body=body;db.commit()
    claimed=db.execute(update(Digest).where(Digest.id==key,Digest.state=='planned').values(state='inflight'))
    db.commit();db.refresh(job)
    if claimed.rowcount!=1:return job.state
    try:
        job.message_id=telegram.send(chat_id,job.body)
        job.state='sent';job.reason='telegram_accepted'
    except Exception:
        job.state='unknown';job.reason='send_result_not_confirmed'
    db.commit()
    return job.state


def tick(settings,now=None):
    now=now or datetime.now(timezone.utc)
    if settings.mode=='off' or now.astimezone(MSK).hour!=12:return
    if not settings.chat_id:raise ValueError('missing_digest_chat')
    from app.database import get_engine,get_session_factory
    from app.reservation_reminders import Clients,ReminderSettings
    from app.config import get_settings
    with get_engine().connect() as lock:
        if not lock.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK}).scalar():return
        try:
            with get_session_factory()() as db:
                key=f'{settings.chat_id}:{now.astimezone(MSK).date()}'
                prior=db.get(Digest,key)
                if prior and prior.state!='planned':return
                held=frozenset(db.scalars(select(MigrationEntry.lead_id).where(MigrationEntry.state.not_in(('preview','verified','rolled_back')))))
                c=Clients(db,ReminderSettings());tg=None
                try:
                    body=render(collect(c,now,held),now)
                    if settings.mode=='live':tg=Telegram(get_settings().telegram_bot_token)
                    deliver(db,tg,settings.chat_id,now,body,preview=settings.mode!='live')
                finally:
                    c.close()
                    if tg:tg.close()
        finally:lock.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':LOCK})


async def worker(settings):
    while True:
        try:await asyncio.to_thread(tick,settings)
        except Exception as exc:log.error('CRM digest: %s',type(exc).__name__)
        await asyncio.sleep(30)
