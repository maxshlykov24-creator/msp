from __future__ import annotations

import ipaddress
import json
import socket
import tempfile
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.call_rules import Analysis, analysis_prompt
from app.config import settings


class RemoteFailure(Exception):
    """Только безопасный код ошибки, без URL записей, токенов и ответа с ПДн."""


class AmbiguousSubmission(RemoteFailure):
    pass


def validate_recording_url(url: str, allowed_hosts: set[str], resolve: bool = True) -> None:
    u = urlsplit(url)
    if (u.scheme != "https" or not u.hostname or u.hostname.lower() not in allowed_hosts
            or u.username or u.password or u.port not in (None, 443)):
        raise RemoteFailure("recording_host_not_allowed")
    if resolve:
        try:
            addresses = socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise RemoteFailure("recording_dns_failed") from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise RemoteFailure("recording_private_address")


def download_audio(url: str):
    allowed = {h.strip().lower() for h in settings.calls_recording_hosts.split(",") if h.strip()}
    spool = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    try:
        with httpx.Client(timeout=httpx.Timeout(90, connect=15), follow_redirects=False) as client:
            for _ in range(5):
                validate_recording_url(url, allowed)
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    if resp.status_code != 200:
                        raise RemoteFailure(f"recording_http_{resp.status_code}")
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > settings.calls_max_audio_mb * 1024 * 1024:
                            raise RemoteFailure("recording_too_large")
                        spool.write(chunk)
                    spool.seek(0)
                    prefix = spool.read(64)
                    if total < 800 or prefix.lstrip().lower().startswith((b"<!doctype", b"<html", b"{")):
                        raise RemoteFailure("recording_not_audio")
                    mime = resp.headers.get("content-type", "").split(";")[0].lower()
                    if not (mime.startswith("audio/") or mime in ("application/octet-stream", "binary/octet-stream")):
                        raise RemoteFailure("recording_invalid_content_type")
                    spool.seek(0)
                    return spool, mime if mime.startswith("audio/") else "audio/mpeg"
            raise RemoteFailure("recording_too_many_redirects")
    except Exception:
        spool.close()
        raise


class NexaraClient:
    def __init__(self):
        self.client = httpx.Client(
            base_url=settings.nexara_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.nexara_api_key}"},
            timeout=httpx.Timeout(120, connect=20),
        )

    def close(self):
        self.client.close()

    def submit(self, audio, mime: str, direction: str, occurred_at: int) -> str:
        try:
            resp = self.client.post("audio/transcriptions/async",
                files={"file": ("call.mp3", audio, mime)},
                data={"model": "nexara-ru", "task": "diarize", "response_format": "verbose_json",
                      "roles": json.dumps(["Менеджер", "Клиент"], ensure_ascii=False),
                      "prompt": analysis_prompt(direction, occurred_at),
                      "json_schema": json.dumps(Analysis.model_json_schema(), ensure_ascii=False)})
        except httpx.TransportError:
            raise AmbiguousSubmission("nexara_submit_transport_ambiguous") from None
        if resp.status_code >= 500:
            raise AmbiguousSubmission(f"nexara_submit_http_{resp.status_code}_ambiguous")
        if resp.status_code >= 400:
            raise RemoteFailure(f"nexara_submit_http_{resp.status_code}")
        try:
            job_id = resp.json().get("job_id")
        except ValueError:
            job_id = None
        if not job_id:
            raise AmbiguousSubmission("nexara_submit_missing_job_id")
        return str(job_id)

    def poll(self, job_id: str) -> dict:
        try:
            resp = self.client.get("audio/transcriptions/async/" + quote(job_id, safe=""))
        except httpx.TransportError:
            raise RemoteFailure("nexara_poll_transport") from None
        if resp.status_code >= 400:
            raise RemoteFailure(f"nexara_poll_http_{resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise RemoteFailure("nexara_poll_invalid_json") from None
