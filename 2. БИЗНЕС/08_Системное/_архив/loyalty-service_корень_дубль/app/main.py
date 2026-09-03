from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, init_db
from app.models import BonusLog
from app.moysklad_client import MoySkladClient
from app.scheduler import shutdown_scheduler, start_scheduler
from app.webhook_handlers import dispatch_webhook_event


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="MartaChe Loyalty Service", lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _webhook_event_list(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    evs = body.get("events")
    if isinstance(evs, list) and evs:
        return evs
    meta = body.get("meta")
    if isinstance(meta, dict) and meta.get("type"):
        return [{"meta": meta}]
    return []


@app.post("/webhook/moysklad")
def webhook_moysklad(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    x_loyalty_secret: Optional[str] = Header(default=None, alias="X-Loyalty-Secret"),
) -> Dict[str, bool]:
    s = get_settings()
    wsec = (s.webhook_secret or "").strip()
    if wsec and (x_loyalty_secret or "").strip() != wsec:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    client = MoySkladClient()
    for ev in _webhook_event_list(body):
        dispatch_webhook_event(db, client, (ev.get("meta") or {}))
    db.commit()
    return {"ok": True}


@app.get("/api/log")
def api_log(
    db: Session = Depends(get_db),
    order: Optional[str] = Query(default=None, description="ID заказа или поле name"),
    agent_id: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    q = select(BonusLog).order_by(BonusLog.ts.desc()).limit(min(max(limit, 1), 2000))
    conds = []
    if order:
        conds.append(or_(BonusLog.customerorder_id == order, BonusLog.customerorder_name == order))
    if agent_id:
        conds.append(BonusLog.agent_id == agent_id)
    if conds:
        q = q.where(and_(*conds))
    rows = list(db.scalars(q).all())
    return {
        "items": [
            {
                "ts": r.ts.isoformat(),
                "action": r.action,
                "customerorder_id": r.customerorder_id,
                "customerorder_name": r.customerorder_name,
                "agent_id": r.agent_id,
                "agent_name": r.agent_name,
                "tier_at_moment": r.tier_at_moment,
                "bonus_amount": r.bonus_amount,
                "bonustransaction_id": r.bonustransaction_id,
                "details": r.details,
            }
            for r in rows
        ]
    }


@app.get("/internal/meta/bonusprogram")
def internal_bonusprogram() -> Dict[str, Any]:
    client = MoySkladClient()
    return client.fetch_bonus_program_meta()
