"""Фикстуры тестов хаба: Kommo в памяти вместо боевого аккаунта.

Правки телефонии и лид-машины проверяются здесь, а не на живых лидах клиента:
Kommo боевой один, а ошибка в маршрутизации звонка стоит продажи.
"""
from __future__ import annotations

import itertools
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import alerts, assignment
from app.config import settings
from app.models import Base

ROP = 15648532        # Александра, добавочный 103
POLINA = 15293564     # клиентский отдел, добавочный 102
PAVEL = 13291175      # владелец, добавочный 101


class FakeKommo:
    """Минимальный Kommo: контакты, сделки, примечания, задачи, справочник людей.

    Повторяет только то, что действительно вызывает код хаба. Поиск по телефону
    матчит подстроку цифр — так же нечётко, как боевой `query`."""

    def __init__(self) -> None:
        self.contacts: dict[int, dict[str, Any]] = {}
        self.leads: dict[int, dict[str, Any]] = {}
        self.notes: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.tasks: list[dict[str, Any]] = []
        self.users_list: list[dict[str, Any]] = [
            {"id": ROP, "name": "Александра Терсинских", "rights": {"is_active": True}},
            {"id": POLINA, "name": "Полина Титова", "rights": {"is_active": True}},
            {"id": PAVEL, "name": "Павел", "rights": {"is_active": True}},
        ]
        self._ids = itertools.count(30000001)

    # ── наполнение из тестов ──
    def add_contact(self, name: str = "", phone: str = "", **fields: Any) -> int:
        cid = next(self._ids)
        cf = [{"field_id": settings.field_phone, "values": [{"value": phone}]}] if phone else []
        for fid, value in fields.items():
            cf.append({"field_id": int(fid), "values": [{"value": value}]})
        self.contacts[cid] = {"id": cid, "name": name, "created_at": cid,
                              "custom_fields_values": cf,
                              "_embedded": {"leads": [], "tags": []}}
        return cid

    def add_lead(self, contact_id: int, pipeline_id: int, status_id: int,
                 responsible: int | None = None, closed_at: int | None = None,
                 name: str = "Сделка") -> int:
        lid = next(self._ids)
        self.leads[lid] = {
            "id": lid, "name": name, "pipeline_id": pipeline_id, "status_id": status_id,
            "responsible_user_id": responsible, "closed_at": closed_at,
            "created_at": lid, "custom_fields_values": [],
            "_embedded": {"contacts": [{"id": contact_id}], "tags": []},
        }
        self.contacts[contact_id]["_embedded"]["leads"].append({"id": lid})
        return lid

    # ── чтение ──
    def users(self) -> list[dict[str, Any]]:
        return self.users_list

    def get_contact(self, contact_id: int, with_: str | None = "leads") -> dict[str, Any] | None:
        return self.contacts.get(int(contact_id))

    def get_lead(self, lead_id: int, with_: str = "contacts") -> dict[str, Any] | None:
        return self.leads.get(int(lead_id))

    def search_contacts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        digits = "".join(ch for ch in str(query) if ch.isdigit())
        if not digits:
            return []
        out = []
        for contact in self.contacts.values():
            for cf in contact.get("custom_fields_values") or []:
                for v in cf.get("values") or []:
                    have = "".join(ch for ch in str(v.get("value") or "") if ch.isdigit())
                    if have and digits in have:
                        out.append(contact)
                        break
        return out[:limit]

    def get_notes(self, entity: str, entity_id: int) -> list[dict[str, Any]]:
        return self.notes.get((entity, int(entity_id)), [])

    # ── мутации ──
    def add_note_raw(self, entity: str, note: dict[str, Any]) -> dict[str, Any] | None:
        self.notes.setdefault((entity, int(note["entity_id"])), []).append(note)
        return {"_embedded": {"notes": [{"id": next(self._ids)}]}}

    def add_note(self, entity: str, entity_id: int, text: str,
                 note_type: str = "common") -> dict[str, Any] | None:
        return self.add_note_raw(entity, {"entity_id": int(entity_id),
                                          "note_type": note_type,
                                          "params": {"text": text}})

    def update_lead(self, lead_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.leads.setdefault(int(lead_id), {"id": int(lead_id)}).update(payload)
        return None

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.contacts.setdefault(int(contact_id), {"id": int(contact_id)}).update(payload)
        return None

    def create_contact(self, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
        item = payload[0]
        cid = next(self._ids)
        self.contacts[cid] = {"id": cid, "created_at": cid,
                              "custom_fields_values": item.get("custom_fields_values") or [],
                              "name": item.get("name", ""),
                              "_embedded": {"leads": [], "tags": []}}
        return {"_embedded": {"contacts": [{"id": cid}]}}

    def create_lead(self, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
        item = payload[0]
        lid = next(self._ids)
        self.leads[lid] = {"id": lid, "created_at": lid, "closed_at": None,
                           "pipeline_id": item.get("pipeline_id"),
                           "status_id": item.get("status_id"),
                           "responsible_user_id": item.get("responsible_user_id"),
                           "name": item.get("name", ""),
                           "custom_fields_values": item.get("custom_fields_values") or [],
                           "_embedded": {"contacts": [], "tags": []}}
        return {"_embedded": {"leads": [{"id": lid}]}}

    def link_lead_contact(self, lead_id: int, contact_id: int, main: bool = False) -> None:
        self.leads[int(lead_id)]["_embedded"]["contacts"].append({"id": int(contact_id)})
        self.contacts[int(contact_id)]["_embedded"]["leads"].append({"id": int(lead_id)})

    def unlink_lead_contact(self, lead_id: int, contact_id: int) -> None:
        return None

    def set_tags(self, entity: str, entity_id: int, tags: list[str]) -> None:
        store = self.contacts if entity == "contacts" else self.leads
        item = store.setdefault(int(entity_id), {"id": int(entity_id), "_embedded": {}})
        item.setdefault("_embedded", {})["tags"] = [{"name": t} for t in tags]

    def post(self, path: str, json: Any) -> dict[str, Any] | None:
        if path == "/tasks":
            for item in json:
                self.tasks.append(dict(item))
            return {"_embedded": {"tasks": [{"id": next(self._ids)}]}}
        return None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return None

    def patch(self, path: str, json: Any) -> dict[str, Any] | None:
        return None


@pytest.fixture()
def fake() -> FakeKommo:
    return FakeKommo()


@pytest.fixture(autouse=True)
def _reset_caches():
    """Справочник людей и имена для алертов кэшируются на 10 минут — между
    тестами кэш обязан быть пустым, иначе результат зависит от порядка."""
    assignment._active_cache["ids"] = None
    assignment._active_cache["ts"] = 0.0
    alerts._user_names["map"] = None
    alerts._user_names["ts"] = 0.0
    yield


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def enable_all(monkeypatch):
    """Флаги «система работает в полную силу»: тесты проверяют логику, а не то,
    что она выключена."""
    for flag in ("enable_assignment", "enable_deal_dedup", "enable_cross_funnel",
                 "enable_contact_soft_merge", "enable_light_chat_merge"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "shadow_mode", False)
    monkeypatch.setattr(settings, "auto_rollout", False)
    return settings


@pytest.fixture(autouse=True)
def _telephony_map(monkeypatch):
    """Карта телефонии фиксируется, а не читается из боевого `.env`.

    Линия 1 переключается между 101 и 103 по сменам (скрипт `lb-line1-auto.sh`),
    поэтому без фиксации результат теста зависел бы от времени суток."""
    monkeypatch.setattr(settings, "telephony_ext_users", "102:15293564,103:15648532,101:13291175")
    monkeypatch.setattr(settings, "telephony_sales_order", "103,101")
    monkeypatch.setattr(settings, "telephony_did_ext", "1:103,3:103")
    monkeypatch.setattr(settings, "telephony_missed_owner_id", ROP)
    yield


@pytest.fixture()
def enable_telephony(monkeypatch):
    monkeypatch.setattr(settings, "enable_telephony", True)
    monkeypatch.setattr(settings, "dial_guard_mode", "off")
    return settings
