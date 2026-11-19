"""Conservative field projections and durable, opt-in execution of field writes.

Existing values and migration history take precedence. Every write requires a
fresh projection; unknown outcomes are reconciled without repeating the PATCH.
"""
from dataclasses import dataclass, field
from datetime import datetime, time

from app.crm_automation import Catalog, MSK, PIPELINES, field_date, field_values, is_test, placeholder_meeting_date, stamp
from app.crm_automation_rules import STAGE_DATE_FIELDS
from app.pipeline_guard import migration_blocked
from app.models import Base
from sqlalchemy import String, Integer, JSON, DateTime, select, update
from sqlalchemy.orm import Mapped, mapped_column
from datetime import timezone
import hashlib
import json

# Live custom-field catalogue, 17 November 2026. Removed stages are intentionally
# not mapped; historical checkboxes remain in CRM without fabrication on import.
STAGE_FLAGS = {
    "Взято в работу": 3630859, "Дано обещание": 3630861,
    "Хочет прийти": 3630865, "Хочет заказать": 3630867,
    "Ждет товар": 3630869, "Товар в магазине": 3630871,
    "Товар отложен": 3630873, "Встреча назначена": 3630875,
    "Аренда оплачена": 3630881, "В аренде": 3630883,
    "Передан на сборку": 3630885, "Собран": 3630887,
    "Отправлен": 3630891, "Доставлен": 3630893,
    "Не выкуплен": 3630895, "Сертификат оплачен": 3630897,
    "Опеределены договоренности": 3630899,
}
TAKEN_DATE, QUALIFIED_DATE = 3434463, 3434465
QUALIFIED_STAGES = frozenset({"Встреча назначена", "Передан на сборку", "Сертификат оплачен"})


@dataclass
class FieldProjection:
    fields: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class FieldExecution(Base):
    __tablename__ = "crm_automation_fields"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[str] = mapped_column(String(160), index=True)
    payload: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    reason: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def plan_fields(db, lead, event_id, projection):
    if not projection.fields: return None
    digest = hashlib.sha256(json.dumps([lead["id"], event_id, projection.fields],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    job = db.get(FieldExecution, digest)
    if job is None:
        job = FieldExecution(id=digest,lead_id=lead["id"],event_id=event_id,payload=projection.fields)
        db.add(job)
        db.flush()
    return job


def fields_match(lead, fields):
    actual = {f["field_id"]: f.get("values") or [] for f in lead.get("custom_fields_values") or []}
    for field in fields:
        values = actual.get(field["field_id"], [])
        expected = field["values"]
        if len(values) != len(expected): return False
        if any(not all(value.get(k) == v for k,v in target.items()) for value,target in zip(values,expected)):
            return False
    return True


def execute_fields(db, c, job, project, *, preview=True):
    if preview: return "preview"
    if job.state not in ("planned", "inflight", "uncertain"): return job.state
    from app.crm_automation import MigrationEntry
    entry = db.get(MigrationEntry, job.lead_id)
    if entry and entry.state not in ("preview", "verified", "rolled_back"): return "migration_guard"
    lead = c.get(f"/api/v4/leads/{job.lead_id}")
    if migration_blocked(lead): return "migration_guard"
    if fields_match(lead, job.payload):
        if job.state == "planned":
            job.state, job.reason = "matched", "already_set_before_write"
        else:
            job.state, job.reason = "applied", "readback_verified"
        db.commit()
        return job.state
    if job.state != "planned":
        job.state,job.reason="uncertain","field_write_not_confirmed"
        db.commit()
        return job.state
    unresolved = db.scalar(select(FieldExecution.id).where(
        FieldExecution.lead_id==job.lead_id,FieldExecution.state.in_(("inflight","uncertain")),FieldExecution.id!=job.id).limit(1))
    if unresolved: return "previous_field_write_uncertain"
    fresh = project(lead)
    if is_test(lead) or lead.get("is_deleted") or fresh.fields != job.payload:
        job.state,job.reason="cancelled","source_or_manual_fields_changed"
        db.commit()
        return job.state
    claimed=db.execute(update(FieldExecution).where(FieldExecution.id==job.id,FieldExecution.state=="planned")
        .values(state="inflight",updated_at=datetime.now(timezone.utc)))
    db.commit();db.refresh(job)
    if claimed.rowcount != 1: return job.state
    try:
        c.patch(f"/api/v4/leads/{job.lead_id}",{"custom_fields_values":job.payload})
        actual=c.get(f"/api/v4/leads/{job.lead_id}")
        job.state="applied" if fields_match(actual,job.payload) else "uncertain"
        job.reason="readback_verified" if job.state=="applied" else "field_write_not_confirmed"
    except Exception:
        job.state,job.reason="uncertain","field_patch_result_unknown"
    db.commit()
    return job.state


def project_fields(lead: dict, catalog: Catalog, *, event_at: datetime | None = None,
                   stage_changed: bool = False, migrating: bool = False,
                   owned_values: dict | None = None) -> FieldProjection:
    out = FieldProjection()
    if migrating or migration_blocked(lead) or lead.get("pipeline_id") not in PIPELINES or lead.get("is_deleted") or is_test(lead):
        return out
    stage = catalog.stage(lead)
    if stage is None:
        out.issues.append("unknown_stage")
        return out

    def fill(field_id, value):
        if not field_values(lead, field_id):
            out.fields.append({"field_id": field_id, "values": [{"value": value}]})

    def owned(field_id):
        values = next((f.get("values") or [] for f in lead.get("custom_fields_values") or []
                       if f.get("field_id") == field_id), [])
        return bool(values and owned_values and values == owned_values.get(field_id))

    if stage_changed:
        if event_at is None:
            out.issues.append("missing_verified_event_time")
        else:
            ts = int(stamp(event_at).timestamp())
            # A checkbox is a monotonic record of a genuine visit to this stage.
            flag = STAGE_FLAGS.get(stage)
            if flag and field_values(lead, flag) != [True]:
                out.fields.append({"field_id": flag, "values": [{"value": True}]})
            if stage == "Взято в работу":
                fill(TAKEN_DATE, ts)
            if stage in QUALIFIED_STAGES:
                fill(QUALIFIED_DATE, ts)
                # Missing taken date is diagnostic, not an invented same-second
                # qualification. Existing F5 historical values are preserved.
                if not field_values(lead, TAKEN_DATE):
                    out.issues.append("taken_date_missing_before_qualification")

    # Copy only a real meeting date into an empty approximate date. Never emit
    # the legacy 01.01.2001 placeholder, and never erase it without review.
    meeting = field_date(lead, 3382311, include_placeholder=True)
    if meeting and placeholder_meeting_date(3382311, meeting.date()):
        out.issues.append("legacy_fake_meeting_date")
    elif meeting:
        fill(3185681, int(meeting.timestamp()))

    required = STAGE_DATE_FIELDS.get(stage)
    if required and field_date(lead, required) is None:
        out.issues.append(f"missing_stage_date:{required}")
    elif required:
        actual = field_date(lead, required).astimezone(MSK)
        expected = int(datetime.combine(actual.date(), time.min, MSK).timestamp())
        present = field_date(lead, 3279145)
        if present is not None and present.astimezone(MSK).date() != actual.date():
            if owned(3279145):
                out.fields.append({"field_id":3279145,"values":[{"value":expected}]})
            else:
                out.issues.append("stage_deadline_differs_from_actual_date")
        else:
            fill(3279145, expected)

    stores = field_values(lead, 3158921)
    if len(stores) == 1 and stores[0] in STORE_DETAILS:
        details = STORE_DETAILS[stores[0]]
        for field_id, key in ((3383324, "address"), (3390831, "metro")):
            existing = field_values(lead, field_id)
            expected = details[key]
            if not existing:
                fill(field_id, expected)
            elif existing != [expected]:
                if owned(field_id):
                    out.fields.append({"field_id":field_id,"values":[{"value":expected}]})
                else:
                    # Historical/custom text is not proof of server ownership.
                    out.issues.append(f"store_technical_field_conflict:{field_id}")
    return out


def project_stage_history(lead: dict, catalog: Catalog, events: list[dict], *,
                          migrating: bool = False) -> FieldProjection:
    """Project analytics from verified business transitions, including passed stages.

    The caller must supply a complete collected interval and exclude migration
    transitions explicitly. Current-stage task supersession must not discard
    these historical facts. No current deadline/address is projected here.
    """
    out = FieldProjection()
    if migrating or migration_blocked(lead) or is_test(lead) or lead.get('is_deleted'):
        return out
    if lead.get('pipeline_id') not in PIPELINES:
        return out
    flags, dates = set(), {}
    for event in events:
        if event.get('lead_id') != lead.get('id'):
            raise ValueError('history_lead_mismatch')
        if event.get('migration'):
            continue
        after = event.get('after') or {}
        if after.get('pipeline_id') not in PIPELINES:
            continue
        at = event.get('at')
        if not isinstance(at, int) or isinstance(at, bool) or at <= 0:
            raise ValueError('invalid_history_time')
        stage = catalog.stage({'pipeline_id':after['pipeline_id'], 'status_id':after.get('id')})
        if stage is None:
            out.issues.append('unknown_historical_stage')
            continue
        if stage in STAGE_FLAGS:
            flags.add(STAGE_FLAGS[stage])
        date_id = TAKEN_DATE if stage == 'Взято в работу' else QUALIFIED_DATE if stage in QUALIFIED_STAGES else None
        if date_id:
            dates[date_id] = min(at, dates.get(date_id, at))
    for field_id in sorted(flags):
        if field_values(lead, field_id) != [True]:
            out.fields.append({'field_id':field_id,'values':[{'value':True}]})
    for field_id, at in sorted(dates.items()):
        # The existing Digital spam bot intentionally clears qualification.
        # Historical stage visits must not undo that exclusion from conversion.
        if field_id==QUALIFIED_DATE and lead.get('status_id')==143 and 'Спам' in field_values(lead,3134051):
            continue
        if not field_values(lead, field_id):
            out.fields.append({'field_id':field_id,'values':[{'value':at}]})
    if QUALIFIED_DATE in dates and TAKEN_DATE not in dates and not field_values(lead, TAKEN_DATE):
        out.issues.append('taken_date_missing_before_qualification')
    return out


# Audited F5 274161 and 271005. Markers are campaign codes, never phone numbers.
UTM_SOURCES = {
    "ya": "Яндекс Директ", "yandex": "Яндекс Директ", "maps": "Яндекс Карты",
    "vk": "ВК", "google": "Гугл Карты", "tg": "Телеграм канал", "2gis": "2ГИС",
    "insta": "Instagram", "avito": "Авито", "zen": "Дзен", "youtube": "Ютуб",
    "tiktok": "TikTok", "pinterest": "Pinterest",
}
MESSAGE_SOURCES = {
    "4976103": "Телеграм канал", "1568905": "Яндекс Карты", "9204606": "2ГИС",
    "3852707": "Instagram", "6491308": "Авито", "5720909": "Гугл Карты",
    "8034410": "Дзен", "2146511": "Ютуб", "9372812": "TikTok",
    "4865913": "Pinterest", "7149201": "Сайт (органика)", "2685302": "ВК",
    "208327404": "Яндекс Директ",
}


def project_first_source(lead: dict, fields: list[dict], *, first_touch: bool = False,
                         incoming_text: str = "", migrating: bool = False) -> FieldProjection:
    """Use only a verified first inbound touch. Never infer it from blank fields.

    Multiple conflicting signals are diagnostic. Later messages and migration
    cannot reattribute a lead; text is used transiently and is not returned.
    """
    import re
    out = FieldProjection()
    if not first_touch or migrating or migration_blocked(lead) or is_test(lead) or lead.get("is_deleted") or lead.get("pipeline_id") not in PIPELINES:
        return out
    primary, technical = field_values(lead, 3781223), field_values(lead, 3134043)
    preserved = set(primary + technical)
    if len(preserved) > 1:
        out.issues.append("conflicting_existing_sources")
        return out
    candidates = set(preserved)
    if not preserved:
        by_code = {f.get("code"): f["id"] for f in fields if f.get("code")}
        utms = field_values(lead, by_code.get("UTM_SOURCE"))
        if utms:
            if len(utms) != 1 or utms[0] not in UTM_SOURCES:
                out.issues.append("unrecognized_utm_source")
                return out
            candidates.add(UTM_SOURCES[utms[0]])
        elif field_values(lead, by_code.get("YCLID")):
            candidates.add("Яндекс Директ")
        for token in re.findall(r"(?<!\d)\d+(?!\d)", incoming_text):
            if token in MESSAGE_SOURCES:
                candidates.add(MESSAGE_SOURCES[token])
    if not candidates and shared_ad_call(lead):
        candidates.add("Другое")
    if len(candidates) > 1:
        out.issues.append("conflicting_first_touch_signals")
        return out
    if not candidates:
        return out
    label = candidates.pop()
    catalog = {f["id"]: f for f in fields}
    proposed = []
    for field_id in (3781223, 3134043):
        if field_values(lead, field_id):
            continue
        enums = [v["id"] for v in (catalog.get(field_id, {}).get("enums") or []) if v.get("value") == label]
        if len(enums) != 1:
            out.issues.append(f"source_enum_unavailable:{field_id}")
            return out
        proposed.append({"field_id": field_id, "values": [{"enum_id": enums[0]}]})
    out.fields = proposed
    return out


SHARED_AD_CALL_TAG = "Вызов-на-номер:79962997303"


def shared_ad_call(lead):
    tags = {tag.get("name", "") for tag in (lead.get("_embedded") or {}).get("tags", [])}
    return {tag for tag in tags if tag.startswith("Вызов-на-номер:")} == {SHARED_AD_CALL_TAG}


def verified_source_projection(c, lead, fields, *, created_at=None, through=0):
    """Attribute only unchanged creation-time inputs, or mirror an existing source.

    An empty source is not proof of first touch. A complete field-event scan must
    establish that current UTM/YCLID values existed at creation, rather than being
    introduced later. Message-code attribution requires separate inbound proof.
    """
    if migration_blocked(lead) or is_test(lead) or lead.get('is_deleted') or lead.get('pipeline_id') not in PIPELINES:
        return FieldProjection()
    if field_values(lead,3781223) or field_values(lead,3134043):
        return project_first_source(lead,fields,first_touch=True)
    if not created_at or lead.get('created_at')!=created_at:
        return FieldProjection(issues=['first_source_creation_not_observed'])
    updated=lead.get('updated_at')
    if not isinstance(updated,int) or updated>through:
        return FieldProjection(issues=['first_source_history_not_caught_up'])
    watched={3781223,3134043}|{f['id'] for f in fields if f.get('code') in ('UTM_SOURCE','YCLID')}
    changes=[]
    for page in range(1,101):
        data=c.get('/api/v4/events',page=page,limit=100,**{'filter[entity]':'lead',
            'filter[entity_id]':lead['id'],'filter[type]':'custom_field_value_changed',
            'filter[created_at][from]':created_at,'filter[created_at][to]':through})
        rows=data.get('_embedded',{}).get('events',[])
        for row in rows:
            if row.get('entity_type')!='lead' or row.get('entity_id')!=lead['id']:
                raise ValueError('unexpected_source_history_entity')
            at=int(row['created_at'])
            if not created_at<=at<=through: raise ValueError('unexpected_source_history_time')
            kind=row.get('type','')
            if not kind.startswith('custom_field_') or not kind.endswith('_value_changed'):
                raise ValueError('unexpected_source_history_type')
            values=[v.get('custom_field_value',{}) for v in [*row.get('value_before',[]),*row.get('value_after',[])]]
            ids={v.get('field_id') for v in values}
            if not ids or None in ids: raise ValueError('incomplete_source_history')
            changes.extend(ids & watched)
        if len(rows)<100 and not data.get('_links',{}).get('next'): break
    else: raise ValueError('incomplete_source_history_scan')
    if changes:
        return FieldProjection(issues=['first_source_inputs_changed_since_creation'])
    if shared_ad_call(lead):
        from app.message_templates import all_pages
        for kind in ("entity_tag_added", "entity_tag_deleted"):
            events = all_pages(c, "/api/v4/events", "events", **{
                "filter[entity]":"lead", "filter[entity_id]":lead["id"], "filter[type]":kind,
                "filter[created_at][from]":created_at, "filter[created_at][to]":through})
            for event in events:
                if (event.get("entity_id") != lead["id"] or event.get("type") != kind
                        or event.get("entity_type") != "lead" or not created_at <= event["created_at"] <= through):
                    raise ValueError("unexpected_tag_history")
                values = event["value_after"] if kind == "entity_tag_added" else event["value_before"]
                if not values:
                    raise ValueError("incomplete_tag_history")
                if any(v["tag"]["name"].startswith("Вызов-на-номер:") for v in values):
                    if kind == "entity_tag_deleted" or event["created_at"] != created_at:
                        return FieldProjection(issues=["called_number_changed_after_creation"])
    return project_first_source(lead,fields,first_touch=True)


# F5 240820/241578 contents; exact live enum labels of field 3158921.
STORE_DETAILS = {'на Пятницкой': {'metro': 'Новокузнецкой', 'address': 'Адрес:\nг. Москва, ул. Пятницкая, д. 8\n\nПарковка:\nБесплатная парковка (звоните за 2 мин.)\n\nКак добраться:\nОт м. Новокузнецкая пройдите вдоль ул. Пятницкая в сторону направления движения машин (3 мин) . Дойдя по адресу, зайдите в арку между заведениями «Cofix» и «INCANTO», после арки поверните налево, железная дверь, 3 этаж.'}, 'на Бауманской': {'metro': 'Бауманской', 'address': 'Адрес:\nг. Москва, Спартаковская пл., д. 14, стр. 2\n\nПарковка:\nБесплатная парковка (звоните за 2 мин.)\n\nКак добраться:\nОт метро пройдите вдоль ул. Бауманская (5 мин), между 13 и 14 домами вы увидите черные открытые ворота и закрытый шлагбаум, зайдите на территорию через крыльцо с надписью «Central Street», идите прямо до магазина №3105 (ближе к концу по правую сторону).'}}
