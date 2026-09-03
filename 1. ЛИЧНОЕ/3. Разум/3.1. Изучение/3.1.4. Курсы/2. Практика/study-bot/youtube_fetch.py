"""
Скачивание аудио с YouTube для транскрибации (через yt-dlp).

Под серверный запуск умеет ходить за PO-token к локальному
sidecar-провайдеру bgutil (https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
адрес — переменная окружения YT_POT_PROVIDER_URL (например, http://pot:4416).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_YT_URL_RE = re.compile(
    r"(https?://(?:www\.|m\.)?youtube\.com/watch\?[^\s<]+"
    r"|https?://(?:www\.|m\.)?youtube\.com/shorts/[^\s<]+"
    r"|https?://(?:music\.)?youtube\.com/watch\?[^\s<]+"
    r"|https?://(?:www\.)?youtube\.com/embed/[^\s<]+"
    r"|https?://youtu\.be/[^\s<]+)",
    re.IGNORECASE,
)


def extract_youtube_url_for_transcription(text: str) -> str | None:
    """
    Вернуть URL ролика, если сообщение состоит только из одной YouTube-ссылки
    (возможны пробелы/переносы, скобки вокруг ссылки).
    """
    t = (text or "").strip().strip("\ufeff")
    if not t:
        return None
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1].strip()
    urls = _YT_URL_RE.findall(t)
    if len(urls) != 1:
        return None
    url = urls[0].rstrip(").,;]")
    remainder = _YT_URL_RE.sub("", t, count=1).strip(" \t\n\r.,;:-•")
    if remainder:
        return None
    return url


def _build_extractor_args() -> dict:
    args: dict = {
        # tv_embedded/android_vr дают pre-signed URLs — не требуют jsinterp.
        # web+pot как фолбэк. tv убран: стабильно даёт DRM.
        "youtube": {"player_client": ["tv_embedded", "android_vr", "android", "web"]},
    }
    pot_url = (os.getenv("YT_POT_PROVIDER_URL") or "").strip()
    if pot_url:
        args["youtubepot-bgutilhttp"] = {"base_url": [pot_url]}
        logger.info("yt-dlp: PO-token провайдер %s", pot_url)
    return args


def download_youtube_best_media(url: str) -> tuple[str, str]:
    """
    Скачать лучший доступный аудио/видеопоток во временную папку.
    Возвращает (путь к файлу, путь к рабочей папке для удаления).
    """
    pot_url = (os.getenv("YT_POT_PROVIDER_URL") or "").strip()
    # Плагин bgutil без рабочего HTTP-сервера сам лезет на 127.0.0.1:4416 и ломает скачивание (403).
    if not pot_url:
        os.environ["YTDLP_NO_PLUGINS"] = "1"
    else:
        os.environ.pop("YTDLP_NO_PLUGINS", None)

    import sys

    if "yt_dlp" in sys.modules and not pot_url:
        logger.warning(
            "yt-dlp уже импортирован в этом процессе; чтобы отключить плагины bgutil, "
            "перезапусти бота после `pip uninstall bgutil-ytdlp-pot-provider`."
        )

    import yt_dlp

    tmpdir = tempfile.mkdtemp(prefix="study_bot_yt_")
    outtmpl = str(Path(tmpdir) / "audio.%(ext)s")
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "socket_timeout": 60,
        "retries": 5,
        "fragment_retries": 5,
        "geo_bypass": True,
        "concurrent_fragment_downloads": 4,
        "extractor_args": _build_extractor_args(),
    }
    if shutil.which("ffmpeg"):
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "192",
            }
        ]
        logger.info("yt-dlp: найден ffmpeg — извлечение в m4a")
    logger.info("yt-dlp: скачивание в %s", tmpdir)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = sorted(Path(tmpdir).glob("audio.*"))
    if not files:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(
            "Не удалось сохранить файл после yt-dlp. Проверь ссылку и доступность YouTube."
        )
    path = str(files[0])
    try:
        sz = Path(path).stat().st_size
    except OSError:
        sz = 0
    logger.info("yt-dlp: готово %s (%s байт)", Path(path).name, sz)
    return path, tmpdir
