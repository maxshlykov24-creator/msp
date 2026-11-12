"""Read-only acceptance report: one frozen run, no provider calls and no publication."""
import argparse
import json
from collections import Counter
from pathlib import Path
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models import CallRecord, CallAnalysisRevision, CallDelivery, SyncState
from app.call_worker import source_hash, stable_hash, candidate_state, now
from app.call_rules import validate_analysis, evaluation_fingerprint


def semantic_checks(call_id, a):
    issues=[]
    criteria={c['id']:c for c in a.get('criteria',[])}
    events=a.get('events',[])
    def condition(cid,key):
        return next((x['status'] for x in criteria.get(cid,{}).get('conditions',[]) if x['key']==key),None)
    def require(ok,label):
        if not ok:issues.append(label)
    if call_id in (505,517,518):
        require(all(criteria.get(i,{}).get('status')!='na' for i in (4,5,6)),'unsupported_stage_exclusion')
    if call_id==489:
        require(any(e['type']=='visit' and e['owner']=='representative' and not e['condition'] for e in events),'inspector_independent_of_transport')
        require(any(e['type']=='vehicle_transport' for e in events),'transport_not_customer_visit')
        require(any(e['type']=='materials' and any('ЕГР' in proof['quote'] or 'чуть позже' in proof['quote'] for proof in e['evidence']) for e in events),'egr_followup_preserved')
    if call_id==506:
        require(all(not e['when'] for e in events if e['type']=='callback'),'callback_promise_not_deadline')
    if call_id==518:require(criteria.get(4,{}).get('stage')=='started','trade_stage_was_started')
    if call_id==488:require(criteria.get(7,{}).get('status')=='no','card_does_not_count')
    if call_id==479:
        require(all(criteria.get(i,{}).get('status')=='yes' for i in (6,7)),'confirmed_half_hour_arrival')
    if call_id==484:
        require(any(e['type']=='callback' and e['status']=='agreed' and 'пятниц' in e['when'] and not e['condition'] for e in events),'friday_callback_without_borrowed_condition')
    if call_id==508:
        require(condition(2,'purchase_time')=='yes' and bool(a.get('purchase_timing_condition')),'conditional_purchase')
        require(condition(5,'make_model')=='yes','known_customer_model')
        require(any(e['type']=='visit' and e['owner']=='representative' and not e['condition'] for e in events),'representative_visit_not_conditioned_on_purchase')
        require(a.get('outcome',{}).get('trade_in')=='not_discussed','ownership_not_trade_discussion')
    if call_id==513:
        require(any(e['type']=='valuation' and e['status']=='agreed' and e['owner']=='manager' for e in events),'manager_remote_valuation')
        require(any(e['type']=='materials' and e['owner']=='client' for e in events),'client_sends_appraisal_materials')
        require(condition(5,'make_model')!='yes','no_guessed_customer_make')
    if call_id in (513,515):
        require(any(r['kind']=='channel' and ('макс' in r['text'].lower() or 'max' in r['text'].lower()) and 'телеграм' not in r['text'].lower() for r in a.get('restrictions',[])),'chosen_max_channel')
    if call_id==478:require(a.get('outcome',{}).get('trade_in')=='discussed_unclear','buying_alternative_not_trade_refusal')
    if call_id==511:
        require(criteria.get(5,{}).get('status')!='na','credit_not_trade_refusal')
        require(any(r['kind'] in ('no_calls','client_initiates') for r in a.get('restrictions',[])),'respect_no_extra_calls')
    if call_id==519:
        require(a.get('category')=='primary_inbound','new_second_purchase')
        require(any(e['type']=='callback' and e['status']=='agreed' and 'завтра' in e['when'] for e in events),'tomorrow_callback')
    if call_id in (480,481,502):require(a.get('category')=='service','organizational_category')
    return issues


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=int,required=True)
    parser.add_argument('--revalidate',action='store_true',help='Recalculate saved observations only; no provider requests')
    args=parser.parse_args()
    gold=json.loads(Path(__file__).with_name('call_qa11_gold.json').read_text())
    with SessionLocal() as db:
        manifest=db.get(SyncState,f'calls_regrade_qa11_week_{args.run}')
        results=json.loads(manifest.value).get('results',{}) if manifest else {}
        if args.revalidate:
            if len(results)!=len(gold['calls']):raise ValueError('complete_frozen_run_required')
            for g in gold['calls']:
                revision=db.get(CallAnalysisRevision,results[str(g['id'])]['id']);row=db.get(CallRecord,g['id'])
                if revision.state not in ('ready','needs_review'):raise ValueError('draft_candidate_required')
                if source_hash(row)!=g['baseline_hash']:raise ValueError('published_source_changed')
                v=validate_analysis(revision.result['provider']['analysis'],row.transcript,row.direction,revision.rule_version)
                history=list(revision.result.get('validation_history',[]))
                if v!=revision.result['verdict'] or revision.result.get('validation_engine_hash')!=evaluation_fingerprint(revision.rule_version):
                    history.append({'verdict':revision.result['verdict'],'engine':revision.result.get('validation_engine_hash'),'replaced_at':now().isoformat()})
                revision.result={**revision.result,'verdict':v,'validation_history':history,'validation_engine_hash':evaluation_fingerprint(revision.rule_version)}
                differences=[];a=v['analysis']
                if g['expected_category'] and a.get('category')!=g['expected_category']:differences.append('category')
                if g['score_asserted'] and v.get('score')!=g['expected_score']:differences.append('score')
                if g['requires_review']:differences.append('audio_review_required')
                revision.review={**revision.review,'reference_differences':differences,'reference_note':g['expectation'],
                    'audio_review_required':g['requires_review'] or revision.review.get('audio_review_required',False)}
                revision.state=candidate_state(v,revision.rule_version)
                if differences or revision.review.get('audio_review_required') or revision.review.get('semantic_findings'):revision.state='needs_review'
                results[str(g['id'])].update(state=revision.state,score=v.get('score'),category=a.get('category'),errors=v['errors'],differences=differences)
            data=json.loads(manifest.value);data.update(results=results,status='revalidated_awaiting_semantic_review',counts=dict(Counter(r['state'] for r in results.values())))
            manifest.value=json.dumps(data);db.commit()
        report=[]
        for g in sorted(gold['calls'],key=lambda g:g['id']):
            result=results.get(str(g['id']))
            if not result:report.append({'call_id':g['id'],'pending':True});continue
            revision=db.get(CallAnalysisRevision,result['id']);row=db.get(CallRecord,g['id'])
            v=revision.result.get('verdict',{});a=v.get('analysis') or {}
            semantic=semantic_checks(g['id'],a)
            if g['expected_category'] and a.get('category')!=g['expected_category']:semantic.append('reference_category')
            if g['score_asserted'] and v.get('score')!=g['expected_score']:semantic.append('reference_score')
            hold=bool(revision.review.get('audio_review_required') or revision.review.get('reference_differences') or revision.review.get('semantic_findings') or v.get('errors') or not v.get('is_scored'))
            report.append({'call_id':g['id'],'revision':revision.id,'state':revision.state,
                'old_score':row.score,'model_score':v.get('score'),'candidate_score':None if hold else v.get('score'),
                'old_category':row.category,'category':a.get('category'),'old_denominator':row.applicable_count,'denominator':v.get('applicable_count'),
                'source_unchanged':source_hash(row)==g['baseline_hash'] and stable_hash(row.transcript)==g['transcript_hash'],
                'expected_audio_review':g['requires_review'],'model_requested_review':not v.get('is_scored') if a.get('category')=='primary_inbound' else revision.state=='needs_review',
                'validation_errors':v.get('errors',[]),'semantic_findings':semantic})
        count=db.scalar(select(func.count()).select_from(CallDelivery).where(CallDelivery.call_id.in_([g['id'] for g in gold['calls']])))
        print(json.dumps({'run':args.run,'expected':32,'finished':len(results),'delivery_count':count,
            'active_rules':db.get(SyncState,'calls_active_rule_version').value,
            'states':dict(Counter(r.get('state','pending') for r in report)),'calls':report},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
