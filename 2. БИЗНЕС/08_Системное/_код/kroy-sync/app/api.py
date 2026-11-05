import hashlib
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Query, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import Balance, Document, Entity, Item, Movement, Outbox, User, engine_for
from .planning import NORMS, NORM_SOURCE, demand, layout_preview, grouped_layout_preview
from .service import DomainError, PERMISSIONS, execute


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str
    command: str
    reason: str = Field(min_length=1, max_length=2000)
    payload: dict


def create_app(engine=None):
    engine = engine or engine_for()
    app = FastAPI(title='YUJI.CLO', docs_url=None, redoc_url=None)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def headers(request: Request, call_next):
        length = request.headers.get('content-length', '0')
        if not length.isdigit() or int(length) > 2_000_000:
            return JSONResponse({'detail': 'Слишком большой запрос'}, status_code=413)
        origin=request.headers.get('origin')
        if origin and request.method not in ('GET','HEAD','OPTIONS'):
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc.lower()!=request.headers.get('host','').lower():
                return JSONResponse({'detail':'Запрос с другого сайта отклонён'},status_code=403)
        response = await call_next(request)
        response.headers.setdefault('Cache-Control', 'no-store')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    def auth(request: Request, token: HTTPAuthorizationCredentials = Depends(bearer)):
        from .authentication import session_user
        if token is None:
            user=session_user(engine,request.cookies.get('yuji_session'))
            if user is None:raise HTTPException(401, 'Войди в аккаунт')
            return user
        digest = hashlib.sha256(token.credentials.encode()).hexdigest()
        with Session(engine) as s:
            user = s.scalar(select(User).where(User.token_hash == digest, User.active.is_(True)))
            if user is None:
                raise HTTPException(401, 'Доступ не найден или отозван')
            s.expunge(user)
            return user

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({'detail': exc.message}, status_code=exc.status)

    @app.get('/api/health')
    def health():
        with Session(engine) as s:
            s.execute(text('SELECT 1'))
        return {'ok': True, 'publication': 'disabled', 'environment': os.environ.get('KROY_ENV', 'development')}

    @app.post('/api/auth/login')
    def login_password(body: dict,request: Request,response: Response):
        from .authentication import login
        token=login(engine,body.get('login'),body.get('password'),request.client.host if request.client else '')
        response.set_cookie('yuji_session',token,max_age=30*86400,path='/yuji/',httponly=True,secure=True,samesite='strict')
        return {'ok':True}

    @app.post('/api/auth/setup')
    def password_setup(body: dict,request: Request,response: Response,user=Depends(auth)):
        from .authentication import setup_password,create_session
        setup_password(engine,user,body.get('password'),retire_key=True,login_name=body.get('login'))
        token=create_session(engine,user.id)
        response.set_cookie('yuji_session',token,max_age=30*86400,path='/yuji/',httponly=True,secure=True,samesite='strict')
        return {'ok':True}

    @app.post('/api/auth/staff-password')
    def staff_password(body: dict,user=Depends(auth)):
        from .authentication import setup_password,verify
        if 'settings' not in PERMISSIONS[user.role]:raise HTTPException(403,'Нет права управления сотрудниками')
        with Session(engine) as s:
            target=s.get(User,body.get('id'));existing=s.get(Entity,'auth-password:'+str(body.get('id')))
            if not target or not target.active:raise HTTPException(404,'Сотрудник не найден')
            if existing:
                if verify(body.get('password'),existing.data):return {'ok':True}
                raise HTTPException(409,'Пароль уже установлен')
            s.expunge(target)
        setup_password(engine,target,body.get('password'))
        return {'ok':True}

    @app.post('/api/auth/logout')
    def logout_password(request: Request,response: Response):
        from .authentication import logout
        logout(engine,request.cookies.get('yuji_session'))
        response.delete_cookie('yuji_session',path='/yuji/',secure=True,httponly=True,samesite='strict')
        return {'ok':True}

    @app.get('/api/me')
    def me(user=Depends(auth)):
        return {'id': user.id, 'name': user.name, 'role': user.role, 'permissions': sorted(PERMISSIONS[user.role])}

    @app.get('/api/users')
    def users(user=Depends(auth)):
        if 'settings' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права управления сотрудниками')
        with Session(engine) as s:
            result = []
            for u in s.scalars(select(User)):
                if u.id=='marketplace-orders-worker':continue
                version = s.get(Entity, 'user-version:' + u.id)
                result.append({'id': u.id, 'name': u.name, 'role': u.role, 'active': u.active,
                               'version': version.version if version else 1})
            return result

    @app.get('/api/photos/{key}')
    def public_product_photo(key: str):
        import re
        from .photos import photo_root, image_type, photo_key, cache_photo
        if not re.fullmatch(r'[a-f0-9]{64}', key): raise HTTPException(404, 'Фото не найдено')
        path = photo_root() / (key + '.img')
        if not path.is_file():
            with Session(engine) as s:
                url = next((i.photo for i in s.scalars(select(Item)) if i.photo and photo_key(i.photo) == key), None)
            if not url: raise HTTPException(404, 'Фото не найдено')
            try: cached = cache_photo(url)
            except Exception: raise HTTPException(503, 'Фото временно недоступно')
            if cached is None: raise HTTPException(404, 'Фото недоступно')
        with path.open('rb') as f: mime = image_type(f.read(16))
        if not mime: raise HTTPException(404, 'Фото недоступно')
        return FileResponse(path, media_type=mime, headers={'Cache-Control':'public, max-age=86400, immutable'})

    @app.get('/api/counting/barcode')
    def counting_barcode(code: str = Query(min_length=1,max_length=300), user=Depends(auth)):
        if 'count' not in PERMISSIONS[user.role]: raise HTTPException(403, 'Нет права пересчёта')
        from .product_identity import barcode_matches
        with Session(engine) as s:
            matches=barcode_matches(s,code.strip())
            if len(matches)!=1 or matches[0].unit not in ('piece','roll'):
                raise HTTPException(422, 'Штрихкод не найден или соответствует нескольким товарам')
            return {'item_id':matches[0].id}

    @app.get('/api/catalog')
    def catalog(user=Depends(auth)):
        with Session(engine) as s:
            balances = {}
            for b in s.scalars(select(Balance)):
                balances.setdefault(b.item_id, []).append({'location': b.location, 'known': b.known,
                    'free': str(b.free) if b.known else None, 'frozen': str(b.frozen) if b.known else None,
                    'reserved': str(b.reserved) if b.known else None, 'damaged': str(b.damaged) if b.known else None,
                    'version': b.version})
            from .photos import photo_preview
            items = list(s.scalars(select(Item).order_by(Item.model, Item.color, Item.size)))
            color_photos = {}
            for i in items:
                if i.photo: color_photos.setdefault((i.kind, i.model, i.color), set()).add(i.photo)
            def preview(i):
                urls = color_photos.get((i.kind, i.model, i.color), set())
                url = i.photo or (next(iter(urls)) if len(urls) == 1 else '')
                return photo_preview(url)
            return {'items': [{'id': i.id, 'name': i.name, 'model': i.model, 'color': i.color, 'size': i.size,
                       'category': i.category, 'kind': i.kind, 'unit': i.unit, 'barcodes': i.barcodes,
                       'photo': i.photo, 'photo_preview': preview(i), 'source': i.source, 'balances': balances.get(i.id, [])}
                      for i in items]}

    @app.get('/api/entities/{kind}')
    def entities(kind: str, include_managed: bool=False, user=Depends(auth)):
        if kind not in ('task', 'freeze', 'count', 'order', 'fbo', 'finance', 'notice', 'integration', 'norm', 'telegram_link', 'mapping'):
            raise HTTPException(404, 'Раздел не найден')
        if kind=='order' and user.role=='cutter':raise HTTPException(403,'Нет доступа к заказам')
        if kind == 'telegram_link' and 'settings' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права управления привязками')
        if kind == 'finance' and 'finance' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет доступа к финансам')
        with Session(engine) as s:
            result = []
            query=select(Entity).where(Entity.kind==kind)
            if kind=='order' and not include_managed:query=query.where(Entity.data['managed_market_id'].as_string().is_(None))
            for e in s.scalars(query):
                if kind == 'task' and user.role == 'cutter' and (e.data.get('status') == 'draft' or e.data.get('assignee') not in (None, '', user.id)):
                    continue
                data = dict(e.data)
                if kind == 'task' and not data.get('created_at'):
                    doc = s.get(Document, e.id)
                    if doc: data['created_at'] = doc.created_at.isoformat()
                result.append({'id': e.id, 'version': e.version, **data})
            return result

    @app.get('/api/orders')
    def market_orders(platform: str='', scheme: str='', status: str='', q: str=Query('',max_length=200), start: str='', end: str='', page: int=Query(1,ge=1), per_page: int=Query(50,ge=1,le=100), attention: bool=False, user=Depends(auth)):
        if user.role=='cutter':raise HTTPException(403,'Нет доступа к заказам')
        from .order_feed import list_orders, STATES
        from datetime import date
        if platform not in ('','wb','ozon') or scheme not in ('','fbs','fbo') or status and status not in STATES: raise HTTPException(422,'Проверь фильтры заказов')
        try:
            if start:date.fromisoformat(start)
            if end:date.fromisoformat(end)
            if start and end and start>end:raise ValueError()
        except ValueError:raise HTTPException(422,'Проверь период заказов')
        with Session(engine) as s:return list_orders(s,platform,scheme,status,q,start,end,page,per_page,attention)

    @app.get('/api/orders/sync')
    def order_sync_state(user=Depends(auth)):
        if user.role=='cutter':raise HTTPException(403,'Нет доступа к заказам')
        from .order_feed import sync_state
        state=sync_state(engine)
        return {k:{x:v for x,v in d.items() if x not in ('account','token_fingerprint')} for k,d in state.items() if not k.startswith('limit:') and k!='wb-identity'}

    @app.post('/api/orders/refresh')
    def order_refresh(user=Depends(auth)):
        if 'orders' not in PERMISSIONS[user.role]:raise HTTPException(403,'Нет права обновления заказов')
        from .order_feed import set_sync
        from .db import now
        set_sync(engine,'request',requested_at=now().isoformat())
        return {'status':'queued'}

    @app.get('/api/orders/{order_id}')
    def market_order_detail(order_id: str,user=Depends(auth)):
        if user.role=='cutter':raise HTTPException(403,'Нет доступа к заказам')
        from .order_feed import public_order
        with Session(engine) as s:
            e=s.get(Entity,order_id)
            if not e or e.kind!='market_order':raise HTTPException(404,'Заказ не найден')
            return public_order(s,e)

    @app.get('/api/journal')
    def journal(item_id: str | None = None, user=Depends(auth)):
        with Session(engine) as s:
            query = select(Document).order_by(Document.created_at.desc()).limit(200)
            if 'finance' not in PERMISSIONS[user.role]:
                query = query.where(Document.kind != 'finance.record')
            if item_id:
                query = query.where(Document.id.in_(select(Movement.document_id).where(Movement.item_id == item_id)))
            docs = list(s.scalars(query))
            movements = list(s.scalars(select(Movement).where(Movement.document_id.in_([d.id for d in docs]))))
            return [{'id': d.id, 'kind': d.kind, 'actor': d.actor, 'reason': d.reason, 'at': d.created_at.isoformat(),
                     'movements': [{'item_id': m.item_id, 'location': m.location, 'bucket': m.bucket, 'quantity': str(m.quantity)}
                                   for m in movements if m.document_id == d.id]} for d in docs]

    @app.get('/api/export')
    def export(user=Depends(auth)):
        if 'export' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права экспорта')
        from fastapi.encoders import jsonable_encoder
        from decimal import Decimal
        from .db import now
        with Session(engine) as s:
            def records(model):
                return [{c.name: getattr(row, c.name) for c in model.__table__.columns}
                        for row in s.scalars(select(model))]
            # Access tokens and user secrets are deliberately outside the business export.
            return jsonable_encoder({'exported_at': now(), 'items': records(Item),
                                     'balances': records(Balance), 'documents': records(Document),
                                     'movements': records(Movement), 'entities': [e for e in records(Entity) if not e['kind'].startswith('auth_')]}, custom_encoder={Decimal: str})

    @app.get('/api/status')
    def status(user=Depends(auth)):
        with Session(engine) as s:
            pending = list(s.scalars(select(Outbox).where(Outbox.status == 'held')))
            snapshots = list(s.scalars(select(Entity).where(Entity.kind == 'snapshot')))
            integration = [e.data for e in s.scalars(select(Entity).where(Entity.kind == 'integration'))]
            maintenance = {e.id.split(':', 1)[1]: e.data for e in s.scalars(select(Entity).where(Entity.kind == 'maintenance'))}
            marketplace = [e.data for e in s.scalars(select(Entity).where(Entity.kind == 'marketplace_snapshot'))]
            open_orders = [e.data for e in s.scalars(select(Entity).where(Entity.kind == 'open_order_summary'))]
            return {'publication': 'disabled', 'queue_held': len(pending), 'maintenance': maintenance, 'open_orders': open_orders,
                    'snapshots': [e.data for e in snapshots], 'integration': integration, 'marketplace': marketplace, 'finance': 'rules_pending',
                    'blockers': ['Пилот: весь каталог. Требуется сверить единственный источник записи',
                                 'Живые сценарии WB и Ozon не пройдены', 'Правила оплаты цеха не получены']}

    @app.post('/api/commands')
    def commands(body: Command, user=Depends(auth)):
        try:
            return execute(engine, user, body.operation_id, body.command, body.payload, body.reason)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, 'Неполные или неверные поля команды') from exc

    @app.post('/api/cutting/sales-refresh')
    def refresh_sales(body: dict, user=Depends(auth)):
        if 'cut_plan' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права подготовки задания')
        from .sales_jobs import enqueue
        try:
            return enqueue(engine, user.id, body)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, 'Проверь период сверки') from exc

    @app.get('/api/cutting/sales-refresh/{job_id}')
    def refresh_progress(job_id: str, user=Depends(auth)):
        from .sales_jobs import public_job
        if 'cut_plan' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права подготовки задания')
        with Session(engine) as s:
            job = s.get(Entity, job_id)
            if not job or job.kind != 'sales_refresh':
                raise HTTPException(404, 'Сверка не найдена')
            return public_job(job)

    @app.get('/api/cutting/sales-status')
    def sales_status(user=Depends(auth)):
        with Session(engine) as s:
            e = s.get(Entity, 'cutting-sales-status')
            return e.data if e else {'platforms': {}}

    @app.get('/api/cutting/assignees')
    def assignees(user=Depends(auth)):
        with Session(engine) as s:
            return [{'id': u.id, 'name': u.name} for u in s.scalars(select(User).where(User.active.is_(True), User.role.in_(['owner', 'cutter'])))]

    @app.post('/api/cutting/demand')
    def cut_demand(body: dict, user=Depends(auth)):
        from .cutting import calculate
        try:
            with Session(engine) as s:
                return calculate(s, body)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, 'Проверь товары, даты и склад') from exc

    @app.get('/api/norms')
    def norms(user=Depends(auth)):
        return {'values': NORMS, 'source': NORM_SOURCE, 'confirmed': False}

    @app.post('/api/planning/layout')
    def layout(body: dict, user=Depends(auth)):
        try:
            return layout_preview(**body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/planning/grouped-layout')
    def grouped_layout(body: dict, user=Depends(auth)):
        try:
            return grouped_layout_preview(**body)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, 'Проверь нормы, размеры, раскладки и доступную ткань') from exc

    @app.post('/api/planning/demand')
    def need(body: dict, user=Depends(auth)):
        try:
            return demand(**body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    dist = Path(__file__).resolve().parents[1] / 'web' / 'dist'
    if dist.is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')
        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html')
    return app
