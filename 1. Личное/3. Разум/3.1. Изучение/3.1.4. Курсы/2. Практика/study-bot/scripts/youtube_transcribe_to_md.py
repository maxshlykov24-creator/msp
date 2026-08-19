#!/usr/bin/env python3
"""
Транскрибация YouTube → markdown (таймлайн + полный текст).

Порядок: субтитры YouTube (youtube-transcript-api) → при отсутствии — см. ceo.mdc (Nexara).

Пример:
  python3 "Личное/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot/scripts/youtube_transcribe_to_md.py" "https://www.youtube.com/watch?v=VIDEO_ID"
  python3 "…/study-bot/scripts/youtube_transcribe_to_md.py" URL -o "Бизнес/05_Маркетинг_и_контент/_ai/out.md"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
STUDY_BOT = SCRIPT_DIR.parent
VAULT_ROOT = STUDY_BOT.parent
if (VAULT_ROOT / "Бизнес").is_dir():
    pass
elif (STUDY_BOT / "youtube_captions.py").is_file():
    VAULT_ROOT = STUDY_BOT.parents[4] if len(STUDY_BOT.parents) > 4 else STUDY_BOT.parent

sys.path.insert(0, str(STUDY_BOT))
from youtube_captions import extract_video_id  # noqa: E402

DEFAULT_OUT_DIR = VAULT_ROOT / "Бизнес" / "05_Маркетинг_и_контент" / "_ai"
INBOX_DIR = VAULT_ROOT / "Личное" / "0. Входящее"
PREFERRED_LANGS = ("ru", "ru-RU", "en", "en-US", "en-GB")


def fmt_ts(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    s = int(float(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def parse_timestamp(url: str) -> int | None:
    u = urlparse(url.strip())
    qs = parse_qs(u.query)
    for key in ("t", "start"):
        if key in qs and qs[key]:
            raw = qs[key][0]
            if raw.isdigit():
                return int(raw)
            m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
            if m:
                h, mi, s = (int(x or 0) for x in m.groups())
                return h * 3600 + mi * 60 + s
    return None


def slugify(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text.strip())
    return (text[:limit].strip("_") or "youtube")[:limit]


def fetch_metadata(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--extractor-args",
        "youtube:player_client=android",
        "--print",
        "%(title)s|||%(duration)s|||%(channel)s",
        "--no-download",
        url,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=90)
        title, duration, channel = out.strip().split("|||", 2)
        return {"title": title, "duration": int(duration), "channel": channel}
    except Exception:
        return {"title": video_id, "duration": None, "channel": "—"}


def fetch_transcript(video_id: str):
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    return api.fetch(video_id, languages=list(PREFERRED_LANGS))


def build_markdown(
    *,
    url: str,
    video_id: str,
    meta: dict,
    transcript,
    mark_ts: int | None,
) -> str:
    title = meta.get("title") or video_id
    duration = meta.get("duration")
    lines = [
        f"# Транскрипт: {title}",
        "",
        f"- **Канал:** {meta.get('channel', '—')}",
        f"- **Video ID:** {video_id}",
    ]
    if duration:
        lines.append(f"- **Длительность:** {fmt_ts(duration)} ({duration} сек)")
    lines.extend(
        [
            f"- **Источник:** {url}",
            f"- **Дата обработки:** {datetime.now():%Y-%m-%d %H:%M}",
            "- **Метод:** субтитры YouTube (auto/manual)",
        ]
    )
    if mark_ts is not None:
        lines.append(f"- **Метка в ссылке:** t={mark_ts}s (~{fmt_ts(mark_ts)})")
    lines.extend(["", "## Таймлайн", ""])

    full_parts: list[str] = []
    segment_count = 0
    for sn in transcript:
        text = sn.text.replace("\n", " ").strip()
        if not text:
            continue
        segment_count += 1
        lines.append(f"**[{fmt_ts(sn.start)}]** {text}")
        lines.append("")
        full_parts.append(text)

    lines.extend(["", "## Полный текст", "", " ".join(full_parts), ""])
    return "\n".join(lines), segment_count


def default_output_path(meta: dict, video_id: str) -> Path:
    slug = slugify(meta.get("title") or video_id)
    name = f"{datetime.now():%Y-%m-%d}_YouTube_{slug}_транскрипт.md"
    return DEFAULT_OUT_DIR / name


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube → markdown транскрипт")
    parser.add_argument("url", help="Ссылка YouTube (watch, youtu.be, shorts)")
    parser.add_argument("-o", "--output", type=Path, help="Путь к .md")
    parser.add_argument(
        "--no-inbox-copy",
        action="store_true",
        help="Не дублировать в Личное/0. Входящее",
    )
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    if not video_id:
        print("Не удалось извлечь video_id из URL", file=sys.stderr)
        return 1

    mark_ts = parse_timestamp(args.url)
    meta = fetch_metadata(video_id)

    try:
        transcript = fetch_transcript(video_id)
    except Exception as e:
        print(
            f"Субтитры недоступны: {e}\n"
            "Fallback: Nexara — см. .cursor/rules/ceo.mdc § Транскрибация YouTube",
            file=sys.stderr,
        )
        return 2

    md, segment_count = build_markdown(
        url=args.url.strip(),
        video_id=video_id,
        meta=meta,
        transcript=transcript,
        mark_ts=mark_ts,
    )

    out = args.output or default_output_path(meta, video_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(json.dumps({"output": str(out), "segments": segment_count, "video_id": video_id}))

    if not args.no_inbox_copy and INBOX_DIR.parent.is_dir():
        inbox = INBOX_DIR / out.name
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text(md, encoding="utf-8")
        print(json.dumps({"inbox_copy": str(inbox)}))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
