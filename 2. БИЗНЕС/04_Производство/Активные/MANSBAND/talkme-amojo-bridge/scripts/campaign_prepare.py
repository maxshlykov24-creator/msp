"""Read explicit pilot payload from stdin; create test deals without dispatching."""
import sys,json,hashlib
import httpx
from sqlalchemy import select
from app import campaign as m
from app.database import get_session_factory
from app.reservation_reminders import Clients,ReminderSettings
from app.stage_notifications import resolve_plan,StageSettings

def prepare(data):
    if data.get('campaign')!=m.CAMPAIGN or len(data.get('recipients',[]))!=3:raise ValueError('explicit_three_person_pilot_required')
    if any(x.get('test') is not True for x in data['recipients']):raise ValueError('pilot_only')
    image=httpx.get(data['asset'],timeout=25);image.raise_for_status()
    if hashlib.sha256(image.content).hexdigest()!=m.ASSET_SHA256:raise ValueError('image_changed')
    m.init()
    with get_session_factory()() as db:
        c=Clients(db,ReminderSettings())
        try:
            owner=m.duty(m.now())
            users=c.get('/api/v4/users').get('_embedded',{}).get('users',[])
            if not any(u['id']==owner and u.get('rights',{}).get('is_active') for u in users):raise ValueError('inactive_shift_owner')
            status=c.get('/api/v4/leads/pipelines/10730010/statuses/89984117')
            if status.get('name')!='Рассылка от 11.11':raise ValueError('stage_changed')
            for source in data['recipients']:
                person=m.key(source['phone'])
                j=db.scalar(select(m.Recipient).where(m.Recipient.campaign==m.CAMPAIGN,m.Recipient.person==person))
                if j:
                    print(json.dumps({'id':j.id,'state':j.state,'lead_id':j.lead_id}));continue
                contact=c.get('/api/v4/contacts/'+str(source['contact_id']))
                phones={m.phone(v['value']) for f in contact.get('custom_fields_values')or[] if f.get('field_code')=='PHONE' for v in f.get('values')or[]}
                if phones!={m.phone(source['phone'])}:raise ValueError('contact_phone_changed')
                routes=resolve_plan(db,c,{},contact,StageSettings(),ReminderSettings(),routing={'order':['telegram','max','whatsapp'],'previous_first':True,'social_fallback':False})
                if any(not r.get('recipient') for r in routes):raise ValueError('recipient_unresolved')
                j=m.Recipient(person=person,segment=1,test=True,contact_id=contact['id'],state='crm_inflight',snapshot={'text':source['text'],'variant':source['variant'],'source_sha256':data['source_sha256'],'asset_sha256':m.ASSET_SHA256,'routes':routes,'shift_owner':owner,'approved_at':m.now().isoformat()})
                db.add(j);db.commit()
                # No retry: on unknown creation result the durable claim requires manual lookup.
                payload=[{'name':f'ТЕСТ · Рассылка от 11.11 · {source["name"]}', 'pipeline_id':10730010,'status_id':89984117,'responsible_user_id':owner,'_embedded':{'contacts':[{'id':contact['id'],'is_main':True}],'tags':[{'name':'ТЕСТ рассылки'},{'name':m.CAMPAIGN}]}}]
                response=c.amo.post('/api/v4/leads',json=payload);response.raise_for_status()
                j.lead_id=response.json()['_embedded']['leads'][0]['id'];j.state='prepared';db.commit()
                lead=c.get('/api/v4/leads/'+str(j.lead_id))
                if lead.get('pipeline_id')!=10730010 or lead.get('status_id')!=89984117 or lead.get('responsible_user_id')!=owner:raise ValueError('deal_readback_mismatch')
                print(json.dumps({'id':j.id,'lead_id':j.lead_id,'state':j.state,'first_channel':routes[0]['transport'],'variant':source['variant']}))
            ctl=db.get(m.Control,'pilot')
            if ctl is None:ctl=m.Control(id='pilot',data={});db.add(ctl)
            ctl.data={'allowlist':[m.key(x['phone']) for x in data['recipients']], 'approved_at':m.now().isoformat()};db.commit()
            m.notice(db,'pilot-start','MANSBAND · Тест рассылки\nПолучатели: Максим, Нина, Полина.\nТекст из согласованных шаблонов, затем фото.\nОдин канал на человека. Массовая очередь выключена.\nРезультат проверим по статусам доставки.')
        finally:c.close()




def import_audience(data):
    """Idempotent import. CSV is authoritative; CRM scan is read-only."""
    import time
    if data.get('campaign')!=m.CAMPAIGN:raise ValueError('campaign_mismatch')
    templates={int(k):v for k,v in data['templates'].items()}
    if set(templates)!=set(range(1,16)):raise ValueError('templates_missing')
    rows=data['recipients']
    if len({m.key(x['phone']) for x in rows})!=len(rows):raise ValueError('duplicate_phone_in_source')
    m.init()
    with get_session_factory()() as db:
        c=Clients(db,ReminderSettings())
        try:
            expected={m.key(x['phone']) for x in rows}
            matches={k:[] for k in expected}
            # Entire CRM index avoids substring queries and sees shared phones.
            page=1
            while True:
                contacts=c.get('/api/v4/contacts',limit=250,page=page).get('_embedded',{}).get('contacts',[])
                for contact in contacts:
                    keys={m.key(v['value']) for f in contact.get('custom_fields_values') or [] if f.get('field_code')=='PHONE' for v in f.get('values') or []}
                    for k in keys & expected:matches[k].append(contact['id'])
                if len(contacts)<250:break
                page+=1
                if page>5000:raise ValueError('contact_scan_limit')
                time.sleep(.3)
            existing={j.person:j for j in db.scalars(select(m.Recipient).where(m.Recipient.campaign==m.CAMPAIGN))}
            counts={'queued':0,'ambiguous':0,'missing':0,'existing':0,'excluded':0}
            for index,row in enumerate(sorted(rows,key=lambda r:(r['segment'],r.get('row',0)))):
                person=m.key(row['phone'])
                if person in existing:counts['existing']+=1;continue
                segment=int(row['segment']);ids=matches[person]
                variant=index%15+1
                state='queued' if 1<=segment<=6 else 'excluded'
                reason='' if state=='queued' else 'excluded_by_source'
                if state=='queued' and len(ids)!=1:
                    state='attention' if ids else 'queued';reason='ambiguous_crm_contacts' if ids else ''
                    counts['ambiguous' if ids else 'missing']+=1
                elif state=='queued':counts['queued']+=1
                else:counts['excluded']+=1
                j=m.Recipient(person=person,contact_id=ids[0] if len(ids)==1 else 0,segment=segment,test=False,state=state,reason=reason,
                    snapshot={'name':row['name'],'phone':m.phone(row['phone']),'template':templates[variant],'variant':variant,'routes':[],
                    'source_sha256':data['source_sha256'],'template_sha256':data['template_sha256'],'asset_sha256':m.ASSET_SHA256,
                    'last_purchase':row.get('last_purchase',0),'source_row':row.get('row',0),'matched_contact_ids':ids})
                db.add(j)
            ctl=db.get(m.Control,'audience-import')
            if not ctl:ctl=m.Control(id='audience-import',data={});db.add(ctl)
            ctl.data={'source_sha256':data['source_sha256'],'counts':counts,'at':m.now().isoformat()}
            db.commit();print(json.dumps(counts))
        finally:c.close()

if __name__=='__main__':
    (import_audience if '--audience' in sys.argv else prepare)(json.load(sys.stdin))
