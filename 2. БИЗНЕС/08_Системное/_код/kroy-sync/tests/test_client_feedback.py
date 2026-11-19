from datetime import date,timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from app.cutting import calculate
from app.db import Entity,Document
from app.service import DomainError
from app.workshop import worksheet
from test_app import setup,balance

def test_finish_resume_receive_and_retry(setup):
    engine,_,cmd=setup
    count=cmd('count.create',dict(location='own',mode='scanned',barcode='001'))
    def step(action,**p):
        nonlocal count
        count=cmd('count.'+action,dict(id=count['id'],version=count['version'],**p))
    step('finish')
    assert balance(engine)['free']==10
    with pytest.raises(DomainError):step('scan',barcode='002')
    step('resume')
    step('scan',barcode='002')
    step('finish')
    payload=dict(id=count['id'],version=count['version'],action='receive')
    op=str(uuid4())
    cmd('count.post',payload,op)
    cmd('count.post',payload,op)
    assert balance(engine)['free']==11 and balance(engine,'m')['free']==1
    with pytest.raises(DomainError):cmd('count.post',payload)

def test_manual_batch_freeze_audit_rollback(setup):
    engine,_,cmd=setup
    line=dict(item_id='s',version=balance(engine)['version'],total=15,frozen=4)
    with pytest.raises(DomainError):
        cmd('stock.batch',dict(location='own',rows=[line,dict(item_id='m',version=1,total=0,frozen=1)]))
    assert balance(engine)['free']==10
    change=cmd('stock.batch',dict(location='own',rows=[line]))
    with Session(engine) as s:
        assert s.get(Document,change['document_id']).payload['changes'][0]['after_total']=='15'
        lot=s.query(Entity).filter_by(kind='freeze').one().id
    line.update(version=balance(engine)['version'],total=12,frozen=2,releases={lot:'2'})
    cmd('stock.batch',dict(location='own',rows=[line]))
    assert balance(engine)['free']==10 and balance(engine)['frozen']==2
    with pytest.raises(DomainError):cmd('stock.batch',dict(location='own',rows=[line]))

def test_report_scoped_by_platform_date_and_exact_code(setup):
    engine,_,cmd=setup
    d=str(date.today()-timedelta(days=2))
    raw=dict(start=d,end=d,key='barcode',file='report.xlsx',sha256='c'*64,rows=[dict(code='001',sales=4,returns=1)])
    wb=cmd('cut.report',{**raw,'platform':'wb'})['id']
    oz=cmd('cut.report',{**raw,'platform':'ozon'})['id']
    with pytest.raises(DomainError):cmd('cut.report',{**raw,'platform':'wb','rows':[dict(code='missing',sales=1,returns=0)]})
    params=dict(location='own',start=d,end=d,item_ids=['s','m'],horizon=3,report_ids={'wb':wb,'ozon':oz})
    with Session(engine) as s:
        rows={r['item_id']:r for r in calculate(s,params)['rows']}
        assert rows['s']['net']==6 and rows['s']['recommendation']==8
        assert rows['m']['net'] is None
        with pytest.raises(DomainError):calculate(s,{**params,'report_ids':{'ozon':wb}})

def test_workshop_receipt_rate_date_advance_and_closed_snapshot(setup):
    engine,_,cmd=setup
    def add(kind,day,ref,**values):
        return cmd('workshop.record',dict(kind=kind,effective_date='2026-09-'+day,month='2026-09',reference=ref,**values))
    add('receipt','10','Акт 1',item_id='s',quantity=4,platform='wb')
    add('advance','04','Платёж 1',amount='70')
    add('opening','01','Сальдо',amount='0')
    with Session(engine) as s:assert worksheet(s,'2026-09')['balance'] is None
    add('rate','02','Ставка 1',item_id='s',amount='100')
    add('rate','15','Ставка 2',item_id='s',amount='120')
    with pytest.raises(DomainError):add('receipt','10','Акт 1',item_id='s',quantity=4,platform='wb')
    with pytest.raises(DomainError):add('payment','04','Платёж 1',amount='70')
    with Session(engine) as s:
        r=worksheet(s,'2026-09')
        assert Decimal(r['balance'])==330
    cmd('workshop.close',dict(month='2026-09',confirmed=True,fingerprint=r['fingerprint']))
    with pytest.raises(DomainError):add('advance','02','Платёж 2',amount='1')
    with Session(engine) as s:assert worksheet(s,'2026-09')['closed']
