"""Клиент Nexara STT (docs.nexara.ru): голос → текст.

Используем ``response_format=json`` без диаризации — нужен только текст.
Опционально передаём ``prompt`` со словарём брендов/кодов, чтобы Whisper реже
коверкал названия (поддержка prompt не гарантирована докой — при ошибке
повторяем запрос без него).
"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)

NEXARA_URL = "https://api.nexara.ru/api/v1/audio/transcriptions"


class NexaraError(RuntimeError):
    pass


class NexaraClient:
    def __init__(self, api_key: str, timeout_sec: int = 180):
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "voice.ogg",
        content_type: str = "audio/ogg",
        prompt: str | None = None,
    ) -> str:
        if not self.api_key:
            raise NexaraError("NEXARA_API_KEY не задан")
        text = await self._request(audio_bytes, filename, content_type, prompt)
        return text

    async def _request(
        self, audio_bytes: bytes, filename: str, content_type: str, prompt: str | None
    ) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=filename, content_type=content_type)
        form.add_field("response_format", "json")
        if prompt:
            form.add_field("prompt", prompt)

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(NEXARA_URL, headers=headers, data=form) as resp:
                body = await resp.text()
                if resp.status == 400 and prompt:
                    # возможно, prompt не поддержан — пробуем без него
                    log.warning("Nexara 400 с prompt, повтор без prompt")
                    return await self._request(audio_bytes, filename, content_type, None)
                log.info(
                    "Nexara запрос: filename=%r content_type=%r size=%d байт -> HTTP %d, тело: %s",
                    filename, content_type, len(audio_bytes), resp.status, body[:300],
                )
                if resp.status != 200:
                    raise NexaraError(f"Nexara HTTP {resp.status}: {body[:300]}")
                try:
                    import json

                    data = json.loads(body)
                except ValueError as e:
                    raise NexaraError(f"Nexara: не JSON: {body[:200]}") from e
                return self._extract_text(data).strip()

    @staticmethod
    def _extract_text(data: dict) -> str:
        """Nexara оборачивает результат в {"transcription": {"text": ...}},
        но на всякий случай поддержим и плоский {"text": ...}."""
        if isinstance(data.get("text"), str) and data["text"]:
            return data["text"]
        inner = data.get("transcription")
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            return inner["text"]
        return ""
