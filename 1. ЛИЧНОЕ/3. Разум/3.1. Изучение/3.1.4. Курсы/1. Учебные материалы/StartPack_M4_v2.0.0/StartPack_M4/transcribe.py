#!/usr/bin/env python3
"""
M4 · Транскрибатор встреч · Внедрение ИИ в бизнес. Второй поток.

Что делает:
    python transcribe.py <путь-к-файлу> [--provider {assembly|openrouter|mymeet}]

Берёт аудио/видео, отправляет в STT-провайдер, сохраняет .md в _ТРАНСКРИПТЫ/<категория>/.

Не пиши код. Просто положи файл в _ТРАНСКРИПТЫ/_INBOX/, скажи в Cursor:
"обработай новый файл из инбокса", и Cursor сам запустит этот скрипт.

Провайдеры:
    assembly   — AssemblyAI: лучше всего для встреч с 2+ спикерами (диаризация)
    openrouter — Whisper через OpenRouter: дёшево, для голосовых/монологов
    mymeet     — MyMeet (mymeet.ai): российский сервис, есть готовый UI

По умолчанию выбор автоматический по длительности и числу спикеров (см. ВЫБОР_МОДЕЛИ.md).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Нужно поставить requests: pip install requests")
    sys.exit(1)


# Загружаем ключи из .env рядом со скриптом
def _load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

ASSEMBLY_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_STT_MODEL", "openai/whisper-large-v3")
MYMEET_KEY = os.environ.get("MYMEET_API_KEY", "")

ROOT = Path(__file__).parent
TRANSCRIPTS_DIR = ROOT / "_ТРАНСКРИПТЫ"


# ─── Категории файлов по caption-подсказке ───────────────────────────
CATEGORIES = {
    "митап": "митапы",
    "митапы": "митапы",
    "meetup": "митапы",
    "подрядчик": "созвоны-подрядчики",
    "созвон": "созвоны-подрядчики",
    "клиент": "клиенты",
    "клиенты": "клиенты",
    "эксперт": "эксперты-консультанты",
    "консультант": "эксперты-консультанты",
    "тренер": "эксперты-консультанты",
    "сопровождение": "эксперты-консультанты",
    "личное": "личное",
    "друг": "личное",
    "идея": "личное",
}


def detect_category(filename: str, hint: str = "") -> str:
    """По имени файла и опц. подсказке решает в какую папку класть."""
    text = (filename + " " + hint).lower()
    for kw, folder in CATEGORIES.items():
        if kw in text:
            return folder
    return "_INBOX"


# ─── Провайдер 1: AssemblyAI (диаризация спикеров) ───────────────────
def transcribe_assemblyai(file_path: Path) -> dict:
    if not ASSEMBLY_KEY:
        raise RuntimeError("ASSEMBLYAI_API_KEY не задан в .env")

    headers = {"authorization": ASSEMBLY_KEY}

    # 1. Залить файл
    print(f"[AssemblyAI] загружаю {file_path.name}…")
    with open(file_path, "rb") as f:
        up = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=f,
            timeout=600,
        )
    up.raise_for_status()
    upload_url = up.json()["upload_url"]

    # 2. Запустить транскрипцию с диаризацией
    submit = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**headers, "content-type": "application/json"},
        json={
            "audio_url": upload_url,
            "speaker_labels": True,
            "language_code": "ru",
        },
        timeout=30,
    )
    submit.raise_for_status()
    transcript_id = submit.json()["id"]

    # 3. Опрашивать пока готов
    print(f"[AssemblyAI] обрабатываю (id={transcript_id})…", end="", flush=True)
    while True:
        r = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        status = data["status"]
        if status == "completed":
            print(" готово.")
            return _format_assembly(data)
        if status == "error":
            raise RuntimeError(f"AssemblyAI: {data.get('error')}")
        print(".", end="", flush=True)
        time.sleep(3)


def _format_assembly(data: dict) -> dict:
    """Превращает ответ AssemblyAI в { 'speakers': bool, 'duration_sec': N, 'lines': [...] }"""
    utterances = data.get("utterances") or []
    if not utterances:
        return {
            "speakers": False,
            "duration_sec": int(data.get("audio_duration") or 0),
            "lines": [{"ts": "00:00", "speaker": "A", "text": data.get("text", "")}],
            "provider": "AssemblyAI",
        }
    lines = [
        {
            "ts": _fmt_ts(u["start"]),
            "speaker": u["speaker"],
            "text": u["text"],
        }
        for u in utterances
    ]
    return {
        "speakers": True,
        "duration_sec": int(data.get("audio_duration") or 0),
        "lines": lines,
        "provider": "AssemblyAI",
    }


# ─── Провайдер 2: OpenRouter Whisper (дёшево, монолог) ───────────────
def transcribe_openrouter(file_path: Path) -> dict:
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY не задан в .env")

    print(f"[OpenRouter/{OPENROUTER_MODEL}] отправляю {file_path.name}…")
    mime = mimetypes.guess_type(file_path.name)[0] or "audio/mpeg"
    with open(file_path, "rb") as f:
        r = requests.post(
            "https://openrouter.ai/api/v1/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "HTTP-Referer": "https://denispleada.ru",
                "X-Title": "M4 Transcriber",
            },
            files={"file": (file_path.name, f, mime)},
            data={"model": OPENROUTER_MODEL, "language": "ru"},
            timeout=900,
        )
    if r.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    text = data.get("text", "")
    return {
        "speakers": False,
        "duration_sec": int(data.get("duration") or 0),
        "lines": [{"ts": "00:00", "speaker": "A", "text": text}],
        "provider": f"OpenRouter / {OPENROUTER_MODEL}",
    }


# ─── Провайдер 3: MyMeet (российский, для Zoom-встреч) ───────────────
def transcribe_mymeet(file_path: Path) -> dict:
    """MyMeet (mymeet.ai) — российский STT с диаризацией. Для тех кто хочет
    готовый UI и оплату с РФ-карты без переходников. API похож на AssemblyAI:
    upload → submit → poll. Базовый URL и схема — см. mymeet.ai/api/docs.

    Это заглушка-эталон. Точные эндпоинты ученик подсмотрит в личном кабинете
    MyMeet → API. Если API недоступен на их тарифе — используют веб-кабинет
    напрямую (закидывают файл, скачивают .md), а скрипт пропускают.
    """
    if not MYMEET_KEY:
        raise RuntimeError(
            "MYMEET_API_KEY не задан в .env. Либо настрой API ключ в личном "
            "кабинете mymeet.ai, либо пользуйся их веб-интерфейсом напрямую — "
            "это тоже валидный путь для М4."
        )

    print(f"[MyMeet] заглушка — реализация после получения тестового ключа.")
    raise NotImplementedError(
        "MyMeet API-обвязка ставится после того, как у тебя появится ключ. "
        "Точные эндпоинты — в личном кабинете mymeet.ai. Логика та же что у "
        "AssemblyAI: upload → poll. Скрипт легко расширяется по образцу выше."
    )


# ─── Авто-выбор провайдера ────────────────────────────────────────────
def auto_provider(file_path: Path, hint: str = "") -> str:
    """Если ученик не указал --provider, выбираем сами."""
    text = (file_path.name + " " + hint).lower()
    # Явные подсказки в caption / имени файла
    if any(k in text for k in ["встреча", "митап", "созвон", "zoom", "клиент", "подрядчик", "эксперт"]):
        return "assembly"
    if any(k in text for k in ["голосовое", "voice", "монолог", "идея", "заметка"]):
        return "openrouter"
    # Эвристика по размеру: > 10 МБ ≈ длинная встреча → diarization нужна
    if file_path.stat().st_size > 10 * 1024 * 1024:
        return "assembly"
    return "openrouter"


# ─── Сохранение .md ───────────────────────────────────────────────────
def _fmt_ts(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def save_markdown(result: dict, source: Path, category: str) -> Path:
    """Создаёт .md в _ТРАНСКРИПТЫ/<категория>/."""
    target_dir = TRANSCRIPTS_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = re.sub(r"[^\w\-.]+", "_", source.stem)
    out_path = target_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}_{stem}.md"

    speakers_count = len({line["speaker"] for line in result["lines"]})
    duration_min = result["duration_sec"] // 60
    duration_sec = result["duration_sec"] % 60

    header = [
        f"# Транскрипт · {source.name}",
        "",
        f"- **Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **Источник:** `{source.name}`",
        f"- **Категория:** {category}",
        f"- **Провайдер:** {result['provider']}",
        f"- **Длительность:** {duration_min} мин {duration_sec} сек",
        f"- **Спикеров:** {speakers_count}",
        "",
        "---",
        "",
    ]
    body = []
    for line in result["lines"]:
        body.append(f"**[{line['ts']}] Speaker {line['speaker']}**")
        body.append(line["text"])
        body.append("")

    out_path.write_text("\n".join(header + body), encoding="utf-8")
    print(f"\n✓ Сохранено: {out_path.relative_to(ROOT)}")
    print("\nДальше — открой файл в Cursor и спроси: «по этому транскрипту")
    print("пополни _КОНТЕКСТ/КОМПАНИЯ.md» или «выдели задачи с дедлайнами».")
    print("Готовые промпты под 5 типов разговора — в ПРОМПТЫ_РАЗБОРА.md.")
    return out_path


# ─── CLI ──────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="M4 Транскрибатор (Cursor-скрипт)")
    p.add_argument("file", help="Путь к аудио/видео файлу")
    p.add_argument(
        "--provider",
        choices=["assembly", "openrouter", "mymeet", "auto"],
        default="auto",
        help="STT-провайдер (по умолчанию — авто-выбор)",
    )
    p.add_argument("--category", default="", help="Папка-категория (митапы/клиенты/...)")
    p.add_argument("--hint", default="", help="Подсказка для авто-выбора (тип встречи)")
    args = p.parse_args()

    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"Файл не найден: {src}")
        return 1
    if not src.is_file():
        print(f"Не файл: {src}")
        return 1

    provider = args.provider if args.provider != "auto" else auto_provider(src, args.hint)
    print(f"Провайдер: {provider}")

    fn = {"assembly": transcribe_assemblyai, "openrouter": transcribe_openrouter, "mymeet": transcribe_mymeet}[provider]

    try:
        result = fn(src)
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        return 2

    category = args.category or detect_category(src.name, args.hint)
    save_markdown(result, src, category)
    return 0


if __name__ == "__main__":
    sys.exit(main())
