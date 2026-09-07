#!/usr/bin/env python3
"""Пайплайн Поставка 28.08 остатки → flat / Прогон / Блокеры / (опц.) МС."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from flatten_xlsx import parse_xlsx  # noqa: E402
from sheets_io import ensure_sheet, get_values, sheets_service, write_values  # noqa: E402

DEFAULT_XLSX = (
    Path(__file__).resolve().parents[2]
    / "поставки"
    / "2026-08-28_Поставка-остатки.xlsx"
)

config.set_run(
    flat="Поставка 28.08 остатки flat",
    run="Прогон 28.08 остатки",
    block="Блокеры 28.08 остатки",
    external_code="postavka-2026-08-28-ostatki",
    description="Поставка 28.08 остатки",
    moment="2026-08-28 18:00:00.000",
)

import run_postavka_1507 as runner  # noqa: E402

NEW_BRANDS: list = []
NEW_MODELS = [
    ("over41d6", "over41d6"),
]


def _clean(v) -> str:
    return str(v or "").replace("\xa0", " ").strip()


def ensure_spr_abbrs(svc) -> None:
    spr = get_values(svc, config.SHEET_SPR, "A:N")
    while len(spr) < 2:
        spr.append([])

    def _has(col_idx: int, name: str) -> bool:
        for r in spr[1:]:
            r = list(r) + [""] * 14
            if _clean(r[col_idx]).lower() == name.lower():
                return True
        return False

    def _first_empty(col_a: int, col_b: int) -> int:
        for i, r in enumerate(spr[1:], start=2):
            r = list(r) + [""] * 14
            if not _clean(r[col_a]) and not _clean(r[col_b]):
                return i
        return len(spr) + 1

    updates = []
    for name, abbr in NEW_BRANDS:
        if _has(10, name):
            print(f"spr brand already has {name}")
            continue
        row = _first_empty(10, 11)
        updates.append((f"{config.SHEET_SPR}!K{row}:L{row}", [[name, abbr]]))
        while len(spr) < row:
            spr.append([])
        r = list(spr[row - 1]) + [""] * 14
        r[10], r[11] = name, abbr
        spr[row - 1] = r
        print(f"APPEND spr K{row}: {name} → {abbr}")
    for name, abbr in NEW_MODELS:
        if _has(12, name):
            print(f"spr model already has {name}")
            continue
        row = _first_empty(12, 13)
        updates.append((f"{config.SHEET_SPR}!M{row}:N{row}", [[name, abbr]]))
        while len(spr) < row:
            spr.append([])
        r = list(spr[row - 1]) + [""] * 14
        r[12], r[13] = name, abbr
        spr[row - 1] = r
        print(f"APPEND spr M{row}: {name} → {abbr}")

    for rng, values in updates:
        svc.spreadsheets().values().update(
            spreadsheetId=config.SPREADSHEET_ID,
            range=rng,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()


def upload_flat(xlsx: Path) -> dict:
    flat, stats = parse_xlsx(xlsx)
    svc = sheets_service()
    ensure_spr_abbrs(svc)
    sid = ensure_sheet(svc, config.SHEET_FLAT, rows=max(len(flat) + 50, 200), cols=16)
    write_values(svc, config.SHEET_FLAT, flat, sheet_id=sid)
    print(
        f"FLAT uploaded '{config.SHEET_FLAT}' rows={len(flat)-1} "
        f"models={stats['models']} sum_fact={stats['sum_fact_qty']}"
    )
    if stats["qty_mismatches"]:
        print("WARN qty fact≠Всего:", stats["qty_mismatches"])
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--upload-flat", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-json", type=Path, default=None)
    ap.add_argument("--apply-variants", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.upload_flat or args.dry_run:
        stats = upload_flat(args.xlsx)
        if args.summary_json:
            args.summary_json.write_text(
                json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    if args.apply or args.apply_variants:
        ensure_spr_abbrs(sheets_service())
        flat, stats = parse_xlsx(args.xlsx)
        print(
            f"local flat models={stats['models']} rows={stats['flat_rows']} "
            f"fact={stats['sum_fact_qty']}",
            flush=True,
        )
        runner.apply_to_ms(
            variants_only=bool(args.apply_variants and not args.apply),
            limit=(args.limit or 1) if args.apply_variants and not args.apply else 0,
            flat_rows=flat,
        )
        return
    if args.dry_run or not args.upload_flat:
        runner.dry_run()


if __name__ == "__main__":
    main()
