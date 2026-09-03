"""Первое сообщение клиенту в WhatsApp отправляет хаб, а не Salesbot.

Повод — 26.08–03.09.2026: бота выключили в UI Kommo, и заявки остались без
ответа. Снаружи это выглядело как «Pleep не пишет первым», хотя причина была в
том, что писать было некому.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import leadflow
from app.actions import Ctx
from app.config import settings
from app.models import FirstTouch

PHONE = "+12139310879"
ROP = 15648532


@pytest.fixture()
def sent(monkeypatch):
    box: list[tuple[str, str]] = []

    def fake_send(phone: str, text: str, channel_id: str = "") -> str:
        box.append((phone, text))
        return "wz-1"

    monkeypatch.setattr("app.wazzup.send_text", fake_send)
    monkeypatch.setattr(settings, "enable_wazzup_first_touch", True)
    monkeypatch.setattr(settings, "wazzup_test_phone", "")
    monkeypatch.setattr(settings, "wa_templates",
                        "1|Hi {name}, LicenseBridge here.||2|Hello {name}!"
                        "||3|Hi {name}||4|Hey {name}")
    return box


def _ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone=PHONE, shadow=False)


def _lead(fake, name="John Smith"):
    contact = fake.add_contact(name=name, phone=PHONE)
    lead_id = fake.add_lead(contact, settings.pipeline_id, settings.status_new,
                            responsible=ROP)
    lead = fake.leads[lead_id]
    lead["_embedded"]["contacts"] = [{"id": contact, "name": name}]
    return lead


def test_first_touch_writes_to_whatsapp(fake, session, sent):
    lead = _lead(fake)

    res = leadflow.send_first_touch(_ctx(fake, session), lead, PHONE)

    assert res["action"] == "first_touch.sent"
    phone, text = sent[0]
    assert phone == PHONE
    # обращаемся по имени, без фамилии
    assert "Hi John," in text or "Hello John!" in text or text.endswith("John")
    row = session.scalar(select(FirstTouch))
    assert row.status == "sent" and row.message_id == "wz-1"


def test_repeated_webhook_does_not_write_twice(fake, session, sent):
    """Kommo повторяет add_lead, а второе «здравствуйте» клиент читает как спам."""
    lead = _lead(fake)
    ctx = _ctx(fake, session)

    leadflow.send_first_touch(ctx, lead, PHONE)
    second = leadflow.send_first_touch(ctx, lead, PHONE)

    assert second["action"] == "first_touch.already"
    assert len(sent) == 1


def test_disabled_flag_keeps_silence(fake, session, sent, monkeypatch):
    monkeypatch.setattr(settings, "enable_wazzup_first_touch", False)
    lead = _lead(fake)

    res = leadflow.send_first_touch(_ctx(fake, session), lead, PHONE)

    assert res["action"] == "first_touch.disabled"
    assert sent == []


def test_missing_template_does_not_invent_text(fake, session, sent, monkeypatch):
    """Текстов вариантов у хаба нет — их переносит владелец из Salesbot.

    Молчание заметно в журнале, а сочинённое письмо клиенту — уже не исправить."""
    monkeypatch.setattr(settings, "wa_templates", "")
    lead = _lead(fake)

    res = leadflow.send_first_touch(_ctx(fake, session), lead, PHONE)

    assert res["action"] == "first_touch.no_template"
    assert sent == []


def test_test_phone_shields_live_leads(fake, session, sent, monkeypatch):
    """Прогон идёт на боевом хабе: пока задан тестовый номер, живым не пишем."""
    monkeypatch.setattr(settings, "wazzup_test_phone", "+15005550101")
    lead = _lead(fake)

    res = leadflow.send_first_touch(_ctx(fake, session), lead, PHONE)

    assert res["action"] == "first_touch.test_mode"
    assert sent == []


def test_failed_send_is_remembered_and_retriable(fake, session, sent, monkeypatch):
    """Wazzup ответил ошибкой: лид не теряется, попытка видна, повтор возможен."""
    def boom(phone: str, text: str, channel_id: str = "") -> str:
        raise RuntimeError("channel not active")

    monkeypatch.setattr("app.wazzup.send_text", boom)
    lead = _lead(fake)
    ctx = _ctx(fake, session)

    res = leadflow.send_first_touch(ctx, lead, PHONE)

    assert res["action"] == "first_touch.failed"
    row = session.scalar(select(FirstTouch))
    assert row.status == "failed" and "channel not active" in row.last_error

    monkeypatch.setattr("app.wazzup.send_text", lambda phone, text, channel_id="": "wz-2")
    assert leadflow.send_first_touch(ctx, lead, PHONE)["action"] == "first_touch.sent"


def test_lead_of_another_pipeline_is_left_alone(fake, session, sent):
    """Сделка «Сборки» — не новая заявка, первым ей не пишем."""
    contact = fake.add_contact(name="Хакоб", phone=PHONE)
    lead_id = fake.add_lead(contact, settings.assembly_pipeline_id,
                            settings.assembly_status_start, responsible=ROP)

    res = leadflow.send_first_touch(_ctx(fake, session), fake.leads[lead_id], PHONE)

    assert res["action"] == "first_touch.skip_pipeline"
    assert sent == []
