"""Versioned messenger rules. Dormant executor until an explicit, audited cutover.

The existing email service is deliberately outside this module. No provider POST
is retried after an uncertain outcome; attempt claims are committed first.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional, Union

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.database import get_engine, get_session_factory

log = logging.getLogger(__name__)
LOCK = 674001929


def now():
    return datetime.now(timezone.utc)


def aware(t):
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TEMPLATES_", extra="ignore")
    executor: Literal["preview", "live"] = "preview"
    # Fail closed unless migration was explicitly allowed by the operator.
    allow_cutover: bool = False


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: int = Field(gt=0)
    op: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte", "empty", "filled"]
    value: Optional[Union[str, int, float, bool]] = None


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["date", "stage"] = "date"
    field: Optional[int] = Field(default=None, gt=0)
    offset: int = Field(default=0, ge=-3660, le=3660)
    unit: Literal["days", "weeks", "months"] = "days"
    time: str = Field(default="10:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    delay_minutes: int = Field(default=0, ge=0, le=525600)
    repeat: Literal["each_entry", "once"] = "each_entry"
    @model_validator(mode="after")
    def date_field(self):
        if self.type == "date" and self.field is None:
            raise ValueError("Выбери поле даты")
        return self


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["text", "certificate"] = "text"
    certificate_pdf: dict = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=150)
    enabled: bool = False
    pipelines: list[int] = Field(default_factory=lambda: [9601214], min_length=1)
    statuses: list[int] = Field(default_factory=list)
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list, max_length=30)
    trigger: Trigger
    recipient: Literal["contact", "field"] = "contact"
    phone_field: Optional[int] = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=10000)
    variants: dict[str, str] = Field(default_factory=dict)
    store_field: int = 3158921
    window_start: int = Field(default=10, ge=0, le=23)
    window_end: int = Field(default=20, ge=1, le=24)
    # Existing stage rules preserve their historical outside-window behavior.
    late_policy: Literal["skip", "same_day", "legacy_stage"] = "skip"
    legacy_next_day: bool = False
    @model_validator(mode="after")
    def complete(self):
        if self.action=="certificate":
            if self.pipelines!=[9601214] or self.statuses!=[76675038] or self.phone_field!=3830052 or self.trigger.field!=3829442 or self.trigger.type!="date" or self.trigger.offset!=0:
                raise ValueError("Выдача сертификата использует фиксированные поля оплаты и получателя")
        if self.window_start >= self.window_end:
            raise ValueError("Начало окна должно быть раньше окончания")
        if self.recipient == "field" and not self.phone_field:
            raise ValueError("Выбери поле телефона получателя")
        if self.trigger.type == "stage" and not self.statuses:
            raise ValueError("Для перехода выбери этапы")
        return self


class Routing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[Literal["telegram", "whatsapp", "max"]] = Field(default_factory=lambda: ["telegram", "whatsapp", "max"])
    previous_first: bool = True
    social_fallback: bool = True
    @model_validator(mode="after")
    def unique(self):
        if len(self.order) != 3 or set(self.order) != {"telegram", "whatsapp", "max"}:
            raise ValueError("Укажи каждый мессенджер ровно один раз")
        return self


class Document(Base):
    __tablename__ = "message_template_document"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int] = mapped_column(Integer, default=0)
    draft: Mapped[dict] = mapped_column(JSON)
    active: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    checked: Mapped[str] = mapped_column(String(64), default="")
    reviewed_overlaps: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_routing: Mapped[str] = mapped_column(String(64), default="")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class History(Base):
    __tablename__ = "message_template_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "message_template_job"
    __table_args__ = (UniqueConstraint("rule_id", "lead_id", "occurrence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    occurrence: Mapped[str] = mapped_column(String(120))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    reason: Mapped[str] = mapped_column(String(150), default="")
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    plan: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    recipient_key: Mapped[str] = mapped_column(String(64), default="")
    rendered: Mapped[str] = mapped_column(String(10000), default="")


class Attempt(Base):
    __tablename__ = "message_template_attempt"
    __table_args__ = (UniqueConstraint("job_id", "position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    transport: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(24))
    message_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    crm_id: Mapped[str] = mapped_column(String(150), unique=True)
    reason: Mapped[str] = mapped_column(String(150), default="")


class Operation(Base):
    __tablename__ = "message_template_operation"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict] = mapped_column(JSON, default=dict)


TABLES = [Document.__table__, History.__table__, Job.__table__, Attempt.__table__, Operation.__table__]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def entries(lead, field):
    from app.reservation_reminders import values
    return values(lead, field)


def condition(lead, c):
    vals = entries(lead, c.field)
    filled = any(v.get("value") not in (None, "") or v.get("enum_id") is not None for v in vals)
    if c.op == "empty": return not filled
    if c.op == "filled": return filled
    if not filled: return False
    eq = any((v.get("enum_id") == c.value if "enum_id" in v and isinstance(c.value, int) else v.get("value") == c.value) for v in vals)
    if c.op == "eq": return eq
    if c.op == "ne": return not eq
    if c.op == "contains": return any(str(c.value).casefold() in str(v.get("value", "")).casefold() for v in vals)
    try:
        if len(vals) != 1: return False
        a, b = float(vals[0]["value"]), float(c.value)
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[c.op]
    except (ValueError, TypeError, KeyError): return False


def matches(lead, r):
    if lead.get("is_deleted") or lead.get("pipeline_id") not in r.pipelines: return False
    if r.statuses and lead.get("status_id") not in r.statuses: return False
    if not r.statuses and lead.get("status_id") in (142, 143): return False
    checks = [condition(lead, c) for c in r.conditions]
    return (all(checks) if r.match == "all" else any(checks)) if checks else True


def date_due(lead, r):
    from app.stage_notifications import calendar_date
    from app.reservation_reminders import MSK
    target = calendar_date(lead, r.trigger.field)
    if target is None: raise ValueError("Не заполнена однозначная дата")
    t = r.trigger
    if t.unit == "months":
        m = target.year * 12 + target.month - 1 + t.offset
        y, month = divmod(m, 12); month += 1
        target = date(y, month, min(target.day, calendar.monthrange(y, month)[1]))
    else: target += timedelta(days=t.offset * (7 if t.unit == "weeks" else 1))
    hour, minute = map(int, t.time.split(":"))
    return datetime(target.year, target.month, target.day, hour, minute, tzinfo=MSK).astimezone(timezone.utc)


def stage_due(stamp, r):
    from app.reservation_reminders import MSK
    due = stamp + timedelta(minutes=r.trigger.delay_minutes)
    local = due.astimezone(MSK)
    if r.window_start <= local.hour < r.window_end: return due
    opening = local.replace(hour=r.window_start, minute=0, second=0, microsecond=0)
    if r.legacy_next_day or local.hour >= r.window_end: opening += timedelta(days=1)
    return opening.astimezone(timezone.utc)


def render(lead, r):
    from app.stage_notifications import calendar_date
    body = r.content
    if r.variants:
        vals = entries(lead, r.store_field)
        key = str(vals[0].get("enum_id")) if len(vals) == 1 else "default"
        body = r.variants.get(key, r.variants.get("default", ""))
        if not body: raise ValueError("Не определён магазин для шаблона")
    def marker(match):
        key = match.group(1)
        if key == "lead.id": return str(lead["id"])
        if key == "lead.price":
            if lead.get("price") is None: raise ValueError("Не заполнен бюджет сделки")
            return str(lead["price"])
        m = re.fullmatch(r"lead\.(field|date)\.(\d+)", key)
        if not m: raise ValueError("Неизвестная переменная: " + key)
        if m[1] == "date":
            d = calendar_date(lead, int(m[2]))
            if d is None: raise ValueError("Не заполнена дата переменной")
            return d.strftime("%d.%m.%Y")
        vals = entries(lead, int(m[2]))
        if len(vals) != 1 or vals[0].get("value") in (None, ""): raise ValueError("Не заполнено поле переменной")
        return str(vals[0]["value"])
    body = re.sub(r"{{\s*([^{}]+?)\s*}}", marker, body)
    if not body.strip() or "{{" in body or "}}" in body: raise ValueError("Некорректный текст или переменная")
    return body


def all_pages(c, path, key, **params):
    result = []
    for page in range(1, 101):
        d = c.get(path, page=page, limit=250, **params)
        rows = d.get("_embedded", {}).get(key, []); result.extend(rows)
        if not d.get("_links", {}).get("next") and len(rows) < 250: return result
    raise ValueError("Справочник или выборка получены не полностью")


def catalog(c):
    if not hasattr(c,"_templates_catalog"):
        c._templates_catalog={"pipelines": all_pages(c, "/api/v4/leads/pipelines", "pipelines"),
            "fields": all_pages(c, "/api/v4/leads/custom_fields", "custom_fields")}
    return c._templates_catalog


def validate_catalog(r, cat):
    fields = {x["id"]: x for x in cat["fields"]}
    pipelines = {p["id"]: p for p in cat["pipelines"]}
    for p in r.pipelines:
        if p not in pipelines: raise ValueError("Воронка удалена или недоступна")
    available = {s["id"] for p in r.pipelines for s in pipelines[p].get("_embedded", {}).get("statuses", [])}
    if not set(r.statuses) <= available: raise ValueError("Этап не принадлежит выбранным воронкам")
    for c in r.conditions:
        f = fields.get(c.field)
        if not f: raise ValueError("Поле условия удалено")
        if f["type"] in ("select", "multiselect", "radiobutton") and c.op not in ("empty", "filled"):
            if c.op not in ("eq", "ne") or c.value not in {x["id"] for x in f.get("enums") or []}:
                raise ValueError("Значение списка удалено или некорректно")
        if c.op in ("gt", "gte", "lt", "lte") and f["type"] not in ("numeric", "date", "date_time", "price"):
            raise ValueError("Сравнение недоступно для этого типа поля")
    if r.trigger.type == "date" and fields.get(r.trigger.field, {}).get("type") not in ("date", "date_time"):
        raise ValueError("Выбранное поле не является датой")
    if r.recipient == "field" and r.phone_field not in fields: raise ValueError("Поле телефона удалено")
    if r.variants:
        store=fields.get(r.store_field)
        if not store:raise ValueError("Поле магазина удалено")
        keys={str(x["id"]) for x in store.get("enums") or []}|{"default"}
        if not set(r.variants)<=keys:raise ValueError("Вариант ссылается на удалённый магазин")
    for body in [r.content, *r.variants.values()]:
        if not body.strip():raise ValueError("Текст варианта пустой")
        static=re.sub(r"{{[^{}]+}}","",body)
        if "{{" in static or "}}" in static:raise ValueError("Незакрытая переменная в тексте")
        for key in re.findall(r"{{\s*([^{}]+?)\s*}}", body):
            if key in ("lead.id", "lead.price"): continue
            m = re.fullmatch(r"lead\.(field|date)\.(\d+)", key)
            if not m or int(m[2]) not in fields: raise ValueError("Неизвестная переменная: " + key)
            if m[1] == "date" and fields[int(m[2])]["type"] not in ("date", "date_time"):
                raise ValueError("Переменная даты ссылается не на дату")


def scan_leads(c, r):
    if not hasattr(c,"_templates_scans"):c._templates_scans={}
    cache_key=(tuple(sorted(r.pipelines)),tuple(sorted(r.statuses)))
    if cache_key in c._templates_scans:return c._templates_scans[cache_key]
    leads = []
    pipelines={p["id"]:p for p in catalog(c)["pipelines"]}
    for pipeline in r.pipelines:
        available={x["id"] for x in pipelines.get(pipeline,{}).get("_embedded",{}).get("statuses",[])}
        statuses=sorted(set(r.statuses)&available if r.statuses else available-{142,143})
        if not statuses:continue
        params = {"with": "contacts"}
        for i, status in enumerate(statuses):
            params[f"filter[statuses][{i}][pipeline_id]"] = pipeline
            params[f"filter[statuses][{i}][status_id]"] = status
        leads.extend(all_pages(c, "/api/v4/leads", "leads", **params))
    result=list({x["id"]: x for x in leads}.values())
    c._templates_scans[cache_key]=result
    return result


def make_plan(db, c, lead, r, routing):
    from app.stage_notifications import resolve_plan, StageSettings, CalendarSettings, certificate_plan, certificate_phone
    from app.reservation_reminders import primary_contact, ReminderSettings, recipient, first
    s, rs = StageSettings(), ReminderSettings()
    if r.recipient == "field":
        cs = CalendarSettings(recipient_phone_field=r.phone_field)
        phone = certificate_phone(lead, cs)
        plan = certificate_plan(db, c, lead, s, rs, cs, routing=routing)
        return plan, digest(["phone", phone, [(p["transport"],p["recipient"],p.get("recipient_contact_id")) for p in plan if p["transport"] in {"instagram","avito"}]])
    cid = primary_contact(lead)
    if not cid: raise ValueError("Не определён основной контакт")
    contact = c.get(f"/api/v4/contacts/{cid}")
    plan = resolve_plan(db, c, lead, contact, s, rs, calendar=True, routing=routing)
    return plan, digest(["contact", cid, [(t,recipient(contact,t,rs)) for t in ("telegram","whatsapp","max")],
        [(t,first(contact,f)) for t,f in (("instagram",3816137),("avito",3642125))]])


def snapshot(db, doc):
    route = db.get(Document, "routing")
    return {"rule": doc.active, "version": doc.published_version,
            "routing": route.active or Routing().model_dump()}


def audit(db, d, actor, action, payload):
    db.add(History(doc_id=d.id, version=d.version, actor=actor[:150], action=action, payload=payload))


def seed(db, pipelines=None):
    """One-time import of definitions, without enabling dormant features."""
    from app import stage_notifications as st
    from app.reservation_reminders import ReminderSettings, TEMPLATE
    if db.get(Document, "routing") is None:
        data = Routing().model_dump()
        db.add(Document(id="routing", kind="routing", draft=data, active=data, published_version=1))
    cs, ss, rs = st.CalendarSettings(), st.StageSettings(), ReminderSettings()
    names = {"meeting_today": "Напоминание о примерке", "meeting_followup": "После примерки",
        "rental_pickup": "Получение аренды", "rental_return": "Возврат аренды",
        "certificate_month": "Сертификат: за месяц", "certificate_fortnight": "Сертификат: за две недели",
        "wants_order": "Хочет заказать", "wants_visit": "Хочет прийти", "free_hold_expired": "Бесплатная бронь истекла"}
    for kind, (status, field, offset, hour, minute) in st.CALENDAR_RULES.items():
        id = "legacy-" + kind
        if db.get(Document, id): continue
        enabled = cs.live_ready() and ss.live_ready() and kind in st.active_calendar_rules(cs)
        variants = {}
        if kind in ("meeting_today", "rental_pickup", "rental_return"):
            variants = {"4765787": {"meeting_today": st.MEETING_PYAT, "rental_pickup": st.RENTAL_PYAT, "rental_return": st.RETURN_PYAT}[kind],
                        "4765789": {"meeting_today": st.MEETING_BAUM, "rental_pickup": st.RENTAL_BAUM, "rental_return": st.RETURN_BAUM}[kind]}
            if kind == "meeting_today": variants["default"] = st.MEETING_BOTH
        content = {"meeting_followup": "Здравствуйте! Удалось ли посетить нас вчера, подобрали желаемый образ?🙌🏻",
            "certificate_month": st.CERTIFICATE_MONTH, "certificate_fortnight": st.CERTIFICATE_FORTNIGHT,
            "wants_order": st.WANTS_ORDER, "wants_visit": st.WANTS_VISIT,
            "free_hold_expired": st.FREE_HOLD_EXPIRED}.get(kind, "Выбери магазин")
        r = Rule(name=names[kind], enabled=enabled, statuses=[getattr(cs, status)], content=content, variants=variants,
            trigger=Trigger(field=getattr(cs, field), offset=-1 if kind == "certificate_month" else offset,
                            unit="months" if kind == "certificate_month" else "days", time=f"{hour:02}:{minute:02}"),
            recipient="field" if kind in st.CERTIFICATE_KINDS else "contact",
            phone_field=cs.recipient_phone_field if kind in st.CERTIFICATE_KINDS else None,
            conditions=[Condition(field=cs.hold_field, op="eq", value=cs.free_hold_enum)] if kind == "free_hold_expired" else [])
        d = r.model_dump(); db.add(Document(id=id, kind="rule", draft=d, active=d, published_version=1))
    for status, kind in st.STAGES.items():
        id = "legacy-" + kind
        if db.get(Document, id): continue
        lead = {"status_id": status, "custom_fields_values": [{"field_id": ss.track_field,"values":[{"value":"{{lead.field.%s}}" % ss.track_field}]}]}
        variants = {}
        if kind in ("in_store", "review"):
            for enum in (ss.pyatnitskaya_enum, ss.baumanskaya_enum):
                lead["custom_fields_values"] = [{"field_id": ss.store_field,"values":[{"enum_id":enum}]}]
                variants[str(enum)] = st.render(lead, ss.model_copy(update={"pyatnitskaya_address": ss.pyatnitskaya_address or "ул. Пятницкая, д. 8"}))
            content = "Выбери магазин"
        else: content = st.render(lead, ss)
        r = Rule(name={"delivered":"Посылка прибыла", "shipped":"Посылка отправлена", "in_store":"Товар в магазине", "review":"Отзыв после покупки"}[kind],
            enabled=ss.live_ready() and (kind != "review" or ss.review_activated_at > 0), statuses=[status],
            trigger=Trigger(type="stage", delay_minutes=60 if kind == "review" else 0, repeat="once"),
            content=content, variants=variants, window_start=0 if kind == "review" and not ss.review_respect_window else 10,
            window_end=24 if kind == "review" and not ss.review_respect_window else 20, late_policy="legacy_stage", legacy_next_day=kind != "review")
        d=r.model_dump(); db.add(Document(id=id, kind="rule", draft=d, active=d, published_version=1, published_at=datetime.fromtimestamp(ss.review_activated_at if kind == "review" else ss.activated_at, timezone.utc)))
    if db.get(Document, "legacy-paid_hold") is None:
        r=Rule(name="Платная отложка", enabled=rs.live_ready(), pipelines=pipelines or [9601214], statuses=[],
            trigger=Trigger(field=rs.until_field, offset=-rs.days_before, time=f"{rs.hour:02}:00"),
            conditions=[Condition(field=rs.hold_field, op="eq", value=rs.paid_enum)],late_policy="same_day",
            content=TEMPLATE.replace("(Отложка до)", "{{lead.date.%s}}" % rs.until_field))
        d=r.model_dump();db.add(Document(id="legacy-paid_hold",kind="rule",draft=d,active=d,published_version=1))
    db.commit()
    paid=db.get(Document,"legacy-paid_hold")
    if pipelines and paid and paid.version==1:
        paid.draft={**paid.draft,"pipelines":pipelines}
        paid.active={**paid.active,"pipelines":pipelines}
        db.commit()


def migrate_jobs(db):
    """Copy legacy ledger once. Preserve provider IDs and do not resend terminal jobs."""
    from app import stage_notifications as st
    from app.reservation_reminders import ReminderJob, ReminderAttempt
    if db.get(Operation, "cutover-v1"): return
    groups = [(list(db.scalars(select(st.CalendarJob))), st.CalendarAttempt, "calendar"),
              (list(db.scalars(select(st.StageJob))), st.StageAttempt, "stage"),
              (list(db.scalars(select(ReminderJob))), ReminderAttempt, "paid")]
    # The old schema did not persist the exact sent body. Never invent a frozen
    # snapshot for a partially sent chain: drain it before cutover instead.
    for rows, cls, group in groups:
        for old in rows:
            if old.state in ("sent","delivered","failed","cancelled","expired"):continue
            attempts=list(db.scalars(select(cls).where(cls.job_id==old.id)))
            if any(a.state!="skipped" for a in attempts):
                raise RuntimeError("Cutover blocked: resolve/drain unfinished legacy delivery chains first")
    for rows, cls, group in groups:
        for old in rows:
            if group == "calendar": rid, occurrence, due = "legacy-"+old.kind, str(old.target_date), old.due_at
            elif group == "stage": rid, occurrence, due = "legacy-"+st.STAGES[old.status_id], "once", old.check_at
            else:
                rid, occurrence = "legacy-paid_hold", str(old.until)
                doc=db.get(Document,rid);r=Rule(**doc.active)
                fake={"custom_fields_values":[{"field_id":r.trigger.field,"values":[{"value":int(datetime.combine(old.until,datetime.min.time(),st.MSK).timestamp())}]}]}
                due=date_due(fake,r)
            doc=db.get(Document,rid)
            if doc is None: continue
            j=Job(rule_id=rid,lead_id=old.lead_id,occurrence=occurrence,due_at=due,
                  state=old.state,reason=old.reason or "",snapshot=None,plan=None,source_at=old.event_at if group=="stage" else None)
            db.add(j);db.flush()
            attempts=list(db.scalars(select(cls).where(cls.job_id == old.id)))
            for pos,a in enumerate(attempts):
                db.add(Attempt(job_id=j.id,position=getattr(a,"position",pos),transport=a.transport,
                    state=a.state,message_id=a.message_id or "",crm_id=a.crm_message_id,reason=a.reason or ""))
            if attempts:
                # Imported chains cannot be reconstructed exactly from legacy rows:
                # stop remaining fallbacks; callbacks still reconcile accepted sends.
                if j.state not in ("sent","failed","cancelled","expired"):
                    j.state,j.reason="uncertain","legacy_chain_requires_review"
    db.add(Operation(id="cutover-v1",digest="v1",state="done",result={}))
    db.commit()


def ingest(db, form, stamp):
    from app.certificate_mail import transitions, MailSettings
    if Settings().executor != "live": return 0
    count=0
    for doc in db.scalars(select(Document).where(Document.kind=="rule")):
        if not doc.active: continue
        r=Rule(**doc.active)
        if r.action == "certificate" or not r.enabled or r.trigger.type != "stage": continue
        for pipeline in r.pipelines:
            for status in r.statuses:
                cfg=MailSettings(pipeline_id=pipeline,status_id=status,activated_at=int(aware(doc.published_at or stamp).timestamp()))
                for event in transitions(form,cfg,stamp):
                    # Last state is rechecked against CRM before each send.
                    occurrence="once" if r.trigger.repeat=="once" else event["key"]
                    j=db.scalar(select(Job).where(Job.rule_id==doc.id,Job.lead_id==event["lead_id"],Job.occurrence==occurrence))
                    previous=list(db.scalars(select(Job).where(Job.rule_id==doc.id,Job.lead_id==event["lead_id"])))
                    if any(x.source_at and aware(x.source_at)>event["source_at"] for x in previous):continue
                    if j and r.trigger.repeat=="once" and (j.source_at is None or event["source_at"]>aware(j.source_at)):
                        if j.state in ("ready","blocked","cancelled") and db.scalar(select(Attempt.id).where(Attempt.job_id==j.id).limit(1)) is None:
                            j.source_at,j.due_at,j.state,j.reason=event["source_at"],stage_due(event["source_at"],r),"ready",""
                            j.snapshot,j.plan,j.recipient_key,j.rendered=None,None,"",""
                    if not j:
                        for old in previous:
                            if old.state in ("ready","blocked"):
                                old.state,old.reason="cancelled","new_stage_entry"
                        db.add(Job(rule_id=doc.id,lead_id=event["lead_id"],occurrence=occurrence,due_at=stage_due(event["source_at"],r),source_at=event["source_at"],snapshot=None));count+=1
    db.commit();return count


def synchronize(db,c,stamp):
    from app.stage_notifications import calendar_date
    for doc in db.scalars(select(Document).where(Document.kind=="rule")):
        if not doc.active: continue
        r=Rule(**doc.active)
        if r.action == "certificate" or not r.enabled: continue
        if r.trigger.type != "date": continue
        found=set()
        for lead in scan_leads(c,r):
            if not matches(lead,r): continue
            try: due=date_due(lead,r)
            except ValueError: continue
            occurrence=str(calendar_date(lead,r.trigger.field));key=(lead["id"],occurrence)
            if due.date() < stamp.date(): continue
            found.add(key)
            j=db.scalar(select(Job).where(Job.rule_id==doc.id,Job.lead_id==lead["id"],Job.occurrence==occurrence))
            if j is None: db.add(Job(rule_id=doc.id,lead_id=lead["id"],occurrence=occurrence,due_at=due))
            elif j.state in ("ready","blocked","cancelled") and db.scalar(select(Attempt.id).where(Attempt.job_id==j.id).limit(1)) is None:
                j.due_at,j.state,j.reason=due,"ready",""
        for j in db.scalars(select(Job).where(Job.rule_id==doc.id,Job.state.in_(["ready","blocked"]))):
            if (j.lead_id,j.occurrence) not in found: j.state,j.reason="cancelled","conditions_changed"
        db.commit()


def reconcile(db):
    from app.reservation_reminders import ReminderEvent, SUCCESS
    for a in db.scalars(select(Attempt).join(Job,Job.id==Attempt.job_id).where(Attempt.state.in_(["inflight","pending","unknown","sent"]),Job.state!="delivered")):
        j=db.get(Job,a.job_id)
        if not a.message_id:
            if a.state=="inflight":a.state,j.state,j.reason="unknown","uncertain","interrupted_send"
            continue
        events=list(db.scalars(select(ReminderEvent).where(ReminderEvent.message_id==a.message_id)))
        if any(e.status in SUCCESS for e in events):
            a.state="sent"
            j.state="delivered" if any(e.status in {"delivered","read"} for e in events) else "sent"
            j.reason=""
        elif any(e.status=="error" for e in events):
            a.state="error"
            if j.state not in ("cancelled","expired","sent","delivered"):
                j.state,j.reason=("ready","confirmed_delivery_error") if j.snapshot else ("failed","confirmed_delivery_error")
    db.commit()


def send(db,j,c,stamp):
    if j.state not in ("ready", "blocked"): return
    from app.reservation_reminders import MSK, ReminderSettings, REJECTED
    doc=db.get(Document,j.rule_id)
    if not doc or not doc.active or not doc.active["enabled"]:
        j.state,j.reason="cancelled","disabled";db.commit();return
    r=Rule(**(j.snapshot["rule"] if j.snapshot else doc.active))
    local=stamp.astimezone(MSK);due=aware(j.due_at)
    started=db.scalar(select(Attempt.id).where(Attempt.job_id==j.id,Attempt.state!="skipped").limit(1)) is not None
    if stamp < due:return
    if r.late_policy=="legacy_stage" and not r.window_start<=local.hour<r.window_end:
        without_delay=r.model_copy(update={"trigger":r.trigger.model_copy(update={"delay_minutes":0})})
        j.due_at=stage_due(stamp,without_delay);db.commit();return
    if (r.late_policy!="legacy_stage" and (local.date()!=due.astimezone(MSK).date() or local.hour>=r.window_end)) or (r.late_policy=="skip" and not started and stamp>due+timedelta(minutes=5)):
        j.state,j.reason="expired","scheduled_window_passed";db.commit();return
    if local.hour<r.window_start:return
    lead=c.get(f"/api/v4/leads/{j.lead_id}", **{"with":"contacts"})
    if not matches(lead,r) or not matches(lead,Rule(**doc.active)):
        j.state,j.reason="cancelled","conditions_changed";db.commit();return
    if r.trigger.type=="date":
        from app.stage_notifications import calendar_date
        if str(calendar_date(lead,r.trigger.field))!=j.occurrence:
            j.state,j.reason="cancelled","date_changed";db.commit();return
    try:
        validate_catalog(r,catalog(c))
        routing=j.snapshot["routing"] if j.snapshot else db.get(Document,"routing").active
        plan,key=make_plan(db,c,lead,r,routing)
        if j.recipient_key and key!=j.recipient_key: raise ValueError("Получатель изменился")
        if not j.snapshot:
            j.snapshot=snapshot(db,doc);j.plan=plan;j.recipient_key=key;j.rendered=render(lead,r)
            db.commit()
        channels={x["channelId"]:x for x in c.wz.get("/channels").json()}
        for pos,route in enumerate(j.plan):
            prior=db.scalar(select(Attempt).where(Attempt.job_id==j.id,Attempt.position==pos))
            if prior:
                if prior.state not in ("skipped","error"):return
                continue
            channel=channels.get(route["channelId"],{})
            available=bool(route["recipient"]) and channel.get("state")=="active"
            if channel.get("transport") != ("tgapi" if route["transport"]=="telegram" else route["transport"]):available=False
            if available:
                fresh=c.get(f"/api/v4/leads/{j.lead_id}", **{"with":"contacts"})
                db.refresh(doc)
                if not doc.active["enabled"] or not matches(fresh,r) or not matches(fresh,Rule(**doc.active)):
                    j.state,j.reason="cancelled","conditions_changed_before_post";db.commit();return
                _,freshkey=make_plan(db,c,fresh,r,routing)
                if freshkey!=key: raise ValueError("Получатель изменился перед отправкой")
                if r.trigger.type=="date" and date_due(fresh,r)!=due: raise ValueError("Дата изменилась")
                current=now()
                if r.late_policy=="skip" and current>due+timedelta(minutes=5) and not started:raise ValueError("Окно первой отправки прошло")
                if current.astimezone(MSK).date()!=local.date() or not r.window_start<=current.astimezone(MSK).hour<r.window_end:raise ValueError("Окно отправки закрыто")
                limit={"telegram":4096,"max":4096,"whatsapp":10000,"instagram":1000,"avito":1000}[route["transport"]]
                if len(j.rendered)>limit:raise ValueError("Текст превышает лимит канала "+route["transport"])
            a=Attempt(job_id=j.id,position=pos,transport=route["transport"],state="inflight" if available else "skipped",crm_id=f"rule-{j.id}-{pos}-{uuid.uuid4().hex}")
            if not available:a.reason="no_recipient" if not route["recipient"] else "inactive_channel"
            db.add(a);j.state="inflight" if available else "ready";db.commit()
            if not available:continue
            try:
                resp=c.wz.post("/message",json={"channelId":route["channelId"],"chatType":route["transport"],**route["recipient"],"text":j.rendered,"clearUnanswered":False,"crmMessageId":a.crm_id})
                try:result=resp.json()
                except ValueError:result={}
                if resp.status_code==201 and result.get("messageId"):
                    a.message_id=str(result["messageId"]);a.state,j.state="pending","pending";db.commit();return
                code=str(result.get("error") or "")[:100]
                if 400<=resp.status_code<500 and code in REJECTED|{"CHANNEL_MAX_PHONE_NOT_OCCUPIED"}:
                    a.state,a.reason,j.state="error",code,"ready";db.commit();started=True;continue
                a.state,j.state,j.reason="unknown","uncertain","provider_result_unknown"
            except httpx.HTTPError:a.state,j.state,j.reason="unknown","uncertain","network_result_unknown"
            db.commit();return
        j.state,j.reason="failed","all_channels_exhausted";db.commit()
    except ValueError as exc:
        j.state,j.reason="blocked",str(exc)[:150];db.commit()


last_scan=None

def tick():
    global last_scan
    if Settings().executor!="live":return
    from app.reservation_reminders import Clients,ReminderSettings
    with get_engine().connect() as conn,get_session_factory()() as db:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:key)"),{"key":LOCK}).scalar():return
        c=None
        try:
            c=Clients(db,ReminderSettings());reconcile(db)
            if last_scan is None or (now()-last_scan).total_seconds()>=30:
                synchronize(db,c,now());last_scan=now()
            for j in list(db.scalars(select(Job).where(Job.state.in_(["ready","blocked"]),Job.due_at<=now()).order_by(Job.due_at).limit(100))):
                try:send(db,j,c,now())
                except Exception as exc:db.rollback();log.error("Managed message failed: %s",type(exc).__name__)
        finally:
            if c:c.close()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"),{"key":LOCK})


async def worker():
    while True:
        try:await asyncio.to_thread(tick)
        except Exception as exc:log.error("Managed worker: %s",type(exc).__name__)
        await asyncio.sleep(5)

# Internal API. Kassa authenticates users; its server alone holds this key.
def authorized(x_reminder_key: str=Header(default="")):
    from app.reservation_reminders import ReminderSettings
    secret=ReminderSettings().admin_key.get_secret_value()
    if not secret or not hmac.compare_digest(secret,x_reminder_key):raise HTTPException(403)


def session():
    with get_session_factory()() as db:yield db


def client(db=Depends(session)):
    from app.reservation_reminders import Clients,ReminderSettings
    c=Clients(db,ReminderSettings())
    try:yield c
    finally:c.close()


def mutation_lock():
    # Same lock as the sender: disable/publish never races a fresh POST claim.
    with get_engine().connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"),{"key":LOCK})
        try:yield
        finally:conn.execute(text("SELECT pg_advisory_unlock(:key)"),{"key":LOCK})


router=APIRouter(prefix="/internal/templates",dependencies=[Depends(authorized)])


class Change(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str=Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["rule","routing","manual"]
    version: int=Field(ge=0)
    payload: dict
    actor: str=Field(min_length=1,max_length=150)


class Publish(BaseModel):
    model_config=ConfigDict(extra="forbid")
    version: int
    actor: str=Field(min_length=1,max_length=150)
    overlaps_confirmed: bool=False
    delete_confirmed: bool=False


def document(db,id):
    d=db.get(Document,id)
    if not d:raise HTTPException(404,"Шаблон не найден")
    return d


def dto(d):
    return {"id":d.id,"kind":d.kind,"version":d.version,"published_version":d.published_version,"draft":d.draft,"active":d.active,
        "checked":bool(d.checked),"published_at":aware(d.published_at).isoformat() if d.published_at else None}


@router.get("")
def listing(db=Depends(session)):
    return {"executor":Settings().executor,"rules":[dto(d) for d in db.scalars(select(Document).order_by(Document.id))],
        "last_scan":last_scan.isoformat() if last_scan else None}


@router.get("/catalog")
def get_catalog(c=Depends(client)):
    return catalog(c)


@router.get("/manual")
def get_manual(c=Depends(client)):
    return {"items":[{**x,"source_hash":remote_digest(x)} for x in all_pages(c,"/api/v4/chats/templates","chat_templates")]}


def remote_digest(value):
    return digest({k:value.get(k) for k in ("id","name","content","type","buttons","attachment","updated_at","is_editable","external_id")})


@router.get("/journal")
def journal(db=Depends(session)):
    labels={"ready":"Запланировано","blocked":"Заблокировано","pending":"Принято провайдером",
        "inflight":"Передаётся провайдеру","sent":"Отправлено","delivered":"Доставлено",
        "cancelled":"Отменено","expired":"Пропущено","failed":"Ошибка","uncertain":"Результат неизвестен",
        "skipped":"Пропущен","error":"Подтверждённый отказ","unknown":"Результат неизвестен"}
    reasons={"conditions_changed":"Изменились условия", "conditions_changed_before_post":"Условия изменились перед отправкой",
        "date_changed":"Дата изменена", "disabled":"Сценарий выключен", "scheduled_window_passed":"Плановое время пропущено",
        "all_channels_exhausted":"Нет доступного канала после подтверждённых отказов или пропусков",
        "provider_result_unknown":"Ответ провайдера требует проверки", "network_result_unknown":"Связь прервалась; результат требует проверки",
        "interrupted_send":"Работа прервалась после начала отправки", "new_stage_entry":"Новый вход на этап заменил ожидание",
        "explicit_test":"Тестовая отправка", "confirmed_delivery_error":"Провайдер подтвердил отказ",
        "test_result_requires_review":"Проверь результат теста", "no_recipient":"Нет адресата для канала", "inactive_channel":"Канал недоступен"}
    def reason(code):return reasons.get(code,code)
    from app.certificate_gift import journal as gift_journal
    return {"items":gift_journal(db)+[{"id":j.id,"rule_id":j.rule_id,"lead_id":j.lead_id,"due_at":aware(j.due_at).isoformat(),
        "state":j.state,"label":labels.get(j.state,j.state),"reason":reason(j.reason),
        "attempts":[{"channel":a.transport,"state":labels.get(a.state,a.state),"reason":reason(a.reason)} for a in db.scalars(select(Attempt).where(Attempt.job_id==j.id))]}
        for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(200))]}


@router.get("/history/{id}")
def history(id:str,db=Depends(session)):
    return {"items":[{"version":h.version,"actor":h.actor,"action":h.action,"at":aware(h.at).isoformat(),"payload":h.payload}
        for h in db.scalars(select(History).where(History.doc_id==id).order_by(History.id.desc()).limit(100))]}


@router.post("/draft",dependencies=[Depends(mutation_lock)])
def draft(body:Change,db=Depends(session),c=Depends(client)):
    if (body.kind=="routing") != (body.id=="routing"):raise HTTPException(422,"Некорректный ID общих настроек")
    d=db.get(Document,body.id)
    if (d.version if d else 0)!=body.version:raise HTTPException(409,"Версия изменилась. Обнови страницу")
    try:
        if body.kind=="rule":
            value=Rule(**body.payload).model_dump()
            if value["action"]=="certificate" and body.id!="gift-certificate":raise ValueError("Допускается один сценарий выдачи сертификата")
        elif body.kind=="routing":value=Routing(**body.payload).model_dump()
        else:
            value=body.payload
            if set(value)-{"remote_id","name","content","source_hash","original","delete","external_id"}:raise ValueError("Некорректный черновик")
            if value.get("remote_id"):
                remote=c.get(f"/api/v4/chats/templates/{int(value['remote_id'])}")
                if remote.get("type")!="amocrm":raise ValueError("Этот тип шаблона доступен только для просмотра")
                if value.get("source_hash")!=remote_digest(remote):raise HTTPException(409,"Шаблон изменился в amoCRM")
                value={**value,"original":remote}
            if not str(value.get("name","")).strip() or not str(value.get("content","")).strip():raise ValueError("Заполни название и текст")
            if len(value["name"])>150 or len(value["content"])>10000:raise ValueError("Название или текст слишком длинные")
        if d:
            if d.kind!=body.kind:raise ValueError("Нельзя менять тип документа")
            d.version+=1;d.draft=value;d.checked=""
        else:
            d=Document(id=body.id,kind=body.kind,version=1,draft=value,active=None);db.add(d)
        audit(db,d,body.actor,"draft",value);db.commit();return dto(d)
    except ValueError as exc:raise HTTPException(422,str(exc))


def check_rule(db,c,d,lead_id=None):
    r=Rule(**d.draft)
    if not r.enabled and d.active and d.active.get("enabled"):return {"total":0,"samples":[],"overlaps":[],"disabled":True}
    validate_catalog(r,catalog(c))
    overlaps=[]
    for other in db.scalars(select(Document).where(Document.kind=="rule",Document.id!=d.id)):
        if not other.active or not other.active.get("enabled"):continue
        o=Rule(**other.active)
        if r.action==o.action and set(r.pipelines)&set(o.pipelines) and (not r.statuses or not o.statuses or set(r.statuses)&set(o.statuses)) and r.trigger.type==o.trigger.type:
            overlaps.append({"id":other.id,"name":o.name})
    targets=list(scan_leads(c,r))
    if lead_id:
        selected=c.get(f"/api/v4/leads/{int(lead_id)}",**{"with":"contacts"})
        targets=[selected,*[x for x in targets if x["id"]!=selected["id"]]]
    rows=[];total=0
    for lead in targets:
        if not matches(lead,r):continue
        total+=1
        if len(rows)>=10:continue
        try:
            body=render(lead,r)
            plan,_=make_plan(db,c,lead,r,(db.get(Document,"routing").active or Routing().model_dump()))
            due=date_due(lead,r) if r.trigger.type=="date" else None
            rows.append({"lead_id":lead["id"],"due_at":due.isoformat() if due else None,"text":body,
                "channels":[p["transport"] for p in plan if p["recipient"]],
                "reason":"Время уже прошло" if due and due<now() else "Только новый переход" if not due else ""})
        except ValueError as exc:rows.append({"lead_id":lead["id"],"reason":str(exc)})
    return {"total":total,"samples":rows,"overlaps":overlaps,"sample_limit":10}


@router.post("/check/{id}")
def check(id:str,body:dict,db=Depends(session),c=Depends(client)):
    d=document(db,id)
    if body.get("version")!=d.version:raise HTTPException(409,"Версия изменилась")
    try:
        if d.kind=="rule":
            if d.draft.get("action")=="certificate":
                from app.certificate_gift import preview
                result=preview(db,c,d,body.get("lead_id"))
                from app.certificate_pdf import render_pdf
                render_pdf({"guest":"Проверка","number":"ПРЕДПРОСМОТР","until":"01.01.2030","start":"01.01.2029","nominal":1,"greeting":""},d.draft.get("certificate_pdf",{}))
            else:result=check_rule(db,c,d,body.get("lead_id"))
        elif d.kind=="routing":Routing(**d.draft);result={"routing":d.draft}
        else:
            if d.draft.get("remote_id"):
                remote=c.get(f"/api/v4/chats/templates/{d.draft['remote_id']}")
                if remote_digest(remote)!=d.draft["source_hash"]:raise HTTPException(409,"Шаблон изменился в amoCRM")
            result={"name":d.draft["name"],"text":d.draft["content"],"delete":bool(d.draft.get("delete"))}
        d.checked=digest([d.version,d.draft])
        d.reviewed_overlaps=sorted([[x["id"],digest(document(db,x["id"]).active)] for x in result.get("overlaps",[])])
        d.reviewed_routing=digest(document(db,"routing").active) if d.kind=="rule" else ""
        db.commit();return result
    except ValueError as exc:raise HTTPException(422,str(exc))


def publish_manual(db,c,d,body):
    p=d.draft;remote_id=p.get("remote_id")
    op_id=f"manual-{d.id}-{d.version}";op=db.get(Operation,op_id)
    external_id="msp-"+hashlib.sha256(d.id.encode()).hexdigest()[:32]
    if op:
        if op.state=="done":return op.result
        # A lost response never grants permission to repeat a create/delete/update.
        rows=all_pages(c,"/api/v4/chats/templates","chat_templates")
        found=[x for x in rows if (x.get("id")==remote_id if remote_id else x.get("external_id")==external_id)]
        if (p.get("delete") and not found) or (len(found)==1 and found[0].get("name")==p["name"] and found[0].get("content")==p["content"]):
            op.state="done";op.result={"remote_id":found[0]["id"] if found else remote_id,"deleted":bool(p.get("delete"))};db.commit();return op.result
        raise HTTPException(409,"Результат прошлой записи неизвестен. Повтор запрещён до сверки")
    if not remote_id:
        previous=list(db.scalars(select(Operation).where(Operation.id.like(f"manual-{d.id}-%"),Operation.state.in_(["uncertain","inflight"]))))
        if previous:raise HTTPException(409,"Есть незавершённая запись этого шаблона. Сначала сверка с amoCRM")
        existing=[x for x in all_pages(c,"/api/v4/chats/templates","chat_templates") if x.get("external_id")==external_id]
        if existing:raise HTTPException(409,"Шаблон уже создан в amoCRM. Обнови список и открой его для редактирования")
    if remote_id:
        remote=c.get(f"/api/v4/chats/templates/{remote_id}")
        if remote_digest(remote)!=p["source_hash"]:raise HTTPException(409,"Шаблон изменился в amoCRM")
    if p.get("delete") and not body.delete_confirmed:raise HTTPException(409,"Подтверди удаление из amoCRM")
    op=Operation(id=op_id,digest=digest(p),state="uncertain",result={});db.add(op)
    audit(db,d,body.actor,"manual_attempt",p);db.commit()
    payload={"name":p["name"],"content":p["content"]}
    try:
        if p.get("delete"):resp=c.amo.delete(f"/api/v4/chats/templates/{remote_id}")
        elif remote_id:resp=c.amo.patch(f"/api/v4/chats/templates/{remote_id}",json=payload)
        else:resp=c.amo.post("/api/v4/chats/templates",json=[{**payload,"external_id":external_id,"type":"amocrm","is_editable":True}])
        if resp.status_code in (400,401,403,404,422):
            op.state="rejected";op.result={"http_status":resp.status_code};db.commit()
            raise HTTPException(422,"amoCRM отклонила запись. Исправь черновик и проверь права интеграции")
        resp.raise_for_status()
        if not remote_id:remote_id=resp.json()["_embedded"]["chat_templates"][0]["id"]
        op.result={"remote_id":remote_id,"deleted":bool(p.get("delete"))};op.state="done";db.commit();return op.result
    except (httpx.HTTPError,KeyError,ValueError):raise HTTPException(502,"Результат записи требует сверки. Повторная отправка запроса не выполняется")


@router.post("/publish/{id}",dependencies=[Depends(mutation_lock)])
def publish(id:str,body:Publish,db=Depends(session),c=Depends(client)):
    d=document(db,id)
    if d.version!=body.version:raise HTTPException(409,"Версия изменилась")
    if d.checked!=digest([d.version,d.draft]):raise HTTPException(409,"Сначала проверь текущий черновик")
    result={}
    try:
        if d.kind=="rule":
            r=Rule(**d.draft)
            if r.enabled:validate_catalog(r,catalog(c))
            # Reject any enabled overlapping rule unless explicitly reviewed.
            overlapping=[x for x in db.scalars(select(Document).where(Document.kind=="rule",Document.id!=d.id))
                         if x.active and x.active.get("enabled") and x.active.get("action","text")==r.action and set(x.active["pipelines"])&set(r.pipelines)
                         and (not r.statuses or not x.active["statuses"] or set(x.active["statuses"])&set(r.statuses))
                         and x.active["trigger"]["type"]==r.trigger.type]
            if r.enabled and sorted([[x.id,digest(x.active)] for x in overlapping]) != (d.reviewed_overlaps or []):
                raise HTTPException(409,"Другие сценарии изменились. Повтори проверку пересечений")
            if r.enabled and d.reviewed_routing and d.reviewed_routing!=digest(document(db,"routing").active):
                raise HTTPException(409,"Порядок каналов изменился. Повтори проверку")
            if r.enabled and overlapping and not body.overlaps_confirmed:raise HTTPException(409,"Подтверди пересечение сценариев")
        elif d.kind=="manual":
            result=publish_manual(db,c,d,body)
            if not result.get("deleted"):
                remote=c.get(f"/api/v4/chats/templates/{result['remote_id']}")
                d.draft={**d.draft,"remote_id":result["remote_id"],"source_hash":remote_digest(remote),"original":remote}
        else:Routing(**d.draft)
        d.active=dict(d.draft);d.published_at=now();d.published_version=d.version;d.checked=""
        audit(db,d,body.actor,"published",{"document":d.active,**result})
        if d.kind in ("rule","routing"):
            for j in db.scalars(select(Job).where(Job.state.in_(["ready","blocked","cancelled","expired","failed"] if d.kind=="rule" else ["ready","blocked"]))):
                if d.kind=="rule" and j.rule_id!=d.id:continue
                if db.scalar(select(Attempt.id).where(Attempt.job_id==j.id,Attempt.state!="skipped").limit(1)) is None:
                    # Skipped channels made no outbound requests; safe to re-plan.
                    for skipped in db.scalars(select(Attempt).where(Attempt.job_id==j.id,Attempt.state=="skipped",Attempt.position>=0)):
                        skipped.position=-skipped.id-1
                    j.snapshot,j.plan,j.recipient_key,j.rendered=None,None,"",""
                    if d.kind=="rule" and not d.active["enabled"]:
                        j.state,j.reason="cancelled","disabled"
                        continue
                    if d.kind=="rule":
                        if d.active["trigger"]["type"]=="stage" and j.state not in ("ready","blocked"):continue
                        lead=c.get(f"/api/v4/leads/{j.lead_id}",**{"with":"contacts"})
                        published=Rule(**d.active)
                        try:
                            if not matches(lead,published):raise ValueError("conditions_changed")
                            j.state,j.reason="ready",""
                            if published.trigger.type=="date":
                                from app.stage_notifications import calendar_date
                                if str(calendar_date(lead,published.trigger.field))!=j.occurrence:raise ValueError("date_changed")
                                j.due_at=date_due(lead,published)
                            elif j.source_at:j.due_at=stage_due(aware(j.source_at),published)
                            else:raise ValueError("trigger_changed")
                        except ValueError as exc:j.state,j.reason="cancelled",str(exc)
                if d.kind=="rule" and not d.active["enabled"]:j.state,j.reason="cancelled","disabled"
        db.commit();return {**dto(d),**result}
    except ValueError as exc:raise HTTPException(422,str(exc))


class TestSend(BaseModel):
    model_config=ConfigDict(extra="forbid")
    version: int
    lead_id: int=Field(gt=0)
    transport: Literal["telegram","whatsapp","max"]
    destination: str=Field(min_length=5,max_length=80)
    confirmed: bool=False
    operation_id: str=Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    actor: str=Field(min_length=1,max_length=150)


@router.post("/test/{id}",dependencies=[Depends(mutation_lock)])
def test_send(id:str,body:TestSend,db=Depends(session),c=Depends(client)):
    from app.reservation_reminders import ReminderSettings
    d=document(db,id)
    if d.draft.get("action")=="certificate":raise HTTPException(409,"Используй отдельный тест PDF сертификата")
    if d.kind!="rule" or d.version!=body.version or d.checked!=digest([d.version,d.draft]):raise HTTPException(409,"Сначала проверь актуальный черновик")
    if not body.confirmed:raise HTTPException(409,"Подтверди тестового получателя")
    opid="test-"+body.operation_id
    if db.get(Operation,opid):raise HTTPException(409,"Этот тест уже запускался. Проверь журнал; повтор запрещён")
    destination=body.destination.strip()
    if body.transport=="telegram" and re.fullmatch(r"@?[a-zA-Z][a-zA-Z0-9_]{4,31}",destination):recipient={"username":destination.lstrip("@")}
    else:
        phone=re.sub(r"[ ()+-]","",destination)
        if not re.fullmatch(r"\d{10,15}",phone):raise HTTPException(422,"Укажи корректный тестовый телефон или Telegram username")
        if len(phone)==11 and phone.startswith("8"):phone="7"+phone[1:]
        recipient={"chatId":phone} if body.transport=="whatsapp" else {"phone":phone}
    r=Rule(**d.draft);validate_catalog(r,catalog(c))
    rendered="ТЕСТ MANSBAND.\n\n"+render(c.get(f"/api/v4/leads/{body.lead_id}"),r)
    if len(rendered)>({"telegram":4096,"max":4096,"whatsapp":10000}[body.transport]):raise HTTPException(422,"Текст превышает лимит канала")
    channel=getattr(ReminderSettings(),body.transport+"_channel")
    channels=c.wz.get("/channels");channels.raise_for_status()
    if not any(x.get("channelId")==channel and x.get("state")=="active" and x.get("transport")==("tgapi" if body.transport=="telegram" else body.transport) for x in channels.json()):raise HTTPException(422,"Тестовый канал недоступен")
    j=Job(rule_id=d.id,lead_id=body.lead_id,occurrence=opid,due_at=now(),state="inflight",reason="explicit_test")
    db.add(j);db.flush();a=Attempt(job_id=j.id,position=0,transport=body.transport,state="inflight",crm_id=opid);db.add(a)
    db.add(Operation(id=opid,digest=digest([id,body.version,body.transport,destination]),state="uncertain",result={"job_id":j.id}))
    # No raw phone/username in audit records.
    audit(db,d,body.actor,"test_send",{"job_id":j.id,"channel":body.transport});db.commit()
    try:
        response=c.wz.post("/message",json={"channelId":channel,"chatType":body.transport,**recipient,"text":rendered,"crmMessageId":opid,"clearUnanswered":False})
        data=response.json()
        if response.status_code==201 and data.get("messageId"):
            a.message_id=str(data["messageId"]);a.state,j.state="pending","pending"
        else:a.state,j.state,j.reason="unknown","uncertain","test_result_requires_review"
    except (httpx.HTTPError,ValueError):a.state,j.state,j.reason="unknown","uncertain","test_result_requires_review"
    db.commit();return {"job_id":j.id,"state":j.state}
