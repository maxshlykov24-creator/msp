"""
Транскрибация через Nexara API (https://docs.nexara.ru/guides).
"""

import json
import logging
import mimetypes
from pathlib import Path

import aiohttp

from config import NEXARA_API_KEY, NEXARA_TRANSCRIBE_URL, TRANSCRIBE_TIMEOUT_SEC

logger = logging.getLogger(__name__)


async def transcribe_voice(file_path: str, language: str = "ru") -> str:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Нет файла: {path}")

    key = (NEXARA_API_KEY or "").strip()
    if not key or "YOUR_" in key:
        raise RuntimeError("NEXARA_API_KEY пустой или плейсхолдер — проверь config.py / .env")

    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"

    size = path.stat().st_size
    logger.info(
        "Nexara: POST %s (%s байт), mime=%s",
        path.name,
        size,
        mime,
    )

    headers = {"Authorization": f"Bearer {key}"}

    # Между байтами ответа Nexara может быть длинная пауза — не обрезать sock_read
    # искусственным потолком (иначе JSON обрывается, а wait_for ещё ждёт).
    sock_read = max(120, int(TRANSCRIBE_TIMEOUT_SEC) + 90)

    async with aiohttp.ClientSession() as session:
        with path.open("rb") as audio_file:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                audio_file,
                filename=path.name,
                content_type=mime,
            )
            # verbose_json даёт segments — если поле text когда-то укорочено, соберём из сегментов.
            data.add_field("response_format", "verbose_json")

            async with session.post(
                NEXARA_TRANSCRIBE_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=30,
                    sock_read=sock_read,
                ),
            ) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    logger.error(
                        "Nexara HTTP %s: %s",
                        resp.status,
                        body_text[:1200],
                    )
                    raise RuntimeError(
                        f"Nexara HTTP {resp.status}: {body_text[:800]}"
                    )
                try:
                    payload = json.loads(body_text)
                except json.JSONDecodeError as e:
                    logger.error("Nexara не JSON: %r", body_text[:500])
                    raise RuntimeError(
                        f"Nexara вернула не JSON: {body_text[:400]!r}"
                    ) from e

    if not isinstance(payload, dict):
        raise RuntimeError(f"Nexara: не объект JSON: {payload!r}"[:500])

    text = payload.get("text")
    segments = payload.get("segments")
    from_segments = ""
    if isinstance(segments, list) and segments:
        from_segments = "".join(
            str(s.get("text") or "") for s in segments
        ).strip()

    cand_plain = str(text).strip() if text else ""
    if from_segments and len(from_segments) > len(cand_plain):
        out = from_segments
        logger.info(
            "Nexara: взяли текст из segments (len=%s, text len=%s, duration=%s)",
            len(out),
            len(cand_plain),
            payload.get("duration"),
        )
    elif cand_plain:
        out = cand_plain
        logger.info(
            "Nexara: OK, длина текста=%s (duration=%s, сегментов=%s)",
            len(out),
            payload.get("duration"),
            len(segments) if isinstance(segments, list) else 0,
        )
    elif from_segments:
        out = from_segments
        logger.info(
            "Nexara: только segments, len=%s, duration=%s",
            len(out),
            payload.get("duration"),
        )
    else:
        raise RuntimeError(
            f"Nexara: нет текста (text/segments): {payload!r}"[:500]
        )
    return out


async def transcribe_video(file_path: str, language: str = "ru") -> str:
    return await transcribe_voice(file_path, language)
