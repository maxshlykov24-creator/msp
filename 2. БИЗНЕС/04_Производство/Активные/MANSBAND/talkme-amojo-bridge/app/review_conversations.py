"""Close the verified amoCRM conversation two days after a managed sale review."""
from datetime import datetime, timedelta

import httpx
from sqlalchemy import String, cast, or_, select

from app import message_templates as templates
from app.pipeline_guard import migration_blocked
from app.reservation_reminders import ReminderEvent, SUCCESS, primary_contact
from app.stage_notifications import ORIGINS

QUIET = timedelta(hours=48)
PREFIX = "review-talk-close-"


def review_anchor(db, job):
    """Prefer the provider request clock. WhatsApp webhook timestamps may be rounded."""
    attempts = db.scalars(select(templates.Attempt).where(templates.Attempt.job_id == job.id))
    proof = db.get(templates.Operation, f"outgoing-proof-{job.id}")
    sources = {a.message_id: a for a in attempts if a.message_id}
    if proof and proof.result.get("message_id"):
        sources.setdefault(proof.result["message_id"], None)
    anchors = []
    for message_id, attempt in sources.items():
        echo = db.get(templates.OutboundEvidence, message_id)
        if echo and (echo.status == "changed" or echo.body_hash != templates.body_hash(job.rendered)):
            continue
        statuses = set(db.scalars(select(ReminderEvent.status).where(ReminderEvent.message_id == message_id)))
        if echo:
            statuses.add(echo.status)
        if not statuses & SUCCESS:
            continue
        trace = db.get(templates.Operation, f"send-trace-{attempt.id}") if attempt else None
        started = trace.result.get("started_at") if trace else None
        if started:
            at = datetime.fromisoformat(started)
            anchors.append((int(at.timestamp()), attempt.transport, 5))
        elif echo:
            width = 62 if echo.transport == "whatsapp" else 5
            anchors.append((int(templates.aware(echo.at).timestamp()), echo.transport, width))
    return anchors[0] if len(anchors) == 1 else None


def close_candidate(db, c, job, stamp, *, apply=False):
    """Return (reason, next check, talk id). A zero next check is terminal."""
    ts = int(stamp.timestamp())
    tomorrow = ts + 86400
    anchor = review_anchor(db, job)
    if not anchor:
        return "unconfirmed_message", tomorrow, None
    at, transport, width = anchor
    due = at + int(QUIET.total_seconds())
    if ts < due:
        return "within_48h", due, None
    sale = c.get(f"/api/v4/leads/{job.lead_id}", **{"with": "contacts"})
    contact = primary_contact(sale)
    if migration_blocked(sale) or not contact or sale.get("status_id") != 142:
        return "sale_not_closed_or_contact_changed", tomorrow, None
    events = templates.all_pages(c, "/api/v4/events", "events", **{
        "filter[entity]": "contact", "filter[entity_id]": contact,
        "filter[type]": "outgoing_chat_message",
        "filter[created_at][from]": at - 5, "filter[created_at][to]": at + width,
    })
    matches = set()
    for event in events:
        if event.get("type") != "outgoing_chat_message" or event.get("entity_type") != "lead":
            continue
        for value in event.get("value_after") or []:
            message = value.get("message") or {}
            if message.get("talk_id") and ORIGINS.get(message.get("origin")) == transport:
                matches.add((message["talk_id"], event["entity_id"]))
    if len(matches) != 1:
        return "no_unique_conversation", tomorrow, None
    talk_id, owner = matches.pop()
    if owner != job.lead_id:
        child = c.get(f"/api/v4/leads/{owner}", **{"with": "contacts,source"})
        if (primary_contact(child) != contact or migration_blocked(child)
                or templates.review_that_opened_lead(db, c, child, stamp) != job.lead_id):
            return "other_sale", 0, talk_id
        if child.get("status_id") not in (142, 143):
            return "technical_lead_still_open", tomorrow, talk_id
    talk_url = f"/api/v4/talks/{talk_id}"
    talk = c.get(talk_url)
    if (talk.get("talk_id"), talk.get("entity_type"), talk.get("entity_id"), talk.get("contact_id")) != (
            talk_id, "lead", owner, contact):
        return "conversation_binding_changed", tomorrow, talk_id
    if talk.get("status") == "closed":
        return "closed", 0, talk_id
    if talk.get("status") != "in_work" or talk.get("is_read") is not True or not talk.get("updated_at"):
        return "unread_or_in_progress", ts + 3600, talk_id
    quiet_until = max(at, talk["updated_at"]) + int(QUIET.total_seconds())
    if quiet_until > ts:
        return "recent_activity", quiet_until, talk_id
    if not apply:
        return "would_close", ts + 60, talk_id
    result = c.amo.post(talk_url + "/close", json={"force_close": True})
    if result.status_code not in (202, 422):
        result.raise_for_status()
        raise ValueError("Unexpected response to closing amoCRM conversation")
    if c.get(talk_url).get("status") == "closed":
        return "closed", 0, talk_id
    return "awaiting_crm", ts + 300, talk_id


def sweep(db, c, stamp):
    """Run under the template worker lock; page the backlog without delaying sends."""
    op = templates.Operation
    job = templates.Job
    eligible = select(job).outerjoin(op, op.id == PREFIX + cast(job.id, String)).where(
        job.rule_id == "legacy-review", job.state.in_(("sent", "delivered")),
        job.due_at <= stamp - QUIET,
        or_(op.id.is_(None), (op.state == "waiting") &
            (op.result["next_check"].as_integer() <= int(stamp.timestamp()))),
    ).order_by(job.due_at, job.id).limit(3)
    for item in list(db.scalars(eligible)):
        try:
            reason, retry, talk_id = close_candidate(db, c, item, stamp, apply=True)
        except (httpx.HTTPError, ValueError) as exc:
            templates.log.warning("Review talk close deferred for job=%s: %s", item.id, type(exc).__name__)
            reason, retry, talk_id = "crm_check_failed", int(stamp.timestamp()) + 900, None
        result = {"reason": reason, "talk_id": talk_id, "next_check": retry}
        key = PREFIX + str(item.id)
        record = db.get(op, key)
        if record is None:
            record = op(id=key)
            db.add(record)
        record.digest, record.result = templates.digest(result), result
        record.state = "waiting" if retry else "done"
        db.commit()
