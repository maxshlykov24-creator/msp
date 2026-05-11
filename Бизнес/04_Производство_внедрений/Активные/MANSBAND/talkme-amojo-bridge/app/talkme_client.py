from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def send_text_to_visitor(
    *,
    client_id: str,
    text: str,
    dialog_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    """
    REST Talk-me: POST /chat/message/sendToClient.
    Документация: X-Token, тело client + operator + message.
    """
    settings = settings or get_settings()
    if not settings.talkme_rest_token:
        raise RuntimeError("TALKME_REST_TOKEN не задан")
    if not settings.talkme_operator_login:
        raise RuntimeError("TALKME_OPERATOR_LOGIN не задан")
    base = settings.talkme_api_base.rstrip("/")
    path = settings.talkme_send_message_path
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    headers = {
        "X-Token": settings.talkme_rest_token,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "client": {"id": client_id},
        "operator": {"login": settings.talkme_operator_login},
        "message": {"text": text},
    }
    if dialog_id:
        body["dialog"] = {"id": dialog_id}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, content=raw, headers=headers)


def list_messages(
    *,
    start_local: str,
    stop_local: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    """
    REST Talk-me: POST /chat/message/getList.
    Body: {"dateRange": {"start": "YYYY-MM-DD HH:MM:SS", "stop": "YYYY-MM-DD HH:MM:SS"}}.
    Время — в часовом поясе аккаунта Talk-me.
    """
    settings = settings or get_settings()
    if not settings.talkme_rest_token:
        raise RuntimeError("TALKME_REST_TOKEN не задан")
    base = settings.talkme_api_base.rstrip("/")
    url = base + "/chat/message/getList"
    headers = {
        "X-Token": settings.talkme_rest_token,
        "Content-Type": "application/json",
    }
    body = {"dateRange": {"start": start_local, "stop": stop_local}}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, content=raw, headers=headers)
