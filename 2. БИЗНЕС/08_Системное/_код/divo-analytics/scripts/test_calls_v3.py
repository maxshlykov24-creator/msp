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
    def test_callback_specific_day_counts(self):
        a=raw();a['events']=[event()];r=self.check(a)
        self.assertEqual(r['errors'],[]);self.assertEqual(r['score'],14.29)
        self.assertEqual(self.check(r['analysis']),r)
    def test_materials_dont_count_or_borrow_visit_time(self):
        a=raw();a['events']=[event('materials'),{**event('visit','Завтра','day',status='tentative'),'id':'e2'}]
        self.assertEqual(self.check(a)['analysis']['criteria'][6]['status'],'no')
    def test_representative_half_hour_visit(self):
        a=raw();a['events']=[event('visit','Через полчаса','bounded','representative')]
        r=self.check(a);self.assertEqual(r['errors'],[]);self.assertEqual(r['score'],28.57)
    def test_visit_day_without_time_not_enough(self):
        a=raw();a['events']=[event('visit','Завтра','day')]
        self.assertEqual(self.check(a)['score'],0)
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
    def test_remote_valuation_chain_is_partial(self):
        a=raw();a['events']=[event('valuation','','none')]
        r=self.check(a);c=r['analysis']['criteria'][4]
        self.assertEqual(c['conditions'][-1]['status'],'yes');self.assertEqual(c['status'],'no')
    def test_photo_request_without_valuation_purpose_rejected(self):
        a=raw();a['events']=[event('valuation','','none')];a['events'][0]['purpose_evidence']=[]
        self.assertIn('event:e1:valuation_purpose_missing',self.check(a)['errors'])
    def test_guessed_make_rejected(self):
        a=raw();a['facts']=[fact('x','make','BMW')];a['facts'][0]['evidence']=[ev('RX350')]
        self.assertIn('fact:x:value_not_literal',self.check(a)['errors'])
    def test_two_facts_cannot_come_from_different_cars(self):
        a=raw();a['facts']=[fact('a','year','2020','target','one'),fact('b','mileage','30000','target','two')]
        for c,f in zip(a['criteria'][2]['conditions'],a['facts']):c.update(status='yes',evidence=f['evidence'],fact_ids=[f['id']])
        self.assertIn('criterion_3:mixed_vehicles',self.check(a)['errors'])
        a['facts'][1]['subject_id']='one';self.assertEqual(self.check(a)['errors'],[])
    def test_superseded_fact_cannot_score(self):
        a=raw();f=fact('a','year','2020','target');f['status']='superseded';a['facts']=[f]
        a['criteria'][2]['conditions'][0].update(status='yes',evidence=f['evidence'],fact_ids=['a'])
        self.assertIn('criterion_3:unproven_target_fact',self.check(a)['errors'])
    def test_no_calls_restriction_invalidates_pending_callback(self):
        a=raw();a['events']=[event()];a['restrictions']=[dict(kind='no_calls',text='Не звоните',evidence=[ev('Не звоните')])]
        r=self.check(a);self.assertEqual(r['score'],0);self.assertIn('ограничение',r['analysis']['recommendation'])
    def test_unknown_only_blocks_when_score_can_change(self):
        a=raw();a['speech_clear']=False;a['uncertainties']=[dict(criterion_id=1,reason='Неясное имя',evidence=[],quality_flags=['speech_clear'])]
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
    def test_candidate_preview_hides_provider_and_does_not_publish(self):
        from app import calls_api
        row,rev=self.candidate();rev.result={**rev.result,'provider':{'secret':'must_not_leak'}};self.db.commit()
        before=copy.deepcopy(row.analysis)
        with patch.object(calls_api,'SessionLocal',self.factory):
            data=calls_api.revision_detail(row.id,rev.id,candidate=True)
        self.assertTrue(data['candidate']);self.assertNotIn('provider',str(data));self.assertEqual(row.analysis,before)
        self.assertTrue(data['comparison']['publication_required'])

if __name__=='__main__':unittest.main()
