from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from app.amojo_sign import build_amojo_signature
from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def _signed_post(
    settings: Settings,
    path: str,
    body_obj: dict[str, Any],
) -> httpx.Response:
    body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    date, md5_hex, sig = build_amojo_signature(
        channel_secret=settings.amo_channel_secret,
        method="POST",
        content_type="application/json",
        body=body,
        path=path,
    )
    url = settings.amojo_host.rstrip("/") + path
    headers = {
        "Date": date,
        "Content-Type": "application/json",
        "Content-MD5": md5_hex,
        "X-Signature": sig,
        "User-Agent": "talkme-amojo-bridge/1.0",
    }
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, content=body, headers=headers)


def post_new_message(
    *,
    scope_id: str,
    payload: dict[str, Any],
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    path = f"/v2/origin/custom/{scope_id}"
    body = {"event_type": "new_message", "payload": payload}
    return _signed_post(settings, path, body)


def post_connect(
    *,
    channel_id: str,
    account_amojo_id: str,
    title: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    path = f"/v2/origin/custom/{channel_id}/connect"
    body = {
        "account_id": account_amojo_id,
        "title": title,
        "hook_api_version": "v2",
        "is_time_window_disabled": True,
    }
    return _signed_post(settings, path, body)


def post_disconnect(
    *,
    channel_id: str,
    account_amojo_id: str,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    path = f"/v2/origin/custom/{channel_id}/disconnect"
    body = {"account_id": account_amojo_id}
    body_raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    from app.amojo_sign import build_amojo_signature
    date, md5_hex, sig = build_amojo_signature(
        channel_secret=settings.amo_channel_secret,
        method="DELETE",
        content_type="application/json",
        body=body_raw,
        path=path,
    )
    url = settings.amojo_host.rstrip("/") + path
    headers = {
        "Date": date,
        "Content-Type": "application/json",
        "Content-MD5": md5_hex,
        "X-Signature": sig,
        "User-Agent": "talkme-amojo-bridge/1.0",
    }
    with httpx.Client(timeout=30.0) as client:
        return client.request("DELETE", url, content=body_raw, headers=headers)


def post_delivery_status(
    *,
    scope_id: str,
    message_amojo_id: str,
    delivery_status: int,
    error_code: Optional[int] = None,
    error_message: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> httpx.Response:
    settings = settings or get_settings()
    path = f"/v2/origin/custom/{scope_id}/{message_amojo_id}/delivery_status"
    body: dict[str, Any] = {"delivery_status": delivery_status}
    if error_code is not None:
        body["error_code"] = error_code
    if error_message:
        body["error_message"] = error_message
    return _signed_post(settings, path, body)
