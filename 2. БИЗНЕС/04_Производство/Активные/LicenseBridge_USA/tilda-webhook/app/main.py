"""Точка входа hub LicenseBridge.

RUN_MODE=api    → FastAPI (вебхуки, кладут события в inbox).
RUN_MODE=worker → фоновый обработчик inbox + scanner (см. app/worker.py).

Запуск api:    uvicorn app.main:app --host 0.0.0.0 --port 8080
Запуск worker: python -m app.worker
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.webhooks import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("main")

app = FastAPI(title="LicenseBridge Hub", version="3.0")
app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    from app.merge_tags import ensure_pool
    from app.db import session_scope

    with session_scope() as s:
        ensure_pool(s)
    log.info("api started shadow=%s account=%s", settings.shadow_mode, settings.kommo_account_id)


if __name__ == "__main__":
    if settings.run_mode == "worker":
        from app.worker import run_loop

        run_loop()
    else:
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=8080)
