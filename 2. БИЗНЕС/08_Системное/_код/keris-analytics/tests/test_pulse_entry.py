"""Подпись входа в пульс: чужой секрет и просрочка не открывают сессию."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64encode

from app.auth import verify_pulse_entry
from app.config import settings


def _sign(secret: str, chat_id: str, exp: int) -> str:
    raw = urlsafe_b64encode(
        json.dumps({"chat_id": chat_id, "exp": exp}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def test_entry_accepts_fresh_token(monkeypatch):
    monkeypatch.setattr(settings, "pulse_entry_secret", "test-secret")
    token = _sign("test-secret", "435207481", int(time.time()) + 60)
    assert verify_pulse_entry(token) == "435207481"


def test_entry_rejects_wrong_secret_and_expiry(monkeypatch):
    monkeypatch.setattr(settings, "pulse_entry_secret", "test-secret")
    wrong = _sign("other", "435207481", int(time.time()) + 60)
    old = _sign("test-secret", "435207481", int(time.time()) - 10)
    assert verify_pulse_entry(wrong) is None
    assert verify_pulse_entry(old) is None
