"""Question deck loader and session queue builder."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

QUESTIONS_FILE = Path(__file__).resolve().parent.parent / "questions.json"


@dataclass
class Question:
    number: int
    category: str
    tag: str   # "лёгкий" | "тепло" | "чуть теплее"
    text: str


_questions: list[Question] | None = None


def get_questions() -> list[Question]:
    global _questions
    if _questions is None:
        with QUESTIONS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        _questions = [Question(**d) for d in data]
    return _questions


def get_question_by_number(n: int) -> Question | None:
    return next((q for q in get_questions() if q.number == n), None)


def _interleave_categories(qs: list[Question]) -> list[Question]:
    """Round-robin through categories to avoid same-category streaks."""
    by_cat: dict[str, list[Question]] = defaultdict(list)
    for q in qs:
        by_cat[q.category].append(q)
    for cat_qs in by_cat.values():
        random.shuffle(cat_qs)

    result: list[Question] = []
    cats = list(by_cat.keys())
    random.shuffle(cats)
    while any(by_cat.values()):
        for cat in cats:
            if by_cat[cat]:
                result.append(by_cat[cat].pop(0))
    return result


def build_queue(
    preset: str,
    used_numbers: set[int],
) -> list[tuple[int, int]]:
    """Return ordered list of (question_number, phase_label) for a new session.

    preset:
        "meeting1" — only лёгкий + тепло (no чуть теплее)
        "full"     — all three tiers

    Phase labels:
        1 = лёгкий, 2 = тепло, 3 = чуть теплее

    Order: лёгкий first (with category interleave), then тепло, then чуть теплее.
    Within each tier, categories are interleaved to maximise variety.
    """
    all_qs = get_questions()
    available = [q for q in all_qs if q.number not in used_numbers]

    # If deck is exhausted, reset silently
    if not available:
        available = list(all_qs)

    light = [q for q in available if q.tag == "лёгкий"]
    warm = [q for q in available if q.tag == "тепло"]
    warmer = [] if preset == "meeting1" else [q for q in available if q.tag == "чуть теплее"]

    ordered = (
        [(q, 1) for q in _interleave_categories(light)]
        + [(q, 2) for q in _interleave_categories(warm)]
        + [(q, 3) for q in _interleave_categories(warmer)]
    )

    return [(q.number, phase) for q, phase in ordered]


def phase_name(phase: int) -> str:
    return {1: "Фаза 1 · разогрев", 2: "Фаза 2 · тёплые", 3: "Фаза 3 · глубже"}.get(phase, "")


def tag_emoji(tag: str) -> str:
    return {"лёгкий": "🌱", "тепло": "🌿", "чуть теплее": "🌸"}.get(tag, "❓")
