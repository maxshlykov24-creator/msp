"""Stage notifications: durable queue, one messenger at a time, confirmed fallback."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.certificate_mail import MSK, transitions, MailSettings
from app.database import get_engine, get_session_factory
from app.models import Base

log = logging.getLogger(__name__)
STAGES = {76655114: "delivered", 76655110: "shipped", 76666306: "in_store"}
SOCIAL = {"vk", "avito", "instagram"}
DIRECT = ("telegram", "whatsapp", "max")
# Legacy Wazzup origins must be verified before enabling live.
ORIGINS = {"com.wazzup24.max": "max", "com.wazzup24.wz": "whatsapp",
           "com.wazzup24-1": "telegram", "com.wazzup24": "vk", "com.wazzup24.vk": "vk",
           "com.wazzup24.avito": "avito", "com.wazzup24.instagram": "instagram"}
SOCIAL_FIELDS = {"vk": 3642123, "avito": 3642125, "instagram": 3816137}


def utcnow():
    return datetime.now(timezone.utc)


class StageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STAGENOTIFY_", extra="ignore")
    mode: Literal["preview", "live"] = "preview"
    activated_at: int = 0
    legacy_checked: bool = False
    origins_checked: bool = False
    track_field: int = 3133923
    store_field: int = 3158921
    pyatnitskaya_enum: int = 4765787
    baumanskaya_enum: int = 4765789
    pyatnitskaya_address: str = ""  # Owner must resolve conflicting street names.
    vk_channel: str = ""
    avito_channel: str = ""
    instagram_channel: str = ""

    def live_ready(self):
        return (self.mode == "live" and self.activated_at > 0 and self.legacy_checked
                and self.origins_checked and bool(self.pyatnitskaya_address))


class StageJob(Base):
    __tablename__ = "stage_notification_job"
    __table_args__ = (UniqueConstraint("lead_id", "status_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer)
    status_id: Mapped[int] = mapped_column(Integer)
    event_key: Mapped[str] = mapped_column(String(64))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    reason: Mapped[str] = mapped_column(String(100), default="")
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class StageAttempt(Base):
    __tablename__ = "stage_notification_attempt"
    __table_args__ = (UniqueConstraint("job_id", "position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    transport: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(24), default="inflight")
    message_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    crm_message_id: Mapped[str] = mapped_column(String(100), unique=True)
    reason: Mapped[str] = mapped_column(String(100), default="")


class Conversation(Base):
    __tablename__ = "stage_notification_conversation"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(80))
    transport: Mapped[str] = mapped_column(String(20))
    chat_id: Mapped[str] = mapped_column(String(200), index=True)
    last_incoming: Mapped[datetime] = mapped_column(DateTime(timezone=True))


TABLES = [StageJob.__table__, StageAttempt.__table__, Conversation.__table__]


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def dispatch_time(now):
    """Outside [10,20), owner explicitly requested NEXT calendar day at 10 MSK."""
    local = now.astimezone(MSK)
    if 10 <= local.hour < 20:
        return now
    return (local + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def ingest(db, form, now, settings=None):
    s = settings or StageSettings()
    if s.activated_at <= 0:
        return 0
    count = 0
    for status_id in STAGES:
        cfg = MailSettings(status_id=status_id, activated_at=s.activated_at)
        for row in transitions(form, cfg, now):
            job = db.scalar(select(StageJob).where(StageJob.lead_id == row["lead_id"], StageJob.status_id == status_id).with_for_update())
            if job is None:
                db.add(StageJob(lead_id=row["lead_id"], status_id=status_id, event_key=row["key"],
                    event_at=row["source_at"], check_at=dispatch_time(row["source_at"])))
                count += 1
            elif row["source_at"] > aware(job.event_at) and job.state in {"ready", "blocked", "cancelled"}:
                if db.scalar(select(StageAttempt.id).where(StageAttempt.job_id == job.id).limit(1)) is None:
                    job.event_key, job.event_at = row["key"], row["source_at"]
                    job.check_at, job.state, job.reason, job.plan = dispatch_time(row["source_at"]), "ready", "", None
                    count += 1
    db.commit()
    return count


def record_messages(db, body):
    # Only routing metadata is retained, never message text or attachments.
    if not body.get("messages"):
        return
    for msg in body.get("messages") or []:
        if msg.get("isEcho") is not False or msg.get("isDeleted") or msg.get("isEdited"):
            continue
        transport, channel, chat = msg.get("chatType"), str(msg.get("channelId") or ""), str(msg.get("chatId") or "")
        if transport not in set(DIRECT) | SOCIAL or not channel or not chat or len(chat) > 200:
            continue
        try:
            stamp = datetime.fromisoformat(str(msg["dateTime"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp > utcnow() + timedelta(minutes=5):
                continue
        except (ValueError, KeyError):
            continue
        key = hashlib.sha256(f"{channel}:{transport}:{chat}".encode()).hexdigest()
        con = db.get(Conversation, key)
        if con is not None and aware(con.last_incoming) >= stamp:
            continue
        if con is None:
            con = Conversation(key=key, channel_id=channel, transport=transport, chat_id=chat)
            db.add(con)
        con.last_incoming = stamp
    db.commit()


def render(lead, s):
    from app.reservation_reminders import first, values
    kind = STAGES[lead["status_id"]]
    if kind == "delivered":
        return "Здравствуйте! Вас приветствует магазин мужских костюмов Mansband.\n\nВаша посылка прибыла в пункт выдачи, ждём вашей примерки!"
    if kind == "shipped":
        track = first(lead, s.track_field).strip()
        if not track or len(track) > 150 or any(c in track for c in "\r\n"):
            raise ValueError("missing_or_invalid_track")
        return ("Здравствуйте!\nВаша посылка отправлена.\n\nДля отслеживания:\n"
                "Ссылка: https://www.cdek.ru/ru/tracking/\nНомер трека: " + track +
                "\n\nЕсли потребуется консультация во время примерки, вы можете позвонить по номеру 8-931-009-02-51.\nЖдём вашей обратной связи!")
    enums = {v.get("enum_id") for v in values(lead, s.store_field)}
    if enums == {s.pyatnitskaya_enum} and s.pyatnitskaya_address:
        address = s.pyatnitskaya_address
    elif enums == {s.baumanskaya_enum}:
        address = "Спартаковская площадь, 14с2"
    else:
        raise ValueError("missing_or_ambiguous_store")
    return ("Вас приветствует магазин мужских костюмов Mansband!\n\nВаш товар прибыл по адресу " + address +
            "\nБесплатная бронь действовует до 22:00 завтрашнего дня, будем ждать вас!\n\n"
            "В случае, если в эти дни не удается посетить магазин, предлагаем возможность продлить бронь на 2 недели с помощью залога 3000₽.\n*залог учитывается при выкупе костюма.")


def order_for(source):
    if source is None or source in SOCIAL:
        return [*DIRECT, *([source] if source else [])]
    if source in DIRECT:
        return [source, *(t for t in DIRECT if t != source)]
    raise ValueError("unknown_conversation_origin")


def resolve_plan(db, c, lead, contact, s, r):
    from app.reservation_reminders import first, recipient
    talks = c.get("/api/v4/talks", **{"filter[contact_id]": contact["id"], "limit": 250}).get("_embedded", {}).get("talks", [])
    if len(talks) >= 250:
        raise ValueError("too_many_conversations")
    talks = {t["talk_id"]: t for t in talks if t.get("contact_id") == contact["id"]}
    data = c.get("/api/v4/events", **{"filter[entity]": "contact", "filter[entity_id]": contact["id"],
        "filter[type]": "incoming_chat_message", "limit": 100})
    latest = []
    for event in data.get("_embedded", {}).get("events", []):
        for value in event.get("value_after") or []:
            msg = value.get("message") or {}
            if msg.get("talk_id") in talks:
                latest.append((int(event["created_at"]), msg.get("origin")))
    source = None
    if latest:
        stamp = max(x[0] for x in latest)
        origins = {x[1] for x in latest if x[0] == stamp}
        mapped = {ORIGINS.get(origin, origin if origin in set(DIRECT) | SOCIAL else "unknown") for origin in origins}
        if len(mapped) != 1 or "unknown" in mapped:
            raise ValueError("unknown_or_ambiguous_conversation_origin")
        source = mapped.pop()
    elif talks:
        origins = {ORIGINS.get(t.get("origin"), "unknown") for t in talks.values()}
        if len(origins) != 1 or "unknown" in origins:
            raise ValueError("no_unique_conversation_origin")
        source = origins.pop()
    channels_response = c.wz.get("/channels")
    channels_response.raise_for_status()
    channels = channels_response.json()
    if not isinstance(channels, list):
        raise ValueError("invalid_channels_response")
    plan = []
    for transport in order_for(source):
        channel = getattr(r, transport + "_channel") if transport in DIRECT else getattr(s, transport + "_channel")
        dest = recipient(contact, transport, r) if transport in DIRECT else ({"chatId": first(contact, SOCIAL_FIELDS[transport]).strip()} if first(contact, SOCIAL_FIELDS[transport]).strip() else None)
        # Incoming metadata pins the actual Wazzup account when it is known.
        cached = None
        if dest and dest.get("chatId"):
            matches = list(db.scalars(select(Conversation).where(Conversation.transport == transport,
                Conversation.chat_id == dest["chatId"]).order_by(Conversation.last_incoming.desc()).limit(2)))
            if len({x.channel_id for x in matches}) == 1 and matches:
                cached = matches[0]
        if cached:
            channel = cached.channel_id
        if not channel:
            candidates = [x for x in channels if x.get("transport") == transport]
            if len(candidates) == 1:
                channel = candidates[0]["channelId"]
        plan.append({"transport": transport, "channelId": channel, "recipient": dest})
    return plan


def reconcile(db, now):
    from app.reservation_reminders import ReminderEvent, SUCCESS
    for a in db.scalars(select(StageAttempt).where(StageAttempt.state.in_(["inflight", "pending", "error", "unknown"]))):
        if not a.message_id:
            if a.state == "inflight":
                a.state = "unknown"
                job = db.get(StageJob, a.job_id)
                job.state, job.reason = "uncertain", "interrupted_send_claim"
            continue
        events = list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id == a.message_id)))
        job = db.get(StageJob, a.job_id)
        if any(e.status in SUCCESS for e in events):
            a.state, job.state, job.reason = "sent", "sent", ""
        elif a.state != "error" and job.state not in {"sent", "cancelled"} and any(e.status == "error" for e in events):
            a.state, a.reason = "error", "confirmed_delivery_error"
            job.state, job.reason, job.check_at = "ready", a.reason, dispatch_time(now)
    db.commit()


def send_job(db, job, c, s, r, now, *, clock=None):
    from app.reservation_reminders import get_lead, primary_contact, REJECTED
    if not s.live_ready() or job.state not in {"ready", "blocked"} or aware(job.check_at) > now:
        return
    if dispatch_time(now) != now:
        job.check_at = dispatch_time(now)
        db.commit()
        return
    lead = get_lead(c, job.lead_id)
    if lead.get("pipeline_id") != 9601214 or lead.get("status_id") != job.status_id or lead.get("is_deleted"):
        job.state, job.reason = "cancelled", "stage_changed"
        db.commit()
        return
    try:
        body = render(lead, s)
        cid = primary_contact(lead)
        if not cid or (job.contact_id and job.contact_id != cid):
            raise ValueError("ambiguous_or_changed_contact")
        if job.plan is None:
            job.plan = resolve_plan(db, c, lead, c.get(f"/api/v4/contacts/{cid}"), s, r)
            job.contact_id = cid
            db.commit()
    except ValueError as exc:
        job.state, job.reason, job.check_at = "blocked", str(exc)[:100], now + timedelta(hours=1)
        db.commit()
        return
    resp = c.wz.get("/channels"); resp.raise_for_status()
    channels = {x["channelId"]: x for x in resp.json()}
    for pos, route in enumerate(job.plan):
        prior = db.scalar(select(StageAttempt).where(StageAttempt.job_id == job.id, StageAttempt.position == pos))
        if prior:
            if prior.state not in {"skipped", "error"}:
                return
            continue
        channel = channels.get(route["channelId"]) or {}
        expected = "tgapi" if route["transport"] == "telegram" else route["transport"]
        available = bool(route["recipient"]) and channel.get("state") == "active" and channel.get("transport") == expected
        if available:
            fresh = get_lead(c, job.lead_id)
            if (fresh.get("pipeline_id") != 9601214 or fresh.get("status_id") != job.status_id
                    or fresh.get("is_deleted") or primary_contact(fresh) != job.contact_id):
                job.state, job.reason = "cancelled", "stage_or_contact_changed"
                db.commit()
                return
            try:
                body = render(fresh, s)
            except ValueError as exc:
                job.state, job.reason, job.check_at = "blocked", str(exc)[:100], now + timedelta(hours=1)
                db.commit()
                return
            current = (clock or utcnow)()
            if dispatch_time(current) != current:
                job.check_at, job.state = dispatch_time(current), "ready"
                db.commit()
                return
        a = StageAttempt(job_id=job.id, position=pos, transport=route["transport"],
            crm_message_id=f"stage-{job.lead_id}-{job.status_id}-{pos}")
        db.add(a)
        if not available:
            a.state, a.reason = "skipped", "no_recipient_or_inactive_channel"
            db.commit()
            continue
        job.state = "inflight"
        db.commit()
        try:
            response = c.wz.post("/message", json={"channelId": route["channelId"], "chatType": route["transport"],
                **route["recipient"], "text": body, "clearUnanswered": False, "crmMessageId": a.crm_message_id})
            try:
                result = response.json()
            except ValueError:
                result = {}
            if response.status_code == 201 and result.get("messageId"):
                a.message_id = str(result["messageId"])
                a.state, job.state, job.reason = "pending", "pending", ""
                db.commit(); reconcile(db, now)
                return
            code = str(result.get("error") or "")[:80]
            # Observed Wazzup 400 rejection on the owner test: no account exists
            # for that MAX phone, no messageId was created.
            if 400 <= response.status_code < 500 and code in REJECTED | {"CHANNEL_MAX_PHONE_NOT_OCCUPIED"}:
                a.state, a.reason, job.state = "error", code, "ready"
                db.commit()
                continue
            a.state, a.reason, job.state, job.reason = "unknown", code or f"http_{response.status_code}", "uncertain", "send_result_unknown"
        except httpx.HTTPError:
            a.state, a.reason, job.state, job.reason = "unknown", "network_error", "uncertain", "send_result_unknown"
        db.commit()
        return
    job.state, job.reason = "failed", "all_channels_exhausted"
    db.commit()


def tick(s):
    from app.reservation_reminders import ReminderSettings, Clients
    if not s.live_ready():
        return
    with get_engine().connect() as lock, get_session_factory()() as db:
        if not lock.execute(text("SELECT pg_try_advisory_lock(674001925)")).scalar():
            return
        c = None
        try:
            now = utcnow(); reconcile(db, now)
            r = ReminderSettings(); c = Clients(db, r)
            jobs = list(db.scalars(select(StageJob).where(StageJob.state.in_(["ready", "blocked"]),
                StageJob.check_at <= now).order_by(StageJob.check_at).limit(20)))
            for job in jobs:
                try:
                    send_job(db, job, c, s, r, utcnow())
                except Exception as exc:
                    db.rollback()
                    log.error("Stage notification failed: %s", type(exc).__name__)
        finally:
            if c: c.close()
            lock.execute(text("SELECT pg_advisory_unlock(674001925)"))


async def worker(s):
    while True:
        try:
            await asyncio.to_thread(tick, s)
        except Exception as exc:
            log.error("Stage worker failed: %s", type(exc).__name__)
        await asyncio.sleep(5)


def summary(db):
    s = StageSettings()
    return {"mode": s.mode, "ready": s.live_ready(), "jobs": [
        {"lead_id": j.lead_id, "status_id": j.status_id, "state": j.state, "reason": j.reason}
        for j in db.scalars(select(StageJob).order_by(StageJob.id.desc()).limit(30))]}
