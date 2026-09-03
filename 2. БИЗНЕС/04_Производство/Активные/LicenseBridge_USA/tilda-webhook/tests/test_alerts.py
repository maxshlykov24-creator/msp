"""Алерт на новую заявку: клиент узнаёт о лиде от системы, а не от клиента.

Повод — 28.08.2026: заявки падали ночью на менеджера вне смены, и первым это
замечал владелец, разбирая просроченные задачи утром.
"""
from __future__ import annotations

import pytest

from app import alerts
from app.config import settings


class FakeClient:
    def __init__(self, users=None, fail=False):
        self._users = users or [{"id": 15648532, "name": "Александра Терсинских"}]
        self._fail = fail

    def users(self):
        if self._fail:
            raise RuntimeError("kommo unavailable")
        return self._users


@pytest.fixture()
def sent(monkeypatch):
    box: list[tuple[str, str]] = []

    def fake_notify(text: str, key: str = "", cooldown: float = 0.0) -> bool:
        box.append((text, key))
        return True

    monkeypatch.setattr(alerts, "notify", fake_notify)
    monkeypatch.setattr(alerts, "_user_names", {"map": None, "ts": 0.0})
    monkeypatch.setattr(settings, "alert_new_lead", True)
    return box


LEAD = {
    "id": 30436589,
    "name": "FB NY",
    "responsible_user_id": 15648532,
    "_embedded": {"tags": [{"name": "facebook"}]},
}


def test_new_lead_names_owner_and_links_card(sent):
    assert alerts.new_lead(FakeClient(), LEAD, "+18184632924") is True
    text, key = sent[0]
    assert "Новая заявка" in text
    assert "Александра Терсинских" in text
    assert "/leads/detail/30436589" in text
    assert "+18184632924" in text
    assert "facebook" in text
    # key по сделке: повтор вебхука Kommo не даёт второго сообщения
    assert key == "new_lead:30436589"


def test_new_lead_silent_when_disabled(sent, monkeypatch):
    monkeypatch.setattr(settings, "alert_new_lead", False)

    assert alerts.new_lead(FakeClient(), LEAD) is False
    assert sent == []


def test_new_lead_survives_users_failure(sent):
    """Kommo не отдал справочник — алерт всё равно уходит, с id вместо имени."""
    assert alerts.new_lead(FakeClient(fail=True), LEAD) is True
    assert "id 15648532" in sent[0][0]


def test_new_lead_without_owner(sent):
    lead = dict(LEAD, responsible_user_id=None)

    assert alerts.new_lead(FakeClient(), lead) is True
    assert "не назначен" in sent[0][0]


def test_new_lead_escapes_html_in_name(sent):
    lead = dict(LEAD, name="<b>FB</b> & NY")

    alerts.new_lead(FakeClient(), lead)
    assert "&lt;b&gt;FB&lt;/b&gt; &amp; NY" in sent[0][0]
