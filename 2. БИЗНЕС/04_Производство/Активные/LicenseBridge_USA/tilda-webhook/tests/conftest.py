"""Общие фикстуры: in-memory sqlite + фейковый Kommo-клиент."""
from __future__ import annotations

import re
import time
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions import Ctx
from app.config import settings
from app.models import Base


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def enable_all():
    """Включает все feature-flags и выключает shadow на время теста."""
    saved = {k: getattr(settings, k) for k in (
        "shadow_mode", "enable_assignment", "enable_deal_dedup", "enable_cross_funnel",
        "enable_contact_soft_merge", "enable_light_chat_merge", "intake_create_in_shadow",
    )}
    settings.shadow_mode = False
    settings.enable_assignment = True
    settings.enable_deal_dedup = True
    settings.enable_cross_funnel = True
    settings.enable_contact_soft_merge = True
    settings.enable_light_chat_merge = True
    settings.intake_create_in_shadow = True
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


@pytest.fixture()
def enable_telephony():
    """Телефония живёт вне авто-раскатки: включается своим флагом."""
    saved = (settings.enable_telephony, settings.rec_link_secret)
    settings.enable_telephony = True
    settings.rec_link_secret = "test-secret"
    yield
    settings.enable_telephony, settings.rec_link_secret = saved


@pytest.fixture()
def enable_leadflow():
    """Лид-машина, как и телефония, живёт вне авто-раскатки — свой флаг."""
    saved = (settings.enable_leadflow, settings.sms_bot_id, settings.ami_secret)
    settings.enable_leadflow = True
    settings.sms_bot_id = 777001
    settings.ami_secret = "test-ami"
    yield
    settings.enable_leadflow, settings.sms_bot_id, settings.ami_secret = saved


@pytest.fixture()
def enable_handoff():
    """`enable_handoff` живёт вне авто-раскатки/FlagSet (см. app/config.py) —
    отдельный фикстур, не пересекается с enable_all."""
    saved = settings.enable_handoff
    settings.enable_handoff = True
    yield
    settings.enable_handoff = saved


class FakeKommo:
    """Мини-эмуляция Kommo API v4 в памяти."""

    def __init__(self) -> None:
        self.contacts: dict[int, dict[str, Any]] = {}
        self.leads: dict[int, dict[str, Any]] = {}
        self.notes: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.tasks: list[dict[str, Any]] = []
        self.bot_runs: list[dict[str, Any]] = []
        self.talks: dict[int, list[dict[str, Any]]] = {}
        self.users_list = [
            {"id": settings.default_sales_owner_id, "rights": {"is_active": True}},
            {"id": 15293564, "rights": {"is_active": True}},  # Полина
            {"id": 15648532, "rights": {"is_active": True}},  # Александра (РОП)
            {"id": 99999999, "rights": {"is_active": False}},  # уволенный
        ]
        self._seq = 1000

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # ── seed helpers ──
    def add_contact(self, name="", phone="", created_at=None, tags=None,
                    lead_ids=None, cfs=None, contact_id=None):
        cid = contact_id or self._next()
        cf = list(cfs or [])
        if phone:
            cf.append({"field_id": settings.field_phone,
                       "values": [{"value": phone, "enum_id": settings.field_phone_enum_work}]})
        self.contacts[cid] = {
            "id": cid, "name": name, "created_at": created_at or int(time.time()),
            "responsible_user_id": 0,
            "custom_fields_values": cf,
            "_embedded": {"leads": [{"id": lid} for lid in (lead_ids or [])],
                          "tags": [{"name": t} for t in (tags or [])]},
        }
        return cid

    def add_lead(self, contact_id, pipeline_id, status_id, created_at=None,
                 closed_at=None, responsible=0, tags=None, lead_id=None,
                 cfs=None, price=None):
        lid = lead_id or self._next()
        self.leads[lid] = {
            "id": lid, "pipeline_id": pipeline_id, "status_id": status_id,
            "created_at": created_at or int(time.time()), "closed_at": closed_at,
            "responsible_user_id": responsible, "price": price,
            "custom_fields_values": list(cfs or []),
            "_embedded": {"contacts": [{"id": contact_id}],
                          "tags": [{"name": t} for t in (tags or [])]},
        }
        c = self.contacts.get(contact_id)
        if c:
            c["_embedded"]["leads"].append({"id": lid})
        return lid

    # ── API surface ──
    def account(self):
        return {"id": settings.kommo_account_id}

    def users(self):
        return self.users_list

    def pipelines(self):
        # sort как в боевом аккаунте: чем больше, тем дальше этап по воронке
        return [
            {"id": settings.pipeline_id,
             "_embedded": {"statuses": [{"id": settings.status_new, "sort": 20},
                                        {"id": 86527439, "sort": 30},   # Первичный контакт
                                        {"id": 96943275, "sort": 40},   # Квалификация
                                        {"id": 106419976, "sort": 50},  # В работе
                                        {"id": settings.status_won, "sort": 10000},
                                        {"id": settings.status_lost, "sort": 11000}]}},
            {"id": settings.assembly_pipeline_id,
             "_embedded": {"statuses": [{"id": 93231787, "sort": 20},
                                        {"id": 101248304, "sort": 30}]}},
        ]

    def custom_fields(self, entity):
        if entity == "leads":
            return [
                {"id": settings.field_channel, "type": "text"},
                {"id": settings.field_utm_source, "type": "text"},
                {"id": settings.field_teletype_id, "type": "text"},
                {"id": settings.field_ttad_id, "type": "tracking_data"},  # не копируемый тип
            ]
        return [{"id": settings.field_phone, "type": "text"}]

    def get_contact(self, cid, with_="leads"):
        return self.contacts.get(int(cid))

    def get_lead(self, lid, with_="contacts"):
        return self.leads.get(int(lid))

    def paginate(self, path, embedded_key, params=None, limit=250, max_pages=1000):
        """Перебор сделок. Фильтр воронки намеренно игнорируем: боевой Kommo
        19.08 так и сделал, и подметание ушло по чужим воронкам. Пусть код
        отсеивает воронку сам, а тест это проверяет."""
        if path != "/leads":
            return iter(())
        return iter(list(self.leads.values()))

    def search_contacts(self, query, limit=10):
        """Как боевой Kommo: совпадение по подстроке цифр значения поля.
        Номер, записанный как `7473361387`, по запросу `+17473361387` НЕ находится."""
        q = re.sub(r"\D", "", query or "")
        out: list[dict[str, Any]] = []
        for c in self.contacts.values():
            for cf in c.get("custom_fields_values") or []:
                for v in cf.get("values") or []:
                    stored = re.sub(r"\D", "", str(v.get("value", "")))
                    if q and stored and q in stored and c not in out:
                        out.append(c)
        return out[:limit]

    def get_notes(self, entity, entity_id):
        return self.notes.get((entity, int(entity_id)), [])

    def get_lead_talks(self, lead_id):
        return self.talks.get(int(lead_id), [])

    def add_note(self, entity, entity_id, text, note_type="common"):
        self.notes.setdefault((entity.rstrip("s") if entity.endswith("s") else entity, int(entity_id)), [])
        # normalize key to leads/contacts
        key = ("leads" if "lead" in entity else "contacts", int(entity_id))
        self.notes.setdefault(key, []).append({"note_type": note_type, "params": {"text": text}})

    def add_note_raw(self, entity, note):
        key = ("leads" if "lead" in entity else "contacts", int(note["entity_id"]))
        stored = {
            "note_type": note.get("note_type", "common"),
            "params": note.get("params") or {},
            "created_at": note.get("created_at"),
        }
        # автор и ответственный примечания: по ним в Kommo видно, чей это звонок
        for field in ("created_by", "responsible_user_id"):
            if field in note:
                stored[field] = note[field]
        self.notes.setdefault(key, []).append(stored)
        return {}

    def create_contact(self, payload):
        p = payload[0]
        cid = self.add_contact(name=p.get("name", ""), cfs=p.get("custom_fields_values"))
        return {"_embedded": {"contacts": [{"id": cid}]}}

    def create_lead(self, payload):
        p = payload[0]
        contact_id = p["_embedded"]["contacts"][0]["id"]
        lid = self.add_lead(contact_id, p["pipeline_id"], p["status_id"],
                            created_at=p.get("created_at"),
                            responsible=p.get("responsible_user_id", 0),
                            cfs=p.get("custom_fields_values"),
                            price=p.get("price"))
        return {"_embedded": {"leads": [{"id": lid}]}}

    def update_lead(self, lead_id, payload):
        self.leads[int(lead_id)].update(payload)
        return {}

    def update_contact(self, contact_id, payload):
        self.contacts[int(contact_id)].update(payload)
        return {}

    def link_lead_contact(self, lead_id, contact_id, main=False):
        self.leads[int(lead_id)]["_embedded"]["contacts"] = [{"id": int(contact_id)}]

    def unlink_lead_contact(self, lead_id, contact_id):
        pass

    def post(self, path, json):
        """Только /tasks и запуск salesbot: прочие мутации — именованные методы."""
        if path == "/tasks":
            out = []
            for t in json:
                tid = self._next()
                self.tasks.append({**t, "id": tid})
                out.append({"id": tid})
            return {"_embedded": {"tasks": out}}
        if path == "/salesbot/run":
            self.bot_runs.extend(json)
            return {}
        raise AssertionError(f"unexpected POST {path}")

    def set_tags(self, entity, entity_id, tags):
        store = self.contacts if entity == "contacts" else self.leads
        e = store.get(int(entity_id))
        if e:
            e["_embedded"]["tags"] = [{"name": t} for t in tags]


@pytest.fixture()
def fake():
    return FakeKommo()


@pytest.fixture()
def ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=False)
