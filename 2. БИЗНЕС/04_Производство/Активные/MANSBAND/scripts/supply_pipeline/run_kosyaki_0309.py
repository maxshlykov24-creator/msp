#!/usr/bin/env python3
"""Пайплайн Косяки 03.09 → flat / Прогон / Блокеры / (опц.) МС."""
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
from sheets_io import ensure_sheet, sheets_service, write_values  # noqa: E402

DEFAULT_XLSX = (
    Path(__file__).resolve().parents[2]
    / "поставки"
    / "2026-09-03_Косяки.xlsx"
)

config.set_run(
    flat="Косяки 03.09 flat",
    run="Прогон косяки 03.09",
    block="Блокеры косяки 03.09",
    external_code="kosyaki-2026-09-03",
    description="Косяки 03.09",
    moment="2026-09-03 12:00:00.000",
)

import run_postavka_1507 as runner  # noqa: E402


def upload_flat(xlsx: Path) -> dict:
    flat, stats = parse_xlsx(xlsx)
    svc = sheets_service()
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
