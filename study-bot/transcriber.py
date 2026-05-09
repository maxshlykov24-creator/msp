"""
Транскрибация через Nexara API (https://docs.nexara.ru/guides).
"""

import mimetypes
from pathlib import Path

import aiohttp

from config import NEXARA_API_KEY, NEXARA_TRANSCRIBE_URL


async def transcribe_voice(file_path: str, language: str = "ru") -> str:
    path = Path(file_path)
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"

    headers = {"Authorization": f"Bearer {NEXARA_API_KEY}"}

    async with aiohttp.ClientSession() as session:
        with path.open("rb") as audio_file:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                audio_file,
                filename=path.name,
                content_type=mime,
            )
            data.add_field("response_format", "json")

            async with session.post(
                NEXARA_TRANSCRIBE_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"Nexara HTTP {resp.status}: {body[:800]}"
                    )
                payload = await resp.json()

    text = payload.get("text") if isinstance(payload, dict) else None
    if not text:
        raise RuntimeError(f"Nexara: нет поля text в ответе: {payload!r}"[:500])
    return str(text).strip()


async def transcribe_video(file_path: str, language: str = "ru") -> str:
    return await transcribe_voice(file_path, language)
