"""Отчёт = в тексте есть хэштег."""

from __future__ import annotations

import re

HASHTAG_RE = re.compile(r"#[^\s#]{1,64}")


def extract_hashtag(text: str | None) -> str | None:
    if not text:
        return None
    m = HASHTAG_RE.search(text)
    return m.group(0) if m else None
