"""Свежие звонки Никиты: Nexara с диаризацией + подпись Никита / клиент.

  PYTHONUNBUFFERED=1 PYTHONPATH=tools python3 tools/transcribe_nikita_speakers.py --budget-rub 1000 --workers 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_ЭТАЛОН" / "звонки"
INDEX = OUT / "_индекс.json"
AUDIO = OUT / "аудио"
TEXTS = OUT / "транскрипты_спикеры"
PROGRESS = OUT / "_прогресс_спикеры.json"
SELECTION = OUT / "_выборка_спикеры.json"
VAULT = ROOT.parents[3]
TRANSCRIBE_PY = (
    VAULT
    / "1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot/scripts"
)
URL = "https://api.nexara.ru/api/v1/audio/transcriptions"
MSK = timezone(timedelta(hours=3))
RATE = 0.72
SKIP_NOTE_IDS = {"343387431"}  # уже размечен в тест_спикеры


def _import_key():
    sys.path.insert(0, str(TRANSCRIBE_PY))
    from transcribe_meeting import _load_dotenv, resolve_api_key  # type: ignore

    _load_dotenv()
    return resolve_api_key()


def load_calls() -> list[dict]:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    return list(data.get("calls") or [])


def pick_newest_by_budget(calls: list[dict], budget_rub: float) -> list[dict]:
    ordered = sorted(calls, key=lambda c: int(c["created_at"]), reverse=True)
    picked: list[dict] = []
    sec = 0
    for call in ordered:
        if str(call.get("note_id")) in SKIP_NOTE_IDS:
            continue
        nxt = sec + int(call["duration"] or 0)
        if picked and (nxt / 60) * RATE > budget_rub:
            break
        picked.append(call)
        sec = nxt
    return picked


def load_progress() -> dict:
    if PROGRESS.is_file():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"done": [], "failed": []}


def save_progress(prog: dict) -> None:
    PROGRESS.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")


def paths(call: dict) -> tuple[Path, Path]:
    note_id = call["note_id"]
    dt = datetime.fromtimestamp(int(call["created_at"]), tz=MSK)
    return AUDIO / f"{note_id}.mp3", TEXTS / f"{dt:%Y-%m-%d}_{note_id}.md"


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
    if not md.is_file() or md.stat().st_size < 80:
        return False
    text = md.read_text(encoding="utf-8")
    return "**Никита:**" in text or "**Клиент:**" in text


def _nikita_score(text: str) -> int:
    t = (text or "").lower()
    n = 0
    intro = "дива" in t or "divo" in t or "моторс" in t or "слушаю" in t
    if "никита" in t and intro:
        n += 10
    elif intro:
        n += 5
    elif "никита" in t:
        n += 1
    if "ривьера" in t:
        n += 2
    return n


def map_speakers(segments: list[dict], call_type: str) -> dict[str, str]:
    scores: dict[str, int] = {}
    order: list[str] = []
    for seg in segments:
        if float(seg.get("start") or 0) > 25:
            continue
        sp = str(seg.get("speaker") or "")
        if not sp:
            continue
        if sp not in scores:
            scores[sp] = 0
            order.append(sp)
        scores[sp] += _nikita_score(str(seg.get("text") or ""))
    all_speakers: list[str] = []
    for seg in segments:
        sp = str(seg.get("speaker") or "")
        if sp and sp not in all_speakers:
            all_speakers.append(sp)
    if not all_speakers:
        return {}
    if not scores:
        mapping = {all_speakers[0]: "Никита"} if call_type == "call_in" else {all_speakers[0]: "Никита"}
        for sp in all_speakers[1:]:
            mapping[sp] = "Клиент"
        return mapping
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order.index(kv[0]) if kv[0] in order else 99))
    top, top_score = ranked[0]
    mapping: dict[str, str] = {}
    if top_score > 0:
        mapping[top] = "Никита"
    elif call_type == "call_in" and order:
        mapping[order[0]] = "Никита"
    else:
        mapping[top] = "Никита"
    for sp in all_speakers:
        if sp not in mapping:
            mapping[sp] = "Клиент"
    return mapping


def _fmt_ts(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    s = int(float(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def labeled_markdown(payload: dict, call: dict, audio: Path, mapping: dict[str, str]) -> str:
    note_id = call["note_id"]
    dt = datetime.fromtimestamp(int(call["created_at"]), tz=MSK)
    lines = [
        f"# Транскрипт: {audio.name}",
        "",
        f"- **Дата звонка:** {dt:%Y-%m-%d %H:%M} МСК",
        f"- **Дата обработки:** {datetime.now():%Y-%m-%d %H:%M}",
        f"- **Сделка amo:** {call.get('lead_id')}",
        f"- **Примечание:** {note_id}",
        f"- **Тип:** {call.get('type')}",
        f"- **Длительность amo (сек):** {call.get('duration')}",
        f"- **Длительность Nexara (сек):** {payload.get('duration', '—')}",
        f"- **Телефон (маска):** {call.get('phone') or 'нет'}",
        f"- **Провайдер:** Nexara, diarize + telephonic, 2 спикера",
        f"- **Роли:** Никита / Клиент (подпись по тексту, не Nexara roles)",
        "",
        "## Диалог",
        "",
    ]
    segs = payload.get("segments") or []
    if not segs:
        text = str(payload.get("text") or "").strip()
        lines.append(text or "_Сегментов нет._")
        lines.append("")
        return "\n".join(lines)
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        raw = str(seg.get("speaker") or "")
        role = mapping.get(raw, "Спикер")
        start = _fmt_ts(seg.get("start"))
        end = _fmt_ts(seg.get("end"))
        txt = str(seg.get("text") or "").strip()
        lines.append(f"**[{start}–{end}] {role}:** {txt}")
        lines.append("")
    return "\n".join(lines)


async def nexara_diarize(session: aiohttp.ClientSession, audio: Path, api_key: str) -> dict:
    mime = mimetypes.guess_type(str(audio))[0] or "audio/mpeg"
    with audio.open("rb") as f:
        data = aiohttp.FormData()
        data.add_field("file", f, filename=audio.name, content_type=mime)
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
                raise RuntimeError(f"Nexara {resp.status}: {body[:400]}")
            return json.loads(body)


async def one(
    call: dict,
    api_key: str,
    session: aiohttp.ClientSession,
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
            payload = await nexara_diarize(session, audio, api_key)
        except Exception as exc:
            async with prog_lock:
                prog["failed"].append({"note_id": note_id, "step": "nexara", "err": str(exc)[:240]})
                save_progress(prog)
            return "fail"
        mapping = map_speakers(payload.get("segments") or [], str(call.get("type") or ""))
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(labeled_markdown(payload, call, audio, mapping), encoding="utf-8")
        async with prog_lock:
            prog["done"].append(note_id)
            prog["last"] = {
                "note_id": note_id,
                "md": str(md),
                "map": mapping,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            save_progress(prog)
        return "ok"


async def run(budget_rub: float, workers: int, api_key: str) -> None:
    calls = pick_newest_by_budget(load_calls(), budget_rub)
    sec = sum(int(c["duration"] or 0) for c in calls)
    SELECTION.write_text(
        json.dumps(
            {
                "budget_rub": budget_rub,
                "rate": RATE,
                "calls": len(calls),
                "duration_min": round(sec / 60, 1),
                "nexara_est_rub": round((sec / 60) * RATE, 1),
                "from_ts": calls[-1]["created_at"] if calls else None,
                "to_ts": calls[0]["created_at"] if calls else None,
                "skip": sorted(SKIP_NOTE_IDS),
                "note_ids": [c["note_id"] for c in calls],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"выборка: {len(calls)} звонков, {round(sec/60,1)} мин, "
        f"~{round((sec/60)*RATE,1)} ₽ → {SELECTION}"
    )
    prog = load_progress()
    done_set = set(str(x) for x in prog.get("done") or [])
    pending = [c for c in calls if str(c["note_id"]) not in done_set and not already_done(paths(c)[1])]
    print(f"к расшифровке: {len(pending)} из {len(calls)}, воркеров {workers}")
    sem = asyncio.Semaphore(workers)
    lock = asyncio.Lock()
    ok = skip = fail = 0
    t0 = time.time()
    done_n = 0

    async def wrapped(call: dict, session: aiohttp.ClientSession) -> None:
        nonlocal ok, skip, fail, done_n
        status = await one(call, api_key, session, sem, prog, lock)
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
                    f"{int(time.time() - t0)}с last={call['note_id']}",
                    flush=True,
                )

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(wrapped(c, session) for c in pending))
    print(f"готово: ok={ok} skip={skip} fail={fail} всего_done={len(prog['done'])}")
    print(f"папка: {TEXTS}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-rub", type=float, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    AUDIO.mkdir(parents=True, exist_ok=True)
    TEXTS.mkdir(parents=True, exist_ok=True)
    api_key = _import_key()
    asyncio.run(run(args.budget_rub, args.workers, api_key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
