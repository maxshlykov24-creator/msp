"""FastAPI-роуты. Вебхуки только валидируют и кладут событие в inbox (ответ 202
за <2с), вся работа с Kommo — в worker."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.config import settings
from app.db import session_scope
from app.identity import normalize_phone
from app.kommo.client import KommoClient
from app.queue import enqueue
from app.resolver import resolve_responsible

log = logging.getLogger("webhooks")
router = APIRouter()


# ── health ──
@router.get("/")
@router.get("/health")
@router.get("/tilda/webhook")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


def _pick(fields: dict[str, str], *keys: str) -> str:
    lower = {k.lower(): v for k, v in fields.items()}
    for k in keys:
        for src in (fields, lower):
            key = k if src is fields else k.lower()
            v = src.get(key)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


async def _form_or_json(request: Request) -> dict[str, str]:
    ct = (request.headers.get("content-type") or "").lower()
    raw = await request.body()
    if "application/json" in ct:
        import json
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(obj, dict):
                return {str(k): "" if v is None else str(v) for k, v in obj.items()}
        except Exception:
            pass
    import urllib.parse
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


# ── Tilda ──
@router.post("/tilda/webhook")
async def tilda_webhook(request: Request) -> Response:
    fields = await _form_or_json(request)
    if fields.get("test") == "test":
        return PlainTextResponse("ok")
    name = _pick(fields, "Name", "name", "Имя", "Full name", "full_name")
    phone = normalize_phone(_pick(fields, "Phone", "phone", "Tel", "tel"))
    email = _pick(fields, "Email", "email", "E-mail")
    license_class = _pick(fields, "class", "Class", "Какая_лицензия_вас_интересует", "license")
    payload = {
        "name": name, "phone": phone, "email": email,
        "channel": "Tilda",
        "utm_source": _pick(fields, "utm_source", "utm-source"),
        "utm_campaign": _pick(fields, "utm_campaign", "utm-campaign"),
        "notes": f"Class: {license_class}" if license_class else "",
    }
    key = f"tilda:{_pick(fields, 'tranid') or phone or email}:{int(time.time())}"
    with session_scope() as s:
        enqueue(s, "tilda", "intake", key, payload, phone=phone or None)
    return PlainTextResponse("ok")


# ── Pleep ──
@router.post("/pleep/sync")
async def pleep_sync(request: Request, x_api_key: str = Header(default="")) -> Response:
    if not settings.pleep_api_key:
        return JSONResponse({"ok": False, "error": "PLEEP_API_KEY not configured"}, 500)
    if x_api_key != settings.pleep_api_key:
        return JSONResponse({"ok": False, "error": "invalid api key"}, 401)
    fields = await _form_or_json(request)
    phone = normalize_phone(fields.get("phone"))
    if not phone:
        return JSONResponse({"ok": False, "error": "phone is required"}, 400)
    payload = {
        "name": fields.get("name", ""), "phone": phone, "email": fields.get("email", ""),
        "channel": "Pleep AI", "notes": fields.get("notes", ""),
        "tags": fields.get("tags", ""),
    }
    key = f"pleep:{phone}:{int(time.time())}"
    with session_scope() as s:
        eid, _ = enqueue(s, "pleep", "intake", key, payload, phone=phone)
    return JSONResponse({"ok": True, "accepted": True, "event_id": eid}, 202)


# ── Kommo native webhook ──
_KOMMO_KEY_RE = re.compile(r"^(leads|contacts)\[(add|update|status|delete)\]\[(\d+)\]\[(\w+)\]$")


def _parse_kommo(fields: dict[str, str]) -> tuple[str | None, list[dict[str, Any]]]:
    """Разбирает form-encoded вебхук Kommo в список событий. Возвращает
    (account_id, events)."""
    account_id = fields.get("account[id]")
    groups: dict[tuple[str, str, str], dict[str, str]] = {}
    for k, v in fields.items():
        m = _KOMMO_KEY_RE.match(k)
        if not m:
            continue
        entity, action, idx, attr = m.groups()
        groups.setdefault((entity, action, idx), {})[attr] = v
    events: list[dict[str, Any]] = []
    for (entity, action, _idx), attrs in groups.items():
        entity_id = attrs.get("id")
        if not entity_id:
            continue
        if entity == "leads" and action == "add":
            events.append({"type": "add_lead", "entity_id": int(entity_id)})
        elif entity == "leads" and action == "status":
            events.append({"type": "status_lead", "entity_id": int(entity_id),
                           "status_id": int(attrs["status_id"]) if attrs.get("status_id") else None})
        elif entity == "contacts" and action in ("add", "update"):
            events.append({"type": "update_contact", "entity_id": int(entity_id)})
    return account_id, events


@router.post("/kommo/webhook/{secret}")
async def kommo_webhook(secret: str, request: Request) -> Response:
    if not settings.kommo_webhook_secret or secret != settings.kommo_webhook_secret:
        return JSONResponse({"ok": False}, 403)
    fields = await _form_or_json(request)
    account_id, events = _parse_kommo(fields)
    if account_id and int(account_id) != settings.kommo_account_id:
        log.warning("kommo webhook wrong account_id=%s", account_id)
        return JSONResponse({"ok": False, "error": "account mismatch"}, 403)
    accepted = 0
    with session_scope() as s:
        for ev in events:
            etype = ev["type"]
            eid = ev["entity_id"]
            marker = ev.get("status_id", "")
            key = f"kommo:{etype}:{eid}:{marker}"
            _, created = enqueue(s, "kommo", etype, key, ev)
            accepted += int(created)
    return JSONResponse({"ok": True, "accepted": accepted, "events": len(events)}, 202)


# ── статус и управление раскаткой ──
def _authorized(key: str) -> bool:
    return bool(settings.internal_api_key) and key == settings.internal_api_key


def _overview(s) -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.models import Decision, DupManual, InboxEvent, MergeSlot
    from app.rollout import describe

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    inbox = dict(s.execute(
        select(InboxEvent.status, func.count()).group_by(InboxEvent.status)
    ).all())
    decisions = dict(s.execute(
        select(Decision.action, func.count()).where(Decision.created_at >= since)
        .group_by(Decision.action).order_by(func.count().desc())
    ).all())
    manual = dict(s.execute(
        select(DupManual.reason, func.count()).where(DupManual.status == "open")
        .group_by(DupManual.reason)
    ).all())
    slots = {f"{k}:{st}": n for k, st, n in s.execute(
        select(MergeSlot.kind, MergeSlot.status, func.count())
        .group_by(MergeSlot.kind, MergeSlot.status)
    ).all()}
    return {
        "rollout": describe(s),
        "inbox": inbox,
        "decisions_24h": decisions,
        "manual_queue_open": manual,
        "merge_slots": slots,
    }


def _status_html(data: dict[str, Any]) -> str:
    r = data["rollout"]
    def rows(d: dict) -> str:
        return "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in d.items()) or \
            "<tr><td colspan=2>—</td></tr>"
    flags_on = [k for k, v in r["flags"].items() if v]
    return f"""<!doctype html><meta charset=utf-8><title>LicenseBridge Hub</title>
<style>body{{font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:24px;max-width:760px}}
h1{{font-size:20px}}h2{{font-size:16px;margin-top:24px}}
table{{border-collapse:collapse;width:100%}}td{{border-bottom:1px solid #eee;padding:4px 8px}}
.b{{padding:2px 8px;border-radius:10px;background:#eef}}</style>
<h1>LicenseBridge Hub</h1>
<p>Этап: <span class=b>{r['stage']} — {r['stage_name']}</span>
{' (на паузе)' if r['paused'] else ''}<br>
Активно: {', '.join(flags_on) or 'ничего (наблюдение)'}<br>
Этап с: {r['stage_entered_at'] or '—'}, выдержка {r['next_stage_after_hours'] or '—'} ч<br>
Здоровье: {'ок' if r['health']['ok'] else r['health']['reason']}
(ошибки {r['health']['deadletter']}, очередь {r['health']['pending']},
решений за окно {r['health']['decisions_window']})<br>
{r['note'] or ''}</p>
<h2>Очередь событий</h2><table>{rows(data['inbox'])}</table>
<h2>Решения за 24 часа</h2><table>{rows(data['decisions_24h'])}</table>
<h2>Ручная склейка (открыто)</h2><table>{rows(data['manual_queue_open'])}</table>
<h2>Пул тегов</h2><table>{rows(data['merge_slots'])}</table>"""


@router.get("/status")
def status(request: Request, key: str = "", x_internal_key: str = Header(default="")) -> Response:
    if not _authorized(key or x_internal_key):
        return JSONResponse({"ok": False}, 401)
    with session_scope() as s:
        data = _overview(s)
    if "text/html" in (request.headers.get("accept") or ""):
        return HTMLResponse(_status_html(data))
    return JSONResponse({"ok": True, **data})


@router.post("/internal/rollout")
async def rollout_control(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Управление этапом без деплоя: {"action": "pause"|"resume"|"set_stage",
    "stage": 0..4}. Этапы: 0 shadow, 1 распределение, 2 сделки, 3 контакты,
    4 контакты с лёгким чатом."""
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or x_internal_key):
        return JSONResponse({"ok": False}, 401)
    from app.rollout import describe as rollout_describe, set_paused, set_stage

    action = (body.get("action") or "").strip()
    with session_scope() as s:
        if action == "pause":
            set_paused(s, True, body.get("note", ""))
        elif action == "resume":
            set_paused(s, False, body.get("note", ""))
        elif action == "set_stage":
            set_stage(s, int(body.get("stage", 0)), body.get("note", "ручная установка"))
        else:
            return JSONResponse({"ok": False, "error": "action: pause|resume|set_stage"}, 400)
        return JSONResponse({"ok": True, "rollout": rollout_describe(s)})


# ── внутренний resolver для телефонии (read-only) ──
@router.post("/internal/responsible/resolve")
async def internal_resolve(request: Request, x_internal_key: str = Header(default="")) -> Response:
    if not settings.internal_api_key or x_internal_key != settings.internal_api_key:
        return JSONResponse({"ok": False}, 401)
    body = await _form_or_json(request)
    phone = body.get("phone", "")
    client = KommoClient()
    try:
        result = resolve_responsible(client, phone)
    finally:
        client.close()
    return JSONResponse({"ok": True, **result})


# ── телефония: диалплан Asterisk ──
@router.post("/internal/call/route")
async def call_route(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Кому звонить по этому номеру. Ответ — простой текст «101-103».

    Разделитель дефис, а не запятая: в диалплане CUT() с запятой требует
    экранирования и на Asterisk 20 отваливается. При любой внутренней проблеме
    отдаём круг продаж, чтобы звонок не потерялся."""
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or x_internal_key):
        return PlainTextResponse("", status_code=401)
    phone = normalize_phone(body.get("phone", ""))
    did = body.get("did", "")
    from app.telephony import ring_order

    client = KommoClient()
    try:
        order, resolved = ring_order(client, phone, did)
    except Exception as exc:  # noqa: BLE001 — телефония важнее точности маршрута
        log.warning("call route failed: %s", exc)
        order, resolved = settings.sales_order, {"found": False, "reason": "error"}
    finally:
        client.close()
    log.info("call route phone=%s did=%s -> %s (%s)", phone, did, order,
             resolved.get("status") or resolved.get("reason"))
    return PlainTextResponse("-".join(order))


@router.post("/internal/call/guard")
async def call_guard(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Разрешён ли исходящий набор на этот номер. Ответ — `ok`, `soft:<причина>`
    или `deny:<причина>`.

    Диалплан спрашивает перед `Dial(...@telnyx)`. При любой внутренней ошибке
    отвечаем `ok`: сторож защищает репутацию номера, но не имеет права мешать
    менеджеру звонить, если сам сломался."""
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or x_internal_key):
        return PlainTextResponse("", status_code=401)
    phone = normalize_phone(body.get("phone", ""))
    from app.telephony import dial_guard, lead_status_for_phone

    status_id = None
    client = KommoClient()
    try:
        status_id = lead_status_for_phone(client, phone)
    except Exception as exc:  # noqa: BLE001 — этап не важнее дозвона
        log.warning("dial guard status lookup failed: %s", exc)
    finally:
        client.close()

    try:
        with session_scope() as s:
            verdict, reason = dial_guard(s, phone, lead_status_id=status_id)
    except Exception as exc:  # noqa: BLE001 — звонок важнее учёта
        log.warning("dial guard failed: %s", exc)
        return PlainTextResponse("ok")
    if verdict != "ok":
        log.warning("dial guard %s phone=%s reason=%s", verdict, phone, reason)
    return PlainTextResponse(verdict if verdict == "ok" else f"{verdict}:{reason}")


@router.post("/internal/alert")
async def internal_alert(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Ретрансляция алерта в Telegram для наблюдателя на Asterisk.

    Токен бота живёт в одном месте — в `.env` хаба, а не копией на каждом
    сервере. `key` в теле (не путать с ключом доступа) гасит повторы одной и той
    же поломки, см. app/alerts.py."""
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or x_internal_key):
        return PlainTextResponse("", status_code=401)
    text = (body.get("text") or "").strip()
    if not text:
        return PlainTextResponse("empty", status_code=400)
    from app.alerts import notify

    sent = notify(text[:3500], key=body.get("dedup", ""))
    return PlainTextResponse("sent" if sent else "skipped")


async def _enqueue_call(request: Request, header_key: str, event_type: str) -> Response:
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or header_key):
        return PlainTextResponse("", status_code=401)
    payload = {k: v for k, v in body.items() if k != "key"}
    phone = normalize_phone(payload.get("phone", ""))
    payload["phone"] = phone
    uniqueid = (payload.get("uniqueid") or "").strip() or f"{event_type}:{int(time.time() * 1000)}"
    key = f"asterisk:{event_type}:{uniqueid}"
    with session_scope() as s:
        eid, created = enqueue(s, "asterisk", event_type, key, payload, phone=phone or None)
    return PlainTextResponse(f"ok {eid} {int(created)}")


@router.post("/internal/call/missed")
async def call_missed(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Никто не ответил: задача ответственному + примечание о пропуске."""
    return await _enqueue_call(request, x_internal_key, "call_missed")


@router.post("/internal/call/finished")
async def call_finished(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Звонок завершён: примечание с длительностью и записью разговора."""
    return await _enqueue_call(request, x_internal_key, "call_finished")


# ── лид-машина: WhatsApp молчит → AI-звонок Pleep → SMS ──
@router.post("/internal/leadflow/ai-call")
async def leadflow_ai_call(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Salesbot не дождался ответа в WhatsApp. Тело: `lead_id` и/или `phone`.

    Ключ события — сама сделка: повторный вебхук по той же заявке второй звонок
    не поставит (клиенту звонят один раз, дальше это уже обзвон)."""
    body = await _form_or_json(request)
    if not _authorized(body.get("key", "") or x_internal_key):
        return JSONResponse({"ok": False}, 401)
    lead_id = str(body.get("lead_id") or body.get("entity_id") or "").strip()
    phone = normalize_phone(body.get("phone", ""))
    if not lead_id and not phone:
        return JSONResponse({"ok": False, "error": "lead_id or phone required"}, 400)
    payload = {"lead_id": lead_id, "phone": phone}
    with session_scope() as s:
        eid, created = enqueue(s, "leadflow", "ai_call",
                               f"leadflow:ai_call:{lead_id or phone}", payload,
                               phone=phone or None)
    return JSONResponse({"ok": True, "event_id": eid, "accepted": created}, 202)


@router.post("/internal/pleep/outcome")
async def pleep_outcome(request: Request, x_internal_key: str = Header(default=""),
                        x_api_key: str = Header(default="")) -> Response:
    """Итог разговора голосового агента Pleep (HTTP tool в его сценарии).

    Тело: `lead_id`/`phone`, `outcome` (qualified | callback | refused |
    no_answer | do_not_call), `summary`. Ключ Pleep подходит наравне с внутренним:
    в панели агента удобнее держать один секрет канала."""
    body = await _form_or_json(request)
    key = body.get("key", "") or x_internal_key
    pleep_ok = bool(settings.pleep_api_key) and x_api_key == settings.pleep_api_key
    if not (_authorized(key) or pleep_ok):
        return JSONResponse({"ok": False}, 401)
    outcome = str(body.get("outcome") or body.get("result") or "").strip().lower()
    lead_id = str(body.get("lead_id") or body.get("entity_id") or "").strip()
    phone = normalize_phone(body.get("phone") or body.get("caller") or "")
    if not lead_id and not phone:
        return JSONResponse({"ok": False, "error": "lead_id or phone required"}, 400)
    payload = {"lead_id": lead_id, "phone": phone, "outcome": outcome,
               "summary": str(body.get("summary") or body.get("transcript") or "")}
    with session_scope() as s:
        eid, created = enqueue(s, "leadflow", "pleep_outcome",
                               f"leadflow:outcome:{lead_id or phone}:{outcome}", payload,
                               phone=phone or None)
    return JSONResponse({"ok": True, "event_id": eid, "accepted": created}, 202)


@router.get("/rec/{sig}/{name}")
def recording(sig: str, name: str) -> Response:
    """Запись разговора по подписанной ссылке — её открывает плеер в карточке
    Kommo. Файл лежит на Asterisk, наружу отдаём только через хаб."""
    import httpx

    from app.telephony import rec_verify

    if not rec_verify(name, sig):
        return PlainTextResponse("not found", status_code=404)
    url = f"{settings.recordings_url.rstrip('/')}/{name}"
    try:
        resp = httpx.get(url, timeout=20.0)
    except httpx.HTTPError as exc:
        log.warning("recording fetch failed %s: %s", name, exc)
        return PlainTextResponse("upstream error", status_code=502)
    if resp.status_code != 200:
        return PlainTextResponse("not found", status_code=404)
    media = "audio/mpeg" if name.lower().endswith(".mp3") else "audio/wav"
    return Response(content=resp.content, media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{name}"',
                             "Cache-Control": "private, max-age=3600"})


# ── ручной прогон handoff Pipeline→Сборка (smoke-тест / backfill) ──
@router.post("/internal/handoff")
async def internal_handoff(request: Request, x_internal_key: str = Header(default="")) -> Response:
    """Прогон по одной сделке вручную: те же guard'ы, что и на вебхуке
    status_lead (см. app/handoff.py) — pipeline_id/won/тег дубля/идемпотентность/
    рейт-лимит. Используется для smoke-теста на боевом (видна ли переписка в
    ленте новой карточки) и для backfill уже выигранных сделок."""
    if not settings.internal_api_key or x_internal_key != settings.internal_api_key:
        return JSONResponse({"ok": False}, 401)
    body = await _form_or_json(request)
    lead_id = body.get("lead_id")
    if not lead_id:
        return JSONResponse({"ok": False, "error": "lead_id required"}, 400)

    from app.actions import Ctx
    from app.handoff import run_handoff
    from app.rollout import current_flags

    client = KommoClient()
    try:
        with session_scope() as s:
            flags = current_flags(s)
            ctx = Ctx(client=client, session=s, inbox_id=None, phone=None,
                      shadow=flags.shadow, flags=flags)
            result = run_handoff(ctx, int(lead_id))
        return JSONResponse({"ok": True, **result})
    finally:
        client.close()
