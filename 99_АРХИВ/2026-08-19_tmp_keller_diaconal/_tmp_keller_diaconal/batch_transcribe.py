#!/usr/bin/env python3
"""Пакетная транскрибация лекций Келлера (Nexara) → один markdown."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(
    0,
    str(
        ROOT
        / "2. Бизнес/04_Производство/Активные/MANSBAND/scripts"
    ),
)
from transcribe_meeting import resolve_api_key  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("keller_batch")

DIR = Path(__file__).resolve().parent
OUT_DIR = DIR / "_transcripts"
NEXARA_URL = "https://api.nexara.ru/api/v1/audio/transcriptions"

FILES = [
    (
        "01_Diaconal_Training_I.mp3",
        "Diaconal Training I — 1 of 4",
        "Diaconal Training (основная серия)",
    ),
    (
        "02_Diaconal_Training_II.mp3",
        "Diaconal Training II — 2 of 4",
        "Diaconal Training (основная серия)",
    ),
    (
        "03_Diaconal_Training_III.mp3",
        "Diaconal Training III — 3 of 4",
        "Diaconal Training (основная серия)",
    ),
    (
        "04_Diaconal_Training_VI.mp3",
        "Diaconal Training VI — 4 of 4",
        "Diaconal Training (основная серия)",
    ),
    (
        "05_Diaconal_Ministry_I.mp3",
        "Doctrine of the Church: Diaconal Ministry I",
        "Doctrine of the Church ’87 — Diaconal Ministry",
    ),
    (
        "06_Diaconal_Ministry_II.mp3",
        "Doctrine of the Church: Diaconal Ministry II",
        "Doctrine of the Church ’87 — Diaconal Ministry",
    ),
    (
        "07_Diaconal_Ministry_III.mp3",
        "Doctrine of the Church: Diaconal Ministry III",
        "Doctrine of the Church ’87 — Diaconal Ministry",
    ),
]

FINAL_MD = (
    ROOT
    / "1. Личное/2. Дух/2.3. Служение/2.3.2. Церковь"
    / "2026-07-27_Keller_Diaconal_Training_и_Ministry_транскрипт.md"
)


def fmt_ts(seconds) -> str:
    if seconds is None:
        return "??:??"
    s = int(float(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def full_text(payload: dict) -> str:
    text = str(payload.get("text") or "").strip()
    segments = payload.get("segments")
    from_seg = ""
    if isinstance(segments, list) and segments:
        from_seg = "".join(str(s.get("text") or "") for s in segments).strip()
    if from_seg and len(from_seg) > len(text):
        return from_seg
    return text or from_seg


def section_md(title: str, source_name: str, payload: dict) -> str:
    duration = payload.get("duration")
    dur_s = f"{float(duration):.0f} сек (~{float(duration)/60:.0f} мин)" if duration else "—"
    body = full_text(payload)
    lines = [
        f"## {title}",
        "",
        f"- **Источник аудио:** `{source_name}`",
        f"- **Автор:** Timothy Keller (Westminster Theological Seminary / Redeemer)",
        f"- **Длительность:** {dur_s}",
        f"- **Дата транскрибации:** {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "### Полный текст",
        "",
        body if body else "_Текст пуст._",
        "",
        "### Таймлайн",
        "",
    ]
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            t = str(seg.get("text") or "").strip()
            if not t:
                continue
            lines.append(
                f"**[{fmt_ts(seg.get('start'))}–{fmt_ts(seg.get('end'))}]** {t}"
            )
            lines.append("")
    else:
        lines.append("_Сегментов нет._")
        lines.append("")
    return "\n".join(lines)


async def transcribe_one(path: Path, api_key: str) -> dict:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "audio/mpeg"
    headers = {"Authorization": f"Bearer {api_key}"}
    log.info("Nexara: %s (%.1f МБ)", path.name, path.stat().st_size / 1024 / 1024)
    async with aiohttp.ClientSession() as session:
        with path.open("rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=path.name, content_type=mime)
            data.add_field("response_format", "verbose_json")
            data.add_field("language", "en")
            async with session.post(
                NEXARA_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=3600),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"{path.name}: HTTP {resp.status}: {body[:800]}")
                return json.loads(body)


def get_api_key() -> str:
    """resolve_api_key() внутри уже крутит asyncio — нельзя вызывать из async main."""
    return resolve_api_key()


async def main(api_key: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str, str, dict]] = []

    for fname, title, series in FILES:
        path = DIR / fname
        if not path.is_file():
            raise FileNotFoundError(path)
        cache = OUT_DIR / f"{path.stem}.json"
        if cache.is_file() and cache.stat().st_size > 100:
            log.info("Кэш: %s", cache.name)
            payload = json.loads(cache.read_text(encoding="utf-8"))
        else:
            payload = await transcribe_one(path, api_key)
            cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info(
                "OK %s: text_len=%s duration=%s",
                fname,
                len(full_text(payload)),
                payload.get("duration"),
            )
        results.append((title, series, fname, payload))

    # собрать один файл, сериями
    parts = [
        "# Timothy Keller — Diaconal Training & Diaconal Ministry (транскрипты)",
        "",
        f"- **Дата сборки:** {datetime.now():%Y-%m-%d}",
        "- **Назначение:** фундамент для изучения дьяконского служения",
        "- **Источник:** Westminster Media (WTS)",
        "- **Язык лекций:** English (транскрипт как есть)",
        "- **Метод:** Nexara API (`verbose_json`)",
        "",
        "## Содержание",
        "",
        "### Часть A. Diaconal Training (основная серия)",
        "1. Diaconal Training I — 1 of 4",
        "2. Diaconal Training II — 2 of 4",
        "3. Diaconal Training III — 3 of 4",
        "4. Diaconal Training VI — 4 of 4",
        "",
        "### Часть B. Doctrine of the Church ’87 — Diaconal Ministry",
        "1. Diaconal Ministry I",
        "2. Diaconal Ministry II",
        "3. Diaconal Ministry III",
        "",
        "---",
        "",
    ]

    current_series = None
    for title, series, fname, payload in results:
        if series != current_series:
            current_series = series
            parts.append(f"# {series}")
            parts.append("")
        parts.append(section_md(title, fname, payload))
        parts.append("---")
        parts.append("")

    FINAL_MD.parent.mkdir(parents=True, exist_ok=True)
    FINAL_MD.write_text("\n".join(parts), encoding="utf-8")
    log.info("Итоговый файл: %s (%s символов)", FINAL_MD, FINAL_MD.stat().st_size)


if __name__ == "__main__":
    key = get_api_key()
    asyncio.run(main(key))
