#!/usr/bin/env python3
"""Статистика корпуса и инвентаризация голосовых."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from export_my_voice import DEFAULT_OUT, DUMP

TONE_SINCE = "2026-01-01"
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", flags=re.UNICODE)
YOU_RE = re.compile(r"\b(ты|тебе|тебя|тобой|тво[йеяюи]\w*)\b", re.IGNORECASE)
VY_RE = re.compile(r"\b(вы|вас|вам|вами|ваш\w*)\b", re.IGNORECASE)
GREET_RE = re.compile(
    r"^(привет|здравствуй|добр(ый|ое|ая)\s+(день|утро|вечер)|хай|ку|йо)\b",
    re.IGNORECASE,
)
BYE_RE = re.compile(
    r"\b(пока|до\s+связи|спокойной\s+ночи|всего\s+доброго|удачи)\b",
    re.IGNORECASE,
)
INTRO_RE = re.compile(
    r"^(ну|так|слушай|смотри|короче|ладно|ок|окей|ага|угу|щас|сейчас|понял)\b",
    re.IGNORECASE,
)
NEXARA_RUB_PER_MIN = 0.36
LOCAL_RTF = 0.25


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def median(values: list[float]) -> float | None:
    if not values:
        return None
    data = sorted(values)
    mid = len(data) // 2
    if len(data) % 2:
        return float(data[mid])
    return (data[mid - 1] + data[mid]) / 2.0


def pct(part: int, total: int) -> str:
    if not total:
        return "—"
    return f"{part / total * 100:.1f}%"


def fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isnan(value):
            return "—"
        if value >= 10:
            return f"{value:.1f}"
        return f"{value:.2f}"
    return str(value)


def in_tone_window(row: dict) -> bool:
    return (row.get("date") or "") >= TONE_SINCE


def words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def burst_sizes(out_rows: list[dict]) -> list[int]:
    """Сколько исходящих подряд в одной реплике (разрыв > 120с или чужое сообщение)."""
    by_chat: dict[int, list[dict]] = defaultdict(list)
    for row in out_rows:
        by_chat[row["chat_id"]].append(row)
    sizes = []
    for items in by_chat.values():
        items.sort(key=lambda r: r.get("date") or "")
        current = 0
        for row in items:
            gap = row.get("seconds_since_prev")
            # prev может быть входящим: если prev исходящий и gap маленький — продолжение
            if current == 0:
                current = 1
                continue
            prev_dir_out = True
            # seconds_since_prev считается от любого предыдущего в ленте
            if gap is not None and gap <= 120:
                # если предыдущее было входящим, это новая реплика
                # heur: если gap маленький и это out после out — burst
                # без prev direction в этой выборке: смотрим только соседние out
                # если между двумя out меньше 120с — считаем одной репликой только
                # когда seconds_since_prev маленькое. Входящее между ними даст
                # больший разрыв по смыслу, но seconds_since_prev будет от in.
                # Для двух out подряд seconds_since_prev обычно мало.
                current += 1
            else:
                sizes.append(current)
                current = 1
        if current:
            sizes.append(current)
    return sizes


def collect_inventory(rows: list[dict]) -> dict:
    voices = [
        r
        for r in rows
        if r.get("kind") in ("voice", "video_note")
    ]
    buckets = defaultdict(lambda: {"count": 0, "sec": 0, "out": 0, "in": 0})
    by_kind = Counter()
    for row in voices:
        scope = row.get("scope") or "personal"
        sec = int(row.get("duration_sec") or 0)
        buckets[scope]["count"] += 1
        buckets[scope]["sec"] += sec
        buckets[scope][row.get("direction") or "in"] += 1
        by_kind[row.get("kind")] += 1
        tone_key = f"{scope}:tone2026"
        if in_tone_window(row):
            buckets[tone_key]["count"] += 1
            buckets[tone_key]["sec"] += sec
            buckets[tone_key][row.get("direction") or "in"] += 1
    total_sec = sum(int(r.get("duration_sec") or 0) for r in voices)
    tone_sec = sum(
        int(r.get("duration_sec") or 0) for r in voices if in_tone_window(r)
    )
    return {
        "all": voices,
        "buckets": buckets,
        "by_kind": by_kind,
        "total_count": len(voices),
        "total_sec": total_sec,
        "tone_count": sum(1 for r in voices if in_tone_window(r)),
        "tone_sec": tone_sec,
    }


def inventory_markdown(inv: dict) -> list[str]:
    minutes = inv["total_sec"] / 60
    tone_min = inv["tone_sec"] / 60
    nexara_all = minutes * NEXARA_RUB_PER_MIN
    nexara_tone = tone_min * NEXARA_RUB_PER_MIN
    local_hours = (minutes * LOCAL_RTF) / 60
    lines = [
        "# Инвентаризация голосовых",
        "",
        "Тариф Nexara: 0,36 ₽/мин, без диаризации, источник [docs.nexara.ru/pricing](https://docs.nexara.ru/pricing).",
        "Локально: оценка 0,25 реального времени (faster-whisper на Mac, гипотеза до прогона).",
        "",
        f"- всего голосовых и кружков: **{inv['total_count']}**, {inv['total_sec']} сек ({minutes:.1f} мин)",
        f"- из них за 2026 (окно тона): **{inv['tone_count']}**, {inv['tone_sec']} сек ({tone_min:.1f} мин)",
        f"- Nexara всё: ≈ **{nexara_all:.0f} ₽**; только 2026: ≈ **{nexara_tone:.0f} ₽**",
        f"- локально всё: ≈ **{local_hours:.1f} ч** чистого прогона",
        "",
        "| scope | штук | минут | исходящие | входящие |",
        "|---|---:|---:|---:|---:|",
    ]
    for scope in ("work", "personal", "close"):
        b = inv["buckets"].get(scope)
        if not b:
            continue
        lines.append(
            f"| {scope} | {b['count']} | {b['sec']/60:.1f} | {b['out']} | {b['in']} |"
        )
    lines.append("")
    lines.append(f"виды: {dict(inv['by_kind'])}")
    lines.append("")
    if inv["total_count"] > 400:
        lines.append(
            "Голосовых много. Третий вариант: стратифицированная выборка 200–300 штук "
            "для профиля тона, без полной расшифровки."
        )
    return lines


def scope_stats(rows: list[dict], scope: str, direction: str, written: bool) -> dict:
    picked = []
    for row in rows:
        if row.get("scope") != scope:
            continue
        if row.get("direction") != direction:
            continue
        if not in_tone_window(row):
            continue
        is_voice = row.get("kind") in ("voice", "video_note")
        if written and is_voice:
            continue
        if not written and not is_voice:
            continue
        picked.append(row)
    texts = [(r.get("text") or "") for r in picked]
    chars = [len(t) for t in texts]
    word_counts = [len(words(t)) for t in texts]
    one_line = sum(1 for t in texts if t.count("\n") == 0)
    with_dot = sum(1 for t in texts if t.rstrip().endswith("."))
    ellipsis = sum(1 for t in texts if "..." in t or "…" in t)
    parens = sum(1 for t in texts if "(" in t or ")" in t)
    caps = sum(1 for t in texts if t.isupper() and len(t) >= 4)
    questions = sum(1 for t in texts if "?" in t)
    you = sum(1 for t in texts if YOU_RE.search(t))
    vy = sum(1 for t in texts if VY_RE.search(t))
    greet = sum(1 for t in texts if GREET_RE.search(t.strip()))
    bye = sum(1 for t in texts if BYE_RE.search(t))
    intro = Counter()
    bigrams = Counter()
    emojis = Counter()
    for t in texts:
        m = INTRO_RE.match(t.strip())
        if m:
            intro[m.group(0).lower()] += 1
        ws = words(t)
        for a, b in zip(ws, ws[1:]):
            bigrams[f"{a} {b}"] += 1
        for em in EMOJI_RE.findall(t):
            emojis[em] += 1
    delays = []
    hours = Counter()
    for r in picked:
        if r.get("seconds_since_prev") is not None:
            delays.append(int(r["seconds_since_prev"]))
        date = r.get("date") or ""
        if len(date) >= 13:
            try:
                hours[int(date[11:13])] += 1
            except ValueError:
                pass
    bursts = burst_sizes(picked) if direction == "out" and written else []
    durations = [int(r.get("duration_sec") or 0) for r in picked if not written]
    dates = [r.get("date") for r in picked if r.get("date")]
    chats = {r["chat_id"] for r in picked}
    return {
        "n": len(picked),
        "chats": len(chats),
        "from": min(dates) if dates else None,
        "to": max(dates) if dates else None,
        "median_chars": median(chars),
        "median_words": median(word_counts),
        "one_line": one_line,
        "dot": with_dot,
        "ellipsis": ellipsis,
        "parens": parens,
        "caps": caps,
        "questions": questions,
        "you": you,
        "vy": vy,
        "greet": greet,
        "bye": bye,
        "intro": intro.most_common(15),
        "bigrams": bigrams.most_common(50),
        "emojis": emojis.most_common(20),
        "median_delay": median(delays),
        "hours": hours,
        "bursts": bursts,
        "median_burst": median(bursts) if bursts else None,
        "median_duration": median(durations) if durations else None,
    }


def render_block(title: str, s: dict) -> list[str]:
    n = s["n"]
    lines = [
        f"### {title}",
        "",
        f"- сообщений: {n}, чатов: {s['chats']}, период: {s['from'] or '—'} → {s['to'] or '—'}",
        f"- медиана длины: {fmt_num(s['median_chars'])} символов, {fmt_num(s['median_words'])} слов",
        f"- в одну строку: {s['one_line']} ({pct(s['one_line'], n)})",
        f"- точка в конце: {s['dot']} ({pct(s['dot'], n)})",
        f"- многоточие: {s['ellipsis']}, скобки: {s['parens']}, CAPS: {s['caps']}",
        f"- с вопросом: {s['questions']} ({pct(s['questions'], n)})",
        f"- «ты»: {s['you']}, «вы»: {s['vy']}",
        f"- приветствие: {s['greet']}, прощание: {s['bye']}",
        f"- медиана паузы до сообщения: {fmt_num(s['median_delay'])} сек",
        f"- медиана дробления реплики: {fmt_num(s['median_burst'])} сообщений подряд",
    ]
    if s["median_duration"] is not None:
        lines.append(f"- медиана длительности голоса: {fmt_num(s['median_duration'])} сек")
    if s["intro"]:
        lines.append("- вводные: " + ", ".join(f"{k} {v}" for k, v in s["intro"]))
    if s["emojis"]:
        lines.append("- эмодзи: " + ", ".join(f"{k} {v}" for k, v in s["emojis"]))
    if s["hours"]:
        top_h = s["hours"].most_common(6)
        lines.append("- часы суток: " + ", ".join(f"{h}:00 → {c}" for h, c in top_h))
    if s["bigrams"]:
        lines.append("- биграммы: " + ", ".join(f"{k} ({v})" for k, v in s["bigrams"][:20]))
    lines.append("")
    return lines


def voice_vs_text(rows: list[dict]) -> list[str]:
    lines = ["## Голос против текста", ""]
    by_chat = defaultdict(lambda: {"text_out": 0, "voice_out": 0, "title": "", "scope": ""})
    for row in rows:
        if row.get("direction") != "out" or not in_tone_window(row):
            continue
        c = by_chat[row["chat_id"]]
        c["title"] = row.get("chat_title") or ""
        c["scope"] = row.get("scope") or ""
        if row.get("kind") in ("voice", "video_note"):
            c["voice_out"] += 1
        else:
            c["text_out"] += 1
    switched = []
    for chat_id, c in by_chat.items():
        total = c["text_out"] + c["voice_out"]
        if total < 8 or c["voice_out"] == 0:
            continue
        share = c["voice_out"] / total
        switched.append((share, chat_id, c))
    switched.sort(key=lambda item: item[0], reverse=True)
    lines.append("Чаты, где исходящие голосовые заметны (доля от твоих сообщений, 2026):")
    lines.append("")
    if not switched:
        lines.append("Недостаточно данных или голосовые ещё без разметки чатов.")
    for share, chat_id, c in switched[:25]:
        title = (c["title"] or "").replace("|", "/")
        lines.append(
            f"- {c['scope']} · {title} · голос {c['voice_out']} / всего {c['text_out']+c['voice_out']} "
            f"({share*100:.0f}%) · `{chat_id}`"
        )
    lines.append("")
    return lines


def write_report(rows: list[dict], path: Path) -> None:
    inv = collect_inventory(rows)
    lines = [
        "# Статистика корпуса Telegram",
        "",
        f"Собрано: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Файл: `{DEFAULT_OUT}`",
        f"Всего строк в корпусе: {len(rows)}",
        "Окно тона: date >= 2026-01-01. База знаний смотрит весь файл без этого фильтра.",
        "Scope `close` появится после твоей разметки; сейчас пусто.",
        "",
    ]
    lines.extend(inventory_markdown(inv))
    lines.append("")
    lines.append("## Письменный и устный тон")
    lines.append("")
    for scope in ("work", "personal", "close"):
        has = any(r.get("scope") == scope for r in rows)
        if not has:
            continue
        lines.append(f"## Scope: {scope}")
        lines.append("")
        lines.extend(render_block("Исходящие, текст, 2026", scope_stats(rows, scope, "out", True)))
        lines.extend(render_block("Исходящие, голос, 2026", scope_stats(rows, scope, "out", False)))
        lines.extend(render_block("Входящие, текст, 2026", scope_stats(rows, scope, "in", True)))
        lines.extend(render_block("Входящие, голос, 2026", scope_stats(rows, scope, "in", False)))
    lines.extend(voice_vs_text(rows))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Статистика выгрузки Telegram")
    parser.add_argument("--input", default=str(DEFAULT_OUT))
    parser.add_argument("--output", default=str(DUMP / "статистика.md"))
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"Нет файла {path}")
    DUMP.mkdir(parents=True, exist_ok=True)
    rows = load_rows(path)
    if args.inventory_only:
        inv = collect_inventory(rows)
        text = "\n".join(inventory_markdown(inv)) + "\n"
        out = DUMP / "инвентаризация.md"
        out.write_text(text, encoding="utf-8")
        print(text)
        print(f"записано: {out}")
        return
    out = Path(args.output)
    write_report(rows, out)
    print(f"записано: {out} ({out.stat().st_size} байт), строк корпуса {len(rows)}")


if __name__ == "__main__":
    main()
