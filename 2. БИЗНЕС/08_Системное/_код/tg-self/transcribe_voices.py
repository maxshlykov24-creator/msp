#!/usr/bin/env python3
"""Скачать голосовые и расшифровать. Аудио после запроса удаляется."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from export_my_voice import DEFAULT_OUT, DUMP
from tg import open_authorized, require_session

CACHE_PATH = DUMP / "voice_cache.jsonl"
NEXARA_URL = "https://api.nexara.ru/api/v1/audio/transcriptions"


def load_env_key() -> str:
    key = (os.environ.get("NEXARA_API_KEY") or "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("NEXARA_API_KEY="):
                value = raw.split("=", 1)[1].strip().strip("'").strip('"')
                if value:
                    return value
    study = (
        Path(__file__).resolve().parents[4]
        / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot"
    )
    if str(study) not in sys.path:
        sys.path.insert(0, str(study))
    try:
        from config import NEXARA_API_KEY  # type: ignore

        value = (NEXARA_API_KEY or "").strip()
        if value and "YOUR_" not in value:
            return value
    except Exception:
        pass
    scripts = (
        Path(__file__).resolve().parents[4]
        / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика"
        / "study-bot/scripts"
    )
    if scripts.is_dir():
        sys.path.insert(0, str(scripts))
        try:
            from transcribe_meeting import resolve_api_key  # type: ignore

            return (resolve_api_key() or "").strip()
        except Exception:
            pass
    return ""


def load_cache(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            uid = row.get("msg_uid")
            text = row.get("text")
            if uid and text:
                data[uid] = text
    return data


def append_cache(path: Path, uid: str, text: str, engine: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"msg_uid": uid, "text": text, "engine": engine},
                ensure_ascii=False,
            )
            + "\n"
        )


def pending_rows(path: Path, since: str = "") -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("kind") not in ("voice", "video_note"):
                continue
            if (row.get("text") or "").strip():
                continue
            if since and (row.get("date") or "") < since:
                continue
            rows.append(row)
    return rows


async def nexara_transcribe(audio_path: Path, api_key: str) -> str:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=180)
    headers = {"Authorization": f"Bearer {api_key}"}
    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio_path.read_bytes(),
        filename=audio_path.name,
        content_type="audio/ogg",
    )
    form.add_field("response_format", "json")
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(NEXARA_URL, headers=headers, data=form) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Nexara HTTP {resp.status}: {body[:300]}")
            data = json.loads(body)
    if isinstance(data.get("text"), str) and data["text"]:
        return data["text"].strip()
    inner = data.get("transcription")
    if isinstance(inner, dict) and isinstance(inner.get("text"), str):
        return inner["text"].strip()
    return ""


def local_transcribe(audio_path: Path) -> str:
    from faster_whisper import WhisperModel

    model = local_transcribe.model  # type: ignore[attr-defined]
    if model is None:
        local_transcribe.model = WhisperModel("small", device="auto")  # type: ignore[attr-defined]
        model = local_transcribe.model  # type: ignore[attr-defined]
    segments, _info = model.transcribe(str(audio_path), language="ru")
    return "".join(seg.text for seg in segments).strip()


local_transcribe.model = None  # type: ignore[attr-defined]


async def download_and_transcribe(
    client,
    row: dict,
    engine: str,
    api_key: str,
) -> str:
    from telethon.errors import FloodWaitError

    chat_id = row["chat_id"]
    _, _, msg_id_s = row["msg_uid"].partition(":")
    msg_id = int(msg_id_s)
    try:
        message = await client.get_messages(chat_id, ids=msg_id)
    except FloodWaitError as exc:
        await asyncio.sleep(int(exc.seconds) + 2)
        message = await client.get_messages(chat_id, ids=msg_id)
    if message is None:
        raise RuntimeError("сообщение не найдено")
    tmpdir = tempfile.mkdtemp(prefix="tg-voice-")
    tmp = Path(tmpdir) / "audio.ogg"
    try:
        await client.download_media(message, file=str(tmp))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("пустое аудио")
        if engine == "nexara":
            return await nexara_transcribe(tmp, api_key)
        return await asyncio.to_thread(local_transcribe, tmp)
    finally:
        if tmp.exists():
            tmp.unlink()
        try:
            Path(tmpdir).rmdir()
        except OSError:
            pass


def rewrite_jsonl(path: Path, updates: dict[str, str]) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    with path.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        for line in src:
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            uid = row.get("msg_uid")
            if uid in updates:
                row["text"] = updates[uid]
                row["text_source"] = "stt"
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


async def async_main(args: argparse.Namespace) -> None:
    src = Path(args.input)
    if not src.exists():
        sys.exit(f"Нет {src}")
    cache = load_cache(CACHE_PATH)
    pending = pending_rows(src, args.since)
    pending = [r for r in pending if r["msg_uid"] not in cache]
    if args.limit:
        pending = pending[: args.limit]
    print(f"к расшифровке: {len(pending)}", flush=True)
    if not pending:
        return

    api_key = ""
    if args.engine == "nexara":
        api_key = load_env_key()
        if not api_key:
            sys.exit(
                "Нет NEXARA_API_KEY. Положи в окружение или в tg-self/.env, "
                "не пиши ключ в чат."
            )

    by_account: dict[str, list[dict]] = {"self": [], "work": []}
    for row in pending:
        by_account.setdefault(row.get("account") or "self", []).append(row)

    updates = dict(cache)
    failed: list[str] = []
    done = 0
    total = len(pending)
    cache_lock = asyncio.Lock()

    for account, rows in by_account.items():
        if not rows:
            continue
        try:
            client = await open_authorized(account)
        except SystemExit as exc:
            print(f"пропускаю {account}: {exc}", flush=True)
            failed.append(f"{account} · сессия не авторизована, {len(rows)} шт. отложено")
            continue
        print(f"== {account}: {len(rows)} (параллельно {args.workers})", flush=True)
        queue: asyncio.Queue = asyncio.Queue()
        for row in rows:
            queue.put_nowait(row)

        async def worker() -> None:
            nonlocal done
            while True:
                try:
                    row = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                uid = row["msg_uid"]
                try:
                    text = await download_and_transcribe(
                        client, row, args.engine, api_key
                    )
                except Exception as exc:
                    print(f"  fail {uid}: {exc}", flush=True)
                    failed.append(f"{uid} · {exc}")
                    continue
                async with cache_lock:
                    updates[uid] = text
                    append_cache(CACHE_PATH, uid, text, args.engine)
                    done += 1
                    current = done
                if current % 25 == 0 or current <= 5:
                    preview = text[:70].replace("\n", " ")
                    print(f"  {current}/{total} {uid} {preview!r}", flush=True)

        await asyncio.gather(*[worker() for _ in range(args.workers)])
        await client.disconnect()

    rewrite_jsonl(src, updates)
    print(f"готово: {done}, ошибок: {len(failed)}", flush=True)
    if failed:
        (DUMP / "voice_failed.md").write_text(
            "\n".join(f"- {x}" for x in failed) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Транскрибация голосовых из выгрузки")
    parser.add_argument("--input", default=str(DEFAULT_OUT))
    parser.add_argument("--engine", choices=("nexara", "local"), required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 = все")
    parser.add_argument("--since", default="", help="ISO-дата, например 2026-01-01")
    parser.add_argument("--workers", type=int, default=8, help="параллельных запросов")
    args = parser.parse_args()
    if args.engine == "nexara":
        key = load_env_key()
        if not key:
            sys.exit(
                "Нет NEXARA_API_KEY. Положи в окружение или в tg-self/.env, "
                "не пиши ключ в чат."
            )
        os.environ["NEXARA_API_KEY"] = key
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
