"""CRM checkbox -> durable per-deal Pleep command; only verified bindings apply."""
from __future__ import annotations

import logging
import hashlib
import hmac
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import PleepControlState, PleepThreadBinding
from app.pleep_control import PleepControlClient, PleepControlError, PleepControlUncertain

log = logging.getLogger("pleep_bridge")


def verify_signature(raw, timestamp, signature, secret, now=None):
    try:
        if not secret or abs((time.time() if now is None else now) - int(timestamp)) > 300:
            return False
    except (TypeError, ValueError):
        return False
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + digest, signature or "")


def register_native_binding(ctx, payload):
    thread_id, lead_id = payload["thread_id"], int(payload["lead_id"])
    remote = PleepControlClient(settings.pleep_public_api_key, settings.pleep_assistant_id)
    try:
        actual = remote.get_thread(thread_id)
    finally:
        remote.close()
    lead = ctx.client.get_lead(lead_id)
    if not lead:
        raise ValueError("Native CRM lead not found")
    from app.identity import contact_phones, normalize_phone
    phone = normalize_phone(actual.get("phone"))
    if phone:
        phones = set()
        for ref in (lead.get("_embedded") or {}).get("contacts", []):
            phones.update(contact_phones(ctx.client.get_contact(int(ref["id"]))))
        if phone not in phones:
            raise ValueError("Native thread/CRM phone mismatch")
    row = ctx.s.get(PleepThreadBinding, thread_id)
    if row and row.lead_id != lead_id:
        raise ValueError("Existing Pleep binding conflict; manual review required")
    if row is None:
        ctx.s.add(PleepThreadBinding(thread_id=thread_id, lead_id=lead_id))
    return {"action": "pleep.binding_verified", "lead_id": lead_id}


def bind_unique_phone(client, remote, thread_id, phone):
    """Never guess among duplicate contacts or parallel open deals."""
    from app.identity import find_contacts_by_phone, normalize_phone
    phone = normalize_phone(phone)
    if not phone:
        return False
    contacts = find_contacts_by_phone(client, phone)
    if len(contacts) != 1:
        return False
    contact = client.get_contact(int(contacts[0]["id"]), with_="leads")
    candidates, open_candidates = [], []
    for ref in (contact.get("_embedded") or {}).get("leads", []):
        lead = client.get_lead(int(ref["id"]))
        if lead and lead.get("pipeline_id") in (settings.pipeline_id, settings.assembly_pipeline_id):
            candidates.append(int(lead["id"]))
            if lead.get("status_id") not in (settings.status_won, settings.status_lost):
                open_candidates.append(int(lead["id"]))
    # A unique closed deal still needs manual robot control on that stage.
    # Never choose arbitrarily among several historical deals.
    if open_candidates:
        candidates = open_candidates
    if len(candidates) != 1:
        return False
    actual = remote.get_thread(thread_id)
    if normalize_phone(actual.get("phone")) != phone:
        return False
    with session_scope() as s:
        row = s.get(PleepThreadBinding, thread_id)
        if row:
            return row.lead_id == candidates[0]
        s.add(PleepThreadBinding(thread_id=thread_id, lead_id=candidates[0]))
    return True


def discover_waba():
    """Bounded background backfill, separate from the telephony event worker."""
    if not settings.enable_pleep_control or not settings.pleep_waba_bot_id:
        return
    with session_scope() as s:
        from app.rollout import current_flags
        if current_flags(s).shadow:
            return
        row = s.get(PleepControlState, 0)
        checkpoint = dict(row.data) if row else {"after": "2026-01-01T00:00:00Z"}
        known = set(s.scalars(select(PleepThreadBinding.thread_id)).all())
    remote = PleepControlClient(settings.pleep_public_api_key, settings.pleep_assistant_id)
    from app.kommo.client import KommoClient
    try:
        with KommoClient() as client:
            body = {"bot_id": settings.pleep_waba_bot_id, "channel": "waba_coexistence",
                    "created_after": checkpoint["after"], "limit": 10, "messages_limit": 1}
            if checkpoint.get("cursor"):
                body["cursor"] = checkpoint["cursor"]
            result = remote._post("messages/get-threads", body)
            for t in result.get("threads", []):
                if t["thread_id"] not in known:
                    bound = bind_unique_phone(client, remote, t["thread_id"], t.get("phone"))
                    log.info("Pleep discovery thread=%s bound=%s", t["thread_id"], bound)
            if result.get("has_more"):
                checkpoint["cursor"] = result["next_cursor"]
            else:
                # Re-scan the full interval so an ambiguous/new contact resolved later
                # is not permanently skipped. No writes to existing bindings.
                checkpoint.pop("cursor", None)
            _save(0, checkpoint)
    finally:
        remote.close()


def start_background(client):
    if settings.enable_pleep_control:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="UTC")
        if settings.pleep_waba_bot_id:
            scheduler.add_job(discover_waba, "interval", seconds=60, max_instances=1,
                              coalesce=True, next_run_time=datetime.now(timezone.utc))
        scheduler.add_job(run_sync, "interval", args=[client],
                          seconds=settings.pleep_control_interval_sec,
                          max_instances=1, coalesce=True)
        scheduler.start()
        return scheduler


def checkbox(lead):
    for f in lead.get("custom_fields_values") or []:
        if f["field_id"] == settings.pleep_control_field:
            values = f.get("values") or []
            return bool(values and values[0].get("value") is True)
    return None


def intent(data, enabled, source, timestamp):
    """Manual edits supersede old events; a replay cannot overwrite a newer resume."""
    d = dict(data)
    if source != "manual" and timestamp <= d.get("cutoff", 0):
        return d
    if source == "manual":
        d["cutoff"] = timestamp
    d["pending"] = {"enabled": enabled, "source": source, "at": timestamp,
                    "attempts": 0, "retry_at": 0}
    return d


def queue_call_pause(ctx, lead_id, uniqueid, contact_id=None):
    if not settings.enable_pleep_control or ctx.shadow or not lead_id:
        return
    targets = set()
    if ctx.s.scalar(select(PleepThreadBinding).where(PleepThreadBinding.lead_id == lead_id)):
        targets.add(int(lead_id))
    if contact_id:
        # Calls may resolve to an assembly deal while the chat is on the sales deal.
        # Match the exact CRM contact ID, never a shared/fuzzy telephone guess.
        for state in ctx.s.scalars(select(PleepControlState)).all():
            if int(contact_id) in state.data.get("contact_ids", []):
                targets.add(state.lead_id)
    # Inbox arrival, not processing time: backlog cannot undo a newer manual resume.
    from app.models import InboxEvent
    event = ctx.s.get(InboxEvent, ctx.inbox_id) if ctx.inbox_id else None
    timestamp = event.created_at.timestamp() if event else time.time()
    for lid in targets:
        row = ctx.s.scalar(select(PleepControlState).where(
            PleepControlState.lead_id == lid).with_for_update())
        if row is None:
            row = PleepControlState(lead_id=lid, data={})
            ctx.s.add(row)
        if row.data.get("last_call") == uniqueid:
            continue
        d = intent(row.data, False, "call", timestamp)
        d["last_call"] = uniqueid
        row.data = d


def _save(lead_id, data):
    with session_scope() as s:
        row = s.scalar(select(PleepControlState).where(
            PleepControlState.lead_id == lead_id).with_for_update())
        if row is None:
            row = PleepControlState(lead_id=lead_id)
            s.add(row)
        old = dict(row.data or {})
        merged = dict(data)
        # A call can finish while Pleep's API request is in flight. Keep that
        # newer durable request instead of overwriting it with an old readback.
        old_pending = old.get("pending") or {}
        baseline = max((merged.get("pending") or {}).get("at", 0),
                       merged.get("cutoff", 0), merged.get("completed_at", 0))
        if old_pending.get("at", 0) > baseline:
            merged["pending"] = old_pending
        if old.get("last_call"):
            merged["last_call"] = old["last_call"]
        merged["human_ts"] = max(old.get("human_ts", 0), merged.get("human_ts", 0))
        row.data = merged


def _fields(client, lead_id, enabled, status, *, expected):
    # Do not overwrite a manual edit made while the remote request was in flight.
    fresh = client.get_lead(lead_id)
    value = checkbox(fresh)
    if (value is not None if expected is None else bool(value) != expected):
        return False
    client.patch(f"/leads/{lead_id}", {"custom_fields_values": [
        {"field_id": settings.pleep_control_field, "values": [{"value": enabled}]},
        {"field_id": settings.pleep_control_status_field, "values": [{"value": status}]},
    ]})
    readback = client.get_lead(lead_id)
    # Kommo omits unchecked checkbox fields from custom_fields_values entirely.
    if bool(checkbox(readback)) is not enabled:
        raise RuntimeError("CRM checkbox readback mismatch")
    return True


def sync_one(client, remote, lead_id, threads):
    with session_scope() as s:
        row = s.get(PleepControlState, lead_id)
        d = dict(row.data) if row else {}
    lead = client.get_lead(lead_id)
    contact_ids = [int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts", [])]
    if "last_flag" in d and d.get("contact_ids") != contact_ids:
        d["contact_ids"] = contact_ids
        _save(lead_id, d)
    else:
        d["contact_ids"] = contact_ids
    flag = checkbox(lead)
    modes = [remote.get_thread(t)["message_handler"] for t in threads]
    actual = all(m == "AI" for m in modes)
    now = time.time()
    if "last_flag" not in d:
        # Bootstrap by observing; never turn on a robot because a new field is blank.
        # An existing operator pause must become durable before showing OFF in CRM.
        if not actual:
            for t in threads:
                remote.set_enabled(t, False)
        if _fields(client, lead_id, actual, "Включён" if actual else "Выключен",
                   expected=flag):
            _save(lead_id, {**d, "last_flag": actual, "cutoff": now,
                            "hard_off_confirmed": not actual})
        return
    flag = bool(flag)
    if flag != d["last_flag"]:
        d = intent(d, flag, "manual", now)
        d["last_flag"] = flag
        _save(lead_id, d)  # Persist intent BEFORE the network mutation.
    if not d.get("pending"):
        if actual != d["last_flag"] or (not d["last_flag"] and not d.get("hard_off_confirmed")):
            # AI->operator is native human takeover; OFF also survives auto-return.
            d = intent(d, False, "pleep_takeover", now)
            _save(lead_id, d)
        else:
            if d.get("error") and _fields(client, lead_id, actual,
                    "Включён" if actual else "Выключен", expected=flag):
                d.pop("error", None)
                _save(lead_id, d)
            return
    pending = d["pending"]
    if pending.get("blocked") or pending.get("retry_at", 0) > now:
        return
    enabled = pending["enabled"]
    try:
        if pending.get("confirmed"):
            if actual != enabled:
                raise PleepControlError("Pleep changed after confirmation; manual review required")
        elif pending.get("uncertain") and enabled:
            if not actual:
                raise PleepControlError("Uncertain resume requires a new manual command")
            # Do not replay a resume that might already have answered a client.
        else:
            for t in threads:
                remote.set_enabled(t, enabled, resume_pending_ack=enabled)
        d["pending"] = {**pending, "confirmed": True}
        _save(lead_id, d)  # CRM failure must not replay an already confirmed resume.
        if _fields(client, lead_id, enabled, "Включён" if enabled else "Выключен",
                   expected=flag):
            d["last_flag"] = enabled
            d["hard_off_confirmed"] = not enabled
            d["completed_at"] = pending["at"]
            d.pop("pending", None)
            d.pop("error", None)
    except PleepControlError as exc:
        pending = dict(pending)
        pending["attempts"] += 1
        pending["uncertain"] = isinstance(exc, PleepControlUncertain)
        pending["retry_at"] = now + min(300, 15 * 2 ** min(pending["attempts"], 4))
        pending["blocked"] = pending["attempts"] >= 5 or not isinstance(exc, PleepControlUncertain)
        d["pending"] = pending
        d["error"] = str(exc)
        client.patch(f"/leads/{lead_id}", {"custom_fields_values": [{
            "field_id": settings.pleep_control_status_field,
            "values": [{"value": "Ошибка управления Pleep. Состояние не подтверждено"}],
        }]})
        log.warning("Pleep control lead=%s error=%s", lead_id, exc)
    _save(lead_id, d)


def run_sync(client):
    if (not settings.enable_pleep_control
            or not settings.pleep_control_field or not settings.pleep_control_status_field):
        return
    with session_scope() as s:
        from app.rollout import current_flags
        if current_flags(s).shadow:
            return
        groups = {}
        for binding in s.scalars(select(PleepThreadBinding)).all():
            groups.setdefault(binding.lead_id, []).append(binding.thread_id)
    remote = PleepControlClient(settings.pleep_public_api_key, settings.pleep_assistant_id)
    try:
        # Kommo's global event stream is required; per-lead events omit chat events.
        # Bot/integration messages (created_by=0) never count as a human response.
        try:
            events = list(client.paginate("/events", "events", params={
                "filter[type]": "outgoing_chat_message",
                "filter[created_at][from]": int(time.time()) - 300, "limit": 250}, max_pages=5))
        except Exception:
            log.exception("Pleep human-message scan failed; manual control continues")
            events = []
        for event in events:
            lid = int(event.get("entity_id") or 0)
            if (event.get("entity_type") != "lead" or lid not in groups
                    or int(event.get("created_by") or 0) not in settings.user_to_ext):
                continue
            ts = int(event.get("created_at") or 0)
            with session_scope() as s:
                row = s.scalar(select(PleepControlState).where(
                    PleepControlState.lead_id == lid).with_for_update())
                if row and ts > max(row.data.get("human_ts", 0), row.data.get("cutoff", 0)):
                    d = intent(row.data, False, "human_message", ts)
                    d["human_ts"] = ts
                    row.data = d
        for lead_id, threads in groups.items():
            try:
                sync_one(client, remote, lead_id, threads)
            except Exception:
                log.exception("Pleep control sync failed lead=%s", lead_id)
                try:
                    client.patch(f"/leads/{lead_id}", {"custom_fields_values": [{
                        "field_id": settings.pleep_control_status_field,
                        "values": [{"value": "Ошибка связи Pleep. Флажок не подтверждён"}],
                    }]})
                    with session_scope() as s:
                        row = s.get(PleepControlState, lead_id)
                        data = dict(row.data) if row else {}
                    _save(lead_id, {**data, "error": "remote sync failed"})
                except Exception:
                    log.exception("Pleep control error reporting failed lead=%s", lead_id)
    finally:
        remote.close()
