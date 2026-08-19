"""
Извлечение текста из YouTube через автогенерируемые/ручные субтитры.
Главный путь транскрибации YouTube — чтобы не упираться в 403 у yt-dlp.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

PREFERRED_LANGS: tuple[str, ...] = (
    "ru",
    "ru-RU",
    "uk",
    "be",
    "en",
    "en-US",
    "en-GB",
)


def extract_video_id(url: str) -> Optional[str]:
    """Достать 11-символьный videoId из любых форм ссылки YouTube."""
    if not url:
        return None
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.netloc or "").lower().lstrip(".")
    path = u.path or ""

    if host.endswith("youtu.be"):
        cand = path.lstrip("/").split("/", 1)[0]
        return cand if _is_id(cand) else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if path == "/watch":
            qs = parse_qs(u.query)
            v = (qs.get("v") or [""])[0]
            return v if _is_id(v) else None
        for prefix in ("/shorts/", "/embed/", "/v/", "/live/"):
            if path.startswith(prefix):
                cand = path[len(prefix):].split("/", 1)[0]
                return cand if _is_id(cand) else None
    return None


def _is_id(value: str) -> bool:
    return bool(value) and bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", value))


def _join(entries: Iterable[dict]) -> str:
    parts = []
    for e in entries:
        t = (e.get("text") if isinstance(e, dict) else getattr(e, "text", "")) or ""
        t = t.replace("\n", " ").strip()
        if t:
            parts.append(t)
    return " ".join(parts).strip()


def fetch_transcript_text(video_id: str) -> Optional[str]:
    """
    Вернуть текст субтитров (ручных или автогенерируемых) или None,
    если для видео их нет / отключены / видео недоступно.

    youtube-transcript-api ≥1.2: YouTubeTranscriptApi().fetch().
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            CouldNotRetrieveTranscript,
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )
    except Exception as e:
        logger.warning("youtube-transcript-api недоступен: %s", e)
        return None

    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=list(PREFERRED_LANGS))
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound):
        logger.info("YouTube: нет субтитров (videoId=%s)", video_id)
        return None
    except CouldNotRetrieveTranscript as e:
        logger.warning("YouTube: fetch ошибка: %s", e)
        return None
    except Exception as e:
        logger.warning("YouTube: fetch неожиданная ошибка: %s", e)
        return None

    text = _join(transcript)
    if text:
        logger.info("YouTube: субтитры OK, len=%s", len(text))
        return text
    return None
