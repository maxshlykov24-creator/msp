"""Сводка Карины уходит один раз после 22:15."""
from __future__ import annotations

import dataclasses
from datetime import datetime

from app import owner_digest
from app.config import settings
from app.models import OwnerDigest


def test_digest_once_after_close(monkeypatch, db_session):
    moment = datetime(2026, 9, 30, 22, 20)
    monkeypatch.setattr(
        owner_digest, "settings",
        dataclasses.replace(settings, karina_bot_token="t", karina_role_ids=["435207481"]),
    )
    monkeypatch.setattr(owner_digest, "count_pipeline_created", lambda *args, **kwargs: 1)
    sent = []
    monkeypatch.setattr(owner_digest, "tg_send_message", lambda *args, **kwargs: sent.append(args[2]) or True)

    first = owner_digest.maybe_send(db_session, moment)
    second = owner_digest.maybe_send(db_session, moment)

    assert first["sent"] is True
    assert second["reason"] == "already"
    assert len(sent) == 1
    assert "Визитов:" in sent[0]
    assert db_session.get(OwnerDigest, "2026-09-30") is not None


def test_sunday_and_month_end_share_one_message(monkeypatch, db_session):
    moment = datetime(2026, 5, 31, 22, 15)  # воскресенье и последний день мая
    monkeypatch.setattr(
        owner_digest, "settings",
        dataclasses.replace(settings, karina_bot_token="t", karina_role_ids=["1"]),
    )
    monkeypatch.setattr(owner_digest, "count_pipeline_created", lambda *args, **kwargs: 0)
    sent = []
    monkeypatch.setattr(owner_digest, "tg_send_message", lambda *args, **kwargs: sent.append(args[2]) or True)

    owner_digest.maybe_send(db_session, moment)

    assert "<b>Неделя</b>" in sent[0]
    assert "<b>Месяц</b>" in sent[0]


def test_digest_list_excludes_role(monkeypatch, db_session):
    moment = datetime(2026, 9, 30, 22, 20)
    monkeypatch.setattr(
        owner_digest, "settings",
        dataclasses.replace(
            settings,
            karina_bot_token="t",
            karina_role_ids=["1", "900"],
            digest_chat_ids=["1"],
        ),
    )
    monkeypatch.setattr(owner_digest, "count_pipeline_created", lambda *args, **kwargs: 0)
    sent = []
    monkeypatch.setattr(
        owner_digest, "tg_send_message",
        lambda *args, **kwargs: sent.append(args[1]) or True,
    )

    owner_digest.maybe_send(db_session, moment)

    assert sent == ["1"]
