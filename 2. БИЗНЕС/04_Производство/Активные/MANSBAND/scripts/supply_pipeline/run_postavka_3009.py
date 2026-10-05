#!/usr/bin/env python3
"""Пайплайн Поставка 30.09 сорочки → flat / Прогон / Блокеры / МС."""
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
    / "2026-09-30_Сорочки-для-внесения.xlsx"
)

# Пустой двойник в справочнике (0 модификаций). Живой товар — с остатком вариаций.
EMPTY_PARENT = "bd2d503c-a742-11f0-0a80-0595001e8bef"
LIVE_PARENT = "bd2d5832-a742-11f0-0a80-0595001e8bf1"

config.set_run(
    flat="Поставка 30.09 flat",
    run="Прогон 30.09",
    block="Блокеры 30.09",
    external_code="postavka-2026-09-30",
    description="Поставка 30.09 сорочки",
    moment="2026-09-30 12:00:00.000",
)

import run_postavka_1507 as runner  # noqa: E402


def _clean(v) -> str:
    return str(v or "").replace("\xa0", " ").strip()


def ensure_shirt_parent(svc) -> None:
    """Справочник: UUID «Сорочка» должен указывать на товар с модификациями."""
    spr = get_values(svc, config.SHEET_SPR, "Q:R")
    target_row = None
    current = ""
    for i, r in enumerate(spr, start=1):
        r = list(r) + ["", ""]
        if _clean(r[0]) == "Сорочка":
            target_row = i
            current = _clean(r[1])
            break
    if target_row is None:
        raise RuntimeError("В справочнике нет строки Наименование=Сорочка")
    if current == LIVE_PARENT:
        print(f"spr parent Сорочка already live R{target_row}")
        return
    if current and current != EMPTY_PARENT:
        raise RuntimeError(
            f"UUID Сорочка в справочнике {current}, ждал {EMPTY_PARENT} или {LIVE_PARENT}"
        )
    svc.spreadsheets().values().update(
        spreadsheetId=config.SPREADSHEET_ID,
        range=f"'{config.SHEET_SPR}'!R{target_row}",
        valueInputOption="RAW",
        body={"values": [[LIVE_PARENT]]},
    ).execute()
    print(f"spr parent Сорочка R{target_row}: {current or 'пусто'} → {LIVE_PARENT}")


def upload_flat(xlsx: Path) -> dict:
    flat, stats = parse_supply_xlsx(xlsx)
    svc = sheets_service()
    ensure_shirt_parent(svc)
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

    if args.upload_flat or args.dry_run or args.apply or args.apply_variants:
        if not args.xlsx.exists():
            raise SystemExit(f"нет файла {args.xlsx}")

    if args.upload_flat or args.dry_run:
        stats = upload_flat(args.xlsx)
        if args.summary_json:
            args.summary_json.write_text(
                json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    if args.apply or args.apply_variants:
        ensure_shirt_parent(sheets_service())
        flat, stats = parse_supply_xlsx(args.xlsx)
        print(
            f"local flat models={stats['models']} rows={stats['flat_rows']} "
            f"fact={stats['sum_fact_qty']}",
            flush=True,
        )
        if stats["qty_mismatches"] or stats.get("kit_spr_mismatches"):
            raise SystemExit(f"ABORT parse: {stats['qty_mismatches']} {stats.get('kit_spr_mismatches')}")
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
