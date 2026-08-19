#!/usr/bin/env python3
"""В текстах: «.0M.» → «.M.»; даты ДД.ММ.ГГГГ не трогаем."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/Users/max/Desktop/CURSOR")
SKIP_DIRS = {".git", "node_modules", ".obsidian"}
EXT = {".md", ".html", ".cursorrules", ".json", ".txt"}
SEG = re.compile(r"\.0([1-9])(\.)")


def strip_once(s: str) -> str:
    out: list[str] = []
    i = 0
    while True:
        m = SEG.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out)
        st, ed = m.start(), m.end()
        out.append(s[i:st])
        digit = m.group(1)
        after = s[m.end() : m.end() + 4]
        date_dd_mm_yyyy = (
            st > 0
            and s[st - 1].isdigit()
            and after.isdigit()
            and len(after) == 4
            and after.startswith("20")
        )
        keep = date_dd_mm_yyyy
        out.append(m.group(0) if keep else f".{digit}{m.group(2)}")
        i = ed


def strip_all(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = strip_once(s)
    return s


def main() -> None:
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        if set(path.parts) & SKIP_DIRS:
            continue
        if path.suffix.lower() not in EXT and path.name not in (".cursorrules",):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = strip_all(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
