"""Отзыв клиента сразу уходит админу и роли Карины, без второго сообщения одному чату."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from types import SimpleNamespace

from app import notify_admins, notify_karina
from app.config import settings


def test_review_text_has_visit_and_body(monkeypatch):
    booking = SimpleNamespace(
        id="KERIS-1175",
        owner_name="Максим",
        owner_phone="+79263097458",
        pet_name="Тестик",
        service_id="dog_hygiene",
        master_id="svetlana",
        addon_ids=[],
        free_addon_ids=[],
        starts_at=datetime(2026, 9, 29, 11, 0),
    )
    review = SimpleNamespace(
        author_name="Максим",
        owner_phone="+79263097458",
        stars=5,
        text="Бережно и <спокойно>",
    )
    monkeypatch.setattr(notify_karina, "_lookups", lambda _booking: ("Гигиена", "Светлана", {}))

    text = notify_karina.review_text(review, booking)

    assert "KERIS-1175" in text
    assert "Максим" in text
    assert "+79263097458" in text
    assert "Светлана" in text
    assert "Гигиена" in text
    assert "Тестик" in text
    assert "29.09" in text and "11:00" in text
    assert "5 из 5" in text
    assert "Бережно и &lt;спокойно&gt;" in text
    assert "dog_hygiene" not in text
    assert "svetlana" not in text


def test_review_reaches_admin_and_karina_once(monkeypatch):
    monkeypatch.setattr(
        notify_admins, "settings",
        dataclasses.replace(
            settings,
            karina_bot_token="admin",
            admin_notify_chat_ids=["435207481"],
            karina_role_ids=["435207481", "900"],
        ),
    )
    sent = []

    def fake_send(token, chat_id, text, **_kwargs):
        sent.append((token, str(chat_id), text, _kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(notify_admins, "tg_send_message", fake_send)

    delivered = notify_admins.notify_review("★ отзыв")

    assert delivered == 2
    assert [item[1] for item in sent] == ["435207481", "900"]
    assert all(item[3] for item in sent)
