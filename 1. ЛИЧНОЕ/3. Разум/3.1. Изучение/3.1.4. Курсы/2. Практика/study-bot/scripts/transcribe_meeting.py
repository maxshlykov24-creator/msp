#!/usr/bin/env python3
"""
Канон М4 · транскрибация файла через Nexara.

Выход: шапка + только таймлайн. Сплошной полный текст не пишется.

  python3 transcribe_meeting.py файл.m4a --client LicenseBridge_USA
  python3 transcribe_meeting.py файл.m4a -o /tmp/out.md
  python3 transcribe_meeting.py файл.m4a --client MANSBAND --diarize

--diarize по умолчанию выкл (дешевле). Включать только после «да» владельца.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

NEXARA_BASE = "https://api.nexara.ru"
TRANSCRIBE_URL = f"{NEXARA_BASE}/api/v1/audio/transcriptions"


def find_vault_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "_СИСТЕМА").is_dir() and (p / "2. БИЗНЕС").is_dir():
            return p
    raise RuntimeError(f"Не найден корень vault от {start}")


VAULT_ROOT = find_vault_root(Path(__file__).resolve())
STUDY_BOT = (
    VAULT_ROOT
    / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot"
)
CLIENTS = VAULT_ROOT / "2. БИЗНЕС/04_Производство/Активные"
INBOX = VAULT_ROOT / "_ВХОДЯЩЕЕ/новое"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (STUDY_BOT / ".env", VAULT_ROOT / ".env"):
        if p.is_file():
            load_dotenv(p)


def _parse_nexara_from_dostupy() -> tuple[str, str]:
    dostupy = VAULT_ROOT / "2. БИЗНЕС" / "08_Системное" / "ДОСТУПЫ.md"
    if not dostupy.is_file():
        return "", ""
    text = dostupy.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "nexara" not in line.lower() or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 3 and "nexara" in parts[0].lower():
            return parts[1], parts[2]
    return "", ""


def resolve_api_key() -> str:
    key = (os.getenv("NEXARA_API_KEY") or "").strip()
    if key and "YOUR_" not in key and "your_" not in key.lower():
        return key

    if STUDY_BOT.is_dir():
        sys.path.insert(0, str(STUDY_BOT))
        try:
            from config import NEXARA_API_KEY  # type: ignore

            k = (NEXARA_API_KEY or "").strip()
            if k and "YOUR_" not in k:
                return k
        except Exception:
            pass

    email = (os.getenv("NEXARA_EMAIL") or "").strip()
    password = (os.getenv("NEXARA_PASSWORD") or "").strip()
    if not email or not password:
        email, password = _parse_nexara_from_dostupy()
    if not email or not password:
        raise RuntimeError(
            "Нет NEXARA_API_KEY и нет пары NEXARA_EMAIL/NEXARA_PASSWORD "
            "(или строка Nexara в ДОСТУПЫ.md)"
        )
    return asyncio.get_event_loop().run_until_complete(
        _login_and_get_api_key(email, password)
    )


async def _login_and_get_api_key(email: str, password: str) -> str:
    body = urlencode({"username": email, "password": password})
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{NEXARA_BASE}/auth/jwt/login",
            headers=headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Nexara login HTTP {resp.status}: {text[:400]}"
                )
            data = json.loads(text)
            token = data.get("access_token")
            if not token:
                raise RuntimeError("Nexara login: нет access_token")

        async with session.get(
            f"{NEXARA_BASE}/view_account",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Nexara view_account HTTP {resp.status}: {text[:400]}"
                )
            account = json.loads(text)
            api_key = (account.get("api_key") or "").strip()
            if api_key:
                logger.info("API key из view_account")
                return api_key

            keys = account.get("api_keys") or []
            if keys and keys[0].get("api_key"):
                logger.info("API key из api_keys[0]")
                return str(keys[0]["api_key"]).strip()

        form = aiohttp.FormData()
        form.add_field("location", account.get("location") or "RUB")
        form.add_field("name", f"vault-{datetime.now():%Y%m%d}")
        async with session.post(
            f"{NEXARA_BASE}/create_key",
            headers={"Authorization": f"Bearer {token}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Nexara create_key HTTP {resp.status}: {text[:400]}"
                )
            created = json.loads(text)
            if created.get("api_key"):
                return str(created["api_key"]).strip()
            new_keys = created.get("api_keys") or created
            if isinstance(new_keys, list) and new_keys:
                k = new_keys[-1].get("api_key")
                if k:
                    return str(k).strip()

    raise RuntimeError("Не удалось получить API key после логина")


def _format_ts(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    s = int(float(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _speaker_label(seg: dict) -> str:
    for key in ("speaker", "speaker_id", "speaker_label"):
        if seg.get(key) is not None:
            return f"Спикер {seg[key]}"
    return ""


def payload_to_markdown(
    payload: dict,
    source: Path,
    *,
    diarize: bool,
) -> str:
    duration = payload.get("duration", "—")
    speakers_note = "да" if diarize else "нет (не заказывали)"
    lines = [
        f"# Транскрипт: {source.name}",
        "",
        f"- **Дата обработки:** {datetime.now():%Y-%m-%d %H:%M}",
        f"- **Длительность (сек):** {duration}",
        f"- **Источник:** `{source}`",
        f"- **Провайдер:** Nexara",
        f"- **Спикеры:** {speakers_note}",
        f"- **Формат:** только таймлайн (сегменты с таймкодами)",
        "",
        "## Таймлайн",
        "",
    ]
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        text = str(payload.get("text") or "").strip()
        if text:
            lines.append(text)
            lines.append("")
            return "\n".join(lines)
        lines.append("_Сегментов нет в ответе API._")
        return "\n".join(lines) + "\n"

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = _format_ts(seg.get("start"))
        end = _format_ts(seg.get("end"))
        sp = _speaker_label(seg)
        txt = str(seg.get("text") or "").strip()
        prefix = f"**[{start}–{end}]**"
        if sp:
            prefix += f" {sp}:"
        lines.append(f"{prefix} {txt}")
        lines.append("")

    return "\n".join(lines)


def resolve_output(args: argparse.Namespace, source: Path) -> Path:
    if args.output:
        return Path(args.output).expanduser().resolve()
    stem = re.sub(r"[^\w.\-]+", "_", source.stem, flags=re.UNICODE)
    name = f"{datetime.now():%Y-%m-%d}_{stem}_транскрипт.md"
    if args.client:
        client_dir = CLIENTS / args.client
        if not client_dir.is_dir():
            raise RuntimeError(
                f"Нет папки клиента: {client_dir}. "
                "Проверь имя в РЕЕСТР_ПРОЕКТОВ.md или укажи --output."
            )
        return client_dir / "04_Встречи" / name
    return INBOX / name


async def transcribe_file(
    file_path: Path,
    api_key: str,
    *,
    diarize: bool = False,
) -> dict:
    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    timeout_sec = int(os.getenv("TRANSCRIBE_TIMEOUT_SEC", "3600"))
    sock_read = max(300, timeout_sec + 120)

    logger.info(
        "Загрузка %s (%s МБ), diarize=%s, timeout sock_read=%s",
        file_path.name,
        round(file_path.stat().st_size / 1024 / 1024, 1),
        diarize,
        sock_read,
    )

    headers = {"Authorization": f"Bearer {api_key}"}

    async with aiohttp.ClientSession() as session:
        with file_path.open("rb") as f:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                f,
                filename=file_path.name,
                content_type=mime,
            )
            data.add_field("response_format", "verbose_json")
            if diarize:
                data.add_field("task", "diarize")
                data.add_field("num_speakers", "2")

            async with session.post(
                TRANSCRIBE_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=60,
                    sock_read=sock_read,
                ),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"Nexara transcribe HTTP {resp.status}: {body[:1200]}"
                    )
                try:
                    return json.loads(body)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"Nexara: не JSON: {body[:500]!r}"
                    ) from e


async def async_main(
    input_path: Path,
    output_path: Path,
    api_key: str,
    *,
    diarize: bool,
) -> None:
    payload = await transcribe_file(input_path, api_key, diarize=diarize)
    md = payload_to_markdown(payload, input_path, diarize=diarize)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    logger.info("Сохранено: %s (%s символов)", output_path, len(md))


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="М4 транскрибация файла (Nexara)")
    parser.add_argument("input", type=Path, help="Путь к audio/video")
    parser.add_argument("-o", "--output", type=Path, help="Путь к .md")
    parser.add_argument(
        "--client",
        default="",
        help="Имя папки в Активные/ → …/04_Встречи/",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="Разбить по спикерам. По умолчанию выкл. Только после «да».",
    )
    args = parser.parse_args()

    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        print(f"Файл не найден: {src}", file=sys.stderr)
        sys.exit(1)

    out = resolve_output(args, src)
    print(f"Выход: {out}")
    print(f"Спикеры: {'да' if args.diarize else 'нет'}")

    api_key = resolve_api_key()
    asyncio.run(async_main(src, out, api_key, diarize=args.diarize))


if __name__ == "__main__":
    main()
