"""Verify published monthly revisions against original snapshots, without provider requests."""
import argparse
import json
from collections import Counter
from datetime import datetime,timezone
from sqlalchemy import select,func
from sqlalchemy.orm import defer
from app.database import SessionLocal
from app.models import CallRecord,CallAnalysisRevision,CallDelivery,SyncState
from app.call_worker import snapshot,stable_hash,manual_history_hash,now
from app.call_rules import V3_RULE_VERSION


def main():
 p=argparse.ArgumentParser();p.add_argument('--month',required=True);p.add_argument('--run',type=int,required=True);p.add_argument('--finalize',action='store_true');args=p.parse_args()
 key=f'calls_regrade_month_{args.month}_{args.run}'
 with SessionLocal() as db:
  manifest=json.loads(db.get(SyncState,key).value);issues=[];held=[];published=0;scored=0;unscored=Counter();states=Counter();versions=Counter();suppressed=0
  for cid in manifest['ids']:
   row=db.get(CallRecord,cid);states[row.state]+=1;versions[row.analysis_version or 'none']+=1
   suppressed+=bool((row.provider_result or {}).get('reanalysis_delivery_suppressed'))
   if row.analysis_version=='sonnet.qa.11' and row.state in ('complete','needs_review','insufficient','ignored_no_conversation'):
    published+=1
    if row.is_scored:scored+=1
    else:unscored[row.category]+=1
   if row.state=='needs_review':held.append({'call_id':cid,'errors':row.validation_errors})
   if row.is_scored and (row.score is None or row.validation_errors or row.state=='needs_review'):issues.append(f'{cid}:unsafe_score')
   if cid in manifest['analysis_ids']:
    result=manifest['results'].get(str(cid))
    if not result:issues.append(f'{cid}:not_finished')
    elif result['state'] not in ('complete','needs_review'):issues.append(f'{cid}:result_unresolved')
    else:
     rev=db.get(CallAnalysisRevision,result['id'])
     if rev.state!='published' or row.rule_version!=V3_RULE_VERSION or row.analysis_version!='sonnet.qa.11':issues.append(f'{cid}:not_published_qa11')
     if rev.base_hash!=manifest['baseline_hashes'][str(cid)]:issues.append(f'{cid}:baseline_changed')
     if stable_hash(row.transcript)!=rev.transcript_hash:issues.append(f'{cid}:transcript_changed')
     if snapshot(row)!=rev.result.get('snapshot'):issues.append(f'{cid}:published_snapshot_changed')
     if manual_history_hash(row)!=rev.result.get('published_manual_history_hash'):issues.append(f'{cid}:manual_history_changed')
     if rev.review.get('decision')=='unresolved_no_score' and (row.score is not None or row.state!='needs_review'):issues.append(f'{cid}:hold_lost')
   db.expunge_all()
  changed_deliveries=db.scalar(select(func.count()).select_from(CallDelivery).where(CallDelivery.call_id.in_(manifest['ids']),CallDelivery.updated_at>=datetime.fromtimestamp(manifest['end'],timezone.utc)))
  delivery_count=db.scalar(select(func.count()).select_from(CallDelivery).where(CallDelivery.call_id.in_(manifest['ids'])))
  if changed_deliveries:issues.append('historical_deliveries_changed')
  if suppressed!=len(manifest['ids']):issues.append('delivery_suppression_missing')
  pending=[cid for cid in manifest['ids'] if db.get(CallRecord,cid).state in ('ready','processing','analysis_ready','analyzing','submitting','submit_ambiguous','analysis_ambiguous','error')]
  if pending:issues.append('processing_not_finished')
  report={'month':args.month,'manifest_status':manifest['status'],'records':len(manifest['ids']),'regraded':len(manifest['results']),'expected_regrade':len(manifest['analysis_ids']),
   'qa11_processed':published,'scored':scored,'unscored_categories':dict(unscored),'held':held,'states':dict(states),'versions':dict(versions),
   'historical_delivery_count':delivery_count,'historical_deliveries_changed':changed_deliveries,'delivery_suppressed_records':suppressed,
   'pending':pending,'issues':issues,'active_rules':db.get(SyncState,'calls_active_rule_version').value}
  if args.finalize:
   if issues:raise ValueError('monthly_verification_failed:'+','.join(issues))
   archived=0
   for rev in db.scalars(select(CallAnalysisRevision).options(defer(CallAnalysisRevision.result)).where(CallAnalysisRevision.call_id.in_(manifest['ids']),CallAnalysisRevision.state.in_(['ready','needs_review','verified']))):
    row=db.get(CallRecord,rev.call_id)
    if row.analysis_version=='sonnet.qa.11' and (row.provider_result or {}).get('published_revision_id'):
     rev.state='superseded';rev.review={**rev.review,'superseded_by_revision':row.provider_result['published_revision_id']};archived+=1
   week=db.get(SyncState,'calls_regrade_qa11_week_119');w=json.loads(week.value);w['status']='published';w['publication_note']='Accepted by owner; unresolved cases published without final score.';week.value=json.dumps(w);db.commit()
   state=db.scalar(select(SyncState).where(SyncState.key==key).with_for_update());m=json.loads(state.value);m.update(status='finished',finished_at=now().isoformat(),final_audit=report,updated_at=now().isoformat());state.value=json.dumps(m);db.commit()
   report['manifest_status']='finished';report['superseded_obsolete_candidates']=archived
  print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
