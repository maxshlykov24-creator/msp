#!/usr/bin/env python3
"""Пайплайн Поставка 29.07 → flat / Прогон / Блокеры / (опц.) МС."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from flatten_xlsx import parse_supply_xlsx  # noqa: E402
from sheets_io import ensure_sheet, get_values, sheets_service, write_values  # noqa: E402

DEFAULT_XLSX = (
    Path(__file__).resolve().parents[2]
    / "поставки"
    / "2026-07-29_Поставка-для-внесения.xlsx"
)

config.set_run(
    flat="Поставка 29.07 flat",
    run="Прогон 29.07",
    block="Блокеры 29.07",
    external_code="postavka-2026-07-29",
    description="Поставка 29.07",
    moment="2026-07-29 12:00:00.000",
)

# после set_run — импорт runner (читает config.* при вызове функций)
import run_postavka_1507 as runner  # noqa: E402


def ensure_model_abbr_smoking_5223(svc) -> None:
    """Дописать Smoking 5223 → S5223 в Справочник, если нет."""
    spr = get_values(svc, config.SHEET_SPR, "A:N")
    for r in spr[1:]:
        r = list(r) + [""] * 14
        m = str(r[12]).replace("\xa0", " ").strip().lower()
        if m == "smoking 5223":
            print(f"spr already has Smoking 5223 → {r[13]!r}")
            return
    # найти первую пустую строку в колонках M/N (индекс с 1)
    target_row = None
    for i, r in enumerate(spr[1:], start=2):
        r = list(r) + [""] * 14
        if not str(r[12]).strip() and not str(r[13]).strip():
            # предпочитаем строки где уже есть соседние данные моделей выше
            target_row = i
            break
    if target_row is None:
        target_row = len(spr) + 1
    range_a1 = f"{config.SHEET_SPR}!M{target_row}:N{target_row}"
    body = {"values": [["Smoking 5223", "S5223"]]}
    svc.spreadsheets().values().update(
        spreadsheetId=config.SPREADSHEET_ID,
        range=range_a1,
        valueInputOption="RAW",
        body=body,
    ).execute()
    print(f"APPEND spr M{target_row}: Smoking 5223 → S5223")


def upload_flat(xlsx: Path) -> dict:
    flat, stats = parse_supply_xlsx(xlsx)
    svc = sheets_service()
    ensure_model_abbr_smoking_5223(svc)
    sid = ensure_sheet(svc, config.SHEET_FLAT, rows=max(len(flat) + 50, 200), cols=14)
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

    if args.apply:
        runner.apply_to_ms(variants_only=False, limit=0)
        return
    if args.apply_variants:
        runner.apply_to_ms(variants_only=True, limit=args.limit or 1)
        return
    if args.dry_run or not args.upload_flat:
        # по умолчанию dry-run после upload
        runner.dry_run()


if __name__ == "__main__":
    main()
