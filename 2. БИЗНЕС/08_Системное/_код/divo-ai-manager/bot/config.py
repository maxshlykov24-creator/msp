"""Настройки из .env. Без внешних зависимостей, читаем сами."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Сервисный ключ Google лежит в divo-cme-stock — не копируем секрет, ищем на месте.
SA_CANDIDATES = (
    ROOT / "google_sa.json",
    ROOT.parent / "divo-cme-stock" / "google_sa.json",
    Path("/root/divo-cme-stock/google_sa.json"),
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ROOT / ".env")


def env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key) or default)
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key) or default)
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    value = env(key).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "да"}


def _google_sa_path() -> str:
    explicit = env("GOOGLE_SA_PATH")
    if explicit:
        return explicit
    for path in SA_CANDIDATES:
        if path.exists():
            return str(path)
    return ""


class Settings:
    root = ROOT
    workspace = ROOT / "workspace"
    kb = ROOT / "workspace" / "KB"
    state_dir = Path(env("STATE_DIR") or (ROOT / "workspace" / "state"))
    paused_dir = Path(env("PAUSED_DIR") or (ROOT / "workspace" / "paused"))

    telegram_token = env("TELEGRAM_BOT_TOKEN")
    admin_chat_id = env("TELEGRAM_ADMIN_CHAT_ID")

    openrouter_key = env("OPENROUTER_API_KEY")
    model = env("OPENROUTER_MODEL", "google/gemini-3.5-flash")
    model_fallback = env("OPENROUTER_MODEL_FALLBACK", "google/gemini-2.5-flash")

    # Запасной путь: OpenRouter отдаёт 403 с российских IP, Gemini напрямую — нет.
    gemini_key = env("GEMINI_API_KEY")
    gemini_model = env("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_model_fallback = env("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash-lite")

    # На VPS задаётся в systemd-юните: socks5://127.0.0.1:1080. Локально пусто.
    llm_proxy = env("LLM_PROXY")

    temperature = env_float("LLM_TEMPERATURE", 0.6)
    max_tokens = env_int("LLM_MAX_TOKENS", 4000)

    # Человеческий слой. Живой продавец печатает 6-9 символов в секунду,
    # а не 22, и не отвечает через полсекунды после вопроса.
    debounce_sec = env_float("DEBOUNCE_SEC", 3.5)
    typing_cps = env_float("TYPING_CPS", 11.0)  # символов в секунду с телефона
    delay_min_sec = env_float("DELAY_MIN_SEC", 2.0)
    delay_max_sec = env_float("DELAY_MAX_SEC", 11.0)
    reveal_bot = env_bool("REVEAL_BOT", False)  # тумблер «ты бот?»

    # Тест механики отправки файлов: заглушки, не реальные фото/видео машин.
    media_dir = ROOT / "workspace" / "media"
    media_photo_placeholder = media_dir / "фото_плейсхолдер.jpg"
    media_video_placeholder = media_dir / "видео_плейсхолдер.mp4"

    history_turns = env_int("HISTORY_TURNS", 40)

    # Догон по номеру, если клиент замолчал. Часы — по Москве.
    nudge_enabled = env_bool("NUDGE_ENABLED", True)
    nudge_wait_1_min = env_int("NUDGE_WAIT_1_MIN", 20)
    nudge_wait_2_min = env_int("NUDGE_WAIT_2_MIN", 120)
    nudge_hour_from = env_int("NUDGE_HOUR_FROM", 10)
    nudge_hour_to = env_int("NUDGE_HOUR_TO", 20)
    nudge_morning_hour = env_int("NUDGE_MORNING_HOUR", 11)

    # Сток
    stock_refresh_min = env_int("STOCK_REFRESH_MIN", 15)
    google_sa_path = _google_sa_path()
    spreadsheet_id = env("SPREADSHEET_ID", "1icRyb3qObLM80b2S-zvwIttB808FIsDM8kB5Wu9u23Q")
    data_sheet = env("DATA_SHEET", "Данные")
    # Машины на складе, но не в продаже: их пишет divo-cme-stock.
    warehouse_sheet = env("WAREHOUSE_SHEET", "Склад")
    # Ручные пометки менеджеров по конкретным машинам. Синк этот лист не трогает.
    marks_sheet = env("MARKS_SHEET", "Пометки")
    # Отчёты автотеки: содержимое меняется редко, кэш живёт неделю.
    autoteka_refresh_h = env_int("AUTOTEKA_REFRESH_H", 24)


settings = Settings()
