#!/usr/bin/env python3
"""
Два прохода под 1. Профиль:
(A) Дети зоны «N. Имя» с простым «k. Имя» → «N.k. Имя» (без ведущего нуля;
    зона 3: первый ребёнок — «3.1.», далее «3.2.»…«3.9.», десятый — «3.10.»).
(B) Дети папки строго «N.kk. Имя» (ровно два числовых сегмента) с «m. Имя» → «N.kk.m. Имя».

Папки уже вида «N.kk.m. Имя» дальше не расширяем — глубже остаётся «1.», «2.»
(в т.ч. всё под …/Нейросети/, Библия/С комментарием, Церковь/Мои служения).
"""
from __future__ import annotations

import re
import os
from pathlib import Path

BASE = Path("/Users/max/Desktop/CURSOR/Личное/1. Профиль")

RE_ZONE = re.compile(r"^(\d+)\.\s+(.+)$")
RE_SIMPLE = re.compile(r"^(\d+)\.\s+(.+)$")
RE_MM = re.compile(r"^(\d+)\.(\d+)\.\s+(.+)$")  # только N.MM. Название (MM — 1..99, напр. 01 или 10)
RE_MMK = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.\s+(.+)$")  # N.MM.k. — не родитель для (B)


def child_dirs(p: Path) -> list[Path]:
    return sorted([x for x in p.iterdir() if x.is_dir()], key=lambda x: x.name)


def parse_simple(name: str) -> tuple[int, str] | None:
    m = RE_SIMPLE.match(name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def is_mm_only(name: str) -> bool:
    return RE_MM.match(name) is not None and RE_MMK.match(name) is None


def phase_a() -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for zone in child_dirs(BASE):
        zm = RE_ZONE.match(zone.name)
        if not zm:
            continue
        znum = int(zm.group(1))
        kids = child_dirs(zone)
        # Уже в формате N.MM. — пропускаем элемент
        to_rename: list[tuple[int, str, Path]] = []
        for k in kids:
            if is_mm_only(k.name):
                continue
            ps = parse_simple(k.name)
            if not ps:
                continue
            idx, rest = ps
            to_rename.append((idx, rest, k))
        to_rename.sort(key=lambda t: t[0])
        for j, (_, rest, old_p) in enumerate(to_rename, start=1):
            if znum == 3:
                if j == 1:
                    new_name = f"3.1. {rest}"
                elif j == 10:
                    new_name = f"3.10. {rest}"
                else:
                    new_name = f"3.{j}. {rest}"
            else:
                new_name = f"{znum}.{j}. {rest}"
            if old_p.name == new_name:
                continue
            out.append((old_p, old_p.parent / new_name))
    return out


def phase_b() -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for p in BASE.rglob("*"):
        if not p.is_dir():
            continue
        rel = p.relative_to(BASE)
        if len(rel.parts) < 2:
            continue
        if not is_mm_only(p.name):
            continue
        mm = RE_MM.match(p.name)
        assert mm
        N, KK = int(mm.group(1)), mm.group(2)
        kids = child_dirs(p)
        to_rename: list[tuple[int, str, Path]] = []
        for k in kids:
            if is_mm_only(k.name) or RE_MMK.match(k.name):
                continue
            ps = parse_simple(k.name)
            if not ps:
                continue
            idx, rest = ps
            to_rename.append((idx, rest, k))
        to_rename.sort(key=lambda t: t[0])
        for m_idx, (_, rest, old_p) in enumerate(to_rename, start=1):
            new_name = f"{N}.{KK}.{m_idx}. {rest}"
            if old_p.name == new_name:
                continue
            out.append((old_p, old_p.parent / new_name))
    return out


def apply_moves(moves: list[tuple[Path, Path]]) -> None:
    moves.sort(key=lambda t: -len(str(t[0]).split(os.sep)))
    for src, dst in moves:
        if not src.exists():
            print("SKIP missing", src)
            continue
        if dst.exists():
            raise FileExistsError(dst)
        src.rename(dst)
        print(f"OK {src.relative_to(BASE)} → {dst.name}")


def main() -> None:
    a = phase_a()
    apply_moves(a)
    b = phase_b()
    apply_moves(b)


if __name__ == "__main__":
    main()
