from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, init_db, get_session_factory
from app.models import AmoOAuthToken
from app.oauth_amocrm import exchange_code_for_tokens, token_expires_at

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _liveinform_header_secret_ok(request: Request) -> bool:
    """Старый путь: секрет в заголовке. Используется только если совпадает (для обратной совместимости)."""
    s = get_settings()
    secret = (s.liveinform_webhook_secret or "").strip()
    if not secret:
        return True
    name = (s.liveinform_webhook_header or "X-LiveInform-Secret").strip()
    got = request.headers.get(name)
    return bool(got) and hmac.compare_digest((got or "").strip(), secret)


def _liveinform_path_secret_ok(token: str) -> bool:
    """Новый путь: секрет в URL (LiveInform не умеет добавлять заголовок)."""
    s = get_settings()
    secret = (s.liveinform_webhook_secret or "").strip()
    if not secret:
        return False
    return hmac.compare_digest((token or "").strip(), secret)


def _parse_form_bytes(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    qs = parse_qs(text, keep_blank_values=True)
    flat: dict[str, Any] = {k: (v[0] if isinstance(v, list) and v else "") for k, v in qs.items()}
    if "data" in flat:
        try:
            obj = json.loads(flat["data"])
            if isinstance(obj, dict):
                return obj
        except Exception:
            log.warning(
                "liveinform: form 'data' is not valid JSON, value[:200]=%r",
                str(flat.get("data", ""))[:200],
            )
    return flat


async def _read_liveinform_payload(request: Request) -> dict[str, Any]:
    """LiveInform шлёт application/x-www-form-urlencoded или multipart/form-data
    с полем data=<json>. Поддерживаем также чистый JSON в теле.
    """
    ctype = (request.headers.get("content-type") or "").lower()

    # multipart/form-data — используем встроенный парсер FastAPI
    if "multipart/form-data" in ctype:
        try:
            form = await request.form()
            data_val = str(form.get("data", "") or "")
            if data_val:
                try:
                    obj = json.loads(data_val)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    log.warning(
                        "liveinform: multipart 'data' not valid JSON: %r", data_val[:200]
                    )
            # нет поля data — отдаём все поля плоским словарём
            return {k: str(v) for k, v in form.items()}
        except Exception as exc:
            log.warning("liveinform: multipart parse error: %s", exc)
            return {}

    raw = await request.body()
    if not raw:
        return {}

    if "application/json" in ctype:
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            log.warning("liveinform: invalid JSON body[:200]=%r", raw[:200])
            return {}

    if "x-www-form-urlencoded" in ctype:
        return _parse_form_bytes(raw)

    try:
        obj = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if b"=" in raw and b"{" not in raw[:1]:
        return _parse_form_bytes(raw)
    log.warning(
        "liveinform: cannot parse body. ctype=%r body[:200]=%r", ctype, raw[:200]
    )
    return {}


def _liveinform_background(payload: dict[str, Any]) -> None:
    SessLocal = get_session_factory()
    db = SessLocal()
    try:
        from app.liveinform_worker import sync_liveinform_event

        sync_liveinform_event(db, payload)
    except Exception:
        log.exception("liveinform background task failed")
    finally:
        db.close()


_AMO_LEAD_ID_RE = re.compile(r"^leads\[\w+\]\[\d+\]\[id\]$")


def _amocrm_webhook_secret_ok(token: str) -> bool:
    s = get_settings()
    secret = (s.amo_webhook_secret or "").strip()
    if not secret:
        return False
    return hmac.compare_digest((token or "").strip(), secret)


def _extract_amocrm_lead_ids(raw: bytes) -> set[int]:
    """amoCRM шлёт x-www-form-urlencoded с вложенными ключами вида
    leads[add][0][id] / leads[status][0][id]. Не разбираем остальные поля —
    по каждому id досылаем GET /leads/{id}, чтобы не зависеть от формата.
    """
    text = raw.decode("utf-8", errors="replace")
    qs = parse_qs(text, keep_blank_values=True)
    ids: set[int] = set()
    for key, values in qs.items():
        if not _AMO_LEAD_ID_RE.match(key):
            continue
        for v in values:
            try:
                ids.add(int(v))
            except (TypeError, ValueError):
                continue
    return ids


def _amocrm_webhook_background(lead_id: int) -> None:
    SessLocal = get_session_factory()
    db = SessLocal()
    try:
        from app.repeat_responsible import maybe_reassign_responsible

        maybe_reassign_responsible(db, lead_id)
    except Exception:
        log.exception("amocrm webhook background task failed lead=%s", lead_id)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    s = get_settings()
    tasks: list[asyncio.Task[None]] = []

    if (s.telegram_bot_token or "").strip():
        from app.bot import run_bot_forever
        tasks.append(asyncio.create_task(run_bot_forever()))

    if (s.max_bot_token or "").strip():
        from app.max_bot import run_max_polling_forever
        tasks.append(asyncio.create_task(run_max_polling_forever()))

    if s.repeat_touch_enabled:
        from app.repeat_worker import run_repeat_touch_forever
        tasks.append(asyncio.create_task(run_repeat_touch_forever()))

    yield

    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="DKAcademy delivery bot + LiveInform", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(code: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        data = exchange_code_for_tokens(code=code)
    except Exception as e:
        log.exception("OAuth exchange failed")
        return HTMLResponse(f"<p>Ошибка OAuth: {e}</p>", status_code=500)
    row = db.execute(select(AmoOAuthToken).where(AmoOAuthToken.id == 1)).scalar_one_or_none()
    if row is None:
        row = AmoOAuthToken(id=1, access_token="", refresh_token="")
        db.add(row)
    row.access_token = str(data.get("access_token", ""))
    row.refresh_token = str(data.get("refresh_token", "") or row.refresh_token)
    row.expires_at = token_expires_at(data)
    row.extra = {"token_type": data.get("token_type"), "raw": data}
    db.commit()
    return HTMLResponse(
        "<p>Токены amo сохранены. Закройте вкладку. Сервис готов дергать API.</p>"
    )


@app.get("/internal/oauth-link", response_class=HTMLResponse)
def oauth_link() -> HTMLResponse:
    s = get_settings()
    if not s.amo_client_id:
        return HTMLResponse("<p>Задайте AMO_CLIENT_ID</p>", status_code=500)
    from urllib.parse import quote

    base = f"https://{s.amo_subdomain}.{s.amo_base_domain}"
    u = (
        f"{base}/oauth?client_id={s.amo_client_id}&state=&mode=post_message"
        f"&redirect_uri={quote(s.redirect_uri, safe='')}"
    )
    return HTMLResponse(f'<p><a href="{u}">Авторизовать интеграцию amo</a></p><pre>{u}</pre>')


@app.post("/webhooks/liveinform")
async def webhook_liveinform(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Старый путь: секрет в заголовке (для обратной совместимости / своих тестов)."""
    if not _liveinform_header_secret_ok(request):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    payload = await _read_liveinform_payload(request)
    background_tasks.add_task(_liveinform_background, payload)
    return Response(status_code=200)


@app.post("/webhooks/liveinform/{token}")
async def webhook_liveinform_with_token(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Боевой путь для LiveInform: секрет идёт в URL.

    LiveInform Callback не позволяет добавить свой HTTP-заголовок к POST,
    поэтому секрет передаём как часть пути.
    """
    if not _liveinform_path_secret_ok(token):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    payload = await _read_liveinform_payload(request)
    background_tasks.add_task(_liveinform_background, payload)
    return Response(status_code=200)


@app.post("/webhooks/amocrm/{token}")
async def webhook_amocrm(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Веб-хук amoCRM (события «Добавление» / «Смена статуса» сделки).

    Используется для автоназначения ответственного в «Повторные продажи»
    (см. app/repeat_responsible.py). Секрет — в пути, как у LiveInform.
    """
    if not _amocrm_webhook_secret_ok(token):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    raw = await request.body()
    lead_ids = _extract_amocrm_lead_ids(raw)
    for lead_id in lead_ids:
        background_tasks.add_task(_amocrm_webhook_background, lead_id)
    return Response(status_code=200)
