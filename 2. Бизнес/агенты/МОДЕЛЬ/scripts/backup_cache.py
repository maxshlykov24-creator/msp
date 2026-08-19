#!/usr/bin/env python3
"""Копирует JSON кэша агента @модель в _ДАННЫЕ/backup/ с таймстемпом."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

FILES = ("models_cache.json", "fast_track_rules.json", "metadata.json")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "_ДАННЫЕ"
    backup_dir = data_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    for name in FILES:
        src = data_dir / name
        if not src.is_file():
            raise SystemExit(f"Нет файла: {src}")
        dst = backup_dir / f"{Path(name).stem}_{ts}.json"
        shutil.copy2(src, dst)
        print(f"OK {dst.relative_to(root)}")


if __name__ == "__main__":
    main()
