"""Клиент TargetSMS: сборка запроса, разбор ответа, ошибки конфигурации/API."""
from __future__ import annotations

import dataclasses

import pytest

from app import targetsms_client
from app.config import settings
from app.targetsms_client import (
    TargetSMSError,
    TargetSMSNotConfigured,
    get_balance,
    get_state,
    send_sms,
    to_targetsms_phone,
)


def _ready(monkeypatch, **overrides):
    base = dict(
        targetsms_enabled=True,
        targetsms_token="test-token",
        targetsms_sender="KerisClub",
    )
    base.update(overrides)
    patched = dataclasses.replace(settings, **base)
    monkeypatch.setattr(targetsms_client, "settings", patched)
    return patched


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or str(self._json)

    def json(self):
        return self._json


def _fake_post(captured: dict, response: _Resp):
    def post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return response

    return post


def test_to_targetsms_phone_normalizes_formats():
    assert to_targetsms_phone("+79991234567") == "79991234567"
    assert to_targetsms_phone("89991234567") == "79991234567"
    assert to_targetsms_phone("79991234567") == "79991234567"
    assert to_targetsms_phone("8 (999) 123-45-67") == "79991234567"


def test_send_sms_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        targetsms_client, "settings", dataclasses.replace(settings, targetsms_enabled=False)
    )
    with pytest.raises(TargetSMSNotConfigured):
        send_sms("+79991234567", "test")


def test_send_sms_raises_when_token_missing(monkeypatch):
    """Без токена канал молчит: не дергаем шлюз с пустым Bearer."""
    _ready(monkeypatch, targetsms_token="")
    with pytest.raises(TargetSMSNotConfigured):
        send_sms("+79991234567", "test")


def test_send_sms_builds_payload_with_sender(monkeypatch):
    _ready(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(targetsms_client.requests, "post", _fake_post(captured, _Resp(200, {
        "sms": [{"number_sms": "1", "id_sms": "2731379410", "parts": "1", "action": "send"}],
    })))

    result = send_sms("+79991234567", "Keris Club. Спасибо за запись. Мастер Светлана, 17.08 20:00.")

    assert captured["url"] == "https://sms.targetsms.ru/sendsmsjson.php"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    body = captured["json"]
    assert "security" not in body
    assert body["type"] == "sms"
    message = body["message"][0]
    assert message["sender"] == "KerisClub"
    assert message["text"].startswith("Keris Club. Спасибо за запись")
    assert message["abonent"] == [{"phone": "79991234567", "number_sms": "1"}]
    assert result["id_sms"] == "2731379410"


def test_send_sms_raises_on_request_error(monkeypatch):
    """`error` — проблема самого запроса: неверный логин, битый JSON."""
    _ready(monkeypatch)
    monkeypatch.setattr(
        targetsms_client.requests, "post",
        _fake_post({}, _Resp(200, {"error": "Неправильный логин или пароль"})),
    )
    with pytest.raises(TargetSMSError, match="логин"):
        send_sms("+79991234567", "test")


def test_send_sms_raises_when_action_is_not_send(monkeypatch):
    """Отказ по конкретному сообщению приходит текстом в `action`, а не в `error`."""
    _ready(monkeypatch)
    monkeypatch.setattr(
        targetsms_client.requests, "post",
        _fake_post({}, _Resp(200, {"sms": [{"number_sms": "1", "action": "Нет отправителя"}]})),
    )
    with pytest.raises(TargetSMSError, match="Нет отправителя"):
        send_sms("+79991234567", "test")


def test_send_sms_raises_on_http_5xx(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        targetsms_client.requests, "post",
        _fake_post({}, _Resp(500, {}, text="internal error")),
    )
    with pytest.raises(TargetSMSError):
        send_sms("+79991234567", "test")


def test_get_state_maps_ids_to_states(monkeypatch):
    _ready(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(targetsms_client.requests, "post", _fake_post(captured, _Resp(200, {
        "state": [
            {"id_sms": "2731379410", "state": "deliver", "num_parts": "1", "price": "9.82"},
            {"id_sms": "2731379411", "state": "not_deliver"},
        ],
    })))

    assert get_state(["2731379410", "2731379411"]) == {
        "2731379410": "deliver",
        "2731379411": "not_deliver",
    }
    assert captured["json"]["type"] == "state"
    assert captured["json"]["get_state"] == ["2731379410", "2731379411"]


def test_get_state_without_ids_does_not_call_api(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        targetsms_client.requests, "post",
        lambda *a, **kw: pytest.fail("пустой список id не должен дергать шлюз"),
    )
    assert get_state([]) == {}


def test_get_balance_reads_money_value(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        targetsms_client.requests, "post",
        _fake_post({}, _Resp(200, {"money": {"currency": "RUR", "value": "150.5"}})),
    )
    assert get_balance() == 150.5
