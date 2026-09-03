"""Формат comment при пуше записи в YCLIENTS.

С 2026-08-17 кличка/порода/вес/дата рождения питомца в comment не попадают —
уходят отдельными доп. полями записи (см. test_yclients_custom_fields.py).
Comment — только реальный текст клиента + служебные флаги без своего доп. поля.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.yclients_client import format_yclients_comment


def _b(**kwargs):
    base = dict(
        id="KERIS-1011",
        pet_name="Боня",
        pet_breed="мальтипу",
        pet_type=SimpleNamespace(value="dog"),
        pet_size="XS",
        pet_weight_kg=None,
        comment="",
        media_consent=True,
        subscription_id=None,
        visits_charged=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_comment_empty_without_client_note():
    text = format_yclients_comment(_b())
    assert text == "(KERIS-1011)"
    assert "Боня" not in text
    assert "мальтипу" not in text


def test_comment_has_only_client_note():
    text = format_yclients_comment(_b(comment="боится фена"))
    assert text == "боится фена (KERIS-1011)"


def test_comment_with_client_note_and_no_media():
    text = format_yclients_comment(_b(comment="боится фена", media_consent=False))
    assert text == "боится фена. нет согласия на фото (KERIS-1011)"


def test_comment_no_keris_server_prefix():
    text = format_yclients_comment(_b(comment="тест"))
    assert "Keris" not in text
    assert "Server" not in text


def test_comment_keeps_id_when_truncated():
    text = format_yclients_comment(_b(comment="x" * 400))
    assert text.endswith("(KERIS-1011)")
    assert len(text) <= 255


def test_comment_subscription_note():
    text = format_yclients_comment(_b(subscription_id=1, visits_charged=1.0))
    assert text == "абонемент, списано 1 (KERIS-1011)"
