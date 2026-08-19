#!/usr/bin/env python3
"""Локальная транскрибация (faster-whisper) — fallback при недоступности Nexara."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

MANSBAND = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = MANSBAND / "2026-06-03_созвон_Миша_касса_транскрипт.md"


def _format_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--model",
        default="medium",
        help="Whisper model: tiny/base/small/medium/large-v3",
    )
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    logger.info("Загрузка модели %s…", args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    logger.info("Транскрибация %s…", args.input.name)
    segments_iter, info = model.transcribe(
        str(args.input),
        language="ru",
        beam_size=5,
        vad_filter=True,
    )

    seg_lines: list[str] = []
    for seg in segments_iter:
        txt = (seg.text or "").strip()
        if not txt:
            continue
        seg_lines.append(
            f"**[{_format_ts(seg.start)}–{_format_ts(seg.end)}]** {txt}"
        )
        if len(seg_lines) % 50 == 0:
            logger.info("… %s сегментов", len(seg_lines))

    md = "\n".join(
        [
            f"# Транскрипт: {args.input.name}",
            "",
            f"- **Дата обработки:** {datetime.now():%Y-%m-%d %H:%M}",
            f"- **Движок:** faster-whisper ({args.model})",
            f"- **Длительность (сек):** {getattr(info, 'duration', '—')}",
            f"- **Источник:** `{args.input}`",
            "- **Формат:** только таймлайн (сегменты с таймкодами)",
            "",
            "## Таймлайн",
            "",
            *seg_lines,
            "",
        ]
    )
    args.output.write_text(md, encoding="utf-8")
    logger.info("Готово: %s (%s сегментов, %s символов)", args.output, len(seg_lines), len(md))


if __name__ == "__main__":
    main()
