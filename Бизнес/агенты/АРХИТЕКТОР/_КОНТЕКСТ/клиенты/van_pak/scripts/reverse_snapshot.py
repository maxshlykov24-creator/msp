#!/usr/bin/env python3
"""
Фаза 0 обратного переноса: снэпшот OLD-аккаунта по 2026 ДО изменений.
Считаем count и Σsum по типам документов, чтобы потом сверить прирост.

  PYTHONPATH=. python3 reverse_snapshot.py
"""
from __future__ import annotations

import json
from pathlib import Path

from ms_common import (
    OLD_TOKEN,
    SCRIPT_DIR,
    YEAR_START,
    get_all,
    make_session,
    old_api_available,
)

DOC_TYPES = ["customerorder", "demand", "invoiceout", "paymentin", "factureout"]


def main() -> None:
    old_s = make_session(OLD_TOKEN)
    if not old_api_available(old_s):
        print("BLOCKER: OLD API 403")
        return

    snap: dict = {"year_start": YEAR_START, "by_type": {}}
    for dt in DOC_TYPES:
        rows = get_all(old_s, dt, filt=f"moment>={YEAR_START}")
        total = sum((r.get("sum") or 0) for r in rows)
        snap["by_type"][dt] = {"count": len(rows), "sum": total}
        print(f"OLD {dt}: count={len(rows)} Σsum={total}")

    out = SCRIPT_DIR / "reverse_snapshot.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Снэпшот: {out}")


if __name__ == "__main__":
    main()
