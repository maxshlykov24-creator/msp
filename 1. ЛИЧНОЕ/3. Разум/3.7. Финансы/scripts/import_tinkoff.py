#!/usr/bin/env python3
"""Import Tinkoff CSV from inbox/ into data/operations.jsonl with auto-categorization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Need PyYAML: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
DATA = ROOT / "data"
OPS_PATH = DATA / "operations.jsonl"
REVIEW_PATH = DATA / "review_queue.json"
RULES_PATH = ROOT / "rules.yaml"

# Possible header aliases (Tinkoff export variants)
HEADER_MAP = {
    "дата операции": "date_op",
    "дата платежа": "date_pay",
    "номер карты": "card",
    "статус": "status",
    "сумма операции": "amount_op",
    "валюта операции": "currency_op",
    "сумма платежа": "amount_pay",
    "валюта платежа": "currency_pay",
    "кэшбэк": "cashback",
    "категория": "tinkoff_category",
    "mcc": "mcc",
    "описание": "description",
    "бонусы (включая кэшбэк)": "bonuses",
    "округление на инвесткопилку": "invest_round",
    "сумма операции с округлением": "amount_rounded",
}


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_amount(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(raw: str) -> str | None:
    """Return ISO date YYYY-MM-DD."""
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_datetime(raw: str) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    return None


def op_id(date: str, amount: float, description: str, card: str) -> str:
    key = f"{date}|{amount:.2f}|{description.strip().lower()}|{card}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def load_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def normalize_row(raw: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k is None:
            continue
        key = HEADER_MAP.get(k.strip().lower().lstrip("\ufeff"))
        if key:
            out[key] = (v or "").strip()
    return out


def classify(row: dict[str, str], rules: dict, amount: float = 0.0) -> dict[str, Any]:
    desc = row.get("description", "") or ""
    desc_l = desc.lower().strip()
    cat = (row.get("tinkoff_category") or "").strip()
    mcc = str(row.get("mcc") or "").strip()
    if mcc.endswith(".0"):
        mcc = mcc[:-2]

    transfer_patterns = [p.lower() for p in (rules.get("transfer_patterns") or [])]
    expense_groups = {
        "Расходы обязательные",
        "Расходы второстепенные",
        "Расходы на других",
        "Десятина и пожертвования",
        "Кредиты и займы",
        "Расходы бизнес",
        "Сбережения и накопления",
    }

    def hit(article: str, group: str, transfer: bool = False, source: str = "") -> dict:
        is_transfer = transfer or any(p in desc_l for p in transfer_patterns)
        if is_transfer:
            article, group = "Перевод между счетами", "Прочие"
        # Входящие на расходную статью — почти всегда ошибка разметки → review
        if amount > 0 and group in expense_groups and not is_transfer:
            if article != "Кэшбэк":
                return {
                    "article": "Неразмечено",
                    "group": "Прочие",
                    "transfer": False,
                    "needs_review": True,
                    "rule_source": source + "+sign_guard",
                }
        needs_review = article == "Неразмечено"
        if cat == "Переводы" and not is_transfer and source in ("tinkoff_category", "fallback"):
            article, group = "Неразмечено", "Прочие"
            needs_review = True
        return {
            "article": article,
            "group": group,
            "transfer": is_transfer,
            "needs_review": needs_review or article == "Неразмечено",
            "rule_source": source,
        }

    # 1) exact description
    exact = rules.get("exact_description") or {}
    for k, v in exact.items():
        if desc_l == str(k).lower().strip():
            return hit(v["article"], v["group"], bool(v.get("transfer")), "exact")

    # 2) contains
    for rule in rules.get("contains") or []:
        if str(rule["match"]).lower() in desc_l:
            return hit(rule["article"], rule["group"], bool(rule.get("transfer")), "contains")

    # 3) MCC
    mcc_map = rules.get("mcc") or {}
    if mcc and mcc in mcc_map:
        v = mcc_map[mcc]
        return hit(v["article"], v["group"], False, "mcc")

    # 4) tinkoff category
    tcat = rules.get("tinkoff_category") or {}
    if cat in tcat:
        v = tcat[cat]
        return hit(v["article"], v["group"], bool(v.get("transfer")), "tinkoff_category")

    return hit("Неразмечено", "Прочие", False, "fallback")


def detect_delimiter(sample: str) -> str:
    if sample.count(";") >= sample.count(","):
        return ";"
    return ","


def read_csv_file(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    # skip BOM / empty
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delim = detect_delimiter(first_line)
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    rows = []
    for raw in reader:
        norm = normalize_row({(k or ""): (v or "") for k, v in raw.items()})
        if norm:
            rows.append(norm)
    return rows


def import_file(path: Path, rules: dict, existing: set[str]) -> tuple[list[dict], list[dict], int]:
    """Returns (new_ops, review_items, skipped_dupes)."""
    new_ops: list[dict] = []
    review: list[dict] = []
    skipped = 0

    for row in read_csv_file(path):
        status = (row.get("status") or "OK").upper()
        if status and status not in ("OK", "AUTHORIZED", ""):
            # skip failed / reversed if marked
            if status in ("FAILED", "DEAD"):
                continue

        amount = parse_amount(row.get("amount_pay"))
        if amount is None:
            amount = parse_amount(row.get("amount_op"))
        if amount is None:
            continue

        date = parse_date(row.get("date_pay") or "") or parse_date(row.get("date_op") or "")
        if not date:
            continue

        desc = row.get("description") or ""
        card = row.get("card") or ""
        oid = op_id(date, amount, desc, card)
        if oid in existing:
            skipped += 1
            continue
        existing.add(oid)

        cls = classify(row, rules, amount=amount)
        # Convention: expense = money out = negative in Tinkoff; we store signed bank amount
        # and also abs for convenience
        op = {
            "id": oid,
            "date": date,
            "datetime": parse_datetime(row.get("date_op") or "") or date,
            "amount": amount,
            "amount_abs": abs(amount),
            "direction": "income" if amount > 0 else "expense" if amount < 0 else "zero",
            "article": cls["article"],
            "group": cls["group"],
            "transfer": cls["transfer"],
            "description": desc,
            "card": card,
            "mcc": (row.get("mcc") or "").replace(".0", ""),
            "tinkoff_category": row.get("tinkoff_category") or "",
            "account": "Тинькофф / Максим",
            "source_file": path.name,
            "rule_source": cls["rule_source"],
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        }
        new_ops.append(op)
        if cls["needs_review"]:
            review.append(
                {
                    "id": oid,
                    "date": date,
                    "amount": amount,
                    "description": desc,
                    "tinkoff_category": op["tinkoff_category"],
                    "suggested_article": cls["article"],
                    "suggested_group": cls["group"],
                }
            )
    return new_ops, review, skipped


def append_ops(path: Path, ops: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for op in ops:
            f.write(json.dumps(op, ensure_ascii=False) + "\n")


def merge_review(path: Path, items: list[dict]) -> int:
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    by_id = {x["id"]: x for x in existing}
    for it in items:
        by_id[it["id"]] = it
    merged = list(by_id.values())
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(merged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Tinkoff CSV into operations journal")
    parser.add_argument("--file", type=Path, help="Single CSV (default: all inbox/*.csv)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rules = load_yaml(RULES_PATH)
    existing = load_existing_ids(OPS_PATH)

    files: list[Path]
    if args.file:
        files = [args.file]
    else:
        files = sorted(INBOX.glob("*.csv"))
    if not files:
        print(f"No CSV in {INBOX}. Put a Tinkoff export there.")
        return 1

    total_new = 0
    total_review = 0
    total_skip = 0
    all_review: list[dict] = []

    for fp in files:
        ops, review, skipped = import_file(fp, rules, existing)
        total_skip += skipped
        if args.dry_run:
            print(f"[dry-run] {fp.name}: +{len(ops)} ops, review {len(review)}, dupes {skipped}")
            for op in ops[:5]:
                print(f"  {op['date']} {op['amount']:+.2f} → {op['group']}/{op['article']} | {op['description'][:40]}")
            continue
        append_ops(OPS_PATH, ops)
        all_review.extend(review)
        total_new += len(ops)
        total_review += len(review)
        print(f"{fp.name}: +{len(ops)} ops, review {len(review)}, dupes {skipped}")

    if not args.dry_run and all_review:
        n = merge_review(REVIEW_PATH, all_review)
        print(f"Review queue: {n} items → {REVIEW_PATH}")
    elif not args.dry_run:
        if not REVIEW_PATH.exists():
            REVIEW_PATH.write_text("[]", encoding="utf-8")
        print("Review queue: empty")

    print(f"Done. New={total_new}, review={total_review}, skipped_dupes={total_skip}")
    print(f"Journal: {OPS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
