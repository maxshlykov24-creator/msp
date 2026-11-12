"""Explicitly authorized monthly rollout; resumable paid jobs and scoreless review holds."""
import argparse
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.database import SessionLocal
from app.models import CallRecord,CallAnalysisRevision,SyncState,CallDelivery
from app.call_worker import (now,source_hash,stage_revision,repair_revision,review_revision,publish_revision,
    publish_review_hold,collect_calls,freeze_delivery)
from app.call_rules import V3_RULE_VERSION
from app.nexara import SonnetCallAgent
from app.amo_client import AmoClient


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=int,required=True);p.add_argument('--month',required=True)
    p.add_argument('--prepare',action='store_true');p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=1)
    args=p.parse_args();start=int(datetime.strptime(args.month,'%Y-%m').replace(tzinfo=ZoneInfo('Europe/Moscow')).timestamp())
    key=f'calls_regrade_month_{args.month}_{args.run}'
    def save(result=None,status='running'):
        with SessionLocal() as db:
            st=db.scalar(select(SyncState).where(SyncState.key==key).with_for_update());m=json.loads(st.value)
            if result:m['results'][str(result['call_id'])]=result
            m.update(processed=len(m['results']),counts=dict(Counter(r['state'] for r in m['results'].values())),updated_at=now().isoformat())
            if status=='stopped_for_check':m['stop_requested']=True
            m['status']='stopped_for_check' if m.get('stop_requested') else ('finished' if m['processed']==len(m['analysis_ids']) else status)
            st.value=json.dumps(m);db.commit()
    if args.prepare:
        with SessionLocal() as db:
            if db.get(SyncState,key):raise ValueError('manifest_already_exists')
        amo=AmoClient()
        try:collected=collect_calls(amo,since_at=start)
        finally:amo.close()
        with SessionLocal() as db:
            end=int(now().timestamp());rows=list(db.scalars(select(CallRecord).where(CallRecord.occurred_at>=start,CallRecord.occurred_at<=end).order_by(CallRecord.id)))
            ids=[];sources={};retried=[]
            for row in rows:
                for d in db.scalars(select(CallDelivery).where(CallDelivery.call_id==row.id,CallDelivery.state=='pending')):freeze_delivery(db,d,row)
                row.provider_result={**(row.provider_result or {}),'reanalysis_delivery_suppressed':True}
                if row.analysis and row.duration_sec>=30 and SonnetCallAgent.catalog(row.transcript):ids.append(row.id);sources[str(row.id)]=source_hash(row)
                if row.state=='error' and row.last_error=='nexara_submit_http_402' and not row.nexara_job_id:
                    row.state='ready';row.last_error='';row.next_attempt_at=None;retried.append(row.id)
                if row.state=='recording_unavailable' and not row.nexara_job_id:
                    row.state='waiting_recording';row.next_attempt_at=None
            week=json.loads(db.get(SyncState,'calls_regrade_qa11_week_119').value)
            m={'month':args.month,'start':start,'end':end,'ids':[r.id for r in rows],'total':len(ids),'analysis_ids':ids,'baseline_hashes':sources,
               'weekly_candidates':{cid:r['id'] for cid,r in week['results'].items() if int(cid) in ids},'results':{},'processed':0,'counts':{},'status':'prepared','updated_at':now().isoformat(),'retried_payment_declines':retried}
            db.add(SyncState(key=key,value=json.dumps(m)));db.commit()
            print(json.dumps({'records':len(rows),'analysis_jobs':len(ids),'weekly_reuse':len(m['weekly_candidates']),'payment_declines_requeued':len(retried),'collected_notes':collected}),flush=True)
        return
    with SessionLocal() as db:m=json.loads(db.get(SyncState,key).value)
    for index,cid in enumerate(m['analysis_ids']):
        if index%args.shards!=args.shard:continue
        with SessionLocal() as db:
            current=json.loads(db.get(SyncState,key).value)
            if current.get('stop_requested'):return
            if str(cid) in current['results']:continue
            row=db.get(CallRecord,cid)
            # A crash after publication but before manifest commit must not trigger another paid request.
            published=db.scalar(select(CallAnalysisRevision).where(CallAnalysisRevision.call_id==cid,CallAnalysisRevision.rule_version==V3_RULE_VERSION,CallAnalysisRevision.state=='published').order_by(CallAnalysisRevision.id.desc()).limit(1))
            if published and row.analysis_version=='sonnet.qa.11':
                result={'call_id':cid,'id':published.id,'state':row.state,'score':row.score,'recovered':True}
            elif source_hash(row)!=m['baseline_hashes'][str(cid)]:raise ValueError(f'month_source_changed:{cid}')
            else:result=None
        if result:save(result);continue
        rid=m['weekly_candidates'].get(str(cid))
        if rid:
            with SessionLocal() as db:rev=db.get(CallAnalysisRevision,rid);result={'id':rid,'call_id':cid,'state':rev.state}
        else:
            result=stage_revision(cid,args.run,rule_version=V3_RULE_VERSION)
            if result['state']=='needs_review':
                with SessionLocal() as db:
                    rev=db.get(CallAnalysisRevision,result['id']);repairable=bool(rev.result.get('verdict',{}).get('errors')) and not rev.result.get('correction_started_at')
                if repairable:
                    try:result=repair_revision(result['id'])
                    except ValueError:pass
        if result['state']=='ready':
            review_revision(result['id'],'codex_qa11_rollout','Owner authorized monthly publication; schema, evidence and source checks passed.')
            published=publish_revision(result['id']);result.update(state='complete',score=published['score'])
        elif result['state']=='verified':
            published=publish_revision(result['id']);result.update(state='complete',score=published['score'])
        elif result['state']=='needs_review':
            published=publish_review_hold(result['id'],'codex_qa11_rollout','Нужна проверка записи или доказательств. Окончательная оценка не установлена.')
            result.update(state='needs_review',score=None)
        else:
            save(result,'stopped_for_check');print(json.dumps(result),flush=True);return
        save(result);print(json.dumps(result),flush=True)
    save()

if __name__=='__main__':main()
