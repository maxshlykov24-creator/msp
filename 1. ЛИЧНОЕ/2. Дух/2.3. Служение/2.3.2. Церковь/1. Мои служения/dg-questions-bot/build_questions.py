#!/usr/bin/env python3
"""Parse the question deck .md file and produce questions.json.

Run from inside dg-questions-bot/:
    python build_questions.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The deck file is one level up (in the same "1. Мои служения" folder)
DECK_FILE = Path(__file__).resolve().parent.parent / "2026_Колода_лидеров_ДГ_200_ВОПРОСОВ.md"
OUT_FILE = Path(__file__).resolve().parent / "questions.json"

CAT_RE = re.compile(r"^## \d+\.\s+(.+)$")
Q_RE = re.compile(r"^`(\d{3})`\s+\[([^\]]+)\]\s+(.+)$")

STOP_SECTIONS = {"Встреча 1", "Только текст"}


def main() -> None:
    if not DECK_FILE.exists():
        print(f"ERROR: deck file not found: {DECK_FILE}", file=sys.stderr)
        sys.exit(1)

    text = DECK_FILE.read_text(encoding="utf-8")
    questions: list[dict] = []
    current_category: str | None = None

    for line in text.splitlines():
        line = line.strip()

        cat_m = CAT_RE.match(line)
        if cat_m:
            cat = cat_m.group(1).strip()
            if cat in STOP_SECTIONS:
                break
            current_category = cat
            continue

        q_m = Q_RE.match(line)
        if q_m and current_category:
            questions.append(
                {
                    "number": int(q_m.group(1)),
                    "category": current_category,
                    "tag": q_m.group(2).strip(),
                    "text": q_m.group(3).strip(),
                }
            )

    OUT_FILE.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ Parsed {len(questions)} questions → {OUT_FILE}")

    # Basic sanity checks
    tags = {q["tag"] for q in questions}
    print(f"   Tags found: {tags}")
    cats = {q["category"] for q in questions}
    print(f"   Categories: {len(cats)}")


if __name__ == "__main__":
    main()
