"""Stage notifications: durable queue, one messenger at a time, confirmed fallback."""
from __future__ import annotations

import asyncio
import calendar as month_calendar
import hashlib
import logging
import re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Date, DateTime, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.certificate_mail import MSK, transitions, MailSettings
from app.database import get_engine, get_session_factory
from app.models import Base

log = logging.getLogger(__name__)
STAGES = {76655114: "delivered", 76655110: "shipped", 76666306: "in_store", 142: "review"}
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
    review_activated_at: int = 0
    review_respect_window: bool = True

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


def review_dispatch(now, settings):
    if not settings.review_respect_window:
        return now.astimezone(timezone.utc)
    local = now.astimezone(MSK)
    if 10 <= local.hour < 20:
        return now.astimezone(timezone.utc)
    opening = local.replace(hour=10, minute=0, second=0, microsecond=0)
    if local.hour >= 20:
        opening += timedelta(days=1)
    return opening.astimezone(timezone.utc)


def stage_due(status_id, event_at, settings):
    return (review_dispatch(event_at + timedelta(hours=1), settings)
            if status_id == 142 else dispatch_time(event_at))


def ingest(db, form, now, settings=None):
    s = settings or StageSettings()
    if s.activated_at <= 0:
        return 0
    count = 0
    for status_id in STAGES:
        activation = s.review_activated_at if status_id == 142 else s.activated_at
        if activation <= 0:
            continue
        cfg = MailSettings(status_id=status_id, activated_at=activation)
        for row in transitions(form, cfg, now):
            job = db.scalar(select(StageJob).where(StageJob.lead_id == row["lead_id"], StageJob.status_id == status_id).with_for_update())
            if job is None:
                db.add(StageJob(lead_id=row["lead_id"], status_id=status_id, event_key=row["key"],
                    event_at=row["source_at"], check_at=stage_due(status_id, row["source_at"], s)))
                count += 1
            elif row["source_at"] > aware(job.event_at) and job.state in {"ready", "blocked", "cancelled"}:
                if db.scalar(select(StageAttempt.id).where(StageAttempt.job_id == job.id).limit(1)) is None:
                    job.event_key, job.event_at = row["key"], row["source_at"]
                    job.check_at, job.state, job.reason, job.plan = stage_due(status_id, row["source_at"], s), "ready", "", None
                    if status_id == 142:
                        job.contact_id = None
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
    if kind == "review":
        return render_review(lead, s)
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


def order_for(source, routing=None):
    if routing is not None:
        order = list(routing["order"])
        if source in DIRECT and routing["previous_first"]:
            order = [source, *(t for t in order if t != source)]
        if source in {"instagram", "avito"} and routing["social_fallback"]:
            order.append(source)
        return order

    if source is None or source in SOCIAL:
        return [*DIRECT, *([source] if source else [])]
    if source in DIRECT:
        return [source, *(t for t in DIRECT if t != source)]
    raise ValueError("unknown_conversation_origin")


def resolve_plan(db, c, lead, contact, s, r, *, calendar=False, calendar_job=None, routing=None):
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
    def origin_transport(origin):
        if calendar and origin == "instagram_business":
            return "instagram"
        if calendar and origin and origin == CalendarSettings().website_origin:
            return "website"
        return ORIGINS.get(origin, origin if origin in set(DIRECT) | SOCIAL else "unknown")
    source = None
    if latest:
        stamp = max(x[0] for x in latest)
        origins = {x[1] for x in latest if x[0] == stamp}
        mapped = {origin_transport(origin) for origin in origins}
        if len(mapped) != 1 or "unknown" in mapped:
            if routing is None:
                raise ValueError("unknown_or_ambiguous_conversation_origin")
            # Unknown transport does not identify an unknown recipient. Managed
            # rules use the configured direct order for the verified contact;
            # no social chat or previous transport is inferred from this origin.
            source = None
        else:
            source = mapped.pop()
    elif talks:
        origins = {origin_transport(t.get("origin")) for t in talks.values()}
        if len(origins) != 1 or "unknown" in origins:
            if routing is None:
                raise ValueError("no_unique_conversation_origin")
            source = None
        else:
            source = origins.pop()
    channels_response = c.wz.get("/channels")
    channels_response.raise_for_status()
    channels = channels_response.json()
    if not isinstance(channels, list):
        raise ValueError("invalid_channels_response")
    plan = []
    preferred = None
    if calendar_job is not None and calendar_job.kind == "meeting_followup":
        yesterday = db.scalar(select(CalendarJob).where(CalendarJob.lead_id == calendar_job.lead_id,
            CalendarJob.kind == "meeting_today", CalendarJob.target_date == calendar_job.target_date,
            CalendarJob.contact_id == contact["id"], CalendarJob.state == "sent"))
        if yesterday is not None and (not latest or max(x[0] for x in latest) <= aware(yesterday.due_at).timestamp()):
            sent = db.scalar(select(CalendarAttempt).where(CalendarAttempt.job_id == yesterday.id, CalendarAttempt.state == "sent"))
            if sent is not None and sent.transport in DIRECT:
                source = sent.transport
                preferred = next((route for route in yesterday.plan or [] if route["transport"] == source), None)
    for transport in (order_for(None if source == "website" else source, routing) if not calendar or source != "vk" else list(routing["order"]) if routing else list(DIRECT)):
        if calendar and transport == "vk":
            continue
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
            candidates = [x for x in channels if x.get("transport") == ("tgapi" if transport == "telegram" else transport)]
            if len(candidates) == 1:
                channel = candidates[0]["channelId"]
        if preferred and preferred["transport"] == transport and preferred["recipient"] == dest:
            channel = preferred["channelId"]
        plan.append({"transport": transport, "channelId": channel, "recipient": dest})
    return plan


def reconcile(db, now, *, job_type=StageJob, attempt_type=StageAttempt, dispatch=dispatch_time, settings=None):
    from app.reservation_reminders import ReminderEvent, SUCCESS
    for a in db.scalars(select(attempt_type).where(attempt_type.state.in_(["inflight", "pending", "error", "unknown"]))):
        if not a.message_id:
            if a.state == "inflight":
                a.state = "unknown"
                job = db.get(job_type, a.job_id)
                job.state, job.reason = "uncertain", "interrupted_send_claim"
            continue
        events = list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id == a.message_id)))
        job = db.get(job_type, a.job_id)
        if any(e.status in SUCCESS for e in events):
            a.state, job.state, job.reason = "sent", "sent", ""
        elif a.state != "error" and job.state not in {"sent", "cancelled"} and any(e.status == "error" for e in events):
            a.state, a.reason = "error", "confirmed_delivery_error"
            retry_at = (review_dispatch(now, settings or StageSettings())
                        if job_type is StageJob and job.status_id == 142 else dispatch(now))
            job.state, job.reason, job.check_at = "ready", a.reason, retry_at
    db.commit()


def send_job(db, job, c, s, r, now, *, clock=None, calendar_settings=None):
    calendar = calendar_settings is not None
    review = not calendar and job.status_id == 142
    certificate = calendar and job.kind in CERTIFICATE_KINDS
    attempt_type = CalendarAttempt if calendar else StageAttempt
    job_type = CalendarJob if calendar else StageJob
    dispatch = ((lambda stamp: calendar_dispatch(job, stamp)) if calendar else
                ((lambda stamp: review_dispatch(stamp, s)) if review else dispatch_time))
    make_body = (lambda lead, _: render_calendar(lead, job, s)) if calendar else render
    def valid(lead):
        return calendar_valid(lead, job, calendar_settings) if calendar else (
            lead.get("pipeline_id") == 9601214 and lead.get("status_id") == job.status_id and not lead.get("is_deleted"))
    from app.reservation_reminders import get_lead, primary_contact, REJECTED
    if not s.live_ready() or (calendar and not calendar_settings.live_ready()) or job.state not in {"ready", "blocked"} or aware(job.check_at) > now:
        return
    if review:
        if s.review_activated_at <= 0 or aware(job.event_at).timestamp() < s.review_activated_at:
            return
        if now < aware(job.event_at) + timedelta(hours=1):
            job.check_at = stage_due(142, aware(job.event_at), s)
            db.commit()
            return
    if dispatch(now) != now:
        job.check_at = dispatch(now)
        db.commit()
        return
    lead = get_lead(c, job.lead_id)
    if not valid(lead):
        job.state, job.reason = "cancelled", "stage_changed"
        db.commit()
        return
    try:
        body = make_body(lead, s)
        cid = 0 if certificate else primary_contact(lead)
        if not certificate and (not cid or (job.contact_id and job.contact_id != cid)):
            raise ValueError("ambiguous_or_changed_contact")
        if job.plan is None:
            if certificate:
                job.plan = certificate_plan(db, c, lead, s, r, calendar_settings)
            else:
                route_options = ({"calendar": True, "calendar_job": job} if calendar else
                                 ({"calendar": True} if review else {}))
                job.plan = resolve_plan(db, c, lead, c.get(f"/api/v4/contacts/{cid}"), s, r, **route_options)
            job.contact_id = cid
            db.commit()
        if certificate:
            phone = certificate_phone(lead, calendar_settings)
            if any(route["recipient"] != phone_destination(phone, route["transport"])
                   for route in job.plan if route["transport"] in DIRECT):
                raise ValueError("certificate_recipient_changed")
    except ValueError as exc:
        job.state, job.reason, job.check_at = "blocked", str(exc)[:100], now + (timedelta(seconds=30) if calendar else timedelta(hours=1))
        db.commit()
        return
    resp = c.wz.get("/channels"); resp.raise_for_status()
    channels = {x["channelId"]: x for x in resp.json()}
    for pos, route in enumerate(job.plan):
        prior = db.scalar(select(attempt_type).where(attempt_type.job_id == job.id, attempt_type.position == pos))
        if prior:
            if prior.state not in {"skipped", "error"}:
                return
            continue
        channel = channels.get(route["channelId"]) or {}
        expected = "tgapi" if route["transport"] == "telegram" else route["transport"]
        available = bool(route["recipient"]) and channel.get("state") == "active" and channel.get("transport") == expected
        if available:
            fresh = get_lead(c, job.lead_id)
            if (not valid(fresh) or (not certificate and primary_contact(fresh) != job.contact_id)):
                job.state, job.reason = "cancelled", "stage_or_contact_changed"
                db.commit()
                return
            if calendar or review:
                from app.reservation_reminders import recipient, first
                if certificate:
                    # The lead recipient is authoritative; never read the buyer's
                    # contact or reuse their messenger IDs as a destination.
                    if certificate_phone(fresh, calendar_settings) != phone:
                        job.state, job.reason = "cancelled", "certificate_recipient_changed"
                        db.commit()
                        return
                    if route["transport"] in DIRECT:
                        current_dest = phone_destination(phone, route["transport"])
                    else:
                        recipient_contact = c.get(f"/api/v4/contacts/{route['recipient_contact_id']}")
                        if not contact_matches_phone(recipient_contact, phone):
                            job.state, job.reason = "cancelled", "certificate_recipient_contact_changed"
                            db.commit()
                            return
                        social_id = first(recipient_contact, SOCIAL_FIELDS[route["transport"]]).strip()
                        current_dest = {"chatId": social_id} if social_id else None
                else:
                    contact = c.get(f"/api/v4/contacts/{job.contact_id}")
                    current_dest = (recipient(contact, route["transport"], r) if route["transport"] in DIRECT else
                        ({"chatId": first(contact, SOCIAL_FIELDS[route["transport"]]).strip()} if first(contact, SOCIAL_FIELDS[route["transport"]]).strip() else None))
                if current_dest != route["recipient"]:
                    job.state, job.reason = "cancelled", "recipient_changed"
                    db.commit()
                    return
            try:
                body = make_body(fresh, s)
            except ValueError as exc:
                job.state, job.reason, job.check_at = "blocked", str(exc)[:100], now + (timedelta(seconds=30) if calendar else timedelta(hours=1))
                db.commit()
                return
            current = (clock or utcnow)()
            if review:
                db.refresh(job)
                if current < aware(job.event_at) + timedelta(hours=1):
                    job.check_at = stage_due(142, aware(job.event_at), s)
                    db.commit()
                    return
            if dispatch(current) != current:
                job.check_at, job.state = dispatch(current), "ready"
                db.commit()
                return
        a = attempt_type(job_id=job.id, position=pos, transport=route["transport"],
            crm_message_id=(f"calendar-{job.lead_id}-{job.kind}-{job.target_date}-{pos}" if calendar else f"stage-{job.lead_id}-{job.status_id}-{pos}"))
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
                db.commit(); reconcile(db, now, job_type=job_type, attempt_type=attempt_type, dispatch=dispatch, settings=s)
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
            now = utcnow(); reconcile(db, now, settings=s)
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
    return {"mode": s.mode, "ready": s.live_ready(),
        "reviews": {"enabled": s.review_activated_at > 0, "activated_at": s.review_activated_at,
                    "delay_seconds": 3600, "respect_window": s.review_respect_window}, "calendar": {
        "mode": CalendarSettings().mode, "ready": CalendarSettings().live_ready() and s.live_ready(),
        "extra_enabled": CalendarSettings().extra_enabled,
        "free_hold_enabled": CalendarSettings().free_hold_enabled,
        "last_scan": calendar_scan, "jobs": [
            {"lead_id": j.lead_id, "kind": j.kind, "date": j.target_date.isoformat(), "due_at": aware(j.due_at).isoformat(), "state": j.state, "reason": j.reason}
            for j in db.scalars(select(CalendarJob).order_by(CalendarJob.id.desc()).limit(50))]}, "jobs": [
        {"lead_id": j.lead_id, "status_id": j.status_id, "state": j.state, "reason": j.reason}
        for j in db.scalars(select(StageJob).order_by(StageJob.id.desc()).limit(30))]}

# Calendar notifications share the proven transport state machine, but have
# separate durable keys: lead + scenario + appointment/return date.
class CalendarSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALENDARNOTIFY_", extra="ignore")
    mode: Literal["preview", "live"] = "preview"
    meeting_field: int = 3382311
    return_field: int = 3322895
    meeting_status: int = 76654894
    paid_status: int = 76674066
    rented_status: int = 76660194
    website_origin: str = ""
    extra_enabled: bool = False
    certificate_status: int = 76675038
    certificate_until_field: int = 3169911
    recipient_phone_field: int = 3830052
    wants_order_status: int = 78616338
    wants_visit_status: int = 77384722
    approximate_date_field: int = 3185681
    free_hold_enabled: bool = False
    hold_status: int = 77086494
    hold_field: int = 3810737
    free_hold_enum: int = 5452539
    hold_until_field: int = 3810739

    def live_ready(self):
        return self.mode == "live"


CALENDAR_RULES = {
    "free_hold_expired": ("hold_status", "hold_until_field", 1, 10, 0),
    "meeting_today": ("meeting_status", "meeting_field", 0, 10, 0),
    "meeting_followup": ("meeting_status", "meeting_field", 1, 10, 30),
    "rental_pickup": ("paid_status", "meeting_field", -1, 11, 0),
    "rental_return": ("rented_status", "return_field", 0, 10, 0),
    "certificate_month": ("certificate_status", "certificate_until_field", 0, 11, 0),
    "certificate_fortnight": ("certificate_status", "certificate_until_field", -14, 11, 0),
    "wants_order": ("wants_order_status", "approximate_date_field", 0, 10, 0),
    "wants_visit": ("wants_visit_status", "approximate_date_field", 0, 10, 0),
}
CERTIFICATE_KINDS = {"certificate_month", "certificate_fortnight"}
EXTRA_KINDS = CERTIFICATE_KINDS | {"wants_order", "wants_visit"}


def active_calendar_rules(settings):
    return {kind: rule for kind, rule in CALENDAR_RULES.items()
            if (settings.extra_enabled or kind not in EXTRA_KINDS)
            and (kind != "free_hold_expired" or settings.free_hold_enabled)}


def calendar_due(kind, target):
    _, _, offset, hour, minute = CALENDAR_RULES[kind]
    if kind == "certificate_month":
        year, month = (target.year - 1, 12) if target.month == 1 else (target.year, target.month - 1)
        day = min(target.day, month_calendar.monthrange(year, month)[1])
        due_date = date(year, month, day)
    else:
        due_date = target + timedelta(days=offset)
    return datetime.combine(due_date, datetime.min.time(), MSK).replace(
        hour=hour, minute=minute).astimezone(timezone.utc)


class CalendarJob(Base):
    __tablename__ = "calendar_notification_job"
    __table_args__ = (UniqueConstraint("lead_id", "kind", "target_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    status_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    target_date: Mapped[date] = mapped_column(Date)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    reason: Mapped[str] = mapped_column(String(100), default="")
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class CalendarAttempt(Base):
    __tablename__ = "calendar_notification_attempt"
    __table_args__ = (UniqueConstraint("job_id", "position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    transport: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(24), default="inflight")
    message_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    crm_message_id: Mapped[str] = mapped_column(String(120), unique=True)
    reason: Mapped[str] = mapped_column(String(100), default="")


TABLES.extend([CalendarJob.__table__, CalendarAttempt.__table__])


def calendar_date(lead, field):
    from app.reservation_reminders import values
    entries = values(lead, field)
    if len(entries) != 1:
        return None
    try:
        raw = entries[0]["value"]
        if isinstance(raw, bool):
            return None
        return datetime.fromtimestamp(int(raw), MSK).date()
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None


def free_hold_valid(lead, settings):
    from app.reservation_reminders import values
    entries = values(lead, settings.hold_field)
    # Exact, single CRM option; missing, paid or ambiguous values never qualify.
    return len(entries) == 1 and entries[0].get("enum_id") == settings.free_hold_enum


def calendar_valid(lead, job, settings):
    status, field, _, _, _ = CALENDAR_RULES[job.kind]
    return (job.kind in active_calendar_rules(settings)
            and lead.get("pipeline_id") == 9601214 and not lead.get("is_deleted")
            and lead.get("status_id") == getattr(settings, status)
            and calendar_date(lead, getattr(settings, field)) == job.target_date
            and (job.kind != "free_hold_expired" or free_hold_valid(lead, settings)))


def calendar_dispatch(job, now):
    due = aware(job.due_at)
    # Never roll a missed calendar reminder into another date.
    if now < due:
        return due
    return now


def sync_calendar(db, leads, settings, now):
    existing = {(j.lead_id, j.kind, j.target_date): j for j in db.scalars(select(CalendarJob))}
    found = set()
    for lead in leads:
        if lead.get("pipeline_id") != 9601214 or lead.get("is_deleted"):
            continue
        for kind, (status, field, offset, hour, minute) in active_calendar_rules(settings).items():
            if lead.get("status_id") != getattr(settings, status):
                continue
            if kind == "free_hold_expired" and not free_hold_valid(lead, settings):
                continue
            target = calendar_date(lead, getattr(settings, field))
            if target is None:
                continue
            due = calendar_due(kind, target)
            if due.astimezone(MSK).date() < now.astimezone(MSK).date():
                continue
            key = (lead["id"], kind, target)
            found.add(key)
            if key not in existing:
                j = CalendarJob(lead_id=lead["id"], status_id=lead["status_id"], kind=kind,
                    target_date=target, due_at=due, check_at=due)
                db.add(j)
                existing[key] = j
            elif existing[key].state == "cancelled":
                j = existing[key]
                if db.scalar(select(CalendarAttempt.id).where(CalendarAttempt.job_id == j.id).limit(1)) is None:
                    j.state, j.reason, j.plan, j.contact_id = "ready", "", None, None
                    j.check_at = due
    for key, j in existing.items():
        if j.state in {"ready", "blocked"} and key not in found:
            j.state, j.reason = "cancelled", "conditions_changed"
    db.commit()
    return {"scanned": len(leads), "eligible": len(found)}


def send_calendar(db, job, c, s, r, settings, now, *, clock=None):
    # Initial sends have a five-minute scheduler tolerance. No afternoon catch-up
    # at activation/restart. Confirmed failures may fall back during the same day.
    due = aware(job.due_at)
    attempts = list(db.scalars(select(CalendarAttempt).where(CalendarAttempt.job_id == job.id)))
    attempted = any(a.state != "skipped" for a in attempts)
    local = now.astimezone(MSK)
    if job.state not in {"ready", "blocked"}:
        return
    if local.date() != due.astimezone(MSK).date() or local.hour >= 20 or (not attempted and now > due + timedelta(minutes=5)):
        if now >= due:
            job.state, job.reason = "expired", "scheduled_window_passed"
            db.commit()
        return
    # Re-check actual clock before each POST as routing and CRM reads take time.
    def guarded_clock():
        stamp = (clock or utcnow)()
        loc = stamp.astimezone(MSK)
        if (loc.date() != due.astimezone(MSK).date() or loc.hour >= 20
                or (not attempted and stamp > due + timedelta(minutes=5))):
            raise ValueError("calendar_window_passed_before_post")
        return stamp
    send_job(db, job, c, s, r, now, clock=guarded_clock, calendar_settings=settings)


MEETING_PYAT = """Вас приветсвует Mansband👋🏻

Напоминаем, что сегодня у вас назначена примерка в нашем магазине по адресу:

г. Москва, ул. Пятницкая, д. 8

Парковка бесплатная, позвоните за 5 минут до приезда

Как добраться:
От м. Новокузнецкая идите вдоль ул. Пятницкая по направлению движения машин (около 3 минут).
Между «Core mio» и «INCANTO» зайдите в арку, затем поверните налево — железная дверь, 3 этаж.

Режим работы:
Будни: 12:00-22:00
Выходные: 11:00-22:00

Примерка проводится без записи в удобное для вас время🙌🏻

Телефон: 8 931 009 02 51"""
MEETING_BAUM = """Вас приветсвует Mansband👋🏻

Напоминаем, что сегодня у вас назначена примерка в нашем магазине по адресу:

г. Москва, Спартаковская пл., д. 14, стр. 2
Парковка бесплатная, позвоните за 5 минут до приезда.

Как добраться:
От м. Бауманская пройдите вдоль ул. Бауманская (около 5 минут).
Между 13 и 14 домами увидите чёрные ворота и шлагбаум, зайдите на территорию через крыльцо с надписью «Central Street».
Идите прямо до магазина Nº3105 (в конце, по правую сторону).


Режим работы:
Будни: 12:00-22:00
Выходные: 11:00-22:00

Примерка проводится без записи в удобное для вас время🙌🏻

Телефон: 8 931 009 02 51"""
MEETING_BOTH = """Вас приветсвует Mansband👋🏻

Напоминаем, что сегодня у вас назначена примерка! Ждем вас в любом магазине нашей сети, вся необходимая информация ниже👇🏻

Адрес №1:

г. Москва, ул. Пятницкая, д. 8

Парковка бесплатная, позвоните за 5 минут до приезда

Как добраться:
От м. Новокузнецкая идите вдоль ул. Пятницкая по направлению движения машин (около 3 минут).
Между «Core mio» и «INCANTO» зайдите в арку, затем поверните налево — железная дверь, 3 этаж.

Адрес №2:

г. Москва, Спартаковская пл., д. 14, стр. 2

Парковка бесплатная, позвоните за 5 минут до приезда.

Как добраться:
От м. Бауманская пройдите вдоль ул. Бауманская (около 5 минут).
Между 13 и 14 домами увидите чёрные ворота и шлагбаум, зайдите на территорию через крыльцо с надписью «Central Street».
Идите прямо до магазина Nº3105 (в конце, по правую сторону).

Режим работы магазинов:
Будни: 12:00-22:00
Выходные: 11:00-22:00

Примерка проводится без записи на любом адресе в удобное для вас время🙌🏻

Телефон: 8 931 009 02 51"""
RENTAL_PYAT = """Вас приветсвует Mansband!
Напоминаем, что забронированный для аренды костюм ждет вас завтра с 12:00 до 20:00, по адресу:

г. Москва, ул. Пятницкая, д. 8

Парковка бесплатная, позвоните за 2 минуты до приезда

Как добраться:
От м. Новокузнецкая идите вдоль ул. Пятницкая по направлению движения машин (около 3 минут).
Между «Core mio» и «INCANTO» зайдите в арку, затем поверните налево — железная дверь, 3 этаж.

Уточняем, что срок аренды 3 дня, включая день получения костюма🙌🏻 """
RENTAL_BAUM = """Вас приветствует магазин Mansband!

Напоминаем, что забронированный для аренды костюм ждет вас завтра с 12:00 до 20:00, по адресу:

г. Москва, Спартаковская пл., д. 14, стр. 2
Парковка бесплатная, позвоните за 2 минуты до приезда.

Как добраться:
От м. Бауманская пройдите вдоль ул. Бауманская (около 5 минут).
Между 13 и 14 домами увидите чёрные ворота и шлагбаум, зайдите на территорию через крыльцо с надписью «Central Street».
Идите прямо до магазина Nº3105 (в конце, по правую сторону).

Уточняем, что срок аренды 3 дня, включая день получения костюма🙌🏻 """
RETURN_PYAT = """Здравствуйте! Напоминаем, что сегодня - день возврата арендованного костюма, будем ждать вас в любое удобное время с 12 до 22:00.

Адрес: г. Москва, ул. Пятницкая, д. 8"""
RETURN_BAUM = """Здравствуйте! Напоминаем, что сегодня - день возврата арендованного костюма, будем ждать вас в любое удобное время с 12 до 22:00

Адрес:
г. Москва, Спартаковская пл., д. 14, стр.2"""


FREE_HOLD_EXPIRED = """Вас приветствует магазин Mansband!
Хотим предупредить, что бесплатный срок брони истёк.

Вы можете продлить бронь костюма на 2 недели, внеся аванс в размере 3000 ₽.
*залог учитывается при выкупе костюма."""


def render_calendar(lead, job, s):
    from app.reservation_reminders import values
    if job.kind == "free_hold_expired":
        return FREE_HOLD_EXPIRED
    if job.kind == "certificate_month":
        return CERTIFICATE_MONTH
    if job.kind == "certificate_fortnight":
        return CERTIFICATE_FORTNIGHT
    if job.kind == "wants_order":
        return WANTS_ORDER
    if job.kind == "wants_visit":
        return WANTS_VISIT
    if job.kind == "meeting_followup":
        return "Здравствуйте! Удалось ли посетить нас вчера, подобрали желаемый образ?🙌🏻"
    store = values(lead, s.store_field)
    enums = {v.get("enum_id") for v in store}
    if enums == {s.pyatnitskaya_enum}:
        return {"meeting_today": MEETING_PYAT, "rental_pickup": RENTAL_PYAT, "rental_return": RETURN_PYAT}[job.kind]
    if enums == {s.baumanskaya_enum}:
        return {"meeting_today": MEETING_BAUM, "rental_pickup": RENTAL_BAUM, "rental_return": RETURN_BAUM}[job.kind]
    if job.kind == "meeting_today" and (not store or enums == {4893873}):
        return MEETING_BOTH
    raise ValueError("missing_or_ambiguous_store")


calendar_scan = {}


def tick_calendar(s, settings):
    from app.reservation_reminders import Clients, ReminderSettings
    if not settings.live_ready() or not s.live_ready():
        return
    with get_engine().connect() as lock, get_session_factory()() as db:
        if not lock.execute(text("SELECT pg_try_advisory_lock(674001926)")).scalar():
            return
        c = None
        try:
            now = utcnow()
            reconcile(db, now, job_type=CalendarJob, attempt_type=CalendarAttempt, dispatch=lambda stamp: stamp)
            r = ReminderSettings(); c = Clients(db, r)
            if not calendar_scan.get("at") or now.timestamp() - calendar_scan["at"] >= 30:
                # Standard stage filters only: custom-field filtering is unavailable
                # in this account. Finish every page before modifying the queue.
                leads = []
                statuses = sorted({getattr(settings, rule[0]) for rule in active_calendar_rules(settings).values()})
                params = {"limit": 250, "with": "contacts"}
                for i, status in enumerate(statuses):
                    params[f"filter[statuses][{i}][pipeline_id]"] = 9601214
                    params[f"filter[statuses][{i}][status_id]"] = status
                for page in range(1, r.max_pages + 1):
                    data = c.get("/api/v4/leads", **params, page=page)
                    batch = data.get("_embedded", {}).get("leads", [])
                    leads.extend(batch)
                    if not data.get("_links", {}).get("next") and len(batch) < 250:
                        break
                else:
                    raise RuntimeError("incomplete_calendar_scan")
                result = sync_calendar(db, leads, settings, utcnow())
                calendar_scan.update(at=utcnow().timestamp(), **result)
            jobs = list(db.scalars(select(CalendarJob).where(CalendarJob.state.in_(["ready", "blocked"]),
                CalendarJob.check_at <= utcnow()).order_by(CalendarJob.check_at)))
            for job in jobs:
                try:
                    send_calendar(db, job, c, s, r, settings, utcnow())
                except Exception as exc:
                    db.rollback()
                    log.error("Calendar notification failed: %s", type(exc).__name__)
        finally:
            if c:
                c.close()
            lock.execute(text("SELECT pg_advisory_unlock(674001926)"))


async def calendar_worker(s):
    settings = CalendarSettings()
    while True:
        try:
            await asyncio.to_thread(tick_calendar, s, settings)
        except Exception as exc:
            log.error("Calendar worker failed: %s", type(exc).__name__)
        await asyncio.sleep(5)


def normalize_recipient_phone(raw):
    """Normalize one phone, including integral CRM numeric/float values.

    Russian local ten-digit numbers use country code 7. Existing international
    country codes are preserved. Multiple phones and arbitrary text are refused.
    """
    if raw is None or isinstance(raw, bool):
        raise ValueError("missing_or_invalid_recipient_phone")
    value = str(raw).strip()
    if not value or len(value) > 80 or '\n' in value or '\r' in value:
        raise ValueError("missing_or_invalid_recipient_phone")
    numeric = (isinstance(raw, (int, float, Decimal)) or
               re.fullmatch(r"\d+\.0+", value) or
               re.fullmatch(r"\d+(?:\.\d+)?[eE][+-]?\d+", value))
    if numeric:
        try:
            number = Decimal(value)
            if not number.is_finite() or number != number.to_integral_value() or not 0 < number < 10**15:
                raise ValueError("missing_or_invalid_recipient_phone")
            value = str(int(number))
        except InvalidOperation:
            raise ValueError("missing_or_invalid_recipient_phone") from None
    if not re.fullmatch(r"\+?[\d ()\-]+", value):
        raise ValueError("missing_or_invalid_recipient_phone")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = '7' + digits
    elif len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not 11 <= len(digits) <= 15 or digits[0] == '0' or len(set(digits)) == 1:
        raise ValueError("missing_or_invalid_recipient_phone")
    return digits


def certificate_phone(lead, settings):
    from app.reservation_reminders import values
    entries = values(lead, settings.recipient_phone_field)
    if len(entries) != 1:
        raise ValueError("missing_or_ambiguous_recipient_phone")
    return normalize_recipient_phone(entries[0].get('value'))


def phone_destination(phone, transport):
    return {'chatId': phone} if transport == 'whatsapp' else {'phone': phone}


def contact_matches_phone(contact, phone):
    for field in contact.get('custom_fields_values') or []:
        if field.get('field_code') != 'PHONE':
            continue
        for value in field.get('values') or []:
            try:
                if normalize_recipient_phone(value.get('value')) == phone:
                    return True
            except ValueError:
                continue
    return False


def certificate_plan(db, c, lead, stage, reminders, settings, routing=None):
    phone = certificate_phone(lead, settings)
    # Search by the recipient only. The deal's linked buyer is never used as a
    # fallback or as the source of Telegram/MAX usernames, IDs, or social chats.
    matches = {}
    for page in range(1, reminders.max_pages + 1):
        data = c.get('/api/v4/contacts', query=phone, limit=250, page=page)
        batch = data.get('_embedded', {}).get('contacts', [])
        for contact in batch:
            if contact_matches_phone(contact, phone):
                matches[contact['id']] = contact
        if not data.get('_links', {}).get('next') and len(batch) < 250:
            break
    else:
        raise ValueError('incomplete_recipient_contact_search')
    if len(matches) > 1:
        raise ValueError('ambiguous_recipient_history')
    if matches:
        contact = next(iter(matches.values()))
        plan = resolve_plan(db, c, lead, contact, stage, reminders, calendar=True, routing=routing)
        for route in plan:
            if route['transport'] in DIRECT:
                route['recipient'] = phone_destination(phone, route['transport'])
            else:
                route['recipient_contact_id'] = contact['id']
        return plan
    return [{'transport': transport, 'channelId': getattr(reminders, transport + '_channel'),
             'recipient': phone_destination(phone, transport)} for transport in (routing['order'] if routing else DIRECT)]


CERTIFICATE_MONTH = """Здравствуйте! Вас приветствует магазин мужских костюмов Mansband👋🏻

До окончания срока действия вашего подарочного сертификата остался 1 месяц🎁
Будем рады видеть вас в гостях в любом магазине нашей сети!

Отправляем всю необходимую информацию👇🏻

Адрес №1:
г. Москва, ул. Пятницкая, д. 8

Как добраться:
От м. Новокузнецкая идите вдоль ул. Пятницкая по направлению движения машин (около 3 минут).
Между «Core Mio» и «INCANTO» зайдите в арку, затем поверните налево — железная дверь, 3 этаж.

Адрес №2:
г. Москва, Спартаковская пл., д. 14, стр. 2

Как добраться:
От м. Бауманская пройдите вдоль ул. Бауманская (около 5 минут). Между 13 и 14 домами увидите чёрные ворота и шлагбаум — зайдите на территорию через крыльцо с надписью «Central Street».
Идите прямо до магазина №3105 (в конце, по правую сторону).

Режим работы:
Будни: 12:00–22:00
Выходные: 11:00–22:00
(Примерка без записи)

Парковка бесплатная, позвоните за 5 минуты до приезда.

Подскажите, когда планируете нас посетить?🙌🏻
"""
CERTIFICATE_FORTNIGHT = """Здравствуйте! Вас приветствует магазин мужских костюмов Mansband👋🏻

До окончания срока действия вашего подарочного сертификата осталось 2 НЕДЕЛИ 🎁
Будем рады видеть вас в гостях в любом магазине нашей сети!

Отправляем всю необходимую информацию 👇

Адрес №1:
г. Москва, ул. Пятницкая, д. 8

Как добраться:
От м. Новокузнецкая идите вдоль ул. Пятницкая по направлению движения машин (около 3 минут).
Между «Core Mio» и «INCANTO» зайдите в арку, затем поверните налево — железная дверь, 3 этаж.

Адрес №2:
г. Москва, Спартаковская пл., д. 14, стр. 2

Как добраться:
От м. Бауманская пройдите вдоль ул. Бауманская (около 5 минут). Между 13 и 14 домами увидите чёрные ворота и шлагбаум — зайдите на территорию через крыльцо с надписью «Central Street».
Идите прямо до магазина №3105 (в конце, по правую сторону).

Режим работы:
Будни: 12:00–22:00
Выходные: 11:00–22:00

Парковка бесплатная, позвоните за 5 минуты до приезда."""
WANTS_ORDER = """Вас приветствует магазин мужских костюмов Mansband!

Ранее вы интересовались нашим ассортиментом, подскажите, на какую дату готовы выбрать дату заказа?

При необходимости, с радостью проконсультируем вас по позициям в наличие еще раз! Очень ждем вашей обратной связи🙌🏻"""

WANTS_VISIT = """Вас приветствует магазин мужских костюмов Mansband!

Ранее вы интересовались нашим ассортиментом, подскажите, удалось ли запланировать время визита к нам?

При необходимости, с радостью проконсультируем вас по позициям в наличие еще раз! Очень ждем вашей обратной связи🙌🏻"""


REVIEW_PREFIX = """Спасибо большое, что доверились Mansband!

Нам особенно важно услышать ваше мнение. Если вам всё понравилось, оставьте пожалуйста отзыв — это лучший комплимент для нашей команды.
Ссылки для удобства представлены ниже:

"""
REVIEW_BAUM = REVIEW_PREFIX + "Магазин на Бауманской: https://yandex.ru/maps/org/mans_band/117054627870/reviews/?add-review=true&ll=37.678172%2C55.776188&z=16"
REVIEW_PYAT = REVIEW_PREFIX + "Магазин на Новокузнецкой: https://yandex.ru/maps/org/mans_band/138838694193/reviews/?add-review=true&ll=37.627553%2C55.744024&z=16"


def render_review(lead, settings):
    from app.reservation_reminders import values
    enums = {v.get('enum_id') for v in values(lead, settings.store_field)}
    if enums == {settings.baumanskaya_enum}:
        return REVIEW_BAUM
    if enums == {settings.pyatnitskaya_enum}:
        return REVIEW_PYAT
    raise ValueError('missing_or_ambiguous_review_store')
