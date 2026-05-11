#!/usr/bin/env python3
"""
Снять ведущий 0 в сегментах «.0M.» (M=1..9) в именах папок под 1. Профиль.
Исключений по веткам нет.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE = Path("/Users/max/Desktop/CURSOR/Личное/1. Профиль")
SEG = re.compile(r"\.0([1-9])(\.)")


def strip_segments(name: str) -> str:
    s = name
    while True:
        n = SEG.sub(r".\1\2", s, count=1)
        if n == s:
            break
        s = n
    return s


def new_name_for(rel: Path) -> str | None:
    r = rel.relative_to(BASE)
    old = r.name
    new = strip_segments(old)
    return new if new != old else None


def main() -> None:
    pairs: list[tuple[Path, Path]] = []
    for p in BASE.rglob("*"):
        if not p.is_dir():
            continue
        nn = new_name_for(p)
        if nn is None:
            continue
        dst = p.parent / nn
        pairs.append((p, dst))

    pairs.sort(key=lambda t: (-len(t[0].parts), str(t[0])))
    for src, dst in pairs:
        if not src.exists():
            print("SKIP", src)
            continue
        if dst.exists():
            raise FileExistsError(f"{dst} уже есть при переходе {src.name} → {dst.name}")
        print(f"{src.relative_to(BASE)} → {dst.name}")
        src.rename(dst)


if __name__ == "__main__":
    main()
