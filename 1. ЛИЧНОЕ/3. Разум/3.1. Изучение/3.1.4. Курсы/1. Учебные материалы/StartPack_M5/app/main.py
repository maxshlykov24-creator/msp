"""ИИ-Слушатель звонков — приёмник вебхуков CRM.

  POST /webhook/amo-call     ← AmoCRM (примечание-звонок)
  POST /webhook/bitrix-call  ← Bitrix24 (событие ONVOXIMPLANTCALLEND)
  GET  /healthz              ← проверка живости

Обработка звонка идёт в фоне (BackgroundTasks): отвечаем CRM 200 сразу,
скачивание+транскрибация (долгие) выполняются после ответа.
"""
import logging

from fastapi import BackgroundTasks, FastAPI, Request

import pipeline

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI-Слушатель звонков", version="1.0")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook/amo-call")
async def amo_call(request: Request, bg: BackgroundTasks):
    payload = await _payload(request)
    bg.add_task(pipeline.process, "amo", payload)
    return {"accepted": True}


@app.post("/webhook/bitrix-call")
async def bitrix_call(request: Request, bg: BackgroundTasks):
    payload = await _payload(request)
    bg.add_task(pipeline.process, "bitrix", payload)
    return {"accepted": True}


async def _payload(request: Request) -> dict:
    if "application/json" in request.headers.get("content-type", ""):
        return await request.json()
    return dict(await request.form())
