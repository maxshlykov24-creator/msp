"""Preflight: что мешает старту, а что просто отсутствует.

Повод — 03.09.2026: после включения ENABLE_LEADFLOW worker ушёл в рестарт по
записи «SMS_BOT_ID не задан», хотя код этот случай обрабатывает и вместо SMS
ставит задачу менеджеру. Неверный id воронки и некупленный бот — разные вещи.
"""
from __future__ import annotations

import pytest

from app import preflight
from app.config import settings


class FakeKommo:
    def __init__(self, *, pipeline_ok: bool = True) -> None:
        self.pipeline_ok = pipeline_ok

    def account(self):
        return {"id": settings.kommo_account_id}

    def pipelines(self):
        main = {
            "id": settings.pipeline_id if self.pipeline_ok else 999,
            "_embedded": {"statuses": [{"id": settings.status_new},
                                       {"id": settings.status_in_work}]},
        }
        assembly = {
            "id": settings.assembly_pipeline_id,
            "_embedded": {"statuses": [{"id": settings.assembly_status_start}]},
        }
        return [main, assembly]

    def users(self):
        return [{"id": settings.default_sales_owner_id},
                {"id": settings.handoff_owner_id}]

    def custom_fields(self, entity: str):
        if entity == "contacts":
            return [{"id": settings.field_phone}]
        return [{"id": settings.field_wa_variant}, {"id": settings.field_call_window}]

    def close(self):
        return None


@pytest.fixture()
def leadflow_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_leadflow", True)
    monkeypatch.setattr(settings, "enable_handoff", True)
    monkeypatch.setattr(settings, "ami_secret", "secret")
    monkeypatch.setattr(settings, "kommo_token", "token")
    return settings


def test_missing_sms_bot_does_not_block_the_worker(leadflow_on, monkeypatch):
    """SMS-бот не куплен — лид-машина работает, вместо SMS будет задача."""
    monkeypatch.setattr(settings, "sms_bot_id", 0)

    assert preflight.run_preflight(FakeKommo()) == []


def test_wrong_pipeline_id_still_blocks(leadflow_on, monkeypatch):
    """Неверный id воронки — настоящая поломка: работать в таком виде нельзя."""
    monkeypatch.setattr(settings, "sms_bot_id", 12345)

    problems = preflight.run_preflight(FakeKommo(pipeline_ok=False))

    assert any("pipeline_id" in p for p in problems)


def test_empty_token_reported_without_network(monkeypatch):
    monkeypatch.setattr(settings, "kommo_token", "")

    assert preflight.run_preflight() == ["KOMMO_TOKEN пуст"]
