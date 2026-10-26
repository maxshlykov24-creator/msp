import hashlib
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
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
    app = FastAPI(title='YUJI · Учёт производства', docs_url=None, redoc_url=None)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def headers(request: Request, call_next):
        length = request.headers.get('content-length', '0')
        if not length.isdigit() or int(length) > 2_000_000:
            return JSONResponse({'detail': 'Слишком большой запрос'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    def auth(token: HTTPAuthorizationCredentials = Depends(bearer)):
        if token is None:
            raise HTTPException(401, 'Войди с личным ключом доступа')
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
                version = s.get(Entity, 'user-version:' + u.id)
                result.append({'id': u.id, 'name': u.name, 'role': u.role, 'active': u.active,
                               'version': version.version if version else 1})
            return result

    @app.get('/api/catalog')
    def catalog(user=Depends(auth)):
        with Session(engine) as s:
            balances = {}
            for b in s.scalars(select(Balance)):
                balances.setdefault(b.item_id, []).append({'location': b.location, 'known': b.known,
                    'free': str(b.free) if b.known else None, 'frozen': str(b.frozen) if b.known else None,
                    'reserved': str(b.reserved) if b.known else None, 'damaged': str(b.damaged) if b.known else None,
                    'version': b.version})
            return {'items': [{'id': i.id, 'name': i.name, 'model': i.model, 'color': i.color, 'size': i.size,
                       'category': i.category, 'kind': i.kind, 'unit': i.unit, 'barcodes': i.barcodes,
                       'photo': i.photo, 'source': i.source, 'balances': balances.get(i.id, [])}
                      for i in s.scalars(select(Item).order_by(Item.model, Item.color, Item.size))]}

    @app.get('/api/entities/{kind}')
    def entities(kind: str, user=Depends(auth)):
        if kind not in ('task', 'freeze', 'count', 'order', 'fbo', 'finance', 'notice', 'integration', 'norm', 'telegram_link', 'mapping'):
            raise HTTPException(404, 'Раздел не найден')
        if kind == 'telegram_link' and 'settings' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет права управления привязками')
        if kind == 'finance' and 'finance' not in PERMISSIONS[user.role]:
            raise HTTPException(403, 'Нет доступа к финансам')
        with Session(engine) as s:
            return [{'id': e.id, 'version': e.version, **e.data} for e in s.scalars(select(Entity).where(Entity.kind == kind))]

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
                                     'movements': records(Movement), 'entities': records(Entity)}, custom_encoder={Decimal: str})

    @app.get('/api/status')
    def status(user=Depends(auth)):
        with Session(engine) as s:
            pending = list(s.scalars(select(Outbox).where(Outbox.status == 'held')))
            snapshots = list(s.scalars(select(Entity).where(Entity.kind == 'snapshot')))
            integration = [e.data for e in s.scalars(select(Entity).where(Entity.kind == 'integration'))]
            maintenance = {e.id.split(':', 1)[1]: e.data for e in s.scalars(select(Entity).where(Entity.kind == 'maintenance'))}
            marketplace = [e.data for e in s.scalars(select(Entity).where(Entity.kind == 'marketplace_snapshot'))]
            return {'publication': 'disabled', 'queue_held': len(pending), 'maintenance': maintenance,
                    'snapshots': [e.data for e in snapshots], 'integration': integration, 'marketplace': marketplace, 'finance': 'rules_pending',
                    'blockers': ['Пилот: весь каталог. Требуется сверить единственный источник записи',
                                 'Живые сценарии WB и Ozon не пройдены', 'Правила оплаты цеха не получены']}

    @app.post('/api/commands')
    def commands(body: Command, user=Depends(auth)):
        try:
            return execute(engine, user, body.operation_id, body.command, body.payload, body.reason)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, 'Неполные или неверные поля команды') from exc

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
