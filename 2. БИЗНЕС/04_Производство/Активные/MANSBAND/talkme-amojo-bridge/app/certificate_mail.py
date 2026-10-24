"""Certificate email on a new amoCRM status transition; no historical scan.

The claim is committed before SMTP. A lost response or process crash requires
manual review, never an automatic duplicate email.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Literal, Optional
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, Integer, String, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import amo_v4
from app.database import get_engine, get_session_factory
from app.models import Base
from app.tokens import get_valid_access_token

MSK = ZoneInfo("Europe/Moscow")
log = logging.getLogger(__name__)

BODY = """Вас приветствует магазин мужских костюмов Mansband👋🏻
Поздравляем! Вам подарили сертификат на сумму {{lead.sale}} в сеть наших магазинов🎁

Сертификат действителен с {{lead.cf(3829442)}} по {{lead.cf(3169911)}}

При посещение магазина достаточно просто показать электронный сертификат или назвать его номер

Адреса наших магазинов:
м. Новокузнецкая ул. Пятницкая д.8 этаж 3
м. Бауманская Спартаковская площадь 14с2

Парковка бесплатная на обоих адресах, позвоните за 5 минут до приезда по номеру 89310090251

Часы работы:
пн-пт с 12:00 до 22:00
сб-вс с 11:00 до 22:00

Ссылка на наш сайт👇🏻
https://mansband.ru/category/suits

Ждем вас в любое удобное время!
По всем вопросам можете обращаться к менеджеру 89310090251"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MailSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CERTMAIL_", extra="ignore")
    mode: Literal["preview", "live"] = "preview"
    pipeline_id: int = 9601214
    status_id: int = 0  # Must be confirmed against the actual account by the owner.
    account_id: int = 32417154
    email_field: int = 3829210
    from_field: int = 3829442
    until_field: int = 3169911
    delay_seconds: int = 300
    number_field: int = 3135463
    include_number: bool = False
    legacy_checked: bool = False
    activated_at: int = 0
    webhook_secret: SecretStr = SecretStr("")
    smtp_host: str = "mail.hosting.reg.ru"
    smtp_port: int = 465
    smtp_user: str = "promo@mansband.ru"
    smtp_password: SecretStr = SecretStr("")
    subject: str = "Вам подарили сертификат Mansband 🎁"
    tick_seconds: int = 5

    def live_ready(self) -> bool:
        return (self.mode == "live" and self.status_id > 0 and self.pipeline_id > 0
                and self.activated_at > 0 and self.legacy_checked
                and bool(self.webhook_secret.get_secret_value())
                and bool(self.smtp_password.get_secret_value()))


class CertificateTransition(Base):
    __tablename__ = "certificate_mail_transition"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    source_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CertificateMailJob(Base):
    __tablename__ = "certificate_mail_job"
    # One certificate sale email per deal, even if the stage is re-entered.
    lead_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    event_key: Mapped[str] = mapped_column(String(64))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    check_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    message_id: Mapped[str] = mapped_column(String(120), default="")
    recipient_hash: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(80), default="")


TABLES = [CertificateTransition.__table__, CertificateMailJob.__table__]
router = APIRouter()


def field_value(lead: dict, field_id: int) -> str:
    for f in lead.get("custom_fields_values") or []:
        if f.get("field_id") == field_id:
            return str(next((v["value"] for v in f.get("values") or []
                            if v.get("value") not in (None, "")), ""))
    return ""


class InvalidCertificate(ValueError):
    pass


def start_date(lead: dict, s: MailSettings):
    try:
        return datetime.fromtimestamp(int(field_value(lead, s.from_field)), MSK).date()
    except (ValueError, OSError, OverflowError):
        raise InvalidCertificate("missing_or_invalid_start")


def anniversary(start):
    # A calendar year: 29 February becomes 28 February in a non-leap year.
    try:
        return start.replace(year=start.year + 1)
    except ValueError:
        return start.replace(year=start.year + 1, day=28)


def email_address(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise InvalidCertificate("empty_email")
    if any(ch in raw for ch in "\r\n,;<>()\t ") or raw.count("@") != 1:
        raise InvalidCertificate("invalid_email")
    local, domain = raw.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise InvalidCertificate("invalid_email")
    if (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or len(domain) > 253 or "." not in domain):
        raise InvalidCertificate("invalid_email")
    labels = domain.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise InvalidCertificate("invalid_email")
    if len(local + "@" + domain) > 254:
        raise InvalidCertificate("invalid_email")
    return local + "@" + domain


def render(lead: dict, s: MailSettings, now: datetime) -> tuple[str, str]:
    start = start_date(lead, s)
    recipient = email_address(field_value(lead, s.email_field))
    price = lead.get("price")
    if isinstance(price, bool) or not re.fullmatch(r"[0-9]+", str(price)) or int(price) <= 0:
        raise InvalidCertificate("missing_or_invalid_amount")
    try:
        until = datetime.fromtimestamp(int(field_value(lead, s.until_field)), MSK).date()
    except (ValueError, OSError, OverflowError):
        raise InvalidCertificate("missing_or_invalid_expiry")
    today = now.astimezone(MSK).date()
    if until < today:
        raise InvalidCertificate("certificate_expired")
    if until != anniversary(start):
        raise InvalidCertificate("expiry_not_anniversary")
    body = (BODY.replace("{{lead.sale}}", f"{int(price):,}".replace(",", " "))
            .replace("{{lead.cf(3829442)}}", start.strftime("%d.%m.%Y"))
            .replace("{{lead.cf(3169911)}}", until.strftime("%d.%m.%Y")))
    if s.include_number:
        number = field_value(lead, s.number_field).strip()
        if not number or len(number) > 100 or any(ch in number for ch in "\r\n"):
            raise InvalidCertificate("missing_or_invalid_number")
        body = body.replace("\n\nСертификат действителен", f"\n\nНомер сертификата: {number}\n\nСертификат действителен", 1)
    return recipient, body


def transitions(form: dict[str, list[str]], s: MailSettings, now: datetime) -> list[dict]:
    grouped: dict[str, dict] = {}
    pattern = re.compile(r"^leads\[status\]\[(\d+)\]\[(id|status_id|old_status_id|pipeline_id|last_modified|account_id)\]$")
    for key, vals in form.items():
        m = pattern.match(key)
        if m and vals:
            grouped.setdefault(m[1], {})[m[2]] = vals[0]
    out = []
    for row in grouped.values():
        try:
            lid = int(row["id"])
            target = int(row["status_id"])
            old = int(row.get("old_status_id") or 0)
            pipe = int(row.get("pipeline_id") or 0)
            account = int(row.get("account_id") or (form.get("account[id]") or [0])[0])
            stamp = int(row["last_modified"])
        except (KeyError, TypeError, ValueError):
            continue
        if (lid <= 0 or target != s.status_id or old == target or account != s.account_id
                or pipe not in (0, s.pipeline_id) or stamp < s.activated_at
                or stamp > int(now.timestamp()) + 300):
            continue
        raw = f"{account}:{lid}:{old}:{target}:{stamp}"
        out.append({"key": hashlib.sha256(raw.encode()).hexdigest(), "lead_id": lid,
                    "source_at": datetime.fromtimestamp(stamp, timezone.utc)})
    return out


def ingest(db: Session, rows: list[dict]) -> int:
    queued = 0
    for row in rows:
        if db.get(CertificateTransition, row["key"]):
            continue
        try:
            with db.begin_nested():
                db.add(CertificateTransition(**row))
                db.flush()
        except IntegrityError:
            continue
        job = db.scalar(select(CertificateMailJob).where(
            CertificateMailJob.lead_id == row["lead_id"]).with_for_update())
        if job is None:
            job = CertificateMailJob(lead_id=row["lead_id"], event_key=row["key"], event_at=row["source_at"])
            db.add(job)
            queued += 1
        elif job.attempted_at is None and job.state in {"queued", "blocked", "cancelled", "expired"}:
            previous = job.event_at.replace(tzinfo=timezone.utc) if job.event_at.tzinfo is None else job.event_at
            if row["source_at"] > previous:
                job.state, job.reason, job.event_key, job.event_at = "queued", "", row["key"], row["source_at"]
                job.due_at, job.check_at = None, None
                queued += 1
    db.commit()
    return queued


def send_smtp(s: MailSettings, recipient: str, body: str, message_id: str, now: datetime) -> None:
    message = EmailMessage()
    message["From"] = f"MANSBAND <{s.smtp_user}>"
    message["To"] = recipient
    message["Subject"] = s.subject
    message["Date"] = format_datetime(now.astimezone(MSK))
    message["Message-ID"] = message_id
    message.set_content(body, charset="utf-8")
    server = None
    try:
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context())
        server.login(s.smtp_user, s.smtp_password.get_secret_value())
        refused = server.send_message(message, from_addr=s.smtp_user, to_addrs=[recipient])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        if server is not None:
            # A failed QUIT after accepted DATA must not turn success into uncertainty.
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                server.close()


def read_lead(db: Session, lead_id: int) -> dict:
    return amo_v4.get_lead(lead_id=lead_id, access_token=get_valid_access_token(db))


def write_expiry(db: Session, lead_id: int, stamp: int, s: MailSettings):
    response = amo_v4.patch_lead_fields(lead_id=lead_id,
        custom_fields_values=[{"field_id": s.until_field, "values": [{"value": stamp}]}],
        access_token=get_valid_access_token(db))
    if response.status_code not in (200, 204):
        raise RuntimeError("expiry_update_failed")


def process_job(db: Session, lead_id: int, s: MailSettings, now: datetime, *, fetch=None, deliver=None, persist_expiry=None):
    if not s.live_ready():
        return
    job = db.get(CertificateMailJob, lead_id)
    if not job or job.state not in {"queued", "blocked"} or job.attempted_at is not None:
        return
    event_at = job.event_at.replace(tzinfo=timezone.utc) if job.event_at.tzinfo is None else job.event_at
    lead = (fetch or read_lead)(db, lead_id)
    if (lead.get("pipeline_id") != s.pipeline_id or lead.get("status_id") != s.status_id
            or lead.get("is_deleted")):
        job.state, job.reason = "cancelled", "stage_changed"
        db.commit()
        return
    try:
        start = start_date(lead, s)
        until = anniversary(start)
        stamp = int(datetime.combine(until, datetime.min.time(), MSK).timestamp())
        if field_value(lead, s.until_field) != str(stamp):
            (persist_expiry or write_expiry)(db, lead_id, stamp, s)
            lead = (fetch or read_lead)(db, lead_id)
            # Never send until the CRM write is confirmed by a fresh read.
            if field_value(lead, s.until_field) != str(stamp):
                raise RuntimeError("expiry_update_not_confirmed")
            if start_date(lead, s) != start:
                return  # Manager changed start during PATCH; recompute on next tick.
            if (lead.get("pipeline_id") != s.pipeline_id or lead.get("status_id") != s.status_id
                    or lead.get("is_deleted")):
                job.state, job.reason = "cancelled", "stage_changed"
                db.commit()
                return
        due = max(event_at + timedelta(seconds=s.delay_seconds),
                  datetime.combine(start, datetime.min.time(), MSK).astimezone(timezone.utc))
        job.due_at = due
        if now < due:
            job.state, job.reason = "queued", "waiting_due_date"
            job.check_at = min(due, now + timedelta(seconds=60))
            db.commit()
            return
        recipient, body = render(lead, s, now)
    except InvalidCertificate as exc:
        job.state, job.reason = "blocked", str(exc)
        job.check_at = now + timedelta(hours=1)
        db.commit()
        return
    job.message_id = f"<mansband-certificate-{lead_id}@mansband.ru>"
    job.recipient_hash = hashlib.sha256(recipient.lower().encode()).hexdigest()
    job.content_hash = hashlib.sha256(body.encode()).hexdigest()
    job.state, job.attempted_at = "sending", now
    db.commit()  # Durable claim before the SMTP side effect.
    try:
        (deliver or send_smtp)(s, recipient, body, job.message_id, now)
        job.state, job.reason = "accepted", "smtp_accepted"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError, smtplib.SMTPAuthenticationError) as exc:
        job.state, job.reason = "failed", type(exc).__name__
    except Exception as exc:
        # No email addresses, message contents, passwords or SMTP responses in logs.
        job.state, job.reason = "uncertain", type(exc).__name__[:80]
    db.commit()


def tick(s: MailSettings):
    if not s.live_ready():
        return
    lock = get_engine().connect()
    db = get_session_factory()()
    try:
        if not lock.execute(text("SELECT pg_try_advisory_lock(674001924)")).scalar():
            return
        now = utcnow()
        for job in db.scalars(select(CertificateMailJob).where(CertificateMailJob.state == "sending")):
            attempted = job.attempted_at
            if attempted and attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=timezone.utc)
            if attempted and now - attempted > timedelta(minutes=2):
                job.state, job.reason = "uncertain", "worker_interrupted"
        db.commit()
        ids = list(db.scalars(select(CertificateMailJob.lead_id).where(
            CertificateMailJob.state.in_(["queued", "blocked"]),
            CertificateMailJob.attempted_at.is_(None),
            (CertificateMailJob.check_at.is_(None) | (CertificateMailJob.check_at <= now))
            ).order_by(CertificateMailJob.check_at.asc().nullsfirst(), CertificateMailJob.created_at).limit(20)))
        for lid in ids:
            try:
                process_job(db, lid, s, utcnow())
            except Exception as exc:
                db.rollback()
                log.error("Certificate preparation failed: %s", type(exc).__name__)
    finally:
        db.close()
        lock.execute(text("SELECT pg_advisory_unlock(674001924)"))
        lock.close()


async def worker(s: MailSettings):
    while True:
        try:
            await asyncio.to_thread(tick, s)
        except Exception as exc:
            log.error("Certificate worker failed: %s", type(exc).__name__)
        await asyncio.sleep(s.tick_seconds)


def summary(db: Session) -> dict:
    s = MailSettings()
    jobs = list(db.scalars(select(CertificateMailJob).order_by(CertificateMailJob.created_at.desc()).limit(50)))
    return {"mode": s.mode, "ready": s.live_ready(), "status_id": s.status_id,
        "jobs": [{"lead_id": j.lead_id, "state": j.state, "reason": j.reason,
                  "due_at": j.due_at.isoformat() if j.due_at else None,
                  "attempted": j.attempted_at is not None} for j in jobs]}


@router.post("/amo/certificates/{secret}")
async def webhook(secret: str, request: Request):
    s = MailSettings()
    if not s.webhook_secret.get_secret_value() or not hmac.compare_digest(secret, s.webhook_secret.get_secret_value()):
        raise HTTPException(403)
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(413)
    try:
        form = parse_qs(raw.decode("utf-8"), max_num_fields=10000)
    except (UnicodeError, ValueError):
        raise HTTPException(400)
    rows = transitions(form, s, utcnow())
    db = get_session_factory()()
    try:
        queued = ingest(db, rows)
        from app.stage_notifications import ingest as ingest_stage
        stage_queued = ingest_stage(db, form, utcnow())
        return {"ok": True, "queued": queued, "stage_queued": stage_queued}
    finally:
        db.close()
