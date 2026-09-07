#!/usr/bin/env python3
"""Метрики стиля владельца по трём кругам: work, personal, close.

Читает _выгрузки/сообщения.jsonl и voice_cache.jsonl, считает то,
из чего собирается профиль тона: длина, серийность, пунктуация,
эмодзи, приветствия, скорость ответа, время суток, доля голосовых.
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DUMP = BASE / "_выгрузки" / "сообщения.jsonl"
CACHE = BASE / "_выгрузки" / "voice_cache.jsonl"
CLOSE = BASE / "_выгрузки" / "close_circle.json"
OUT = BASE / "_выгрузки" / "метрики_тона.md"

MSK = timezone(timedelta(hours=3))
EMOJI = re.compile(
    "[\U0001f300-\U0001faff\u2600-\u27bf\u2190-\u21ff\u2b00-\u2bff\ufe0f]"
)
WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
GREET = re.compile(
    r"^\W*(привет|здравствуй|доброе утро|добрый день|добрый вечер|хай|"
    r"приветствую|доброго|салют|шалом|мир вам)",
    re.IGNORECASE,
)


def load_close() -> set:
    if not CLOSE.exists():
        return set()
    data = json.loads(CLOSE.read_text(encoding="utf-8"))
    return {int(x) for x in data.get("chat_ids", [])}


def load_voice_texts() -> dict:
    texts = {}
    if not CACHE.exists():
        return texts
    for line in CACHE.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        uid = row.get("msg_uid")
        text = (row.get("text") or "").strip()
        if uid and text:
            texts[uid] = text
    return texts


def circle_of(row: dict, close_ids: set) -> str:
    if row.get("chat_id") in close_ids:
        return "close"
    return row.get("scope") or "personal"


def pct(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    return f"{100.0 * part / whole:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()

    close_ids = load_close()
    voice_texts = load_voice_texts()

    # накопители по кругам
    mine = defaultdict(list)          # circle -> [row]
    by_chat = defaultdict(lambda: defaultdict(list))  # circle -> chat -> [row]

    for line in DUMP.open(encoding="utf-8"):
        row = json.loads(line)
        if (row.get("date") or "") < args.since:
            continue
        c = circle_of(row, close_ids)
        by_chat[c][row["chat_id"]].append(row)
        if row.get("direction") == "out":
            mine[c].append(row)

    lines = ["# Метрики тона владельца", ""]
    lines.append(f"Окно: с {args.since}. Расшифровано голосовых: {len(voice_texts)}.")
    lines.append("")

    for circle in ("work", "close", "personal"):
        rows = mine.get(circle) or []
        if not rows:
            continue
        title = {
            "work": "РАБОЧИЙ",
            "close": "БЛИЗКИЙ КРУГ",
            "personal": "ЛИЧНЫЙ (остальные)",
        }[circle]
        lines.append(f"## {title}")
        lines.append("")

        texts, lens, words_all = [], [], Counter()
        voice_n = text_n = 0
        emoji_msgs = caps_msgs = greet_msgs = 0
        qmark = excl = dots = 0
        no_end_dot = 0
        hours = Counter()
        for r in rows:
            kind = r.get("kind")
            body = (r.get("text") or "").strip()
            if kind in ("voice", "video_note"):
                voice_n += 1
                body = voice_texts.get(r["msg_uid"], "").strip()
                if not body:
                    continue
                texts.append(("voice", body))
            elif body:
                text_n += 1
                texts.append(("text", body))
            else:
                continue
            dt = datetime.fromisoformat(r["date"]).astimezone(MSK)
            hours[dt.hour] += 1
            lens.append(len(body))
            words_all.update(w.lower() for w in WORD.findall(body))
            if EMOJI.search(body):
                emoji_msgs += 1
            letters = [ch for ch in body if ch.isalpha()]
            if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.6:
                caps_msgs += 1
            if GREET.match(body):
                greet_msgs += 1
            qmark += body.count("?")
            excl += body.count("!")
            dots += body.count("...")
            if body and body[-1] not in ".!?:)…":
                no_end_dot += 1

        typed = [t for k, t in texts if k == "text"]
        spoken = [t for k, t in texts if k == "voice"]
        total = len(texts)

        lines.append(f"- Моих сообщений с текстом: **{total}** "
                     f"(набрано {len(typed)}, голосом {len(spoken)}; "
                     f"всего голосовых {voice_n})")
        if lens:
            lines.append(f"- Длина, символов: медиана **{int(statistics.median(lens))}**, "
                         f"средняя {int(statistics.mean(lens))}, "
                         f"90-й перцентиль {int(sorted(lens)[int(len(lens)*0.9)])}, "
                         f"максимум {max(lens)}")
        if typed:
            tl = [len(t) for t in typed]
            lines.append(f"  - только набранные: медиана {int(statistics.median(tl))}")
        if spoken:
            sl = [len(t) for t in spoken]
            lines.append(f"  - только голосом (в знаках расшифровки): "
                         f"медиана {int(statistics.median(sl))}")
        lines.append(f"- Эмодзи хотя бы один: **{pct(emoji_msgs, total)}**")
        lines.append(f"- КАПС целиком: {pct(caps_msgs, total)}")
        lines.append(f"- Начинается с приветствия: **{pct(greet_msgs, total)}**")
        lines.append(f"- Знаков на сообщение: `?` {qmark/total:.2f}, "
                     f"`!` {excl/total:.2f}, `...` {dots/total:.2f}")
        lines.append(f"- Без точки в конце: **{pct(no_end_dot, total)}**")

        # серийность и скорость ответа
        bursts, gaps_reply = [], []
        for chat_rows in by_chat[circle].values():
            chat_rows.sort(key=lambda r: r["date"])
            run = 0
            for i, r in enumerate(chat_rows):
                if r.get("direction") == "out":
                    run += 1
                    if i and chat_rows[i - 1].get("direction") == "in":
                        gap = r.get("seconds_since_prev")
                        if isinstance(gap, (int, float)) and 0 < gap < 86400:
                            gaps_reply.append(gap)
                else:
                    if run:
                        bursts.append(run)
                    run = 0
            if run:
                bursts.append(run)
        if bursts:
            multi = sum(1 for b in bursts if b > 1)
            lines.append(f"- Серия подряд: медиана **{statistics.median(bursts):.0f}**, "
                         f"средняя {statistics.mean(bursts):.2f}, "
                         f"серий длиннее одного {pct(multi, len(bursts))}, "
                         f"максимум {max(bursts)}")
        if gaps_reply:
            g = sorted(gaps_reply)
            med = g[len(g) // 2]
            fast = sum(1 for x in g if x <= 300)
            slow = sum(1 for x in g if x > 3600)
            lines.append(f"- Ответ на входящее: медиана **{med/60:.1f} мин**, "
                         f"до 5 минут {pct(fast, len(g))}, "
                         f"дольше часа {pct(slow, len(g))}")
        if hours:
            top_h = ", ".join(f"{h}:00 ({pct(n, total)})"
                              for h, n in hours.most_common(4))
            night = sum(n for h, n in hours.items() if h < 7 or h >= 23)
            lines.append(f"- Часы пик (МСК): {top_h}; ночью 23–07 {pct(night, total)}")

        # лексика: убираем служебные
        stop = set("""и в не на я что а с как это по для то же но у мне ты
        да он его к так вы все мы бы за от есть или если там уже нет ну
        меня тебе там их когда он она они был была быть будет чтобы""".split())
        chars = [w for w, n in words_all.most_common(200)
                 if w not in stop and len(w) > 2][:35]
        lines.append(f"- Частые слова: {', '.join(chars[:25])}")
        lines.append("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано: {OUT}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
