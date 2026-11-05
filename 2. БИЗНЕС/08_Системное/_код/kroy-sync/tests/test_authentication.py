from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.api import create_app
from app.db import Entity,User,transaction
from test_app import setup


def test_password_setup_session_reload_logout_and_revocation(setup):
 engine,user,_=setup;c=TestClient(create_app(engine),base_url='https://testserver')
 old={'Authorization':'Bearer test-only'}
 assert c.post('/api/auth/setup',headers=old,json={'login':'chosen-login','password':'a-secure-test-password'}).status_code==200
 assert not any(e['kind'].startswith('auth_') for e in c.get('/api/export',headers={'Cookie':'yuji_session='+c.cookies.get('yuji_session')}).json()['entities'])
 assert c.get('/api/me',headers={'Cookie':'yuji_session='+c.cookies.get('yuji_session')}).json()['id']=='owner'
 assert c.post('/api/auth/setup',headers=old,json={'login':'owner','password':'different-password'}).status_code==401
 with Session(engine) as s:
  d=s.get(Entity,'auth-password:owner').data;assert 'a-secure-test-password' not in str(d)
 c.cookies.clear();assert c.post('/api/auth/login',json={'login':'chosen-login','password':'wrong-password'}).status_code==401
 assert c.post('/api/auth/login',json={'login':'chosen-login','password':'a-secure-test-password'}).status_code==200
 cookie=c.cookies.get('yuji_session');headers={'Cookie':'yuji_session='+cookie}
 assert c.get('/api/me',headers=headers).status_code==200
 with transaction(engine) as s:s.get(User,'owner').active=False
 assert c.get('/api/me',headers=headers).status_code==401
 with transaction(engine) as s:s.get(User,'owner').active=True
 assert c.post('/api/auth/logout',headers=headers).status_code==200
 assert c.get('/api/me',headers=headers).status_code==401


def test_auth_origin_and_brute_force_limits(setup):
 engine,_,_=setup;c=TestClient(create_app(engine),base_url='https://testserver')
 assert c.post('/api/auth/login',headers={'Origin':'https://other.example'},json={'login':'owner','password':'x'}).status_code==403
 for _ in range(5):assert c.post('/api/auth/login',json={'login':'owner','password':'x'}).status_code==401
 assert c.post('/api/auth/login',json={'login':'owner','password':'x'}).status_code==429
