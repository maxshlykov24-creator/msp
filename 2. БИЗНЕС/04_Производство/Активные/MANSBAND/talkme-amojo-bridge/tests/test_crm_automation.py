from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import crm_automation as a


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in a.TABLES:
        table.create(engine)
    with Session(engine) as session:
        yield session


def catalog():
    return a.Catalog([{"id": p, "_embedded": {"statuses": [
        {"id": s, "name": name, "type": 0} for s, name in statuses]}}
        for p, statuses in [
            (a.SALES, [(11, "Взято в работу"), (12, "Счёт запрошен"), (13, "Нет соответствия")]),
            (a.OFFLINE, [(21, "Взято в работу")]),
            (a.COMPANY, [(31, "Счет запрошен")]),
        ]])


def lead(**kwargs):
    return {"id": 55, "pipeline_id": a.SALES, "status_id": 11,
            "custom_fields_values": [{"field_id": a.TYPE_FIELD, "values": [{"value": "Продажа офлайн"}]}], **kwargs}


def test_history_keeps_passed_stage_and_earliest_real_time():
    from app.crm_automation_fields import project_stage_history, TAKEN_DATE
    item=lead(status_id=12)
    events=[{'lead_id':55,'at':at,'after':{'pipeline_id':a.SALES,'id':stage}}
            for at,stage in [(200,12),(150,11),(100,11)]]
    projected=project_stage_history(item,catalog(),events)
    assert {f['field_id']:f['values'][0]['value'] for f in projected.fields} == {3630859:True,TAKEN_DATE:100}
    assert project_stage_history(item,catalog(),list(reversed(events))).fields == projected.fields
    item['custom_fields_values'].append({'field_id':TAKEN_DATE,'values':[{'value':50}]})
    assert all(f['field_id']!=TAKEN_DATE for f in project_stage_history(item,catalog(),events).fields)


def test_history_excludes_migration_and_refuses_another_lead():
    from app.crm_automation_fields import project_stage_history
    events=[{'lead_id':55,'at':100,'after':{'pipeline_id':a.OFFLINE,'id':21},'migration':True}]
    assert project_stage_history(lead(),catalog(),events).fields == []
    events[0]['lead_id']=56
    with pytest.raises(ValueError,match='history_lead_mismatch'):
        project_stage_history(lead(),catalog(),events)


def test_explicit_type_change_moves_between_known_funnels():
    cat = a.Catalog([{"id": p, "_embedded": {"statuses": [{"id": s, "name": "Взято в работу", "type": 0}]}}
                     for p, s in ((a.OFFLINE, 21), (a.ONLINE, 41), (a.CAMPAIGN, 51), (a.SALES, 11))])
    item = lead(pipeline_id=a.OFFLINE, status_id=21, custom_fields_values=[
        {"field_id": a.TYPE_FIELD, "values": [{"value": "Продажа онлайн"}]}])
    moved = a.route_candidate(item, cat, type_change=True)
    assert (moved.state, moved.pipeline_id, moved.status_id) == ("candidate", a.ONLINE, 41)
    assert a.route_candidate(item, cat).reason == "existing_pipeline_requires_review"
    assert a.route_candidate(lead(pipeline_id=a.CAMPAIGN, status_id=51), cat, type_change=True).state == "excluded"
    assert a.route_candidate(lead(isTest=True), catalog(), type_change=True).state == "candidate"
    empty = lead(custom_fields_values=[])
    binding = {"lead_id": 55, "kind": "sale", "funnel": "offline"}
    assert a.route_with_bindings(empty, catalog(), [binding], type_change=True).reason == "missing_or_ambiguous_type"
    assert a.route_with_bindings(empty, catalog(), [binding]).state == "candidate"


def test_route_preserves_stage_and_refuses_guess():
    r = a.route_candidate(lead(), catalog())
    assert (r.state, r.pipeline_id, r.status_id) == ("candidate", a.OFFLINE, 21)
    assert a.route_candidate(lead(status_id=13), catalog()).reason == "no_equivalent_stage"
    assert a.route_candidate(lead(custom_fields_values=[]), catalog()).state == "manual"
    assert a.route_candidate(lead(), catalog(), linked_kind="Аренда").reason == "kassa_type_conflict"


@pytest.mark.parametrize("extra", [{"status_id": 142}, {"status_id": 143}, {"isTest": True},
    {"pipeline_id": 999}, {"_embedded": {"tags": [{"name": "ТЕСТ НЕ ОТПРАВЛЯТЬ"}]}}])
def test_route_exclusions(extra):
    assert a.route_candidate(lead(**extra), catalog()).state == "excluded"


def test_stage_alias_only_explicit():
    item = lead(status_id=12, custom_fields_values=[{"field_id": a.TYPE_FIELD,
                "values": [{"value": "Продажа компании"}]}])
    assert a.route_candidate(item, catalog()).status_id == 31
    assert a.canonical_stage("Ждет товар") != a.canonical_stage("Товар в пути")


@pytest.mark.parametrize("hour,minute,day,due_hour,due_minute", [
    (0, 5, 17, 10, 15), (9, 59, 17, 10, 15), (10, 0, 17, 10, 15),
    (22, 59, 17, 23, 14), (23, 0, 18, 10, 15), (23, 59, 18, 10, 15)])
def test_first_response_boundaries(hour, minute, day, due_hour, due_minute):
    result = a.first_response_due(datetime(2026, 11, 17, hour, minute, tzinfo=a.MSK)).astimezone(a.MSK)
    assert (result.day, result.hour, result.minute) == (day, due_hour, due_minute)


def test_deadline_calendar_and_native_not_guessed():
    start = datetime(2026, 12, 31, 22, tzinfo=a.MSK)
    assert a.deadline(start, "hours", 22).astimezone(a.MSK) == datetime(2027, 1, 1, 20, tzinfo=a.MSK)
    assert a.deadline(start, "end_of_day").astimezone(a.MSK).hour == 23
    assert a.deadline(start, "native_tomorrow") == a.stamp(start + timedelta(days=1, minutes=5))
    with pytest.raises(ValueError, match="timezone"):
        a.deadline(datetime(2026, 11, 17), "hours", 1)


class Client:
    def __init__(self, error=False):
        self.posts = []
        self.patches = []
        self.error = error
        self.task = None

    def get(self, path):
        return self.task if "/tasks/" in path else lead()

    def post(self, path, body):
        self.posts.append(body)
        self.task = {"id": 777, **body[0]}
        if self.error:
            raise TimeoutError()
        return {"_embedded": {"tasks": [{"id": 777}]}}

    def patch(self, path, body):
        self.patches.append(body)
        return {}


def plan(db, occurrence="event1"):
    return a.plan_task(db, "new", 55, occurrence, text="Ответить", responsible=1,
                       task_type=2, due=datetime(2026, 11, 17, 10, 15, tzinfo=a.MSK))


def execute(db, job, c, **kwargs):
    return a.execute_task(db, job, c, enabled_rules=frozenset({"new"}), current=lambda _: True, **kwargs)


def test_preview_and_disabled_rules_never_post(db):
    c = Client()
    job = plan(db)
    assert execute(db, job, c) == "preview"
    assert a.execute_task(db, job, c, enabled_rules=frozenset(), current=lambda _: True, preview=False) == "preview"
    assert not c.posts


def test_same_event_once_new_entry_separate(db):
    c = Client()
    job = plan(db)
    assert plan(db).id == job.id
    assert execute(db, job, c, preview=False) == "created"
    assert execute(db, job, c, preview=False) == "created"
    assert len(c.posts) == 1
    assert plan(db, "event2").id != job.id


def test_timeout_reconciled_without_resend(db):
    c = Client(error=True)
    job = plan(db)
    assert execute(db, job, c, preview=False) == "uncertain"
    assert execute(db, job, c, preview=False) == "uncertain"
    assert a.reconcile_task(db, job, [], complete_scan=False) == "uncertain"
    assert a.reconcile_task(db, job, [], complete_scan=True) == "uncertain"
    assert a.reconcile_task(db, job, [c.task], complete_scan=True) == "created"
    assert len(c.posts) == 1


def test_manual_task_never_closed(db):
    c = Client()
    job = plan(db)
    execute(db, job, c, preview=False)
    c.task["text"] = "Ручная задача"
    assert a.complete_owned_task(db, job, c, "Этап завершён", preview=False) == "ownership_conflict"
    assert not c.patches


def test_close_own_task_only(db):
    c = Client()
    job = plan(db)
    execute(db, job, c, preview=False)
    assert a.complete_owned_task(db, job, c, "Этап завершён", preview=False) == "completed"
    assert c.patches == [[{"id": 777, "is_completed": True, "result": {"text": "Этап завершён"}}]]


def test_migration_guard_and_condition_recheck(db):
    c = Client()
    job = plan(db)
    db.add(a.MigrationEntry(lead_id=55, state="moving", before={}, target={}, preserved_occurrence="event1"))
    db.commit()
    assert execute(db, job, c, preview=False) == "migration_guard"
    assert not c.posts


def test_source_manual_value_protected():
    assert a.source_fill_patch(lead(), a.TYPE_FIELD, 1) is None
    assert a.source_fill_patch(lead(custom_fields_values=[]), 8, 9)["custom_fields_values"][0]["values"] == [{"enum_id": 9}]


def test_store_projection_uses_live_enum_and_preserves_custom_text():
    from app.crm_automation_fields import project_fields
    item=lead(custom_fields_values=[{"field_id":3158921,"values":[{"value":"на Бауманской"}]}])
    fields=project_fields(item,catalog()).fields
    assert {f["field_id"] for f in fields}=={3383324,3390831}
    assert next(f for f in fields if f["field_id"]==3390831)["values"]==[{"value":"Бауманской"}]
    item["custom_fields_values"].append({"field_id":3383324,"values":[{"value":"Ручной адрес"}]})
    result=project_fields(item,catalog())
    assert all(f["field_id"]!=3383324 for f in result.fields)
    assert "store_technical_field_conflict:3383324" in result.issues


def test_stage_deadline_uses_actual_date_but_does_not_overwrite_existing():
    from app.crm_automation_fields import project_fields
    cat=a.Catalog([{"id":a.SALES,"_embedded":{"statuses":[{"id":90,"name":"Дано обещание","type":0}]}}])
    due=datetime(2026,11,20,17,tzinfo=a.MSK)
    item=lead(status_id=90,custom_fields_values=[{"field_id":3815335,"values":[{"value":int(due.timestamp())}]}])
    result=project_fields(item,cat)
    expected=int(datetime(2026,11,20,tzinfo=a.MSK).timestamp())
    assert result.fields==[{"field_id":3279145,"values":[{"value":expected}]}]
    item["custom_fields_values"].append({"field_id":3279145,"values":[{"value":expected+86400}]})
    result=project_fields(item,cat)
    assert result.fields==[] and "stage_deadline_differs_from_actual_date" in result.issues


def test_field_execution_reconciles_timeout_without_second_patch(db):
    from app import crm_automation_fields as f
    f.FieldExecution.__table__.create(db.get_bind())
    item=lead(custom_fields_values=[])
    project=lambda value:f.project_fields(value,catalog(),event_at=datetime(2026,11,17,10,tzinfo=a.MSK),stage_changed=True)
    job=f.plan_fields(db,item,"event1",project(item));db.commit()
    class FieldsClient:
        calls=0
        def get(self,path): return item
        def patch(self,path,body):
            self.calls+=1
            item["custom_fields_values"]=body["custom_fields_values"]
            raise TimeoutError()
    c=FieldsClient()
    assert f.execute_fields(db,c,job,project,preview=False)=="uncertain"
    assert f.execute_fields(db,c,job,project,preview=False)=="applied"
    assert c.calls==1


def test_field_execution_does_not_overwrite_intervening_manual_edit(db):
    from app import crm_automation_fields as f
    f.FieldExecution.__table__.create(db.get_bind())
    item=lead(custom_fields_values=[])
    project=lambda value:f.project_fields(value,catalog(),event_at=datetime(2026,11,17,10,tzinfo=a.MSK),stage_changed=True)
    job=f.plan_fields(db,item,"event1",project(item));db.commit()
    item["custom_fields_values"]=[{"field_id":f.TAKEN_DATE,"values":[{"value":12345}]}]
    class FieldsClient:
        def get(self,path): return item
        def patch(self,path,body): raise AssertionError("Manual value must not be overwritten")
    assert f.execute_fields(db,FieldsClient(),job,project,preview=False)=="cancelled"


def test_binding_classification_is_preview_and_duplicates_block():
    item = lead(custom_fields_values=[])
    binding = {"lead_id":55,"kind":"sale","funnel":"offline"}
    assert a.route_with_bindings(item, catalog(), [binding]).state == "candidate"
    assert item["custom_fields_values"] == []
    assert a.route_with_bindings(item, catalog(), [binding, binding]).reason == "multiple_kassa_bindings"
    assert a.route_with_bindings(item, catalog(), [{**binding,"kind":"deferred"}]).reason == "unknown_kassa_base_type"
    assert a.route_with_bindings(item, catalog(), [{**binding,"isTest":True}]).state == "excluded"


def test_rule_dates_use_calendar_identity_across_pipeline_move():
    from app.crm_automation_rules import RULES, semantic_occurrence, task_times
    rule = next(r for r in RULES if r.id == "rental_return")
    date = datetime(2026, 11, 18, tzinfo=a.MSK)
    item = lead(custom_fields_values=[{"field_id":3322895,"values":[{"value":int(date.timestamp())}]}])
    assert semantic_occurrence(rule, item, "old") == semantic_occurrence(rule, {**item,"pipeline_id":a.RENTAL}, "new")
    starts, due = task_times(rule, item, date-timedelta(days=3))
    assert starts.astimezone(a.MSK).hour == 10
    assert due.astimezone(a.MSK) == datetime(2026, 11, 18, 23, 59, tzinfo=a.MSK)


def test_control_tasks_wait_for_sla_without_duplicate_execution():
    from app.crm_automation_rules import RULES, task_times
    rule = next(r for r in RULES if r.id == "assembly_control")
    start = datetime(2026, 11, 17, 12, tzinfo=a.MSK)
    activate, due = task_times(rule, lead(), start)
    assert activate == due == a.stamp(start+timedelta(hours=22))


def test_meeting_time_change_same_day_has_new_identity():
    from app.crm_automation_rules import RULES, semantic_occurrence
    rule = next(r for r in RULES if r.id == "visit_followup")
    morning = datetime(2026, 11, 18, 10, tzinfo=a.MSK)
    def item(date):
        return lead(custom_fields_values=[{"field_id":3382311,"values":[{"value":int(date.timestamp())}]}])
    assert semantic_occurrence(rule, item(morning), "same") != semantic_occurrence(rule, item(morning+timedelta(hours=8)), "same")


def revise(db, job, client):
    return a.revise_task(db, job, client, due=datetime(2026,11,19,18,tzinfo=a.MSK),
        responsible=2, available_at=datetime(2026,11,19,10,tzinfo=a.MSK),
        current=lambda _: True, preview=False)


def test_replan_changes_pending_obligation_without_post(db):
    c, job = Client(), plan(db)
    assert revise(db,job,c) == "replanned"
    assert job.payload["responsible_user_id"] == 2
    assert job.payload["complete_till"] == int(datetime(2026,11,19,18,tzinfo=a.MSK).timestamp())
    assert not c.posts and not c.patches


@pytest.mark.parametrize("key,value", [("text","Ручная правка"), ("responsible_user_id",3), ("complete_till",123)])
def test_revision_preserves_manual_change(db,key,value):
    c, job = Client(), plan(db)
    execute(db,job,c,preview=False)
    c.task[key] = value
    assert revise(db,job,c) == "manual_edit_conflict"
    assert not c.patches


def test_revision_does_not_reopen_completed_task(db):
    c, job = Client(), plan(db)
    execute(db,job,c,preview=False)
    c.task["is_completed"] = True
    assert revise(db,job,c) == "completed"
    assert not c.patches


def test_revision_unknown_response_readback_without_retry(db):
    c, job = Client(), plan(db)
    execute(db,job,c,preview=False)
    def lost_response(path, body):
        c.patches.append(body)
        c.task.update(body[0])
        raise TimeoutError()
    c.patch = lost_response
    assert revise(db,job,c) == "update_uncertain"
    assert revise(db,job,c) == "update_uncertain"
    assert len(c.patches) == 1
    assert a.reconcile_task_revision(db,job,c.task) == "created"
    assert job.payload["responsible_user_id"] == 2
    assert job.pending_payload is None


def test_revision_success_requires_readback(db):
    c, job = Client(), plan(db)
    execute(db,job,c,preview=False)
    assert revise(db,job,c) == "update_uncertain"
    assert job.pending_payload is not None
    assert job.payload["responsible_user_id"] == 1


def test_field_projection_preserves_history_and_does_not_fabricate_dates():
    from app.crm_automation_fields import project_fields, TAKEN_DATE
    item = lead(custom_fields_values=[{"field_id": TAKEN_DATE, "values": [{"value": 1000}]}])
    now = datetime(2026, 11, 17, 10, tzinfo=a.MSK)
    projected = project_fields(item, catalog(), event_at=now, stage_changed=True)
    assert all(f["field_id"] != TAKEN_DATE for f in projected.fields)
    assert projected.fields == [{"field_id":3630859,"values":[{"value":True}]}]
    assert project_fields(item, catalog(), event_at=now, stage_changed=True, migrating=True).fields == []
    assert project_fields(item, catalog(), stage_changed=True).issues == ["missing_verified_event_time"]


def test_field_projection_does_not_copy_fake_date_or_replace_manual_approximation():
    from app.crm_automation_fields import project_fields
    fake = int(datetime(2001, 1, 1, tzinfo=a.MSK).timestamp())
    item = lead(custom_fields_values=[{"field_id":3382311,"values":[{"value":fake}]}])
    assert project_fields(item, catalog()).fields == []
    assert project_fields(item, catalog()).issues == ["legacy_fake_meeting_date"]
    real = int(datetime(2026, 11, 20, tzinfo=a.MSK).timestamp())
    item["custom_fields_values"][0]["values"][0]["value"] = real
    assert project_fields(item, catalog()).fields == [{"field_id":3185681,"values":[{"value":real}]}]
    item["custom_fields_values"].append({"field_id":3185681,"values":[{"value":real+86400}]})
    assert project_fields(item, catalog()).fields == []


def test_all_confirmed_test_bindings_excluded_but_mixed_bindings_need_review():
    bindings = [{"lead_id":55, "isTest":True}, {"lead_id":55, "isTest":True}]
    assert a.route_with_bindings(lead(), catalog(), bindings).state == "excluded"
    bindings[1]["isTest"] = False
    assert a.route_with_bindings(lead(), catalog(), bindings).reason == "multiple_kassa_bindings"


def test_native_deadlines_match_live_isolated_calibration():
    first = datetime.fromtimestamp(1794899724, a.MSK)
    second = datetime.fromtimestamp(1794899878, a.MSK)
    assert int(a.deadline(first, "native_tomorrow").timestamp()) == 1794986424
    assert int(a.deadline(first, "native_three_days").timestamp()) == 1795159224
    assert int(a.deadline(first, "native_week").timestamp()) == 1795208340
    assert int(a.deadline(second, "minutes", 15).timestamp()) == 1794900778
    assert a.deadline(second, "native_immediate") == a.stamp(second + timedelta(minutes=5))
    assert a.deadline(second, "end_of_day").astimezone(a.MSK).strftime("%H:%M:%S") == "23:59:00"
    for day in (21,22):
        weekend = datetime(2026,11,day,10,17,31,tzinfo=a.MSK)
        assert a.deadline(weekend, "native_week") == a.stamp(weekend + timedelta(days=7))


def test_first_source_protects_manual_and_later_messages_and_rejects_conflicts():
    from app.crm_automation_fields import project_first_source
    fields = [{"id":3781223,"enums":[{"id":1,"value":"ВК"},{"id":2,"value":"Яндекс Директ"}]},
              {"id":3134043,"enums":[{"id":3,"value":"ВК"},{"id":4,"value":"Яндекс Директ"}]},
              {"id":7,"code":"UTM_SOURCE"}]
    empty = lead(custom_fields_values=[])
    assert project_first_source(empty, fields, incoming_text="2685302").fields == []
    assert project_first_source(empty, fields, first_touch=True, incoming_text="79926853021").fields == []
    assert project_first_source(empty, fields, first_touch=True, incoming_text="2685302").fields == [
        {"field_id":3781223,"values":[{"enum_id":1}]},{"field_id":3134043,"values":[{"enum_id":3}]}]
    manual = lead(custom_fields_values=[{"field_id":3781223,"values":[{"value":"ВК"}]}])
    assert project_first_source(manual, fields, first_touch=True, incoming_text="208327404").fields == [
        {"field_id":3134043,"values":[{"enum_id":3}]}]
    conflict = lead(custom_fields_values=[{"field_id":7,"values":[{"value":"ya"}]}])
    assert project_first_source(conflict, fields, first_touch=True, incoming_text="2685302").issues == ["conflicting_first_touch_signals"]
    assert project_first_source(empty, fields, first_touch=True, incoming_text="2685302", migrating=True).fields == []


def test_delayed_task_cannot_execute_before_scheduled_time(db):
    start = datetime(2026,11,20,12,tzinfo=a.MSK)
    job = a.plan_task(db,'new',55,'later',text='Проверка',due=start+timedelta(hours=1),
                      responsible=1,task_type=1,available_at=start)
    c = Client()
    assert execute(db,job,c,preview=False,now=start-timedelta(seconds=1)) == 'not_due'
    assert c.posts == []
    assert execute(db,job,c,preview=False,now=start) == 'created'
    assert len(c.posts) == 1


def test_first_source_requires_observed_creation_and_unchanged_inputs():
    from app.crm_automation_fields import verified_source_projection
    fields=[{'id':3781223,'enums':[{'id':1,'value':'ВК'}]},
            {'id':3134043,'enums':[{'id':2,'value':'ВК'}]}, {'id':7,'code':'UTM_SOURCE'}]
    current=lead(created_at=100,updated_at=110,custom_fields_values=[{'field_id':7,'values':[{'value':'vk'}]}])
    class Client:
        events=[]
        def get(self,path,**params):
            assert params['filter[entity_id]']==current['id']
            return {'_embedded':{'events':self.events}}
    c=Client()
    assert verified_source_projection(c,current,fields,through=120).fields==[]
    assert verified_source_projection(c,current,fields,created_at=100,through=105).fields==[]
    assert len(verified_source_projection(c,current,fields,created_at=100,through=120).fields)==2
    c.events=[{'entity_type':'lead','entity_id':current['id'],'created_at':110,
               'type':'custom_field_7_value_changed','value_before':[],
               'value_after':[{'custom_field_value':{'field_id':7,'text':'vk'}}]}]
    result=verified_source_projection(c,current,fields,created_at=100,through=120)
    assert result.fields==[] and result.issues==['first_source_inputs_changed_since_creation']
    c.events[0]['entity_id']=current['id']+1
    with pytest.raises(ValueError,match='unexpected_source_history_entity'):
        verified_source_projection(c,current,fields,created_at=100,through=120)


def test_first_source_mirrors_manual_value_without_reattribution():
    from app.crm_automation_fields import verified_source_projection
    fields=[{'id':3134043,'enums':[{'id':2,'value':'ВК'}]}]
    current=lead(custom_fields_values=[{'field_id':3781223,'values':[{'value':'ВК'}]}])
    result=verified_source_projection(None,current,fields)
    assert result.fields==[{'field_id':3134043,'values':[{'enum_id':2}]}]


@pytest.mark.parametrize('failure,expected', [('Спам', False), ('Дорого', True)])
def test_history_respects_spam_qualification_exclusion(failure,expected):
    from app.crm_automation_fields import project_stage_history, QUALIFIED_DATE
    cat=a.Catalog([{'id':a.SALES,'_embedded':{'statuses':[
        {'id':11,'name':'Встреча назначена','type':0}]}}])
    item=lead(status_id=143,custom_fields_values=[
        {'field_id':3134051,'values':[{'value':failure}]}])
    events=[{'lead_id':55,'at':100,'after':{'pipeline_id':a.SALES,'id':11}}]
    projected=project_stage_history(item,cat,events)
    assert any(f['field_id']==QUALIFIED_DATE for f in projected.fields) is expected


def test_matching_field_before_first_write_does_not_transfer_ownership(db):
    from app import crm_automation_fields as f
    f.FieldExecution.__table__.create(db.get_bind())
    item=lead(custom_fields_values=[])
    projection=f.FieldProjection(fields=[{"field_id":3279145,"values":[{"value":123}]}])
    job=f.plan_fields(db,item,"preview",projection)
    item["custom_fields_values"]=projection.fields
    class ReadOnly:
        def get(self,path): return item
    assert f.execute_fields(db,ReadOnly(),job,lambda _:projection,preview=False)=="matched"


def test_shared_ad_number_uses_other_only_with_exact_single_called_number():
    from app import crm_automation_fields as f
    fields=[{"id":i,"enums":[{"id":i+1,"value":"Другое"}]} for i in (3134043,3781223)]
    item=lead(custom_fields_values=[],_embedded={"tags":[{"name":f.SHARED_AD_CALL_TAG}]})
    result=f.project_first_source(item,fields,first_touch=True)
    assert {x["field_id"] for x in result.fields}=={3134043,3781223}
    item["_embedded"]["tags"].append({"name":"Вызов-на-номер:1"})
    assert not f.project_first_source(item,fields,first_touch=True).fields

def test_manually_rescheduled_owned_task_survives_stage_exit(db):
    c=Client()
    job=plan(db)
    execute(db,job,c,preview=False)
    c.task["complete_till"]+=3600
    assert a.complete_owned_task(db,job,c,"Этап изменился",preview=False)=="manual_edit_conflict"
    assert not c.patches and job.state=="created"


def test_migration_preflight_holds_unfinished_kassa_edit_and_tag():
    from app.pipeline_guard import MIGRATION_TAG
    item = lead()
    binding = {"lead_id": item["id"], "kind": "company", "edit_pending": True}
    assert a.route_with_bindings(item, catalog(), [binding]).reason == "kassa_edit_pending"
    item["_embedded"] = {"tags": [{"name": MIGRATION_TAG}]}
    assert a.route_with_bindings(item, catalog(), []).reason == "migration_guard"
