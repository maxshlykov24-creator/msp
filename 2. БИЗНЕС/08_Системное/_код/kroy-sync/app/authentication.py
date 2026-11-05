"""Password authentication and revocable server-side device sessions."""
import base64,hashlib,hmac,secrets,re
from datetime import timedelta
from sqlalchemy import select
from .db import Entity,User,now,transaction
from .service import require

ITERATIONS=600_000

def password_record(password):
    require(isinstance(password,str) and 10<=len(password)<=256,'Пароль должен содержать от 10 до 256 символов',422)
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,ITERATIONS)
    return {'salt':base64.b64encode(salt).decode(),'digest':base64.b64encode(digest).decode(),'iterations':ITERATIONS}

def verify(password,record):
    if not isinstance(password,str) or len(password)>256:return False
    try:
        salt=base64.b64decode(record['salt'],validate=True);expected=base64.b64decode(record['digest'],validate=True)
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,int(record['iterations']))
        return hmac.compare_digest(actual,expected)
    except (ValueError,KeyError,TypeError):return False

def setup_password(engine,user,password,retire_key=False,login_name=None):
    name=str(login_name or user.id).strip().lower()
    require(re.fullmatch(r'[a-z0-9_-]{1,64}',name) is not None,'Логин: латиница, цифры, дефис или подчёркивание',422)
    alias='auth-login:'+hashlib.sha256(name.encode()).hexdigest()
    record={**password_record(password),'login_name':name}
    with transaction(engine) as s:
        current=s.get(User,user.id);require(current is not None and current.active,'Доступ отозван',403)
        key='auth-password:'+user.id;require(s.get(Entity,key) is None,'Пароль уже установлен',409)
        require(s.get(Entity,alias) is None,'Логин уже занят',409)
        other=s.get(User,name);require(other is None or other.id==user.id,'Логин уже занят',409)
        s.add(Entity(id=alias,kind='auth_login',version=1,data={'user_id':user.id}))
        s.add(Entity(id=key,kind='auth_password',version=1,data=record))
        if retire_key:current.token_hash=secrets.token_hex(32)

def login(engine,login,password,ip):
    login=str(login or '').strip();key='auth-limit:'+hashlib.sha256((ip+'|'+login.lower()).encode()).hexdigest()
    with transaction(engine) as s:
        limit=s.get(Entity,key);data=dict(limit.data) if limit else {};until=data.get('until')
        require(not until or now().isoformat()>=until,'Слишком много попыток. Попробуй позже',429)
        alias=s.get(Entity,'auth-login:'+hashlib.sha256(login.lower().encode()).hexdigest())
        user=s.get(User,alias.data['user_id'] if alias else login);credential=s.get(Entity,'auth-password:'+(user.id if user else login))
        record=dict(credential.data) if credential else {'salt':base64.b64encode(b'0'*16).decode(),'digest':base64.b64encode(b'0'*32).decode(),'iterations':ITERATIONS}
        valid=bool(user and user.active and credential and record.get('login_name',login.lower())==login.lower() and verify(password,record))
        if not user or not credential:verify(password,record)
        if not valid:
            failures=data.get('failures',0)+1;updated={'failures':failures,'until':(now()+timedelta(minutes=15)).isoformat() if failures>=5 else None}
            if limit:limit.data=updated;limit.version+=1
            else:s.add(Entity(id=key,kind='auth_limit',version=1,data=updated))
        else:
            if limit:limit.data={'failures':0,'until':None};limit.version+=1
            token=secrets.token_urlsafe(32);session='auth-session:'+hashlib.sha256(token.encode()).hexdigest()
            s.add(Entity(id=session,kind='auth_session',version=1,data={'user_id':user.id,'expires_at':(now()+timedelta(days=30)).isoformat(),'active':True}))
    require(valid,'Неверный логин или пароль',401)
    return token

def create_session(engine,user_id):
    token=secrets.token_urlsafe(32);key='auth-session:'+hashlib.sha256(token.encode()).hexdigest()
    with transaction(engine) as s:
        user=s.get(User,user_id);require(user is not None and user.active,'Доступ отозван',403)
        s.add(Entity(id=key,kind='auth_session',version=1,data={'user_id':user_id,'expires_at':(now()+timedelta(days=30)).isoformat(),'active':True}))
    return token

def session_user(engine,token):
    if not token or len(token)>200:return None
    from sqlalchemy.orm import Session
    key='auth-session:'+hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        e=s.get(Entity,key)
        if not e or not e.data.get('active') or e.data['expires_at']<=now().isoformat():return None
        u=s.get(User,e.data['user_id'])
        if not u or not u.active:return None
        s.expunge(u);return u

def logout(engine,token):
    if not token:return
    key='auth-session:'+hashlib.sha256(token.encode()).hexdigest()
    with transaction(engine) as s:
        e=s.get(Entity,key)
        if e:e.data={**e.data,'active':False};e.version+=1
