"""Один звонок: диаризация Nexara (telephonic, 2 спикера) + сырой JSON."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "_ЭТАЛОН" / "звонки" / "аудио" / "343387431.mp3"
OUT_DIR = ROOT / "_ЭТАЛОН" / "звонки" / "тест_спикеры"
VAULT = ROOT.parents[3]
TRANSCRIBE_PY = (
    VAULT
    / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot/scripts"
)
URL = "https://api.nexara.ru/api/v1/audio/transcriptions"


def _key() -> str:
    sys.path.insert(0, str(TRANSCRIBE_PY))
    from transcribe_meeting import _load_dotenv, resolve_api_key  # type: ignore

    _load_dotenv()
    return resolve_api_key()


async def run(api_key: str) -> None:
    if not AUDIO.is_file():
        raise SystemExit(f"нет аудио: {AUDIO}")
    mime = mimetypes.guess_type(str(AUDIO))[0] or "audio/mpeg"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        with AUDIO.open("rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=AUDIO.name, content_type=mime)
            data.add_field("response_format", "verbose_json")
            data.add_field("task", "diarize")
            data.add_field("num_speakers", "2")
            data.add_field("diarization_setting", "telephonic")
            async with session.post(
                URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=900),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise SystemExit(f"Nexara {resp.status}: {body[:800]}")
                payload = json.loads(body)

    raw = OUT_DIR / "343387431_raw.json"
    raw.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    segs = payload.get("segments") or []
    speakers = sorted({str(s.get("speaker")) for s in segs if isinstance(s, dict)})
    print("duration", payload.get("duration"))
    print("speakers", speakers)
    print("segments", len(segs))
    print("keys_segment", sorted(segs[0].keys()) if segs else [])
    print("saved", raw)
    for s in segs[:8]:
        print(f"  {s.get('speaker')} [{s.get('start'):.1f}-{s.get('end'):.1f}] {str(s.get('text') or '')[:120]}")


if __name__ == "__main__":
    asyncio.run(run(_key()))
