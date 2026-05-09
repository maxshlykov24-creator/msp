"""
Скопируй в config.py и заполни значения (или используй переменные окружения из .env).
В репозитории должен быть только config_example.py; config.py в .gitignore.
"""

from pathlib import Path

_BASE = Path(__file__).resolve().parent

TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
NEXARA_API_KEY = "YOUR_NEXARA_API_KEY_HERE"

DISTRIBUTION_FOLDER = str(_BASE / "Распределение")

TRANSCRIPTION_LANGUAGE = "ru"

MAX_FILE_SIZE = 20 * 1024 * 1024

DEBUG_MODE = False

NEXARA_TRANSCRIBE_URL = "https://api.nexara.ru/api/v1/audio/transcriptions"
