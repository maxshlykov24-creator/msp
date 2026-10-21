"""Paid reservation reminder: durable, at most one attempt per transport.

Runs separately from the chat bridge. A lost HTTP response is uncertain, never
permission to try another messenger. Wazzup callbacks are forwarded unchanged
to the previous subscriber before returning its response.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import get_settings
from app.database import get_engine, get_session_factory
from app.models import Base
from app.oauth_amocrm import api_base
from app.tokens import get_valid_access_token

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
ORDER = ("telegram", "whatsapp", "max")
SUCCESS = {"sent", "delivered", "read"}
# These are explicit rejections, not transport timeouts or unknown server errors.
REJECTED = {
    "BAD_CONTACT", "CHAT_NO_ACCESS", "CHANNEL_BLOCKED", "CHANNEL_WAPI_REJECTED",
    "CHANNEL_NO_MONEY", "MESSAGE_CHANNEL_UNAVAILABLE", "MESSAGES_NOT_TEXT_FIRST",
    "MESSAGES_IS_SPAM", "CHANNEL_LIMIT_EXCEEDED", "TEMPLATE_REJECTED",
}
TEMPLATE = (
    "Вас приветствует Mansband👋🏻 \n\n"
    "Напоминаем, что (Отложка до) истекает срок хранения ваших отложенных позиций"
    "\n\nВ какой день планируете нас посетить?☺️"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReminderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REMINDER_", extra="ignore")
    mode: Literal["preview", "live"] = "preview"
    hour: int = 10
    end_hour: int = 20
    days_before: int = 3
    hold_field: int = 3810737
    paid_enum: int = 5452541
    until_field: int = 3810739
    telegram_id_field: int = 3346485
    telegram_username_field: int = 3346483
    telegram_url_field: int = 3324273
    max_id_field: int = 3807405
    telegram_channel: str = ""
    whatsapp_channel: str = ""
    max_channel: str = ""
    api_key: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    admin_key: SecretStr = SecretStr("")
    # Original Wazzup webhook URL, including its private path/query.
    forward_url: SecretStr = SecretStr("")
    webhook_ready: bool = False
    legacy_checked: bool = False
    scan_seconds: int = 900
    tick_seconds: int = 30
    max_pages: int = 100

    def live_ready(self) -> bool:
        return (
            self.mode == "live" and self.webhook_ready and self.legacy_checked
            and bool(self.api_key.get_secret_value())
            and bool(self.webhook_secret.get_secret_value())
            and all(getattr(self, k + "_channel") for k in ORDER)
            and 0 <= self.hour < self.end_hour <= 24
        )


class ReminderJob(Base):
    __tablename__ = "reservation_reminder_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    until: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("lead_id", "until", name="uq_reservation_reminder"),)


class ReminderAttempt(Base):
    __tablename__ = "reservation_reminder_attempt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey(ReminderJob.id), index=True)
    transport: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(24), default="inflight")
    message_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True)
    crm_message_id: Mapped[str] = mapped_column(String(100), unique=True)
    reason: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("job_id", "transport", name="uq_reservation_attempt"),)


class ReminderEvent(Base):
    __tablename__ = "reservation_reminder_event"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


TABLES = [ReminderJob.__table__, ReminderAttempt.__table__, ReminderEvent.__table__]


def values(doc: dict, field_id: int) -> list[dict]:
    return next((f.get("values") or [] for f in doc.get("custom_fields_values") or []
                 if f.get("field_id") == field_id), [])


def first(doc: dict, field_id: int) -> str:
    return str(next((v["value"] for v in values(doc, field_id) if v.get("value") not in (None, "")), ""))


def expiry(lead: dict, s: ReminderSettings) -> date | None:
    if not any(v.get("enum_id") == s.paid_enum for v in values(lead, s.hold_field)):
        return None
    try:
        return datetime.fromtimestamp(int(first(lead, s.until_field)), MSK).date()
    except (ValueError, OverflowError, OSError):
        return None


def is_open(lead: dict) -> bool:
    return bool(lead.get("id")) and lead.get("status_id") not in (142, 143) and not lead.get("is_deleted")


def in_window(until: date, now: datetime, s: ReminderSettings) -> bool:
    local = now.astimezone(MSK)
    return (until - timedelta(days=s.days_before) == local.date()
            and s.hour <= local.hour < s.end_hour)


def render(until: date) -> str:
    return TEMPLATE.replace("(Отложка до)", until.strftime("%d.%m.%Y"))


def primary_contact(lead: dict) -> int | None:
    linked = (lead.get("_embedded") or {}).get("contacts") or []
    main = [c for c in linked if c.get("is_main")]
    choice = main if main else linked
    return int(choice[0]["id"]) if len(choice) == 1 else None


def recipient(contact: dict, transport: str, s: ReminderSettings) -> dict[str, str] | None:
    if transport == "telegram":
        cid = first(contact, s.telegram_id_field).strip()
        if cid:
            return {"chatId": cid}
        username = first(contact, s.telegram_username_field).strip().lstrip("@")
        if not username:
            url = first(contact, s.telegram_url_field).strip()
            parsed = urlparse(url if "://" in url else "https://" + url)
            if parsed.hostname in {"t.me", "telegram.me", "www.t.me"}:
                username = parsed.path.strip("/")
        if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", username):
            return {"username": username}
    if transport == "max":
        cid = first(contact, s.max_id_field).strip()
        if cid:
            return {"chatId": cid}
    phones = [v for f in contact.get("custom_fields_values") or []
              if f.get("field_code") == "PHONE" for v in f.get("values") or []]
    work = [v for v in phones if v.get("enum_code") == "WORK"]
    candidates = work if len(work) == 1 else phones
    numbers = set()
    for v in candidates:
        number = re.sub(r"\D", "", str(v.get("value") or ""))
        if len(number) == 11 and number.startswith("8"):
            number = "7" + number[1:]
        if 10 <= len(number) <= 15:
            numbers.add(number)
    if len(numbers) != 1:
        return None
    phone = numbers.pop()
    return {"chatId": phone} if transport == "whatsapp" else {"phone": phone}


class Clients:
    def __init__(self, db: Session, s: ReminderSettings):
        self.s = s
        self.amo = httpx.Client(base_url=api_base(get_settings()), timeout=25,
            headers={"Authorization": "Bearer " + get_valid_access_token(db)})
        # No retry transport. There is only one outbound attempt per channel.
        self.wz = httpx.Client(base_url="https://api.wazzup24.com/v3", timeout=25,
            headers={"Authorization": "Bearer " + s.api_key.get_secret_value()})

    def close(self):
        self.amo.close()
        self.wz.close()

    def get(self, path: str, **params) -> dict:
        r = self.amo.get(path, params=params)
        r.raise_for_status()
        return {} if r.status_code == 204 else r.json()

def get_lead(c: Clients, lead_id: int) -> dict:
    return c.get(f"/api/v4/leads/{lead_id}", **{"with": "contacts"})


def scan(c: Clients, s: ReminderSettings) -> list[dict]:
    # This account rejects custom-field filters. Standard stage filters are live-verified.
    pipelines = c.get("/api/v4/leads/pipelines").get("_embedded", {}).get("pipelines", [])
    params: dict[str, Any] = {"limit": 250, "with": "contacts"}
    i = 0
    for p in pipelines:
        for st in p.get("_embedded", {}).get("statuses", []):
            if st["id"] not in (142, 143) and st.get("type") != 1:
                params[f"filter[statuses][{i}][pipeline_id]"] = p["id"]
                params[f"filter[statuses][{i}][status_id]"] = st["id"]
                i += 1
    if not i:
        raise RuntimeError("No open stages; scan refused")
    leads = []
    for page in range(1, s.max_pages + 1):
        data = c.get("/api/v4/leads", **params, page=page)
        part = data.get("_embedded", {}).get("leads", [])
        leads.extend(part)
        if not data.get("_links", {}).get("next") and len(part) < 250:
            return leads
    raise RuntimeError("Incomplete open-lead scan; no jobs updated")


def sync_jobs(db: Session, leads: list[dict], s: ReminderSettings, now: datetime) -> dict:
    eligible = {int(x["id"]): expiry(x, s) for x in leads if is_open(x) and expiry(x, s)}
    existing = {(j.lead_id, j.until): j for j in db.scalars(select(ReminderJob)).all()}
    for lead_id, until in eligible.items():
        # Don't catch up overdue reminder dates on initial rollout.
        if until - timedelta(days=s.days_before) < now.astimezone(MSK).date():
            continue
        if (lead_id, until) not in existing:
            db.add(ReminderJob(lead_id=lead_id, until=until))
    for job in existing.values():
        if job.state == "ready" and eligible.get(job.lead_id) != job.until:
            job.state, job.reason = "cancelled", "conditions_changed"
        elif job.state == "ready" and job.until - timedelta(days=s.days_before) < now.astimezone(MSK).date():
            job.state, job.reason = "expired", "reminder_day_passed"
    db.commit()
    return {"open_scanned": len(leads), "paid": len(eligible), "complete": True}


def record_events(db: Session, body: dict) -> None:
    rows = list(body.get("statuses") or [])
    rows += [m for m in body.get("messages") or [] if m.get("isEcho")]
    for item in rows:
        mid, status = str(item.get("messageId") or ""), str(item.get("status") or "")
        if not mid or status not in SUCCESS | {"error"}:
            continue
        error = item.get("error") or {}
        code = str(error.get("error") or "")[:80] if isinstance(error, dict) else ""
        key = hashlib.sha256(f"{mid}:{status}:{code}".encode()).hexdigest()
        if db.get(ReminderEvent, key) is None:
            db.add(ReminderEvent(key=key, message_id=mid[:80], status=status, error_code=code))
    db.commit()


def reconcile(db: Session) -> None:
    for attempt in db.scalars(select(ReminderAttempt).where(
            ReminderAttempt.state.in_(["inflight", "pending", "unknown", "error"]))):
        if not attempt.message_id:
            # Persisted before POST: after a crash, never submit the same attempt again.
            continue
        events = list(db.scalars(select(ReminderEvent).where(
            ReminderEvent.message_id == attempt.message_id)))
        job = db.get(ReminderJob, attempt.job_id)
        if any(e.status in SUCCESS for e in events):
            attempt.state, job.state, job.reason = "sent", "sent", ""
        elif attempt.state != "error" and job.state not in {"sent", "cancelled", "expired"} and any(e.status == "error" for e in events):
            attempt.state = "error"
            attempt.reason = next(e.error_code for e in events if e.status == "error")
            job.state, job.reason = "ready", "confirmed_channel_error"
        job.updated_at = utcnow()
    db.commit()


def send_job(db: Session, job_id: int, c: Clients, s: ReminderSettings, now: datetime) -> None:
    if not s.live_ready():
        return
    # All dispatches run under one PostgreSQL advisory lock in tick().
    job = db.get(ReminderJob, job_id)
    if job.state != "ready" or not in_window(job.until, now, s):
        return
    lead = get_lead(c, job.lead_id)
    if not is_open(lead) or expiry(lead, s) != job.until:
        job.state, job.reason = "cancelled", "conditions_changed"
        db.commit()
        return
    cid = primary_contact(lead)
    if not cid or (job.contact_id and job.contact_id != cid):
        job.state, job.reason = "blocked", "ambiguous_or_changed_contact"
        db.commit()
        return
    contact = c.get(f"/api/v4/contacts/{cid}")
    job.contact_id = cid
    channels_response = c.wz.get("/channels")
    channels_response.raise_for_status()
    channels = {x["channelId"]: x for x in channels_response.json()}
    for transport in ORDER:
        prior = db.scalar(select(ReminderAttempt).where(
            ReminderAttempt.job_id == job.id, ReminderAttempt.transport == transport))
        if prior:
            if prior.state not in {"error", "skipped"}:
                return
            continue
        dest = recipient(contact, transport, s)
        channel_id = getattr(s, transport + "_channel")
        channel = channels.get(channel_id) or {}
        expected = "tgapi" if transport == "telegram" else transport
        if channel and channel.get("transport") != expected:
            job.state, job.reason = "blocked", "channel_transport_mismatch"
            db.commit()
            return
        attempt = ReminderAttempt(job_id=job.id, transport=transport,
            crm_message_id=f"hold-paid-{job.lead_id}-{job.until.isoformat()}-{transport}")
        db.add(attempt)
        if not dest or channel.get("state") != "active":
            attempt.state = "skipped"
            attempt.reason = "no_recipient" if not dest else "channel_not_active"
            db.commit()
            continue
        job.state = "inflight"
        db.commit()  # Commit before the external side effect, never resend on a crash.
        payload = {"channelId": channel_id, "chatType": transport, **dest,
            "text": render(job.until), "clearUnanswered": False,
            "crmMessageId": attempt.crm_message_id}
        try:
            response = c.wz.post("/message", json=payload)
            try:
                result = response.json()
            except ValueError:
                result = {}
            if response.status_code == 201 and result.get("messageId"):
                attempt.message_id = str(result["messageId"])
                attempt.state, job.state = "pending", "pending"
                db.commit()
                # A callback can arrive before the POST response.
                reconcile(db)
                return
            code = str(result.get("error") or "")[:80]
            if 400 <= response.status_code < 500 and code in REJECTED:
                attempt.state, attempt.reason = "error", code
                job.state = "ready"
                db.commit()
                continue
            attempt.state, attempt.reason = "unknown", code or f"http_{response.status_code}"
            job.state, job.reason = "uncertain", "send_result_unknown"
            db.commit()
            return
        except httpx.HTTPError:
            attempt.state, attempt.reason = "unknown", "network_error"
            job.state, job.reason = "uncertain", "send_result_unknown"
            db.commit()
            return
    job.state, job.reason = "failed", "all_channels_exhausted"
    db.commit()


def tick(s: ReminderSettings, *, do_scan: bool = False) -> dict:
    db = get_session_factory()()
    lock = None
    c = None
    try:
        # Separate connection: commits in the ORM do not release a session advisory lock.
        lock = get_engine().connect()
        if not lock.execute(text("SELECT pg_try_advisory_lock(674001923)")).scalar():
            return {"busy": True}
        c = Clients(db, s)
        now = utcnow()
        result = sync_jobs(db, scan(c, s), s, now) if do_scan else {}
        reconcile(db)
        if s.live_ready():
            ids = list(db.scalars(select(ReminderJob.id).where(ReminderJob.state == "ready")))
            for jid in ids:
                send_job(db, jid, c, s, utcnow())
        return result
    finally:
        if c:
            c.close()
        db.close()
        if lock is not None:
            lock.execute(text("SELECT pg_advisory_unlock(674001923)"))
            lock.close()


last_scan: dict = {}
callback_stats: dict = {"received": 0, "forwarded_ok": 0, "forwarded_failed": 0}


async def worker(s: ReminderSettings):
    import time as clock
    last = 0.0
    while True:
        try:
            due = clock.monotonic() - last >= s.scan_seconds
            result = await asyncio.to_thread(tick, s, do_scan=due)
            if due and result.get("complete"):
                last = clock.monotonic()
                last_scan.update({**result, "at": utcnow().isoformat()})
        except Exception as exc:
            # Do not print exception URLs, upstream secrets, or customer details.
            log.error("Reminder worker failed: %s", type(exc).__name__)
        await asyncio.sleep(s.tick_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app import certificate_mail

    s = ReminderSettings()
    if s.mode == "live" and not s.live_ready():
        raise RuntimeError("Reminder live prerequisites are incomplete")
    Base.metadata.create_all(get_engine(), tables=TABLES)
    mail_settings = certificate_mail.MailSettings()
    if mail_settings.mode == "live" and not mail_settings.live_ready():
        # A new mail rule must not take down existing Wazzup forwarding.
        log.error("Certificate email live prerequisites are incomplete; sending disabled")
    Base.metadata.create_all(get_engine(), tables=certificate_mail.TABLES)
    task = asyncio.create_task(worker(s))
    mail_task = asyncio.create_task(certificate_mail.worker(mail_settings))
    try:
        yield
    finally:
        task.cancel()
        mail_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await mail_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Mansband reservation reminders", lifespan=lifespan)

from app.certificate_mail import router as certificate_router

app.include_router(certificate_router)


@app.get("/health")
def health():
    return {"ok": True, "mode": ReminderSettings().mode}


@app.get("/internal/status")
def status(x_reminder_key: str = Header(default="")):
    s = ReminderSettings()
    if not s.admin_key.get_secret_value() or not hmac.compare_digest(x_reminder_key, s.admin_key.get_secret_value()):
        raise HTTPException(403)
    db = get_session_factory()()
    try:
        jobs = list(db.scalars(select(ReminderJob)))
        attempts = list(db.scalars(select(ReminderAttempt)))
        from app.certificate_mail import summary as mail_summary

        return {"mode": s.mode, "ready": s.live_ready(), "hour_msk": s.hour,
            "last_scan": last_scan, "callback_forwarding": callback_stats,
            "certificate_mail": mail_summary(db),
            "jobs": [{"lead_id": j.lead_id, "until": j.until.isoformat(), "state": j.state,
                      "reason": j.reason} for j in jobs],
            "attempts": [{"job_id": a.job_id, "transport": a.transport, "state": a.state,
                          "reason": a.reason} for a in attempts]}
    finally:
        db.close()


@app.post("/wazzup/{secret}")
async def webhook(secret: str, request: Request):
    s = ReminderSettings()
    if not s.webhook_secret.get_secret_value() or not hmac.compare_digest(secret, s.webhook_secret.get_secret_value()):
        raise HTTPException(403)
    if int(request.headers.get("content-length", "0")) > 2_000_000:
        raise HTTPException(413)
    raw = await request.body()
    if len(raw) > 2_000_000:
        raise HTTPException(413)
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected object")
    callback_stats["received"] += 1
    forwarded = None
    url = s.forward_url.get_secret_value()
    if url:
        # Preserve existing Integracio integration and its original Authorization.
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in {"authorization", "content-type"}}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
                forwarded = await c.post(url, content=raw, headers=headers)
        except httpx.HTTPError:
            callback_stats["forwarded_failed"] += 1
            raise HTTPException(502, "Existing subscriber unavailable")
        callback_stats["last_http"] = forwarded.status_code
        callback_stats["last_at"] = utcnow().isoformat()
        counter = "forwarded_ok" if 200 <= forwarded.status_code < 300 else "forwarded_failed"
        callback_stats[counter] += 1
    db = get_session_factory()()
    try:
        record_events(db, body)
    except Exception as exc:
        db.rollback()
        log.error("Reminder event persistence failed: %s", type(exc).__name__)
        # Never break an existing successfully forwarded notification for our feature.
        if forwarded is None:
            raise HTTPException(503)
    finally:
        db.close()
    if forwarded is not None:
        return Response(content=forwarded.content, status_code=forwarded.status_code,
                        headers={"Content-Type": forwarded.headers.get("content-type", "application/json")})
    return {"ok": True}
