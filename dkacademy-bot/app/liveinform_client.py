from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class LiveinformError(RuntimeError):
    pass


class TrackResult:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        r = raw.get("result") or {}
        if not isinstance(r, dict):
            r = {}
        self.result: dict[str, Any] = r
        self.liveinform_id: str = str(r.get("liveinform_id", "") or "")
        self.tracking: str = str(r.get("tracking", "") or "")
        self.status_code: str = str(r.get("status", "") or "")
        self.phone: str = str(r.get("phone", "") or "")
        self.lastcheck: str = str(r.get("lastcheck", "") or "")
        self.delivery: str = str(r.get("delivery", "") or "")
        tr = r.get("track")
        self.track_timeline: list[dict[str, Any]] = tr if isinstance(tr, list) else []


def track_liveinform(*, liveinform_id: str) -> TrackResult:
    s = get_settings()
    api_id = (s.liveinform_api_id or "").strip()
    if not api_id:
        raise LiveinformError("LIVEINFORM_API_ID не задан")
    if not liveinform_id.strip():
        raise LiveinformError("Пустой liveinform_id")

    payload = {"api_id": api_id, "liveinform_id": str(liveinform_id).strip()}
    with httpx.Client(timeout=45.0) as client:
        r = client.post(s.liveinform_track_url, data=payload)
        body: Any = {}
        try:
            body = r.json()
        except Exception:
            body = {}
        top_status = ""
        if isinstance(body, dict):
            top_status = str(body.get("status", "") or "")
        if r.status_code >= 400:
            raise LiveinformError(f"HTTP {r.status_code}: {body!s}")
        if top_status and top_status.upper() != "OK":
            err = ""
            if isinstance(body, dict) and body.get("error"):
                err = str(body.get("error"))
            raise LiveinformError(f"Ответ LiveInform: {top_status} {err}".strip())

    if not isinstance(body, dict):
        raise LiveinformError("Некорректный JSON")

    out = TrackResult(body)
    if not out.tracking and not out.result:
        raise LiveinformError("Пустой result в ответе track")
    return out

