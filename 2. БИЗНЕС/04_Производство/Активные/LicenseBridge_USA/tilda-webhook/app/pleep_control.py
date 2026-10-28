"""Explicit Pleep per-thread control with ownership checks and live readback.

The application ID in the dashboard URL is NOT the assistant ID in this API.
Resuming can answer a pending client message; callers must account for that.
"""
from __future__ import annotations

import re
from typing import Any

import httpx


class PleepControlError(RuntimeError):
    pass


class PleepControlUncertain(PleepControlError):
    """A write may have arrived. Reconcile instead of blindly replaying it."""


class PleepControlClient:
    def __init__(self, key: str, assistant_id: str, *, transport=None):
        if not key or not assistant_id:
            raise ValueError("Pleep key and verified assistant ID required")
        self.assistant_id = assistant_id
        self.http = httpx.Client(
            base_url="https://microservice.pleep.app/api/v1/",
            headers={"X-API-Key": key}, timeout=20, transport=transport,
            follow_redirects=False,
        )

    def close(self):
        self.http.close()

    def _post(self, path: str, body: dict, *, mutation=False) -> dict[str, Any]:
        try:
            response = self.http.post(path, json=body)
        except httpx.TransportError:
            error = PleepControlUncertain if mutation else PleepControlError
            raise error("Pleep transport failure; no automatic replay") from None
        if response.status_code >= 500 and mutation:
            raise PleepControlUncertain("Pleep write outcome unknown")
        try:
            result = response.json()
        except ValueError:
            error = PleepControlUncertain if mutation else PleepControlError
            raise error("Pleep response is not JSON") from None
        if not isinstance(result, dict):
            error = PleepControlUncertain if mutation else PleepControlError
            raise error("Pleep response is not an object")
        if not 200 <= response.status_code < 300 or result.get("ok") is not True:
            # Do not include full upstream bodies: they may contain client data.
            code = str((result.get("error") or {}).get("code") or "API_ERROR")
            raise PleepControlError(f"Pleep HTTP {response.status_code}: {code}")
        data = result.get("data")
        if not isinstance(data, dict):
            error = PleepControlUncertain if mutation else PleepControlError
            raise error("Pleep response has no data object")
        return data

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"thread_[0-9a-fA-F-]{36}", thread_id):
            raise ValueError("Real Pleep thread ID required; legacy is not an ID")
        data = self._post("leads/get-lead", {"thread_id": thread_id})
        if data.get("thread_id") != thread_id or data.get("assistant_id") != self.assistant_id:
            raise PleepControlError("Pleep thread/assistant mismatch")
        if data.get("message_handler") not in ("AI", "OPERATOR"):
            raise PleepControlError("Unknown Pleep handler state")
        return data

    def set_enabled(self, thread_id: str, enabled: bool, *, resume_pending_ack=False) -> dict:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a JSON boolean")
        if enabled and not resume_pending_ack:
            raise ValueError("Resuming may answer a pending client message")
        # Verify ownership before any mutation, including an OFF request.
        self.get_thread(thread_id)
        data = self._post("leads/set-ai", {"thread_id": thread_id,
                                          "ai_enabled": enabled}, mutation=True)
        if data.get("thread_id") != thread_id or data.get("ai_enabled") is not enabled:
            raise PleepControlUncertain("Pleep did not confirm requested state")
        if type(data.get("answered_pending_message")) is not bool:
            raise PleepControlUncertain("Pleep did not confirm pending-message outcome")
        try:
            actual = self.get_thread(thread_id)
        except PleepControlError:
            raise PleepControlUncertain("Pleep write acknowledged; readback failed") from None
        expected = "AI" if enabled else "OPERATOR"
        if actual["message_handler"] != expected:
            raise PleepControlUncertain("Pleep readback differs from requested handler")
        return data
