from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import crm_automation as a
from app import crm_automation_worker as w


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in w.TABLES:
        table.create(engine)
    with Session(engine) as session:
        yield session


def catalog():
    return a.Catalog([{"id": a.SALES, "_embedded": {"statuses": [
        {"id": 11, "name": "Взято в работу", "type": 0},
        {"id": 12, "name": "Новая заявка", "type": 0},
    ]}}])


def event(key="one", at=1000):
    return {"id":key,"entity_id":55,"entity_type":"lead","type":"lead_status_changed",
        "created_at":at,"value_before":[{"lead_status":{"id":12,"pipeline_id":a.SALES}}],
        "value_after":[{"lead_status":{"id":11,"pipeline_id":a.SALES}}]}


class Client:
    def __init__(self, events=None):
        self.events = events or []
        self.lead = {"id":55,"pipeline_id":a.SALES,"status_id":11,"responsible_user_id":1}
        self.tasks = {}
        self.posts = []
        self.patches = []

    def get(self,path,**params):
        if path == "/api/v4/events": return {"_embedded":{"events":self.events}}
        if path == "/api/v4/tasks": return {"_embedded":{"tasks":list(self.tasks.values())}}
        if path.startswith("/api/v4/tasks/"): return self.tasks[int(path.rsplit("/",1)[1])]
        if path.startswith("/api/v4/leads/"): return self.lead
        raise AssertionError(path)

    def post(self,path,body):
        self.posts.append(body)
        tid = len(self.posts)
        self.tasks[tid] = {**body[0],"id":tid}
        return {"_embedded":{"tasks":[self.tasks[tid]]}}

    def patch(self,path,body):
        self.patches.append(body)
        for change in body: self.tasks[change["id"]].update(change)
        return {}

    def kassa_state(self,lead_id,*,include_promise=False):
        return {"version":1,"lead_id":lead_id,"bindings":[],"tasks":[]}


def collect(db,c):
    return w.collect(db,c,w.Settings(mode="preview",activated_at=1,enabled_rules="qualify"),1200)


def test_configuration_requires_explicit_boundary_and_rules():
    assert w.Settings().mode == "off"
    with pytest.raises(ValueError): w.Settings(mode="live").rule_ids()
    with pytest.raises(ValueError): w.Settings(mode="preview",activated_at=1).rule_ids()
    with pytest.raises(ValueError): w.Settings(mode="live",activated_at=1,enabled_rules="qualify").rule_ids()


def test_durable_intake_deduplicates_and_records_no_contact_data(db):
    c=Client([event()])
    assert collect(db,c)==1
    assert collect(db,c)==0
    saved=db.get(w.Event,"one")
    assert saved.after=={"id":11,"pipeline_id":a.SALES}
    assert db.get(w.Cursor,"stage-v1").through==1200


def test_invalid_or_incomplete_events_do_not_advance_cursor(db):
    c=Client([event()]);collect(db,c)
    c.events=[event("bad",1300),{**event("wrong",1301),"type":"unexpected"}]
    with pytest.raises(ValueError):
        w.collect(db,c,w.Settings(mode="preview",activated_at=1,enabled_rules="qualify"),1400)
    assert db.get(w.Cursor,"stage-v1").through==1200
    assert db.get(w.Event,"bad") is None


def test_event_plans_once_then_dispatches_owned_task(db):
    c=Client([event()]);collect(db,c)
    saved=db.get(w.Event,"one")
    w.process_event(db,c,saved,catalog(),frozenset({"qualify"}))
    w.process_event(db,c,saved,catalog(),frozenset({"qualify"}))
    assert not c.posts
    assert len(list(db.scalars(select(a.TaskExecution))))==1
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert len(c.posts)==1
    c.lead["responsible_user_id"]=2
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert c.tasks[1]["responsible_user_id"]==2
    assert len(c.posts)==1


def test_migration_holds_event_without_post(db):
    from app.pipeline_guard import MIGRATION_TAG
    c=Client([event()]);collect(db,c)
    c.lead["_embedded"]={"tags":[{"name":MIGRATION_TAG}]}
    saved=db.get(w.Event,"one")
    w.process_event(db,c,saved,catalog(),frozenset({"qualify"}))
    assert saved.state=="held"
    assert list(db.scalars(select(a.TaskExecution)))==[]


def test_same_second_multiple_events_never_guesses_order(db):
    c=Client([event(),event("two")]);collect(db,c)
    for saved in db.scalars(select(w.Event)):
        w.process_event(db,c,saved,catalog(),frozenset({"qualify"}))
        assert saved.reason=="ambiguous_same_second_events"
    assert not c.posts


def test_genuine_reentry_closes_previous_owned_task_and_creates_one_new(db):
    c=Client([event()]);collect(db,c)
    w.process_event(db,c,db.get(w.Event,"one"),catalog(),frozenset({"qualify"}))
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    c.events=[event("two",1100)];collect(db,c)
    w.process_event(db,c,db.get(w.Event,"two"),catalog(),frozenset({"qualify"}))
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert c.tasks[1]["is_completed"] is True
    assert len(c.posts)==2
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert len(c.posts)==2


def creation(db,c,at=1000):
    c.events=[{"id":"added","entity_id":55,"entity_type":"lead","type":"lead_added",
               "created_at":at,"value_before":[],"value_after":[]}]
    cfg=w.Settings(mode="preview",activated_at=1,enabled_rules="new,qualify")
    w.collect(db,c,cfg,1200,event_type="lead_added")
    return db.get(w.Event,"added")


def test_creation_waits_for_status_history_and_then_plans_initial_task(db):
    c=Client();saved=creation(db,c)
    c.lead.update(status_id=12,created_at=1000,updated_at=1000)
    w.process_event(db,c,saved,catalog(),frozenset({"new"}))
    assert saved.reason=="creation_snapshot_not_covered"
    c.events=[];collect(db,c)
    w.process_event(db,c,saved,catalog(),frozenset({"new"}))
    tasks=list(db.scalars(select(a.TaskExecution)))
    assert len(tasks)==1 and tasks[0].rule_id=="new"
    assert saved.after=={"pipeline_id":a.SALES,"id":12}


def test_creation_does_not_invent_initial_stage_after_transition(db):
    c=Client([event("stage",1100)]);collect(db,c)
    saved=creation(db,c)
    c.lead.update(created_at=1000,updated_at=1100)
    w.process_event(db,c,saved,catalog(),frozenset({"new","qualify"}))
    assert saved.state=="superseded"
    w.process_event(db,c,db.get(w.Event,"stage"),catalog(),frozenset({"new","qualify"}))
    assert len(list(db.scalars(select(a.TaskExecution))))==1


def test_creation_same_second_does_not_block_verified_transition(db):
    c=Client([event("stage")]);collect(db,c)
    saved=creation(db,c)
    c.lead.update(created_at=1000,updated_at=1000)
    w.process_event(db,c,saved,catalog(),frozenset({"qualify"}))
    assert saved.state=="superseded"
    w.process_event(db,c,db.get(w.Event,"stage"),catalog(),frozenset({"qualify"}))
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert len(c.posts)==1


def test_creation_does_not_plan_transition_only_rule(db):
    c=Client();collect(db,c);saved=creation(db,c)
    c.lead.update(created_at=1000,updated_at=1000,status_id=99)
    cat=a.Catalog([{"id":a.SALES,"_embedded":{"statuses":[{"id":99,"name":"Хочет прийти","type":0}]}}])
    w.process_event(db,c,saved,cat,frozenset({"wants_visit"}))
    assert list(db.scalars(select(a.TaskExecution)))==[]


def test_worker_reconciles_lost_post_response_without_resending(db):
    c=Client([event()]);collect(db,c)
    w.process_event(db,c,db.get(w.Event,"one"),catalog(),frozenset({"qualify"}))
    original=c.post
    def lost(path,body):
        original(path,body)
        raise TimeoutError()
    c.post=lost
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    task=db.scalar(select(a.TaskExecution))
    assert task.state=="uncertain"
    w.reconcile_pending(db,c,frozenset({"qualify"}))
    assert task.state=="created" and task.task_id==1
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert len(c.posts)==1


def test_worker_missing_task_stays_uncertain_without_post(db):
    c=Client([event()]);collect(db,c)
    w.process_event(db,c,db.get(w.Event,"one"),catalog(),frozenset({"qualify"}))
    task=db.scalar(select(a.TaskExecution));task.state="inflight";db.commit()
    w.reconcile_pending(db,c,frozenset({"qualify"}))
    assert task.state=="uncertain" and task.reason=="not_found"
    w.dispatch(db,c,catalog(),frozenset({"qualify"}),cutover_at=1)
    assert not c.posts


def test_binding_gate_never_selects_first_or_guesses_type():
    lead={"id":55}
    binding={"lead_id":"55","kind":"sale","funnel":"offline","isTest":False}
    snap={"version":1,"lead_id":55,"bindings":[binding]}
    assert w.binding_issue(lead,snap) is None
    assert w.binding_issue(lead,{**snap,"bindings":[binding,binding]})=="multiple_kassa_bindings"
    assert w.binding_issue(lead,{**snap,"bindings":[{**binding,"kind":"deferred"}]})=="unknown_kassa_base_type"
    assert w.binding_issue(lead,{**snap,"bindings":[{**binding,"edit_pending":True}]})=="kassa_edit_pending"
    assert w.binding_issue(lead,{**snap,"bindings":[]},required=True)=="missing_kassa_binding"
    assert w.binding_issue(lead,{**snap,"bindings":[{**binding,"isTest":True}]})=="test_kassa_binding"


def test_courier_completion_retries_idempotent_handoff_not_crm_task(db):
    c=Client()
    c.lead.update(pipeline_id=a.ONLINE,status_id=89900829)
    cat=a.Catalog([{'id':a.ONLINE,'_embedded':{'statuses':[{'id':89900829,'name':'Собран','type':0}]}}])
    job=a.plan_task(db,'courier',55,'entry:courier-event',text='Вызвать курьера',
        due=datetime.now(timezone.utc),responsible=1,task_type=3946594)
    job.state,job.reason,job.task_id='completed','completed_externally',777
    db.add(w.Event(id='courier-event',lead_id=55,at=1000,before={'id':1,'pipeline_id':a.ONLINE},after={'id':89900829,'pipeline_id':a.ONLINE}))
    db.commit()
    c.tasks[777]={**job.payload,'id':777,'is_completed':True}
    c.kassa_state=lambda lid:{'version':1,'lead_id':lid,'bindings':[
        {'lead_id':lid,'kind':'delivery','funnel':'online','isTest':False,'edit_pending':False}],'tasks':[]}
    calls=[]
    def handoff(value):
        calls.append(value.task_id)
        if len(calls)==1:raise TimeoutError('response lost')
    c.courier_completed=handoff
    w.handoff_completed_couriers(db,c,cat,{'courier'})
    assert db.get(w.CourierHandoff,777).state=='pending'
    w.handoff_completed_couriers(db,c,cat,{'courier'})
    w.handoff_completed_couriers(db,c,cat,{'courier'})
    assert calls==[777,777]
    assert db.get(w.CourierHandoff,777).state=='acknowledged'
    assert not c.posts and not c.patches
    assert c.lead['status_id']==89900829


def test_automatic_task_closure_does_not_confirm_courier(db):
    c=Client()
    job=a.plan_task(db,'courier',55,'entry:obsolete',text='Вызвать курьера',
        due=datetime.now(timezone.utc),responsible=1,task_type=3946594)
    job.state,job.reason,job.task_id='completed','Условие задачи больше не действует',888
    db.commit()
    c.courier_completed=lambda value:pytest.fail('automatic closure is not business completion')
    w.handoff_completed_couriers(db,c,catalog(),{'courier'})
    assert db.get(w.CourierHandoff,888) is None


def test_field_worker_keeps_history_when_task_event_is_superseded(db):
    c=Client([event('taken',1000),event('later',1100)])
    c.events[1]['value_after'][0]['lead_status']['id']=12
    c.lead.update(status_id=12,updated_at=1100)
    collect(db,c)
    def patch(path,body):
        c.patches.append(body)
        current={x['field_id']:x for x in c.lead.get('custom_fields_values',[])}
        current.update({x['field_id']:x for x in body['custom_fields_values']})
        c.lead['custom_fields_values']=list(current.values())
    c.patch=patch
    w.process_fields(db,c,catalog(),preview=True)
    assert not c.patches
    w.process_fields(db,c,catalog(),preview=False)
    w.process_fields(db,c,catalog(),preview=False)
    assert len(c.patches)==1
    fields={x['field_id']:x['values'][0]['value'] for x in c.lead['custom_fields_values']}
    assert fields[3434463]==1000 and fields[3630859] is True
    assert c.lead['status_id']==12


def test_field_worker_waits_for_complete_history(db):
    c=Client([event()]);collect(db,c)
    c.lead['updated_at']=1201
    w.process_fields(db,c,catalog(),preview=False)
    assert db.get(w.FieldWatch,55).issues==['history_not_caught_up']
    assert not c.patches


def test_owned_deadline_tracks_changes_and_reversions_but_preserves_manual_edit(db):
    c=Client([event()]);collect(db,c)
    c.lead.update(status_id=11,updated_at=1000)
    cat=a.Catalog([{'id':a.SALES,'_embedded':{'statuses':[{'id':11,'name':'Дано обещание','type':0}]}}])
    c.lead['custom_fields_values']=[{'field_id':3815335,'values':[{'value':1794902400}]}]
    def patch(path,body):
        c.patches.append(body)
        current={x['field_id']:x for x in c.lead['custom_fields_values']}
        current.update({x['field_id']:x for x in body['custom_fields_values']})
        c.lead['custom_fields_values']=list(current.values())
    c.patch=patch
    def change(field_id,value):
        next(x for x in c.lead['custom_fields_values'] if x['field_id']==field_id)['values']=[{'value':value}]
    def deadline_value():
        return next(x for x in c.lead['custom_fields_values'] if x['field_id']==3279145)['values'][0]['value']
    w.process_fields(db,c,cat,preview=False)
    initial=deadline_value()
    change(3815335,1794902400+86400)
    w.process_fields(db,c,cat,preview=False)
    assert deadline_value()==initial+86400
    change(3815335,1794902400)
    w.process_fields(db,c,cat,preview=False)
    assert deadline_value()==initial
    change(3279145,initial+3*86400)
    change(3815335,1794902400+86400)
    count=len(c.patches)
    w.process_fields(db,c,cat,preview=False)
    assert len(c.patches)==count
    assert deadline_value()==initial+3*86400
    assert 'stage_deadline_differs_from_actual_date' in db.get(w.FieldWatch,55).issues


def test_live_requires_distinct_explicit_cutover_boundary():
    cfg=dict(mode='live',activated_at=100,enabled_rules='qualify',allow_cutover=True,service_key='x'*32)
    for boundary in (0,99):
        with pytest.raises(ValueError,match='explicit_cutover_boundary_required'):
            w.Settings(**cfg,cutover_at=boundary).rule_ids()
    assert w.Settings(**cfg,cutover_at=101).rule_ids()==frozenset({'qualify'})


def test_preview_backlog_is_not_replayed_after_cutover(db):
    c=Client([event()]);collect(db,c)
    e=db.get(w.Event,'one');w.process_event(db,c,e,catalog(),frozenset({'qualify'}))
    w.dispatch(db,c,catalog(),frozenset({'qualify'}),cutover_at=1100)
    assert c.posts==[] and c.patches==[]
    job=db.scalar(select(a.TaskExecution))
    assert job.reason=='pre_cutover_obligation_requires_reconciliation'
    assert job.task_id is None


@pytest.mark.parametrize('manual_values', [[], [{'value':42}]])
def test_history_does_not_restore_owned_date_after_manual_edit(db, manual_values):
    c=Client([event()]);collect(db,c)
    c.lead.update(updated_at=1000)
    def patch(path,body):
        c.patches.append(body)
        current={x['field_id']:x for x in c.lead.get('custom_fields_values',[])}
        current.update({x['field_id']:x for x in body['custom_fields_values']})
        c.lead['custom_fields_values']=list(current.values())
    c.patch=patch
    w.process_fields(db,c,catalog(),preview=False)
    date=next(x for x in c.lead['custom_fields_values'] if x['field_id']==3434463)
    assert date['values']==[{'value':1000}]
    date['values']=manual_values
    count=len(c.patches)
    w.process_fields(db,c,catalog(),preview=False)
    w.process_fields(db,c,catalog(),preview=False)
    assert len(c.patches)==count
    assert date['values']==manual_values
    if not manual_values:
        assert 'manual_change_preserved:3434463' in db.get(w.FieldWatch,55).issues


def test_retry_queue_rotates_even_with_new_work(db):
    for i in range(205):
        db.add(w.Event(id=str(i), lead_id=55, at=i, before={}, after={},
                       state="blocked" if i < 55 else "received"))
    db.commit()
    first = w.pending_events(db)
    second = w.pending_events(db)
    assert len(first) == 200
    assert {e.id for e in first + second} == {str(i) for i in range(205)}


def test_migration_excludes_only_verified_transition_id(db):
    before = {"pipeline_id":a.SALES, "id":11}
    after = {"pipeline_id":a.OFFLINE, "id":21}
    db.add(a.MigrationEntry(lead_id=55, state="verified", before={"pipeline_id":a.SALES,"status_id":11},
        target={"pipeline_id":a.OFFLINE,"status_id":21,"transition_event_id":"first"}, preserved_occurrence="original"))
    db.commit()
    for key,expected in [("first",True),("manual",False)]:
        event=w.Event(id=key,lead_id=55,at=123,before=before,after=after)
        assert w.migration_event(db,event) is expected


def test_shop_control_requires_matching_open_work():
    snapshot={"bindings":[{"number":18}],"tasks":[
        {"kind":"reserve","deal_number":18,"status":"done"},
        {"kind":"reserve","deal_number":19,"status":"pending"},
        {"kind":"movement","deal_number":18,"status":"pending"}]}
    assert not w.shop_control_needed("hold_control",snapshot)
    assert w.shop_control_needed("transfer_control",snapshot)
    assert w.shop_control_needed("company_invoice",{"bindings":[]})


def test_scoped_call_write_preserves_intervening_edit(db):
    from app import crm_automation_fields as f
    c=Client()
    item=dict(c.lead,updated_at=100,custom_fields_values=[],
              _embedded={"tags":[{"name":f.SHARED_AD_CALL_TAG}]})
    fields=[{"id":i,"enums":[{"id":i+1,"value":"Другое"}]} for i in (3134043,3781223)]
    source=f.project_first_source(item,fields,first_touch=True)
    c.lead=dict(item,custom_fields_values=[{"field_id":3781223,"values":[{"value":"ВК"}]}])
    w.apply_shared_ad_source(db,c,item,fields,source)
    assert not c.patches
    assert db.scalar(select(f.FieldExecution)).state=="cancelled"


@pytest.mark.parametrize("stage,legacy,posts",[(77384722,False,1),(77384722,True,0),(78616338,False,0)])
def test_visit_task_only_for_current_store_visit(db,monkeypatch,stage,legacy,posts):
    from app.message_templates import Job
    from datetime import timedelta
    Job.__table__.create(db.get_bind())
    monkeypatch.setenv("CALENDARNOTIFY_EXTRA_ENABLED","true")
    now=datetime(2026,11,18,10,tzinfo=a.MSK)
    c=Client()
    c.lead.update(status_id=stage,custom_fields_values=[
        {"field_id":3158921,"values":[{"enum_id":4765787}]},
        {"field_id":3185681,"values":[{"value":int(now.timestamp())}]}])
    db.add(Job(lead_id=55,rule_id="legacy-wants_visit",occurrence=now.date().isoformat(),
        due_at=now.astimezone(timezone.utc),state="sent"))
    if legacy:
        c.tasks[77]={"id":77,"entity_id":55,"entity_type":"leads","text":w.OLD_VISIT_TEXT,"task_type_id":2}
    db.commit()
    w.visit_tasks(db,c,now,int(now.timestamp())+1)
    assert not c.posts
    for _ in range(2): w.visit_tasks(db,c,now,int(now.timestamp())-1)
    assert len(c.posts)==posts
    if posts: assert c.posts[0][0]["complete_till"]==int((now+timedelta(hours=3)).timestamp())


def test_typed_cutover_ignores_sales_and_cross_pipeline_moves(db):
    c=Client([event("sales",1000)])
    collect(db,c)
    w.process_event(db,c,db.get(w.Event,"sales"),catalog(),frozenset({"qualify"}))
    scope={a.COMPANY}
    w.dispatch(db,c,catalog(),{"qualify"},cutover_at=999,pipelines=scope)
    assert not c.posts
    cat=a.Catalog([{"id":a.COMPANY,"_embedded":{"statuses":[{"id":11,"name":"Взято в работу","type":0}]}}])
    c.lead.update(pipeline_id=a.COMPANY)
    for key,at,origin in [("move",1001,a.SALES),("organic",1002,a.COMPANY)]:
        e=w.Event(id=key,lead_id=55,at=at,before={"pipeline_id":origin,"id":12},
                  after={"pipeline_id":a.COMPANY,"id":11})
        db.add(e);db.commit()
        w.process_event(db,c,e,cat,frozenset({"qualify"}))
        w.dispatch(db,c,cat,{"qualify"},cutover_at=999,pipelines=scope)
        assert len(c.posts)==int(key=="organic")


@pytest.mark.parametrize("user_map,assignee",[({},1),({"staff-1":8},8)])
def test_linked_promise_keeps_author_date_and_single_owned_task(db,user_map,assignee):
    c=Client([event()])
    c.settings=w.Settings(promise_users=user_map)
    snap={"version":1,"lead_id":55,"bindings":[{"number":31,"kind":"company","lead_id":55}],
          "tasks":[],"execution_owners":{"promise":"amocrm"},
          "promise":{"author_id":"staff-1","author":"Автор","comment":"Позвонить","due_date":"2026-11-19"}}
    def read(lead_id,*,include_promise=False):
        assert include_promise
        return snap
    c.kassa_state=read
    cat=a.Catalog([{"id":a.SALES,"_embedded":{"statuses":[{"id":11,"name":"Дано обещание","type":0}]}}])
    collect(db,c)
    w.process_event(db,c,db.get(w.Event,"one"),cat,{"promise"})
    def run():
        w.dispatch(db,c,cat,{"promise"},cutover_at=1)
    snap["tasks"]=[{"kind":"promise_follow","status":"pending"}]
    run()
    assert not c.posts
    snap["tasks"]=[]
    run()
    task=c.tasks[1]
    assert task["responsible_user_id"]==assignee and "Автор: Автор" in task["text"] and "Позвонить" in task["text"]
    assert task["complete_till"]==int(datetime(2026,11,19,23,59,tzinfo=a.MSK).timestamp())
    snap["promise"].update(comment="Перезвонить",due_date="2026-11-20")
    run()
    assert len(c.posts)==1 and "Перезвонить" in task["text"] and "20.11.2026" in task["text"]
    task["text"]="Ручная редакция"
    run()
    assert task["text"]=="Ручная редакция" and len(c.patches)==1


def test_promise_uses_live_cash_settings_even_after_unlink():
    details={"author_id":"employee","author":"Автор","comment":"Обещание","due_date":"2026-11-20"}
    lead={"responsible_user_id":12}
    for mapped,expected in [(51,51),(None,12)]:
        snapshot={"promise":{**details,"amo_user_id":mapped}}
        assert w.promise_details(snapshot,lead,{"employee":41})[2]==expected
