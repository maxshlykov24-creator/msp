"""Скачать записи Mango и расшифровать звонки Никиты через Nexara.

Без диаризации. Умеет продолжать с места остановки.

  PYTHONPATH=tools python3 tools/transcribe_nikita_calls.py
  PYTHONPATH=tools python3 tools/transcribe_nikita_calls.py --limit 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_ЭТАЛОН" / "звонки"
INDEX = OUT / "_индекс.json"
AUDIO = OUT / "аудио"
TEXTS = OUT / "транскрипты"
PROGRESS = OUT / "_прогресс.json"
VAULT = ROOT.parents[3]
TRANSCRIBE_PY = (
    VAULT
    / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot/scripts"
)
MSK = timezone(timedelta(hours=3))


def _import_nexara():
    sys.path.insert(0, str(TRANSCRIBE_PY))
    from transcribe_meeting import (  # type: ignore
        _load_dotenv,
        payload_to_markdown,
        resolve_api_key,
        transcribe_file,
    )

    _load_dotenv()
    return resolve_api_key, transcribe_file, payload_to_markdown


RATE_RUB_PER_MIN = 0.36
SELECTION = OUT / "_выборка.json"


def load_calls() -> list[dict]:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    return list(data.get("calls") or [])


def pick_newest_by_budget(calls: list[dict], budget_rub: float) -> list[dict]:
    ordered = sorted(calls, key=lambda c: int(c["created_at"]), reverse=True)
    picked: list[dict] = []
    sec = 0
    for call in ordered:
        nxt = sec + int(call["duration"] or 0)
        if picked and (nxt / 60) * RATE_RUB_PER_MIN > budget_rub:
            break
        picked.append(call)
        sec = nxt
    return picked


def load_progress() -> dict:
    if PROGRESS.is_file():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"done": [], "failed": [], "skipped_no_audio": []}


def save_progress(prog: dict) -> None:
    PROGRESS.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")


def paths(call: dict) -> tuple[Path, Path]:
    note_id = call["note_id"]
    dt = datetime.fromtimestamp(int(call["created_at"]), tz=MSK)
    day = dt.strftime("%Y-%m-%d")
    return AUDIO / f"{note_id}.mp3", TEXTS / f"{day}_{note_id}.md"


def download(link: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(link, headers={"User-Agent": "divo-ai-manager/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read()
    if len(raw) < 800:
        raise RuntimeError(f"слишком короткий файл {len(raw)} байт")
    if raw[:15].lower().startswith(b"<!doctype") or raw[:6].lower() == b"<html":
        raise RuntimeError("Mango отдал HTML, не аудио")
    dest.write_bytes(raw)


def already_done(md: Path) -> bool:
    return md.is_file() and md.stat().st_size > 80


async def one(
    call: dict,
    api_key: str,
    transcribe_file,
    payload_to_markdown,
    sem: asyncio.Semaphore,
    prog: dict,
    prog_lock: asyncio.Lock,
) -> str:
    note_id = str(call["note_id"])
    audio, md = paths(call)
    if already_done(md):
        async with prog_lock:
            if note_id not in prog["done"]:
                prog["done"].append(note_id)
        return "skip"

    async with sem:
        if not audio.is_file() or audio.stat().st_size < 800:
            try:
                await asyncio.to_thread(download, call["link"], audio)
            except Exception as exc:
                async with prog_lock:
                    prog["failed"].append({"note_id": note_id, "step": "download", "err": str(exc)[:240]})
                    save_progress(prog)
                return "fail"

        try:
            payload = await transcribe_file(audio, api_key, diarize=False)
        except Exception as exc:
            async with prog_lock:
                prog["failed"].append({"note_id": note_id, "step": "nexara", "err": str(exc)[:240]})
                save_progress(prog)
            return "fail"

        body = payload_to_markdown(payload, audio, diarize=False)
        header = [
            f"- **Сделка amo:** {call.get('lead_id')}",
            f"- **Примечание:** {note_id}",
            f"- **Тип:** {call.get('type')}",
            f"- **Длительность amo (сек):** {call.get('duration')}",
            f"- **Телефон (маска):** {call.get('phone') or 'нет'}",
            "",
        ]
        lines = body.splitlines()
        out: list[str] = []
        inserted = False
        for i, line in enumerate(lines):
            out.append(line)
            if not inserted and i >= 2 and line == "":
                out.extend(header)
                inserted = True
        text = "\n".join(out)
        if not text.endswith("\n"):
            text += "\n"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(text, encoding="utf-8")
        async with prog_lock:
            prog["done"].append(note_id)
            prog["last"] = {
                "note_id": note_id,
                "md": str(md),
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            save_progress(prog)
        return "ok"


async def run(
    limit: int,
    workers: int,
    api_key: str,
    transcribe_file,
    payload_to_markdown,
    *,
    newest: bool,
    budget_rub: float,
) -> None:
    calls = load_calls()
    if newest and budget_rub > 0:
        calls = pick_newest_by_budget(calls, budget_rub)
        sec = sum(int(c["duration"] or 0) for c in calls)
        SELECTION.write_text(
            json.dumps(
                {
                    "budget_rub": budget_rub,
                    "rate": RATE_RUB_PER_MIN,
                    "calls": len(calls),
                    "duration_min": round(sec / 60, 1),
                    "nexara_est_rub": round((sec / 60) * RATE_RUB_PER_MIN, 1),
                    "from_ts": calls[-1]["created_at"] if calls else None,
                    "to_ts": calls[0]["created_at"] if calls else None,
                    "note_ids": [c["note_id"] for c in calls],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"выборка: {len(calls)} звонков, {round(sec/60,1)} мин, "
            f"~{round((sec/60)*RATE_RUB_PER_MIN,1)} ₽ → {SELECTION}"
        )
    if limit:
        calls = calls[:limit]
    prog = load_progress()
    done_set = set(prog.get("done") or [])
    pending = [c for c in calls if str(c["note_id"]) not in done_set and not already_done(paths(c)[1])]
    print(f"к расшифровке: {len(pending)} из {len(calls)}, воркеров {workers}")
    sem = asyncio.Semaphore(workers)
    lock = asyncio.Lock()
    ok = skip = fail = 0
    t0 = time.time()
    done_n = 0

    async def wrapped(call: dict) -> None:
        nonlocal ok, skip, fail, done_n
        status = await one(call, api_key, transcribe_file, payload_to_markdown, sem, prog, lock)
        async with lock:
            done_n += 1
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            if done_n % 10 == 0 or status == "fail":
                print(
                    f"  {done_n}/{len(pending)} ok={ok} skip={skip} fail={fail} "
                    f"{int(time.time() - t0)}с last={call['note_id']}"
                )

    await asyncio.gather(*(wrapped(c) for c in pending))
    print(f"готово: ok={ok} skip={skip} fail={fail} всего_done={len(prog['done'])}")
    print(f"прогресс: {PROGRESS}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--newest", action="store_true", help="брать с конца, не с начала года")
    parser.add_argument("--budget-rub", type=float, default=0, help="потолок Nexara в рублях")
    args = parser.parse_args()
    AUDIO.mkdir(parents=True, exist_ok=True)
    TEXTS.mkdir(parents=True, exist_ok=True)
    resolve_api_key, transcribe_file, payload_to_markdown = _import_nexara()
    api_key = resolve_api_key()
    asyncio.run(
        run(
            args.limit,
            args.workers,
            api_key,
            transcribe_file,
            payload_to_markdown,
            newest=args.newest,
            budget_rub=args.budget_rub,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
