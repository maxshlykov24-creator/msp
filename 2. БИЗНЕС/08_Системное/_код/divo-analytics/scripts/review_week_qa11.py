"""Frozen weekly candidate run. Never publishes, activates rules or creates deliveries."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models import CallRecord, CallAnalysisRevision, CallDelivery, SyncState
from app.call_worker import stage_revision, repair_revision, stable_hash, source_hash


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=int,required=True)
    parser.add_argument('--calls',type=int,nargs='*')
    args=parser.parse_args()
    gold=json.loads(Path(__file__).with_name('call_qa11_gold.json').read_text())
    entries=[g for g in gold['calls'] if not args.calls or g['id'] in args.calls]
    key=f'calls_regrade_qa11_week_{args.run}'
    def progress(result=None,status='running'):
        with SessionLocal() as db:
            state=db.get(SyncState,key)
            data=json.loads(state.value) if state else {'total':len(gold['calls']),'results':{},'rules':gold['rules'],'analysis':gold['analysis']}
            if result:data['results'][str(result['call_id'])]=result
            data.update(status=status,processed=len(data['results']),updated_at=datetime.now(timezone.utc).isoformat(),
                        counts=dict(Counter(r['state'] for r in data['results'].values())))
            if not state:state=SyncState(key=key);db.add(state)
            state.value=json.dumps(data);db.commit()
    progress()
    for g in entries:
        with SessionLocal() as db:
            row=db.get(CallRecord,g['id'])
            if stable_hash(row.transcript)!=g['transcript_hash'] or source_hash(row)!=g['baseline_hash']:
                progress(status='source_changed');raise RuntimeError('frozen_source_changed:'+str(g['id']))
        result=stage_revision(g['id'],args.run,rule_version=gold['rules'])
        if result['state'] in ('error','ambiguous','running','correcting'):
            progress(result,status='stopped_for_check');print(json.dumps(result),flush=True);return
        with SessionLocal() as db:
            rev=db.get(CallAnalysisRevision,result['id'])
            repair=bool(rev.result.get('verdict',{}).get('errors')) and not rev.result.get('correction_started_at')
        if repair:
            try:result=repair_revision(result['id'])
            except ValueError as e:result['repair_blocked']=str(e)
        with SessionLocal() as db:
            rev=db.get(CallAnalysisRevision,result['id']);v=rev.result.get('verdict',{});a=v.get('analysis') or {}
            differences=[]
            if g['expected_category'] and a.get('category')!=g['expected_category']:differences.append('category')
            if g['score_asserted'] and v.get('score')!=g['expected_score']:differences.append('score')
            if g['requires_review']:differences.append('audio_review_required')
            rev.review={**rev.review,'reference_run':args.run,'reference_differences':differences,
                        'reference_note':g['expectation'],'audio_review_required':g['requires_review'] or rev.review.get('audio_review_required',False)}
            if differences and rev.state=='ready':rev.state='needs_review'
            db.commit()
            result.update(state=rev.state,category=a.get('category'),score=v.get('score'),differences=differences,
                          errors=v.get('errors',[]))
            row=db.get(CallRecord,g['id'])
            assert source_hash(row)==g['baseline_hash'],'published_result_changed'
        progress(result);print(json.dumps(result),flush=True)
    progress(status='candidates_awaiting_review')


if __name__=='__main__':main()
