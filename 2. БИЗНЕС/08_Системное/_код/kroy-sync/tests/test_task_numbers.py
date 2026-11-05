from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from app.db import Entity, transaction
from app.task_numbers import backfill_task_numbers
from app.service import DomainError
from test_app import setup


def draft(**meta):
    return dict(location='own', products=[], materials=[], cutting=dict(schema=1, **meta))


def test_number_retry_edit_comment_and_failed_issue(setup):
    engine,u,cmd=setup
    with pytest.raises(DomainError): cmd('task.issue', draft())
    op=str(uuid4());p=draft(comment='Не смешивать партии\nСверить цвет')
    one=cmd('task.save',p,op)
    assert cmd('task.save',p,op)==one
    edit=cmd('task.save',{**p,'id':one['id'],'version':one['version']})
    two=cmd('task.save',draft())
    with Session(engine) as s:
        assert s.get(Entity,one['id']).data['number']=='КР-001'
        assert s.get(Entity,one['id']).data['cutting']['comment']==p['cutting']['comment']
        assert s.get(Entity,two['id']).data['number']=='КР-002'
    assert edit['version']>one['version']
    with pytest.raises(DomainError,match='Комментарий'):cmd('task.save',draft(comment='x'*4001))


def test_concurrent_numbers_are_unique(setup):
    engine,u,cmd=setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:cmd('task.save',draft()),range(8)))
    with Session(engine) as s:
        numbers={s.get(Entity,r['id']).data['number'] for r in results}
    assert numbers=={f'КР-{i:03d}' for i in range(1,9)}


def test_backfill_preserves_plan_facts_and_original_reference(setup):
    engine,u,cmd=setup
    old=dict(number='КР-ABCDEF12',title='Старое',created_at='2026-10-01T00:00:00Z',products=[{'item_id':'s','quantity':'3'}],facts=['fact'])
    with transaction(engine) as s:
        s.add(Entity(id='legacy',kind='task',version=2,data=old))
    with transaction(engine) as s:assert backfill_task_numbers(s)==1
    with transaction(engine) as s:assert backfill_task_numbers(s)==0
    with Session(engine) as s:
        e=s.get(Entity,'legacy');data=dict(e.data)
        assert data.pop('number')=='КР-001'
        assert data.pop('legacy_number')==old['number']
        assert data=={k:v for k,v in old.items() if k!='number'}
        assert e.version==3
    new=cmd('task.save',draft())
    with Session(engine) as s:assert s.get(Entity,new['id']).data['number']=='КР-002'
