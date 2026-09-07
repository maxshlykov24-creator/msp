#!/usr/bin/env python3
"""Перенос сырья из 1. ЛИЧНОЕ/_ВХОДЯЩЕЕ/{1.1,1.3,1.4} в 1. ЛИЧНОЕ/2. Дух/.

Соответствие нумерации (инбокс «как будто под 1. Профиль», целевая ветка в профиле — 2. Дух):
- 1.1. Изучение/…  → 2.1. Изучение/… (1.1.k. → 2.1.k.; Библия: подпапка «С комментарием» → «1. С комментарием»)
- 1.3. Служение/…  → 2.3. Служение/… (1.3.k. → 2.3.k.)
- 1.4. Прочее/     → 2.1. Изучение/2.1.4. Прочее/

Не трогает: README.md, .DS_Store, папку Проповеди и прочие ветки инбокса.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "Личное" / "_ВХОДЯЩЕЕ"
PROFILE_DUKH = ROOT / "Личное" / "2. Дух"
IZ = PROFILE_DUKH / "2.1. Изучение"
SL = PROFILE_DUKH / "2.3. Служение"
PROCHEE_214 = IZ / "2.1.4. Прочее"

SKIP_NAMES = {".DS_Store"}
SKIP_TOP = {"Проповеди"}


def map_izuchenie_rest(rest: str) -> Path:
    """rest — относительный путь после «1.1. Изучение/»."""
    if "/" not in rest:
        return IZ / rest
    head, tail = rest.split("/", 1)
    # 1.1.1. Библия / С комментарием / *.md
    if head == "1.1.1. Библия" and tail.startswith("С комментарием/"):
        inner = tail[len("С комментарием/") :]
        return IZ / "2.1.1. Библия" / "1. С комментарием" / inner
    mapped_head = head
    for old_prefix, new_prefix in (
        ("1.1.1.", "2.1.1."),
        ("1.1.2.", "2.1.2."),
        ("1.1.3.", "2.1.3."),
        ("1.1.4.", "2.1.4."),
    ):
        if head.startswith(old_prefix):
            mapped_head = new_prefix + head[len(old_prefix) :]
            break
    return IZ / mapped_head / tail


def map_sluzhenie_rest(rest: str) -> Path:
    """rest — после «1.3. Служение/»."""
    if "/" not in rest:
        return SL / rest
    head, tail = rest.split("/", 1)
    mapped_head = head
    for old_prefix, new_prefix in (("1.3.1.", "2.3.1."), ("1.3.2.", "2.3.2.")):
        if head.startswith(old_prefix):
            mapped_head = new_prefix + head[len(old_prefix) :]
            break
    return SL / mapped_head / tail


def dest_for_inbox_file(rel: Path) -> Path | None:
    parts = rel.parts
    if not parts:
        return None
    top = parts[0]
    if top in SKIP_TOP:
        return None
    if top == "README.md" and len(parts) == 1:
        return None
    rel_s = rel.as_posix()
    if rel_s.startswith("1.1. Изучение/"):
        rest = rel_s[len("1.1. Изучение/") :]
        return map_izuchenie_rest(rest)
    if rel_s.startswith("1.3. Служение/"):
        rest = rel_s[len("1.3. Служение/") :]
        return map_sluzhenie_rest(rest)
    if rel_s.startswith("1.4. Прочее/"):
        rest = rel_s[len("1.4. Прочее/") :]
        return PROCHEE_214 / rest
    return None


def main() -> None:
    PROCHEE_214.mkdir(parents=True, exist_ok=True)
    moved = 0
    skipped = 0
    for path in sorted(INBOX.rglob("*")):
        if not path.is_file():
            continue
        if path.name in SKIP_NAMES:
            continue
        if path.name == "README.md" and path.parent == INBOX:
            continue
        try:
            rel = path.relative_to(INBOX)
        except ValueError:
            continue
        dest = dest_for_inbox_file(rel)
        if dest is None:
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            print(f"конфликт (уже есть): {dest}", file=sys.stderr)
            sys.exit(1)
        shutil.move(str(path), str(dest))
        moved += 1
    print(f"перенесено файлов: {moved}; пропущено (не из 1.1/1.3/1.4): {skipped}")


if __name__ == "__main__":
    main()
