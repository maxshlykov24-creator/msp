"""
Скопируй в config.py и заполни значения — или задай переменные окружения / `.env`
(см. `.env.example`). В Docker используется копия этого файла как `config.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_BASE = Path(__file__).resolve().parent


def _env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is not None and (s := v.strip()):
        return s
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


TELEGRAM_BOT_TOKEN = _env_str(
    "TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE"
)
NEXARA_API_KEY = _env_str("NEXARA_API_KEY", "YOUR_NEXARA_API_KEY_HERE")

DISTRIBUTION_FOLDER = _env_str(
    "DISTRIBUTION_FOLDER", str(_BASE / "Распределение")
)

TRANSCRIPTION_LANGUAGE = _env_str("TRANSCRIPTION_LANGUAGE", "ru")

# 0 — без лимита (скачанное с YouTube и входящие файлы; лимиты API Telegram/Nexara остаются).
MAX_FILE_SIZE = _env_int("MAX_FILE_SIZE", 0)

TRANSCRIBE_TIMEOUT_SEC = _env_int("TRANSCRIBE_TIMEOUT_SEC", 180)
YOUTUBE_DOWNLOAD_TIMEOUT_SEC = _env_int("YOUTUBE_DOWNLOAD_TIMEOUT_SEC", 900)

DEBUG_MODE = _env_bool("DEBUG_MODE", False)

NEXARA_TRANSCRIBE_URL = _env_str(
    "NEXARA_TRANSCRIBE_URL",
    "https://api.nexara.ru/api/v1/audio/transcriptions",
)

# Groq LLM (текстовый собеседник). Ключ: https://console.groq.com
GROQ_API_KEY = _env_str("GROQ_API_KEY", "")
GROQ_MODEL = _env_str("GROQ_MODEL", "llama-3.3-70b-versatile")
LLM_SYSTEM_PROMPT = _env_str(
    "LLM_SYSTEM_PROMPT",
    "Ты умный собеседник: коротко, по делу, без воды. Отвечай на русском.",
)
LLM_TIMEOUT_SEC = _env_int("LLM_TIMEOUT_SEC", 120)

# Ежедневный сброс истории LLM (выкл. при длинной памяти + саммаризация)
LLM_DAILY_RESET_ENABLED = _env_bool("LLM_DAILY_RESET_ENABLED", False)
LLM_DAILY_RESET_TZ = _env_str("LLM_DAILY_RESET_TZ", "Europe/Moscow")
LLM_DAILY_RESET_HOUR = _env_int("LLM_DAILY_RESET_HOUR", 0)
LLM_DAILY_RESET_MINUTE = _env_int("LLM_DAILY_RESET_MINUTE", 0)
LLM_DAILY_RESET_NOTIFY = _env_bool("LLM_DAILY_RESET_NOTIFY", True)

# Авто-саммаризация (сжатие старых реплик)
LLM_SUMMARIZE_THRESHOLD = _env_int("LLM_SUMMARIZE_THRESHOLD", 40)
LLM_SUMMARIZE_BATCH = _env_int("LLM_SUMMARIZE_BATCH", 30)
LLM_SUMMARIZE_SYSTEM_PROMPT = _env_str(
    "LLM_SUMMARIZE_SYSTEM_PROMPT",
    "Ты сжимаешь фрагмент диалога в одну выжимку для дальнейшего контекста. "
    "Сохрани: факты, имена, числа, решения, договорённости, открытые вопросы. "
    "Пиши кратко на русском (списком или короткими абзацами). Не выдумывай.",
)
