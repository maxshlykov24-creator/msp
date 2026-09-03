"""Код входа в кабинет: Telegram → MAX. SMS-канал для входа не включён."""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from app import max_bind, sms_otp, telegram_bind
from app.config import settings
from app.models import SmsOtp
from app.sms_otp import OtpError, request_code, sms_otp_text, verify_code


def _channels(monkeypatch, **overrides):
    """Каналы доставки: по умолчанию все выключены (клиент нигде не привязан)."""
    monkeypatch.setattr(
        sms_otp,
        "settings",
        dataclasses.replace(settings, **overrides),
    )


def test_sms_otp_text_matches_registered_template():
    """Текст держим под согласованный шаблон `Код для входа в Keris Club: %d`,
    хотя по SMS код пока не шлём."""
    assert sms_otp_text("4821") == "Код для входа в Keris Club: 4821"


def test_request_code_prefers_telegram(monkeypatch, db_session):
    _channels(monkeypatch, client_bot_token="tg-token")
    telegram_bind.upsert_client(db_session, "+79991112233", 555000111222)
    db_session.commit()
    sent: list[tuple] = []
    monkeypatch.setattr(
        sms_otp, "tg_send_message",
        lambda token, chat_id, text: sent.append((chat_id, text)) or True,
    )
    monkeypatch.setattr(sms_otp.secrets, "randbelow", lambda n: 4821)

    result = request_code(db_session, "8 (999) 111-22-33")

    assert result["ok"] is True
    assert result["channel"] == "telegram"
    assert result["need_bind"] is False
    assert sent[0][0] == 555000111222
    assert "4821" in sent[0][1]
    assert db_session.query(SmsOtp).one().phone == "+79991112233"


def test_request_code_falls_back_to_max(monkeypatch, db_session):
    _channels(monkeypatch, max_bot_token="max-token")
    max_bind.upsert_client(db_session, "+79991112233", 775149185013)
    db_session.commit()
    sent: list[tuple] = []
    monkeypatch.setattr(
        sms_otp.max_http, "send_message",
        lambda user_id, text, buttons=None: sent.append((user_id, text)) or True,
    )
    monkeypatch.setattr(sms_otp.secrets, "randbelow", lambda n: 4821)

    result = request_code(db_session, "+79991112233")

    assert result["channel"] == "max"
    assert sent[0][0] == 775149185013


def test_request_code_without_channels_asks_to_bind(monkeypatch, db_session):
    """Бота нет — это не 503, а приглашение в мессенджер. SMS с кодом не шлём."""
    _channels(monkeypatch)
    monkeypatch.setattr(sms_otp.secrets, "randbelow", lambda n: 4821)

    result = request_code(db_session, "+79991112233")

    assert result["ok"] is False
    assert result["need_bind"] is True
    assert result["links"]["telegram"].startswith("https://t.me/")
    assert result["links"]["max"].startswith("https://max.ru/")
    # Код всё равно выдан: клиент сейчас привяжет бота и получит его туда.
    assert db_session.query(SmsOtp).one().channel == ""


def test_deliver_pending_sends_code_after_bind(monkeypatch, db_session):
    _channels(monkeypatch)
    monkeypatch.setattr(sms_otp.secrets, "randbelow", lambda n: 4821)
    request_code(db_session, "+79991112233")

    _channels(monkeypatch, client_bot_token="tg-token")
    sent = []
    monkeypatch.setattr(
        sms_otp, "tg_send_message",
        lambda token, chat_id, text: sent.append((chat_id, text)) or True,
    )
    telegram_bind.upsert_client(db_session, "+79991112233", 555000111222)
    db_session.commit()

    assert sms_otp.deliver_pending(db_session, "+79991112233") == "telegram"
    assert len(sent) == 1
    # Досылка — это новый код (в базе только хеш), прошлый погашен.
    assert verify_code(db_session, "+79991112233", "4821") == {"ok": True, "phone": "+79991112233"}
    assert sms_otp.deliver_pending(db_session, "+79991112233") == ""


def _issued_via_telegram(monkeypatch, db_session):
    """Выданный и доставленный код 4821 — общая подготовка для проверок verify."""
    _channels(monkeypatch, client_bot_token="tg-token")
    telegram_bind.upsert_client(db_session, "+79991112233", 555000111222)
    db_session.commit()
    monkeypatch.setattr(sms_otp, "tg_send_message", lambda token, chat_id, text: True)
    monkeypatch.setattr(sms_otp.secrets, "randbelow", lambda n: 4821)
    request_code(db_session, "+79991112233")


def test_verify_accepts_correct_code(monkeypatch, db_session):
    _issued_via_telegram(monkeypatch, db_session)
    assert verify_code(db_session, "+79991112233", "4821") == {"ok": True, "phone": "+79991112233"}
    row = db_session.query(SmsOtp).one()
    assert row.consumed_at is not None


def test_verify_rejects_wrong_code(monkeypatch, db_session):
    _issued_via_telegram(monkeypatch, db_session)
    with pytest.raises(OtpError) as exc:
        verify_code(db_session, "+79991112233", "0000")
    assert exc.value.message == "Неверный код."


def test_resend_too_soon_returns_timer(monkeypatch, db_session):
    """Повтор в пределах минуты: не ошибка, а таймер — прошлый код ещё живой."""
    _issued_via_telegram(monkeypatch, db_session)

    again = request_code(db_session, "+79991112233")

    assert again["ok"] is True
    assert 0 < again["retry_after"] <= sms_otp.RESEND_SEC
    assert db_session.query(SmsOtp).count() == 1
    assert verify_code(db_session, "+79991112233", "4821")["ok"] is True


def test_code_survives_page_close(monkeypatch, db_session):
    """Страницу закрыли и открыли заново — код с прошлой попытки подходит."""
    _issued_via_telegram(monkeypatch, db_session)
    row = db_session.query(SmsOtp).one()
    row.created_at = row.created_at - timedelta(minutes=4)
    db_session.commit()

    assert verify_code(db_session, "+79991112233", "4821")["ok"] is True


def test_code_expires(monkeypatch, db_session):
    _issued_via_telegram(monkeypatch, db_session)
    row = db_session.query(SmsOtp).one()
    row.expires_at = row.created_at - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(OtpError):
        verify_code(db_session, "+79991112233", "4821")
