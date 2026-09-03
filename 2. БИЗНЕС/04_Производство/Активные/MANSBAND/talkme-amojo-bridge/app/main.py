from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.amojo_client import post_connect, post_delivery_status, post_disconnect, post_new_message
from app.amojo_sign import verify_amojo_webhook_body
from app.config import get_settings
from app.database import get_db, init_db
from app.models import AmojoChannelState, AmoOAuthToken, ConversationMap, KvState, ProcessedEvent
from app.oauth_amocrm import exchange_code_for_tokens, fetch_account_amojo_id, token_expires_at
from app.talkme_client import list_messages, send_text_to_visitor
from app.talkme_parse import parse_talkme_incoming, parse_talkme_profile, talkme_incoming_has_attachments
from app.tokens import get_valid_access_token

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    s = get_settings()
    poll_task: Optional[asyncio.Task[None]] = None
    if s.talkme_poll_enabled:
        poll_task = asyncio.create_task(_talkme_poll_loop())
        log.info("Talk-me poll loop scheduled")
    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()


app = FastAPI(title="Talk-me ↔ amoCRM bridge", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _ext_conv(dialog_id: str) -> str:
    return f"tm-{dialog_id}"


def _dialog_from_ext(client_id: str) -> str:
    if client_id.startswith("tm-"):
        return client_id[3:]
    return client_id


def _require_internal(x_internal_secret: Optional[str]) -> None:
    s = get_settings()
    if not s.internal_secret or (x_internal_secret or "").strip() != s.internal_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    code: str,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """OAuth amoCRM: обмен code → токены в БД."""
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
        "<p>Токен сохранён. Можно вызывать POST /internal/connect с заголовком X-Internal-Secret.</p>"
    )


@app.get("/internal/oauth-link", response_class=HTMLResponse)
def oauth_link() -> HTMLResponse:
    """Подсказка: ссылка для админа (client_id, redirect, subdomain)."""
    s = get_settings()
    if not s.amo_client_id:
        return HTMLResponse("Задайте AMO_CLIENT_ID", status_code=500)
    from urllib.parse import quote

    base = f"https://{s.amo_subdomain}.{s.amo_base_domain}"
    u = f"{base}/oauth?client_id={s.amo_client_id}&state=&mode=post_message&redirect_uri={quote(s.redirect_uri, safe='')}"
    return HTMLResponse(f'<p><a href="{u}">Авторизовать интеграцию (открыть в браузере)</a></p><pre>{u}</pre>')


@app.post("/internal/connect")
def internal_connect(
    x_internal_secret: Optional[str] = Header(default=None, alias="X-Internal-Secret"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_internal(x_internal_secret)
    s = get_settings()
    if not s.amo_channel_id or not s.amo_channel_secret:
        raise HTTPException(503, detail="AMO_CHANNEL_ID / AMO_CHANNEL_SECRET: ждём ответ ТП")
    token = get_valid_access_token(db)
    amojo_id = fetch_account_amojo_id(access_token=token, settings=s)
    r = post_connect(channel_id=s.amo_channel_id, account_amojo_id=amojo_id, title=s.integration_title, settings=s)
    if r.status_code >= 400:
        log.error("connect failed: %s %s", r.status_code, r.text)
        raise HTTPException(r.status_code, detail=r.text)
    data = r.json()
    scope = str(data.get("scope_id", ""))
    st = db.execute(select(AmojoChannelState).where(AmojoChannelState.id == 1)).scalar_one_or_none()
    if st is None:
        st = AmojoChannelState(id=1)
        db.add(st)
    st.channel_id = s.amo_channel_id
    st.scope_id = scope
    st.amojo_account_id = str(amojo_id)
    db.commit()
    return {"ok": True, "scope_id": scope, "amojo_account_id": amojo_id}


@app.post("/internal/reconnect")
def internal_reconnect(
    x_internal_secret: Optional[str] = Header(default=None, alias="X-Internal-Secret"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Жёсткий сброс канала: disconnect → connect.

    Применять, если amoCRM выборочно блокирует исходящие из UI («Внутренняя ошибка сервера»),
    а наш мост при этом не получает хуков — это «санкции» на уровне канала amojo.
    """
    _require_internal(x_internal_secret)
    s = get_settings()
    if not s.amo_channel_id or not s.amo_channel_secret:
        raise HTTPException(503, detail="AMO_CHANNEL_ID / AMO_CHANNEL_SECRET не заданы")
    token = get_valid_access_token(db)
    amojo_id = fetch_account_amojo_id(access_token=token, settings=s)
    rd = post_disconnect(channel_id=s.amo_channel_id, account_amojo_id=amojo_id, settings=s)
    log.info("reconnect: disconnect %s %s", rd.status_code, rd.text[:200])
    rc = post_connect(channel_id=s.amo_channel_id, account_amojo_id=amojo_id, title=s.integration_title, settings=s)
    if rc.status_code >= 400:
        log.error("reconnect: connect failed: %s %s", rc.status_code, rc.text)
        raise HTTPException(rc.status_code, detail=rc.text)
    data = rc.json()
    scope = str(data.get("scope_id", ""))
    st = db.execute(select(AmojoChannelState).where(AmojoChannelState.id == 1)).scalar_one_or_none()
    if st is None:
        st = AmojoChannelState(id=1)
        db.add(st)
    st.channel_id = s.amo_channel_id
    st.scope_id = scope
    st.amojo_account_id = str(amojo_id)
    db.commit()
    return {
        "ok": True,
        "disconnect_status": rd.status_code,
        "connect_status": rc.status_code,
        "scope_id": scope,
        "is_time_window_disabled": data.get("is_time_window_disabled"),
    }


def _resolve_scope_id_string(db: Session) -> Optional[str]:
    s = get_settings()
    if s.amo_scope_id and s.amo_scope_id.strip():
        return s.amo_scope_id.strip()
    st = db.execute(select(AmojoChannelState).where(AmojoChannelState.id == 1)).scalar_one_or_none()
    if st and st.scope_id:
        return st.scope_id
    return None


def _get_scope_id(db: Session) -> str:
    x = _resolve_scope_id_string(db)
    if not x:
        raise HTTPException(503, detail="Нет SCOPE: выполните /internal/connect или AMO_SCOPE_ID в .env")
    return x


def _amojo_outgoing_task(scope_id: str, raw: dict[str, Any]) -> None:
    from app.database import get_session_factory

    settings = get_settings()
    Sess = get_session_factory()
    db = Sess()
    try:
        msg = raw.get("message") or {}
        conv = msg.get("conversation") or {}
        client_conv_id = conv.get("client_id")
        if not client_conv_id:
            log.warning("amojo hook: no message.conversation.client_id")
            return
        inner = msg.get("message") or {}
        amojo_message_id = inner.get("id")
        if not amojo_message_id:
            log.warning("amojo hook: no message id")
            return
        dedup = f"amojo-out:{amojo_message_id}"
        dedup_claim = db.execute(
            pg_insert(ProcessedEvent.__table__)
            .values(source="amojo", dedup_key=dedup)
            .on_conflict_do_nothing(index_elements=["source", "dedup_key"])
        )
        if dedup_claim.rowcount == 0:
            return
        db.commit()
        text = (inner.get("text") or "").strip()
        msg_type = (inner.get("type") or "text").strip().lower()
        media = (inner.get("media") or "").strip()
        file_name = (inner.get("file_name") or "").strip()
        if msg_type != "text":
            # Talk-me sendToClient принимает только текст: вложение шлём ссылкой.
            label = {
                "picture": "[фото]",
                "image": "[фото]",
                "video": "[видео]",
                "voice": "[голосовое]",
                "audio": "[аудио]",
                "file": "[файл]",
                "sticker": "[стикер]",
            }.get(msg_type, f"[{msg_type}]")
            parts: list[str] = [label]
            if file_name:
                parts.append(file_name)
            if media:
                parts.append(media)
            if text:
                parts.append(text)
            text = "\n".join(parts).strip()
        if not text:
            r = post_delivery_status(
                scope_id=scope_id,
                message_amojo_id=str(amojo_message_id),
                delivery_status=-1,
                error_code=905,
                error_message="пустое сообщение",
                settings=settings,
            )
            log.info("delivery_status empty: %s %s", r.status_code, r.text)
            return
        cm = (
            db.execute(
                select(ConversationMap).where(
                    ConversationMap.scope_id == scope_id,
                    ConversationMap.external_conversation_id == str(client_conv_id),
                )
            )
            .scalar_one_or_none()
        )
        # dialog_id берём из последнего активного (обновляется при каждом входящем),
        # т.к. внешний conv_id может быть стабильным "tmc-{client_id}" без диалога внутри.
        dialog_id: Optional[str] = None
        if cm and cm.talkme_dialog_id:
            dialog_id = cm.talkme_dialog_id
        elif str(client_conv_id).startswith("tm-"):
            dialog_id = _dialog_from_ext(str(client_conv_id))
        elif cm and isinstance(cm.talkme_context, dict):
            last = cm.talkme_context.get("last") or {}
            data_obj = last.get("data") if isinstance(last, dict) else {}
            dlg = data_obj.get("dialog") if isinstance(data_obj, dict) else {}
            did = dlg.get("id") if isinstance(dlg, dict) else None
            if did is not None:
                dialog_id = str(did)
        talkme_cid = cm.talkme_client_ref if cm else None
        if not talkme_cid and cm and isinstance(cm.talkme_context, dict):
            try:
                last = cm.talkme_context.get("last") or {}
                data_obj = last.get("data") if isinstance(last, dict) else {}
                client_obj = data_obj.get("client") if isinstance(data_obj, dict) else {}
                fallback = client_obj.get("clientId") if isinstance(client_obj, dict) else None
                if isinstance(fallback, str) and fallback:
                    talkme_cid = fallback
                    cm.talkme_client_ref = fallback
                    db.commit()
            except Exception:
                log.exception("amojo outgoing: failed to backfill talkme_client_ref from context")
        # Автодискавери через Talk-me REST: ищем clientId по dialog_id за последние 14 дней.
        if not talkme_cid:
            try:
                tz = settings.talkme_account_tz_offset_hours
                now_u = datetime.now(timezone.utc)
                start_l = (now_u - timedelta(days=14) + timedelta(hours=tz)).strftime("%Y-%m-%d %H:%M:%S")
                stop_l = (now_u + timedelta(hours=tz)).strftime("%Y-%m-%d %H:%M:%S")
                rr = list_messages(start_local=start_l, stop_local=stop_l, settings=settings)
                if rr.status_code == 200:
                    doc = rr.json()
                    if doc.get("success"):
                        for cl in (doc.get("result") or []):
                            if not isinstance(cl, dict):
                                continue
                            cid = cl.get("clientId")
                            if not cid:
                                continue
                            for m in (cl.get("messages") or []):
                                if isinstance(m, dict) and str(m.get("dialogId")) == str(dialog_id):
                                    talkme_cid = cid
                                    break
                            if talkme_cid:
                                break
                if talkme_cid and cm is not None:
                    cm.talkme_client_ref = talkme_cid
                    db.commit()
                    log.info("amojo outgoing: discovered talkme_client_ref via REST for dialog=%s", dialog_id)
            except Exception:
                log.exception("amojo outgoing: REST discovery failed")
        if not talkme_cid:
            log.warning("amojo outgoing: no talkme_client_ref for conv=%s", client_conv_id)
            post_delivery_status(
                scope_id=scope_id,
                message_amojo_id=str(amojo_message_id),
                delivery_status=-1,
                error_code=902,
                error_message="нет talkme_client_ref в БД — клиент не писал через мост",
                settings=settings,
            )
            return
        try:
            r = send_text_to_visitor(
                client_id=talkme_cid,
                text=text,
                dialog_id=dialog_id,
            )
            if r.status_code < 300:
                post_delivery_status(
                    scope_id=scope_id,
                    message_amojo_id=str(amojo_message_id),
                    delivery_status=2,
                    settings=settings,
                )
            else:
                post_delivery_status(
                    scope_id=scope_id,
                    message_amojo_id=str(amojo_message_id),
                    delivery_status=-1,
                    error_code=901,
                    error_message=r.text[:500],
                    settings=settings,
                )
        except Exception as e:
            log.exception("Talk-me send failed")
            post_delivery_status(
                scope_id=scope_id,
                message_amojo_id=str(amojo_message_id),
                delivery_status=-1,
                error_code=901,
                error_message=str(e)[:500],
                settings=settings,
            )
    finally:
        db.close()


@app.post("/amojo/v2/hooks/{scope_id}")
async def amojo_webhook(
    scope_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
    db: Session = Depends(get_db),
) -> Response:
    raw_body = await request.body()
    s = get_settings()
    if not s.amo_channel_secret:
        raise HTTPException(503, detail="AMO_CHANNEL_SECRET не задан")
    if not verify_amojo_webhook_body(
        channel_secret=s.amo_channel_secret, raw_body=raw_body, x_signature_header=x_signature or ""
    ):
        raise HTTPException(401, detail="Invalid signature")
    try:
        saved = _get_scope_id(db)
        if saved != scope_id:
            log.warning("scope_id mismatch path=%s db=%s", scope_id, saved)
    except HTTPException:
        pass  # connect ещё не делали; обрабатываем по path
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        data = {}
    background_tasks.add_task(_amojo_outgoing_task, scope_id, data)
    return Response(status_code=200)


def _talkme_incoming_task(payload: dict[str, Any]) -> None:
    from app.database import get_session_factory

    settings = get_settings()
    Sess = get_session_factory()
    db = Sess()
    try:
        sec, dialog_id, text, msg_id, vname, _vref = parse_talkme_incoming(payload)
        wsec = (settings.talkme_webhook_secret or "").strip()
        if wsec and (sec or "").strip() != wsec:
            log.warning("Talk-me secret mismatch")
            return
        scope_id = _resolve_scope_id_string(db)
        if not scope_id or not settings.amo_channel_secret:
            log.warning("Channel not configured (scope_id or secret)")
            return
        if not (text or "").strip() and not talkme_incoming_has_attachments(payload):
            log.info("Talk-me skip: empty text and no attachments dialog=%s msg_id=%s", dialog_id, msg_id)
            return
        dedup = f"tm:{msg_id}"
        dedup_claim = db.execute(
            pg_insert(ProcessedEvent.__table__)
            .values(source="talkme", dedup_key=dedup)
            .on_conflict_do_nothing(index_elements=["source", "dedup_key"])
        )
        if dedup_claim.rowcount == 0:
            return
        db.commit()

        profile = parse_talkme_profile(payload)
        p_client_id = profile.get("client_id")

        # Стабильный ключ беседы по клиенту, если он известен.
        # Это превращает "новый Talk-me dialog у того же клиента" в "продолжение существующей беседы amoCRM"
        # → НЕ создаются дубли сделок.
        ext_id = f"tmc-{p_client_id}" if p_client_id else _ext_conv(str(dialog_id))

        cm = (
            db.execute(
                select(ConversationMap).where(
                    ConversationMap.scope_id == scope_id,
                    ConversationMap.external_conversation_id == ext_id,
                )
            )
            .scalar_one_or_none()
        )
        # Backward-compat: если ext_id новый (tmc-...), но раньше беседа была заведена как tm-{dialog_id}
        # с тем же talkme_client_ref — переиспользуем её, не плодим новую.
        if cm is None and p_client_id:
            cm = (
                db.execute(
                    select(ConversationMap)
                    .where(
                        ConversationMap.scope_id == scope_id,
                        ConversationMap.talkme_client_ref == p_client_id,
                    )
                    .order_by(ConversationMap.id.desc())
                )
                .scalars()
                .first()
            )
            if cm is not None:
                ext_id = cm.external_conversation_id
        if cm is None:
            cm = ConversationMap(
                scope_id=scope_id,
                external_conversation_id=ext_id,
                talkme_context={},
                talkme_client_ref=p_client_id,
                talkme_dialog_id=str(dialog_id) if dialog_id is not None else None,
            )
            db.add(cm)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                cm = (
                    db.execute(
                        select(ConversationMap).where(
                            ConversationMap.scope_id == scope_id,
                            ConversationMap.external_conversation_id == ext_id,
                        )
                    )
                    .scalar_one()
                )
        cm.talkme_context = {"last": payload}
        if p_client_id and not cm.talkme_client_ref:
            cm.talkme_client_ref = p_client_id
        if dialog_id is not None:
            cm.talkme_dialog_id = str(dialog_id)

        msec = int(datetime.now(timezone.utc).timestamp() * 1000)
        ts = int(msec // 1000)
        guest_name = (profile.get("name") or vname or "Гость")[:200]
        sender_id = f"tm-user-{ext_id}"[:200]
        sender: dict[str, Any] = {"id": sender_id, "name": guest_name}
        sub_profile: dict[str, str] = {}
        p_phone = profile.get("phone")
        if p_phone:
            sub_profile["phone"] = p_phone
        p_email = profile.get("email")
        if p_email:
            sub_profile["email"] = p_email
        if sub_profile:
            sender["profile"] = sub_profile
        p_link = profile.get("profile_link")
        if p_link:
            sender["profile_link"] = p_link
        out_payload: dict[str, Any] = {
            "timestamp": ts,
            "msec_timestamp": msec,
            "msgid": f"tm-{msg_id}"[:200],
            "conversation_id": ext_id,
            "sender": sender,
            "message": {"type": "text", "text": text or ""},
            "silent": False,
        }
        r = post_new_message(scope_id=scope_id, payload=out_payload, settings=settings)
        if r.status_code >= 400:
            db.rollback()
            log.error("new_message to amo failed: %s %s", r.status_code, r.text)
            return
        db.commit()
    except Exception as e:
        db.rollback()
        log.exception("Talk-me task failed: %s", e)
    finally:
        db.close()


@app.post("/webhooks/talkme")
async def webhooks_talkme(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    body = await request.body()
    s = get_settings()
    if s.talkme_raw_log:
        head = body[:8000].decode("utf-8", errors="replace")
        log.info("TALKME_RAW_PAYLOAD len=%d head=%s", len(body), head)
    try:
        data = json.loads(body.decode("utf-8") or "{}") if body else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    background_tasks.add_task(_talkme_incoming_task, data)
    return Response(status_code=200)


def _kv_get(db: Session, key: str) -> Optional[str]:
    row = db.execute(select(KvState).where(KvState.key == key)).scalar_one_or_none()
    return row.value if row is not None else None


def _kv_set(db: Session, key: str, value: str) -> None:
    row = db.execute(select(KvState).where(KvState.key == key)).scalar_one_or_none()
    if row is None:
        row = KvState(key=key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)


def _poll_payload(client: dict[str, Any], msg: dict[str, Any], secret: str) -> dict[str, Any]:
    """Собрать псевдо-webhook payload из элемента ответа /chat/message/getList."""
    content = msg.get("content") if isinstance(msg.get("content"), dict) else {}
    text = msg.get("text") or (content.get("text") if isinstance(content, dict) else "") or ""
    return {
        "eventId": "newMessageFromClient",
        "data": {
            "client": client,
            "dialog": {"id": msg.get("dialogId")},
            "message": {
                "id": msg.get("id"),
                "text": text,
                "content": content or {"text": text},
            },
            "messages": [msg],
        },
        "secretKey": secret or "",
    }


async def _talkme_poll_loop() -> None:
    s = get_settings()
    if not s.talkme_poll_enabled:
        return
    interval = max(5, int(s.talkme_poll_interval_sec))
    lookback = max(30, int(s.talkme_poll_lookback_sec))
    init_lb = max(60, int(s.talkme_poll_initial_lookback_sec))
    tz_off = int(s.talkme_account_tz_offset_hours)
    log.info(
        "Talk-me poll: every=%ds lookback=%ds init=%ds tz=+%dh",
        interval, lookback, init_lb, tz_off,
    )
    await asyncio.sleep(5)

    while True:
        try:
            from app.database import get_session_factory

            Sess = get_session_factory()
            db = Sess()
            try:
                raw = _kv_get(db, "talkme_poll_last_seen_utc")
            finally:
                db.close()

            if raw:
                try:
                    last_utc = datetime.fromisoformat(raw)
                    if last_utc.tzinfo is None:
                        last_utc = last_utc.replace(tzinfo=timezone.utc)
                    else:
                        last_utc = last_utc.astimezone(timezone.utc)
                except Exception:
                    last_utc = datetime.now(timezone.utc) - timedelta(seconds=init_lb)
            else:
                last_utc = datetime.now(timezone.utc) - timedelta(seconds=init_lb)

            now_utc = datetime.now(timezone.utc)
            start_utc = last_utc - timedelta(seconds=lookback)
            start_local = (start_utc + timedelta(hours=tz_off)).strftime("%Y-%m-%d %H:%M:%S")
            stop_local = (now_utc + timedelta(hours=tz_off)).strftime("%Y-%m-%d %H:%M:%S")

            r = await asyncio.to_thread(
                list_messages, start_local=start_local, stop_local=stop_local, settings=s
            )
            if r.status_code >= 400:
                log.warning("Talk-me poll http=%s body=%s", r.status_code, r.text[:300])
            else:
                try:
                    doc = r.json()
                except Exception:
                    doc = {}
                if not doc.get("success"):
                    log.warning("Talk-me poll fail %s", json.dumps(doc)[:300])
                else:
                    new_max = last_utc
                    fetched = 0
                    new_count = 0
                    for cl in (doc.get("result") or []):
                        if not isinstance(cl, dict):
                            continue
                        for m in (cl.get("messages") or []):
                            fetched += 1
                            if not isinstance(m, dict):
                                continue
                            if m.get("whoSend") != "client":
                                continue
                            ts_str = m.get("dateTimeUTC") or ""
                            try:
                                m_utc = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            except Exception:
                                m_utc = now_utc
                            if m_utc <= last_utc:
                                continue
                            new_count += 1
                            if m_utc > new_max:
                                new_max = m_utc
                            payload = _poll_payload(cl, m, s.talkme_webhook_secret or "")
                            try:
                                await asyncio.to_thread(_talkme_incoming_task, payload)
                            except Exception:
                                log.exception("Talk-me poll: incoming task failed")

                    if new_count > 0:
                        Sess2 = get_session_factory()
                        db2 = Sess2()
                        try:
                            _kv_set(db2, "talkme_poll_last_seen_utc", new_max.isoformat())
                            db2.commit()
                        finally:
                            db2.close()
                    log.info(
                        "Talk-me poll: range=%s..%s fetched=%d new=%d",
                        start_local, stop_local, fetched, new_count,
                    )
        except asyncio.CancelledError:
            log.info("Talk-me poll loop cancelled")
            raise
        except Exception:
            log.exception("Talk-me poll loop error")
        await asyncio.sleep(interval)
