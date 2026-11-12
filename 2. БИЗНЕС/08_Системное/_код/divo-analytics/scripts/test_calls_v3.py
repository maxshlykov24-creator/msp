"""QA11 regressions: separate observations, strict points, immutable history."""
import copy
import unittest
import json
import httpx
from unittest.mock import patch
from scripts.test_calls import analysis_v2, RevisionTest
from app.call_rules import validate_analysis, V3_RULE_VERSION, analysis_schema, analysis_prompt


TEXT='Автосалон. Обмен рассматриваете? Пока не знаю. Lexus RX350. Завтра сын посмотрит, если подходит, сразу заберу. Перезвоню в пятницу. Хорошо, договорились. Через полчаса приеду. Отправлю визитку. Предварительно оценить автомобиль? Да, пришлите фотографии. Не звоните, сам позвоню. Год 2020. Пробег 30000. Не подходит, не будем продолжать подбор. До свидания.'
TR={'text':TEXT,'duration':60,'segments':[{'text':TEXT,'start':0,'end':60}]}
def ev(q):return {'quote':q,'start':0,'end':60}
def raw():
    a=analysis_v2()
    a.update(category_evidence=[ev('Автосалон.')],facts=[],events=[],restrictions=[],uncertainties=[],
             purchase_context='new_need',purchase_context_evidence=[],purchase_timing_condition='',purchase_timing_evidence=[])
    a['outcome'].update(trade_in='not_discussed',trade_in_evidence=[],meeting_evidence=[])
    a['next_step']['evidence']=[]
    for c in a['criteria']:
        c.update(status='no',evidence=[],stage='not_started',stage_evidence=[])
        for x in c['conditions']:x.update(status='no',evidence=[],fact_ids=[])
    return a
def event(kind='callback',when='в пятницу',precision='day',owner='manager',status='agreed'):
    quote={'callback':'Перезвоню в пятницу.','materials':'Отправлю визитку.','visit':'Через полчаса приеду.','valuation':'Предварительно оценить автомобиль?'}[kind]
    return dict(id='e1',type=kind,status=status,owner=owner,recipient='client',when=when,precision=precision,condition='',
        evidence=[ev(quote)],agreement_evidence=[ev('Хорошо, договорились.')] if status=='agreed' else [],
        time_evidence=[ev(when)] if when else [],condition_evidence=[],purpose_evidence=[ev(quote)] if kind=='valuation' else [])
def fact(i,kind,value,subject='customer',subject_id='one'):
    return dict(id=i,kind=kind,value=value,subject=subject,subject_id=subject_id,status='known',evidence=[ev(value)])

class RulesV3Test(unittest.TestCase):
    def check(self,a):return validate_analysis(a,TR,'in',V3_RULE_VERSION)
    def check_extra(self,a,extra):
        text=TEXT+' '+extra
        return validate_analysis(a,{'text':text,'duration':60,'segments':[{'text':text,'start':0,'end':60}]},'in',V3_RULE_VERSION)
    def test_vague_time_cannot_be_promoted_by_precision_label(self):
        for label in ('day','bounded','fixed'):
            a=raw();a['events']=[event(when='Позже',precision=label)]
            r=self.check_extra(a,'Позже');self.assertEqual(r['score'],0)
            self.assertEqual(r['analysis']['events'][0]['precision'],'none')
    def test_callback_action_is_not_time(self):
        a=raw();a['events']=[event(when='Перезвоню в пятницу.',precision='day')]
        a['events'][0]['when']='Перезвоню';a['events'][0]['time_evidence']=[ev('Перезвоню')]
        r=self.check(a);self.assertEqual(r['analysis']['events'][0]['when'],'');self.assertEqual(r['score'],0)
    def test_financing_refusal_does_not_exclude_trade(self):
        a=raw();a['outcome'].update(trade_in='refused',trade_in_evidence=[ev('Кредит не нужен')])
        r=self.check_extra(a,'Кредит не нужен');self.assertNotEqual(r['analysis']['criteria'][4]['status'],'na');self.assertIsNone(r['score'])
    def test_unlinked_cancellation_holds_old_appointment(self):
        a=raw();a['events']=[event('visit','Через полчаса','bounded','client'),{**event('visit','Через полчаса','bounded','client',status='declined'),'id':'cancel','evidence':[ev('Отменяем этот приезд, не приеду.')]}]
        r=self.check_extra(a,'Отменяем этот приезд, не приеду.');self.assertIsNone(r['score']);self.assertIn('event:cancel:unlinked_cancellation',r['errors'])
        a['events'][1]['supersedes']=['e1'];r=self.check_extra(a,'Отменяем этот приезд, не приеду.')
        self.assertEqual(r['score'],0);self.assertFalse(r['analysis']['outcome']['meeting_agreed'])
        self.assertEqual(r,self.check_extra(r['analysis'],'Отменяем этот приезд, не приеду.'))
    def test_event_link_requires_explicit_correction(self):
        a=raw();a['events']=[event(),{**event(),'id':'later','supersedes':['e1']}]
        self.assertIn('event:later:supersedes_proof_missing',self.check(a)['errors'])
    def test_conditional_visit_is_tentative(self):
        a=raw();a['events']=[event('visit','Через полчаса','bounded','client')]
        a['events'][0].update(condition='если успею',condition_evidence=[ev('если успею')])
        r=self.check_extra(a,'если успею');self.assertEqual(r['score'],0);self.assertFalse(r['analysis']['outcome']['meeting_agreed'])
    def test_conflicting_current_values_need_resolution(self):
        a=raw();a['facts']=[fact('y1','year','2020','target'),fact('y2','year','2021','target'),fact('m','mileage','30000','target')]
        r=self.check_extra(a,'Год 2021.');self.assertIsNone(r['score'])
        a['facts'][0]['status']='superseded';self.assertEqual(self.check_extra(a,'Год 2021.')['score'],14.29)
    def test_second_vehicle_does_not_block_complete_first(self):
        a=raw();a['facts']=[fact('y','year','2020','target'),fact('m','mileage','30000','target'),fact('other','color','красный','target','two')]
        r=self.check_extra(a,'красный');self.assertEqual(r['score'],14.29);self.assertEqual(r['errors'],[])
    def test_unanswered_question_is_not_known_fact(self):
        a=raw();a['facts']=[fact('year','year','2021','target'),fact('m','mileage','30000','target')]
        a['facts'][0]['evidence']=[ev('Год 2021?')]
        r=self.check_extra(a,'Год 2021?');self.assertIsNone(r['score']);self.assertEqual(r['analysis']['facts'][0]['status'],'uncertain')
    def test_confirmation_is_added_to_question_proof_once(self):
        a=raw();a['facts']=[fact('year','year','2021','target')];a['facts'][0]['evidence']=[{'quote':'Год 2021?','start':1,'end':2}]
        tr={'text':'Год 2021? Да, верно.','duration':4,'segments':[{'text':'Год 2021?','start':1,'end':2},{'text':'Да, верно.','start':2.2,'end':3}]}
        a['category_evidence']=[];r=validate_analysis(a,tr,'in',V3_RULE_VERSION)
        self.assertEqual(len(r['analysis']['facts'][0]['evidence']),2)
        self.assertEqual(r,validate_analysis(r['analysis'],tr,'in',V3_RULE_VERSION))
    def test_original_and_numeric_mileage_are_not_conflicting(self):
        a=raw();a['facts']=[fact('m','mileage','30000','target'),fact('h','mileage','пробег оригинальный','target')]
        self.assertEqual(self.check_extra(a,'пробег оригинальный')['score'],14.29)
    def test_diagnostic_errors_survive_normalized_revalidation(self):
        a=raw();a['facts']=[fact('x','make','BMW')];a['facts'][0]['evidence']=[ev('RX350')]
        r=self.check(a);self.assertEqual(r,self.check(r['analysis']));self.assertIn('fact:x:value_not_literal',r['errors'])

    def test_business_hours_are_not_appointment_time(self):
        a=raw();a['events']=[event('visit','сегодня с 10 до 18','bounded','client')]
        a['events'][0]['time_evidence']=[ev('сегодня с 10 до 18 работаем')]
        self.assertEqual(self.check_extra(a,'сегодня с 10 до 18 работаем')['score'],0)
    def test_independent_inspector_not_conditioned_on_transport(self):
        a=raw();a['events']=[event('visit','','none','representative',status='proposed')]
        a['events'][0].update(condition='эвакуатор',condition_evidence=[ev('эвакуатор')],evidence=[ev('два варианта: специалист или эвакуатор')])
        r=self.check_extra(a,'два варианта: специалист или эвакуатор')
        self.assertIn('event:e1:alternative_condition_requires_review',r['errors'])

    def test_vehicle_transport_is_not_a_customer_visit(self):
        a=raw();a['events']=[event('visit','Через полчаса','bounded','manager')]
        a['events'][0].update(condition='эвакуатор',condition_evidence=[ev('эвакуатор')],evidence=[ev('доставим на эвакуаторе')])
        r=self.check_extra(a,'эвакуатор, доставим на эвакуаторе')
        self.assertEqual(r['analysis']['events'][0]['type'],'vehicle_transport')
        self.assertEqual(r['score'],0);self.assertFalse(r['analysis']['outcome']['meeting_agreed'])

    def test_callback_specific_day_counts(self):
        a=raw();a['events']=[event()];r=self.check(a)
        self.assertEqual(r['errors'],[]);self.assertEqual(r['score'],14.29)
        self.assertEqual(self.check(r['analysis']),r)
    def test_unknown_owner_does_not_turn_known_deadline_into_failure(self):
        a=raw();a['events']=[event(owner='unknown')]
        r=self.check(a);self.assertIsNone(r['score'])
        self.assertEqual(r['analysis']['criteria'][6]['status'],'unknown')
        a['events'].append({**event('materials','','none'),'id':'e2'})
        self.assertIsNone(self.check(a)['score'])
    def test_materials_dont_count_or_borrow_visit_time(self):
        a=raw();a['events']=[event('materials'),{**event('visit','Завтра','day',status='tentative'),'id':'e2'}]
        self.assertEqual(self.check(a)['analysis']['criteria'][6]['status'],'no')
    def test_representative_half_hour_visit(self):
        a=raw();a['events']=[event('visit','Через полчаса','bounded','representative')]
        r=self.check(a);self.assertEqual(r['errors'],[]);self.assertEqual(r['score'],28.57)
    def test_visit_day_without_time_not_enough(self):
        a=raw();a['events']=[event('visit','Завтра','day')]
        self.assertEqual(self.check(a)['score'],0)
    def test_observed_invitation_survives_missing_event_without_inventing_time(self):
        a=raw();c=a['criteria'][5]
        c['conditions'][0].update(status='yes',evidence=[ev('Через полчаса приеду.')])
        r=self.check(a)
        self.assertEqual(r['analysis']['criteria'][5]['conditions'][0]['status'],'yes')
        self.assertEqual(r['analysis']['events'][0]['status'],'proposed');self.assertEqual(r['score'],0)
        self.assertEqual(self.check(r['analysis']),r)
    def test_future_sending_cannot_be_marked_completed(self):
        a=raw();a['events']=[event('materials','','none',status='completed')]
        self.assertEqual(self.check(a)['analysis']['events'][0]['status'],'proposed')
    def test_stage_question_is_not_achievement(self):
        a=raw();a['criteria'][3].update(stage='started',stage_evidence=[ev('Обмен рассматриваете?')])
        r=self.check(a);self.assertEqual(r['analysis']['outcome']['trade_in'],'discussed_unclear')
        self.assertEqual(r['analysis']['criteria'][3]['status'],'no')
    def test_refusal_cannot_exclude_started_stage(self):
        a=raw();c=a['criteria'][3];c.update(stage='started',stage_evidence=[ev('Обмен рассматриваете?')],na_reason='no_suitable_offer')
        a['scenario'].update(kind='no_suitable_offer',mismatch_confirmed=True,customer_declined=True,dialogue_closed=True,
          mismatch_evidence=[ev('Не подходит')],refusal_evidence=[ev('не будем продолжать подбор')],closure_evidence=[ev('До свидания.')])
        r=self.check(a);self.assertIn('criterion_4:unproven_na',r['errors']);self.assertIsNone(r['score'])
    def test_goodbye_alone_cannot_exclude_stages(self):
        a=raw();a['scenario'].update(kind='no_suitable_offer',mismatch_confirmed=True,customer_declined=True,dialogue_closed=True)
        self.assertIn('scenario:separate_mismatch_refusal_closure_required',self.check(a)['errors'])
    def test_customer_model_independent_of_trade(self):
        a=raw();a['facts']=[fact('make','make','Lexus'),fact('model','model','RX350')]
        r=self.check(a);c=r['analysis']['criteria'][4]
        self.assertEqual(c['conditions'][0]['status'],'yes');self.assertEqual(c['status'],'no');self.assertEqual(r['errors'],[])
    def test_explicit_lexus_and_spoken_rx_do_not_require_guessing_brand(self):
        a=raw();a['facts']=[fact('make','make','Лексус'),fact('model','model','350 Эрекс')]
        a['facts'][1]['status']='uncertain'
        text=TEXT+' Лексус, 350 Эрекс.'
        tr={'text':text,'duration':60,'segments':[{'text':text,'start':0,'end':60}]}
        r=validate_analysis(a,tr,'in',V3_RULE_VERSION)
        self.assertEqual(r['analysis']['criteria'][4]['conditions'][0]['status'],'yes')
        self.assertEqual(r['analysis']['facts'][1]['value'],'350 Эрекс')
        a['facts'][0]['status']='uncertain'
        self.assertEqual(validate_analysis(a,tr,'in',V3_RULE_VERSION)['analysis']['criteria'][4]['conditions'][0]['status'],'no')
    def test_remote_valuation_chain_is_partial(self):
        a=raw();a['events']=[event('valuation','','none')]
        r=self.check(a);c=r['analysis']['criteria'][4]
        self.assertEqual(c['conditions'][-1]['status'],'yes');self.assertEqual(c['status'],'no')
    def test_photo_request_without_valuation_purpose_rejected(self):
        a=raw();a['events']=[event('valuation','','none')];a['events'][0]['purpose_evidence']=[]
        self.assertIn('event:e1:valuation_purpose_missing',self.check(a)['errors'])
    def test_guessed_make_rejected(self):
        a=raw();a['facts']=[fact('x','make','BMW')];a['facts'][0]['evidence']=[ev('RX350')]
        r=self.check(a);self.assertIn('fact:x:value_not_literal',r['errors'])
        self.assertNotEqual(r['analysis']['facts'][0]['value'],'BMW')
    def test_two_facts_cannot_come_from_different_cars(self):
        a=raw();a['facts']=[fact('a','year','2020','target','one'),fact('b','mileage','30000','target','two')]
        for c,f in zip(a['criteria'][2]['conditions'],a['facts']):c.update(status='yes',evidence=f['evidence'],fact_ids=[f['id']])
        self.assertIn('criterion_3:mixed_vehicles',self.check(a)['errors'])
        a['facts'][1]['subject_id']='one';self.assertEqual(self.check(a)['errors'],[])
    def test_two_customer_supplied_properties_count_without_model_selected_ids(self):
        a=raw();a['facts']=[fact('a','year','2020','target'),fact('b','mileage','30000','target')]
        r=self.check(a);self.assertEqual(r['score'],14.29)
        self.assertEqual(r['analysis']['criteria'][2]['status'],'yes')
    def test_stock_count_is_not_a_property_of_one_car(self):
        a=raw();a['facts']=[fact('a','year','2020','target'),fact('b','other','50 штук в наличии','target')]
        text=TEXT+' 50 штук в наличии.'
        tr={'text':text,'duration':60,'segments':[{'text':text,'start':0,'end':60}]}
        self.assertEqual(validate_analysis(a,tr,'in',V3_RULE_VERSION)['score'],0)
    def test_superseded_fact_cannot_score(self):
        a=raw();f=fact('a','year','2020','target');f['status']='superseded';a['facts']=[f]
        a['criteria'][2]['conditions'][0].update(status='yes',evidence=f['evidence'],fact_ids=['a'])
        self.assertIn('criterion_3:unproven_target_fact',self.check(a)['errors'])
    def test_no_calls_restriction_invalidates_pending_callback(self):
        a=raw();a['events']=[event()];a['restrictions']=[dict(kind='no_calls',text='Не звоните',evidence=[ev('Не звоните')])]
        r=self.check(a);self.assertEqual(r['score'],0);self.assertIn('ограничение',r['analysis']['recommendation'])
    def test_unknown_only_blocks_when_score_can_change(self):
        a=raw();a['speech_clear']=False;a['uncertainties']=[dict(criterion_id=1,reason='Неясное имя',evidence=[],quality_flags=['speech_clear'],condition_keys=['self_name'])]
        self.assertEqual(self.check(a)['score'],0)
        for x in a['criteria'][0]['conditions']:x.update(status='yes',evidence=[ev('Автосалон.')])
        a['criteria'][0]['conditions'][1].update(status='unknown',evidence=[])
        self.assertIsNone(self.check(a)['score'])
    def test_display_uncertainty_does_not_cancel_proven_condition(self):
        a=raw();a['events']=[event()]
        a['uncertainties']=[dict(criterion_id=7,impact='display',reason='Искажено написание',evidence=[])]
        self.assertEqual(self.check(a)['score'],14.29)
        a['recording_complete']=False
        self.assertIsNone(self.check(a)['score'])
    def test_unclear_failed_condition_is_not_an_independent_proven_failure(self):
        a=raw()
        for x in a['criteria'][0]['conditions']:x.update(status='yes',evidence=[ev('Автосалон.')])
        a['criteria'][0]['conditions'][1].update(status='no',evidence=[])
        a['uncertainties']=[dict(criterion_id=1,impact='score',condition_keys=['self_name'],reason='Неясное имя',evidence=[])]
        r=self.check(a)
        self.assertEqual(r['analysis']['criteria'][0]['status'],'unknown');self.assertIsNone(r['score'])
        a['criteria'][0]['conditions'][0].update(status='no',evidence=[])
        self.assertEqual(self.check(a)['score'],0)
    def test_clear_omission_is_not_asr_uncertainty(self):
        a=raw();a['uncertainties']=[dict(criterion_id=1,impact='score',condition_keys=['salon'],reason='Менеджер не назвал название салона',evidence=[])]
        r=self.check(a);self.assertEqual(r['score'],0)
        self.assertEqual(self.check(r['analysis']),r)
    def test_missing_name_after_introduction_requires_audio(self):
        a=raw()
        for x in a['criteria'][0]['conditions']:x.update(status='yes',evidence=[ev('Автосалон.')])
        text=TEXT+' Меня зовут, я вас слушаю.'
        tr={'text':text,'duration':60,'segments':[{'text':text,'start':0,'end':60}]}
        r=validate_analysis(a,tr,'in',V3_RULE_VERSION)
        self.assertIsNone(r['score']);self.assertEqual(r['analysis']['criteria'][0]['conditions'][1]['status'],'unknown')
    def test_photo_sender_is_not_appraiser(self):
        a=raw();a['events']=[event('valuation','','none','client')]
        self.assertIn('event:e1:valuation_owner_is_appraiser_not_photo_sender',self.check(a)['errors'])
    def test_unknown_versions_rejected(self):
        self.assertIn('unsupported_rule_version',validate_analysis({},TR,'in','future')['errors'])
        for fn,args in ((analysis_schema,('future',)),(analysis_prompt,('in',0,'future'))):
            with self.assertRaises((KeyError,ValueError)):fn(*args)
    def test_repeat_requires_same_purchase(self):
        a=raw();a.update(category='repeat',criteria=[],purchase_context='new_need')
        self.assertIn('category:repeat_requires_same_purchase',self.check(a)['errors'])

    def test_provider_wire_expands_and_keeps_local_validation(self):
        from app.nexara import SonnetCallAgent
        a=raw()
        for k in ('summary','category_reason','recommendation','next_step'):a.pop(k)
        for group,keys in [('quality',('recording_complete','speech_clear','roles_reliable')),('purchase',('purchase_context','purchase_context_evidence','purchase_timing_condition','purchase_timing_evidence'))]:
            a[group]={k:a.pop(k) for k in keys}
        a['quality']['conversation_occurred']=True
        a['category_evidence']=[{'quote':'s0001'}]
        for c in a['criteria']:
            for k in ('status','explanation','evidence'):c.pop(k)
        for k in ('meeting_agreed','meeting_when','next_contact','meeting_evidence'):a['outcome'].pop(k)
        client=SonnetCallAgent.__new__(SonnetCallAgent)
        client.client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={
            'choices':[{'finish_reason':'stop','message':{'content':'```json\n'+json.dumps(a)+'\n```'}}]})))
        try:r=client.analyze(TR,'in',0,rule_version=V3_RULE_VERSION)
        finally:client.close()
        self.assertEqual(self.check(r['analysis'])['score'],0)
        body,_=SonnetCallAgent.request_body(TR,'in',0,rule_version=V3_RULE_VERSION)
        self.assertEqual(body['response_format']['type'],'json_object')
        wire=json.loads(body['messages'][0]['content'].split('Верни только JSON-объект по этой схеме:\n')[1])
        self.assertLess(len(wire['properties']),15)
        self.assertIn('quality',wire['properties'])

class CandidateV3Test(RevisionTest):
    def test_semantic_correction_is_durable_and_bounded(self):
        from app import call_worker
        from app.nexara import SonnetCallAgent
        from scripts.test_calls import analysis_v2
        row,previous=self.candidate()
        previous.result={**previous.result,'provider':{'analysis':analysis_v2(),'references':{'saved':'observation'}}};self.db.commit()
        with patch.object(call_worker,'SonnetCallAgent') as agent:
            agent.catalog=SonnetCallAgent.catalog;agent.return_value.analyze.return_value={'analysis':analysis_v2()}
            first=call_worker.stage_revision(row.id,correction_revision=previous.id,findings=['Missing event'])
            second=call_worker.stage_revision(row.id,correction_revision=previous.id,findings=['Missing event'])
            self.assertEqual(first['id'],second['id']);agent.return_value.analyze.assert_called_once()
            with self.assertRaisesRegex(ValueError,'semantic_correction_already_attempted'):
                call_worker.stage_revision(row.id,correction_revision=previous.id,findings=['Another request'])
    def test_changed_calculation_cannot_publish_under_old_approval(self):
        from app import call_worker
        row,rev=self.candidate();call_worker.review_revision(rev.id,'test','Checked')
        changed=copy.deepcopy(rev.result['verdict']);changed['score']=12.34
        with patch.object(call_worker,'validate_analysis',return_value=changed):
            with self.assertRaisesRegex(ValueError,'revision_verdict_changed_since_review'):
                call_worker.publish_revision(rev.id)

    def test_publish_unresolved_keeps_no_score_and_history(self):
        from app import call_worker
        row,rev=self.candidate();rev.rule_version=V3_RULE_VERSION;rev.analysis_version='sonnet.qa.11';rev.state='needs_review'
        a=raw();v=validate_analysis(a,TR,'in',V3_RULE_VERSION)
        row.transcript=TR;self.db.commit()
        rev.base_hash=call_worker.source_hash(row);rev.transcript_hash=call_worker.stable_hash(TR)
        rev.result={**rev.result,'verdict':v,'provider':{'analysis':a},**call_worker.candidate_validation_metadata(V3_RULE_VERSION)}
        rev.review={**rev.review,'audio_review_required':True};self.db.commit()
        call_worker.publish_review_hold(rev.id,'test','Unresolved recording');self.db.expire_all()
        self.assertIsNone(row.score);self.assertFalse(row.is_scored);self.assertEqual(row.state,'needs_review')
        self.assertTrue(row.provider_result['reanalysis_delivery_suppressed'])
        self.assertEqual(rev.review['decision'],'unresolved_no_score')
        call_worker.rollback_revision(rev.id,'test','Rollback hold');self.db.expire_all()
        self.assertEqual(row.analysis['summary'],'Обсудили автомобиль')

    def test_invalid_provider_schema_can_be_published_only_as_scoreless_hold(self):
        from app import call_worker
        row,rev=self.candidate();rev.rule_version=V3_RULE_VERSION;rev.analysis_version='sonnet.qa.11';rev.state='needs_review'
        v=validate_analysis({},row.transcript,'in',V3_RULE_VERSION)
        rev.result={**rev.result,'verdict':v,'provider':{'analysis':{}},**call_worker.candidate_validation_metadata(V3_RULE_VERSION)};self.db.commit()
        call_worker.publish_review_hold(rev.id,'test','Provider response requires review');self.db.expire_all()
        self.assertEqual(row.analysis,{});self.assertEqual(row.category,'insufficient')
        self.assertEqual(row.state,'needs_review');self.assertIsNone(row.score);self.assertTrue(row.validation_errors)

    def test_published_qa11_review_is_visible_without_score_or_valid_analysis(self):
        from app import calls_api
        from app.models import CallRecord
        from sqlalchemy import select
        row=self.reportable();row.rule_version=V3_RULE_VERSION;row.analysis_version='sonnet.qa.11'
        row.state='needs_review';row.score=None;row.is_scored=False;row.analysis={};row.validation_errors=['schema:invalid']
        self.db.commit()
        self.assertIn(row.id,list(self.db.scalars(select(CallRecord.id).where(*calls_api.published_conditions()))))
        row.rule_version='2026-11-12.1';self.db.commit()
        self.assertNotIn(row.id,list(self.db.scalars(select(CallRecord.id).where(*calls_api.published_conditions()))))

    def test_failed_paid_response_is_saved_without_retry(self):
        from app import call_worker
        from app.nexara import RemoteFailure, SonnetCallAgent
        from app.models import CallAnalysisRevision
        row=self.reportable();failure=RemoteFailure('agent_invalid_output')
        failure.provider_payload={'id':'known-generation','choices':[]}
        with patch.object(call_worker,'SonnetCallAgent') as agent:
            agent.catalog=SonnetCallAgent.catalog
            agent.return_value.analyze.side_effect=failure
            first=call_worker.stage_revision(row.id);second=call_worker.stage_revision(row.id)
            agent.return_value.analyze.assert_called_once()
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(self.db.get(CallAnalysisRevision,first['id']).result['failed_provider_response']['id'],'known-generation')

    def test_candidate_preview_hides_provider_and_does_not_publish(self):
        from app import calls_api
        row,rev=self.candidate();rev.result={**rev.result,'provider':{'secret':'must_not_leak'}};self.db.commit()
        before=copy.deepcopy(row.analysis)
        with patch.object(calls_api,'SessionLocal',self.factory):
            data=calls_api.revision_detail(row.id,rev.id,candidate=True)
        self.assertTrue(data['candidate']);self.assertNotIn('provider',str(data));self.assertEqual(row.analysis,before)
        self.assertTrue(data['comparison']['publication_required'])

if __name__=='__main__':unittest.main()
